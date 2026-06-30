"""
AI Proctoring System - Flask Backend
=====================================
Real-time proctoring using MediaPipe Face Mesh for gaze tracking,
blink detection, drowsiness monitoring, and multi-face detection.
"""

# -------------------------
# Imports
# -------------------------
from flask import (
    Flask, Response, render_template, jsonify,
    request, redirect, url_for, send_file,
    session, flash
)
import cv2
import mediapipe as mp
import numpy as np
import csv
from datetime import datetime
import threading
import os
import atexit
import time

# -------------------------
# App Configuration
# -------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'proctoring-secret-key-change-in-prod')

# Admin credentials (override via environment variables in production)
ADMIN_USERNAME = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASS', 'admin123')

# Directory for exported CSV reports
EXPORTS_DIR = os.path.join(app.root_path, 'exports')
os.makedirs(EXPORTS_DIR, exist_ok=True)


# -------------------------
# Camera Manager - Lazy Initialization
# -------------------------
class CameraManager:
    """Thread-safe manager for the webcam and MediaPipe FaceMesh.

    Resources are only allocated when first requested, avoiding
    module-level camera opens that block import and testing.
    """

    def __init__(self):
        self._camera = None
        self._lock = threading.Lock()
        self._face_mesh = None

    def get_camera(self):
        """Return the cv2.VideoCapture instance, opening it lazily."""
        if self._camera is None or not self._camera.isOpened():
            with self._lock:
                if self._camera is None or not self._camera.isOpened():
                    self._camera = cv2.VideoCapture(0)
                    self._camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    self._camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    self._camera.set(cv2.CAP_PROP_FPS, 30)
        return self._camera

    def get_face_mesh(self):
        """Return the FaceMesh instance, creating it lazily."""
        if self._face_mesh is None:
            self._face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=True,
                refine_landmarks=False,
                max_num_faces=2,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
        return self._face_mesh

    def release(self):
        """Release camera and FaceMesh resources."""
        if self._camera and self._camera.isOpened():
            self._camera.release()
        if self._face_mesh:
            self._face_mesh.close()


cam_manager = CameraManager()
atexit.register(cam_manager.release)


# -------------------------
# Thread-Safe State & Memory Map
# -------------------------
state_lock = threading.Lock()

state = {
    'faces': 0,
    'blinks': 0,
    'focus': 100,
    'gaze': 'Center',
    'status': 'Waiting',
    'look_away_count': 0,
    'cheated': False
}

student_details_lock = threading.Lock()
student_details = {}


def register_student(regno, details):
    """Store student details in thread-safe memory mapping."""
    with student_details_lock:
        student_details[regno] = details


def get_student_details(regno):
    """Retrieve student details from thread-safe memory mapping."""
    with student_details_lock:
        return student_details.get(regno, {})


def update_state(**kwargs):
    """Atomically update one or more keys in the shared state dict."""
    with state_lock:
        state.update(kwargs)


def get_state():
    """Return a snapshot copy of the shared state dict."""
    with state_lock:
        return dict(state)


# -------------------------
# Eye Landmarks & Constants
# -------------------------
LEFT_EYE = [33, 160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]

LEFT_CORNER = 33
RIGHT_CORNER = 263
NOSE_TIP = 1

EAR_THRESHOLD = 0.20   # Eye Aspect Ratio threshold for blink detection
FRAME_SKIP = 2          # Run MediaPipe every Nth frame for performance
JPEG_QUALITY = 85       # JPEG encoding quality (0-100)


# -------------------------
# Tracking Variables
# -------------------------
blink_counter = 0
closed_frames = 0
focus_frames = 0
total_frames = 0
look_away_count = 0
last_gaze = 'Center'


# -------------------------
# EAR Calculation
# -------------------------
def eye_aspect_ratio(eye):
    """Compute the Eye Aspect Ratio (EAR) for a set of 6 eye landmarks.

    EAR ≈ 0.2-0.3 when the eye is open and drops toward 0 when closed.
    Formula:  EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
    """
    A = np.linalg.norm(eye[1] - eye[5])
    B = np.linalg.norm(eye[2] - eye[4])
    C = np.linalg.norm(eye[0] - eye[3])

    return (A + B) / (2.0 * C)


# -------------------------
# Screenshot Directory & Helper
# -------------------------
SCREENSHOTS_DIR = os.path.join(app.root_path, 'screenshots')
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

last_screenshot_time = {}
screenshot_lock = threading.Lock()


def save_alert_screenshot(frame, status, regno='unknown'):
    """Save a screenshot of the frame to the screenshots directory if status is an alert."""
    if status not in ['Drowsy', 'Multiple Faces', 'No Face', 'Looked Away']:
        return

    now = time.time()
    with screenshot_lock:
        last_time = last_screenshot_time.get(status, 0)
        # Throttle: save at most one screenshot per alert status every 10 seconds
        if now - last_time < 10:
            return
        last_screenshot_time[status] = now

    # Save the frame
    timestamp = datetime.now().strftime('%H%M%S')
    filename = f"alert_{regno}_{status.replace(' ', '_')}_{timestamp}.jpg"
    filepath = os.path.join(SCREENSHOTS_DIR, filename)

    try:
        cv2.imwrite(filepath, frame)
        print(f"[Proctoring] Saved screenshot: {filename} (Reason: {status})")
    except Exception as e:
        print(f"[Proctoring] Failed to save screenshot: {e}")


# -------------------------
# Real-Time Report Writer & State Reset
# -------------------------
def save_or_update_report(regno):
    """Automatically save or update the CSV report file in real-time during proctoring."""
    if regno == 'unknown':
        return
    details = get_student_details(regno)
    current_state = get_state()

    # The CSV reports in exports directory will have a standard structure
    filename = f'report_{regno}.csv'
    filepath = os.path.join(EXPORTS_DIR, filename)

    try:
        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Time', 'Student', 'RegNo', 'Email', 'Subject',
                'Faces', 'Blinks', 'Focus', 'Gaze', 'Status', 'LookAwayCount', 'Cheated'
            ])
            writer.writerow([
                datetime.now().strftime('%H:%M:%S'),
                details.get('name', 'N/A'),
                details.get('regno', 'N/A'),
                details.get('email', 'N/A'),
                details.get('subject', 'N/A'),
                current_state.get('faces', 0),
                current_state.get('blinks', 0),
                current_state.get('focus', 100),
                current_state.get('gaze', 'Center'),
                current_state.get('status', 'Waiting'),
                current_state.get('look_away_count', 0),
                'Yes' if current_state.get('cheated', False) else 'No'
            ])
    except Exception as e:
        print(f"[Proctoring] Failed to write report file: {e}")


def reset_proctoring_state():
    """Reset all proctoring tracking states to default clean values."""
    global blink_counter, closed_frames, focus_frames, total_frames, look_away_count, last_gaze
    blink_counter = 0
    closed_frames = 0
    focus_frames = 0
    total_frames = 0
    look_away_count = 0
    last_gaze = 'Center'
    update_state(
        faces=0,
        blinks=0,
        focus=100,
        gaze='Center',
        status='Waiting',
        look_away_count=0,
        cheated=False
    )


# -------------------------
# Video Stream Generator
# -------------------------
# Lock for thread-safe access to MediaPipe FaceMesh
face_mesh_lock = threading.Lock()

def analyze_single_frame(frame, regno='unknown'):
    """Analyze a single video frame for face count, gaze direction, and eye blinks."""
    global blink_counter, closed_frames, focus_frames, total_frames, look_away_count, last_gaze

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    face_mesh = cam_manager.get_face_mesh()
    
    with face_mesh_lock:
        results = face_mesh.process(rgb)

    status = 'No Face'
    gaze = 'Center'

    if results.multi_face_landmarks:
        faces = len(results.multi_face_landmarks)
        face = results.multi_face_landmarks[0]
        h, w, _ = frame.shape

        # --- Gaze Direction ---
        nose = face.landmark[NOSE_TIP]
        left = face.landmark[LEFT_CORNER]
        right = face.landmark[RIGHT_CORNER]
        center_x = (left.x + right.x) / 2

        if nose.x < center_x - 0.02:
            gaze = 'Left'
        elif nose.x > center_x + 0.02:
            gaze = 'Right'
        else:
            gaze = 'Center'

        # Count transitions away from Center
        if gaze in ['Left', 'Right'] and last_gaze == 'Center':
            look_away_count += 1
            save_alert_screenshot(frame, 'Looked Away', regno)
        last_gaze = gaze

        # --- Eye Aspect Ratio ---
        left_eye_pts = np.array(
            [[int(face.landmark[i].x * w), int(face.landmark[i].y * h)] for i in LEFT_EYE]
        )
        right_eye_pts = np.array(
            [[int(face.landmark[i].x * w), int(face.landmark[i].y * h)] for i in RIGHT_EYE]
        )

        ear = (eye_aspect_ratio(left_eye_pts) + eye_aspect_ratio(right_eye_pts)) / 2.0

        # --- Blink & Drowsiness Detection ---
        # Note: Since the browser uploads frames at ~4fps, closed frames limits are adjusted:
        if ear < EAR_THRESHOLD:
            closed_frames += 1
            if closed_frames >= 8:  # ~2 seconds at 4fps → drowsy
                status = 'Drowsy'
        else:
            if 1 <= closed_frames < 8:
                blink_counter += 1
            closed_frames = 0
            status = 'Focused'

        # Multiple faces override
        if faces > 1:
            status = 'Multiple Faces'

        update_state(
            faces=faces,
            gaze=gaze,
            status=status,
            blinks=blink_counter,
            look_away_count=look_away_count,
            cheated=(look_away_count > 20 or blink_counter > 300)
        )
    else:
        update_state(
            faces=0,
            status='No Face',
            gaze='Center',
            look_away_count=look_away_count,
            cheated=(look_away_count > 20 or blink_counter > 300)
        )

    # --- Focus Percentage ---
    total_frames += 1
    if status == 'Focused':
        focus_frames += 1
    if total_frames > 0:
        update_state(focus=int((focus_frames / total_frames) * 100))

    # --- Screenshot Detection / Saving ---
    if status in ['Drowsy', 'Multiple Faces', 'No Face']:
        save_alert_screenshot(frame, status, regno)

    # --- Update CSV Report ---
    save_or_update_report(regno)


# -------------------------
# Video Stream Generator
# -------------------------
def generate_frames(regno='unknown'):
    """Yield MJPEG frames with proctoring analysis overlaid (for backwards compatibility/fallback)."""
    frame_count = 0

    while True:
        camera = cam_manager.get_camera()
        success, frame = camera.read()

        if not success:
            time.sleep(0.01)
            continue

        # Mirror the frame so it feels natural to the student
        frame = cv2.flip(frame, 1)
        frame_count += 1

        if frame_count % FRAME_SKIP == 0:
            analyze_single_frame(frame, regno)

        # Encode every frame for smooth video output
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ret:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')


# -------------------------
# Routes
# -------------------------

@app.route('/')
def index():
    """Serve the login page."""
    return render_template('login.html')


@app.route('/login/student', methods=['POST'])
def student_login():
    """Handle student login form submission."""
    reset_proctoring_state()
    session['role'] = 'student'
    session['student'] = {
        'name': request.form['name'],
        'regno': request.form['regno'],
        'email': request.form['email'],
        'subject': request.form['subject']
    }
    register_student(session['student']['regno'], session['student'])
    return redirect(url_for('dashboard'))


@app.route('/login/admin', methods=['POST'])
def admin_login():
    """Handle admin login with credential validation."""
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session['role'] = 'admin'
        session['admin_name'] = username
        return redirect(url_for('admin_panel'))
    else:
        flash('Invalid admin credentials', 'error')
        return redirect(url_for('index'))


@app.route('/dashboard')
def dashboard():
    """Student proctoring dashboard (requires student session)."""
    if session.get('role') != 'student':
        return redirect(url_for('index'))
    return render_template('dashboard.html', student=session['student'])


@app.route('/admin')
def admin_panel():
    """Admin monitoring panel (requires admin session)."""
    if session.get('role') != 'admin':
        return redirect(url_for('index'))
    return render_template('admin.html', admin_name=session.get('admin_name', 'Admin'))


@app.route('/video')
def video():
    """Stream the live MJPEG video feed."""
    student_data = session.get('student', {})
    regno = student_data.get('regno', 'unknown')
    return Response(
        generate_frames(regno),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


@app.route('/stats')
def stats():
    """Return current proctoring state as JSON."""
    return jsonify(get_state())


@app.route('/process_frame', methods=['POST'])
def process_frame():
    """Receive a frame from the client browser, run proctoring analysis, and return stats."""
    if 'frame' not in request.files:
        return jsonify({'error': 'No frame uploaded'}), 400

    file = request.files['frame']
    file_bytes = np.frombuffer(file.read(), np.uint8)
    frame = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

    if frame is not None:
        student_data = session.get('student', {})
        regno = student_data.get('regno', 'unknown')
        analyze_single_frame(frame, regno)

    return jsonify(get_state())


@app.route('/export')
def export():
    """Export the current proctoring snapshot to a timestamped CSV file."""
    current_state = get_state()
    student_data = session.get('student', {})

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'report_{timestamp}.csv'
    filepath = os.path.join(EXPORTS_DIR, filename)

    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'Time', 'Student', 'RegNo', 'Email', 'Subject',
            'Faces', 'Blinks', 'Focus', 'Gaze', 'Status', 'LookAwayCount', 'Cheated'
        ])
        writer.writerow([
            datetime.now().strftime('%H:%M:%S'),
            student_data.get('name', 'N/A'),
            student_data.get('regno', 'N/A'),
            student_data.get('email', 'N/A'),
            student_data.get('subject', 'N/A'),
            current_state['faces'],
            current_state['blinks'],
            current_state['focus'],
            current_state['gaze'],
            current_state['status'],
            current_state.get('look_away_count', 0),
            'Yes' if current_state.get('cheated', False) else 'No'
        ])

    return send_file(filepath, as_attachment=True, download_name=filename)


@app.route('/admin/download/<filename>')
def admin_download(filename):
    """Download a specific student report file from the exports directory."""
    if session.get('role') != 'admin':
        return redirect(url_for('index'))
    return send_from_directory(EXPORTS_DIR, filename, as_attachment=True)


@app.route('/admin/reports')
def admin_reports():
    """List all student reports from the exports directory."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    import glob
    csv_files = glob.glob(os.path.join(EXPORTS_DIR, 'report_*.csv'))
    reports = []

    for file_path in csv_files:
        try:
            with open(file_path, 'r') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                if rows:
                    row = rows[0]
                    # Parse numerical fields for safety
                    blinks = int(row.get('Blinks', 0))
                    look_aways = int(row.get('LookAwayCount', 0))

                    # Cheated condition: looked away > 20 times OR blinks > 300
                    cheated = (look_aways > 20 or blinks > 300)

                    reports.append({
                        'filename': os.path.basename(file_path),
                        'time': row.get('Time', 'N/A'),
                        'name': row.get('Student', 'N/A'),
                        'regno': row.get('RegNo', 'N/A'),
                        'email': row.get('Email', 'N/A'),
                        'subject': row.get('Subject', 'N/A'),
                        'faces': row.get('Faces', 'N/A'),
                        'blinks': blinks,
                        'focus': row.get('Focus', 'N/A'),
                        'gaze': row.get('Gaze', 'N/A'),
                        'status': row.get('Status', 'N/A'),
                        'look_aways': look_aways,
                        'cheated': cheated
                    })
        except Exception as e:
            print(f"Error reading report {file_path}: {e}")

    # Sort reports by timestamp (latest first)
    reports.sort(key=lambda x: x['filename'], reverse=True)
    return jsonify(reports)


@app.route('/admin/screenshots/<regno>')
def admin_screenshots(regno):
    """Return a list of screenshots for the specified student regno."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    files = []
    if os.path.exists(SCREENSHOTS_DIR):
        for f in os.listdir(SCREENSHOTS_DIR):
            if f.startswith(f"alert_{regno}_") and f.endswith(".jpg"):
                files.append(f)

    # Sort screenshots so latest is first
    files.sort(reverse=True)
    return jsonify(files)


@app.route('/screenshots/<path:filename>')
def serve_screenshot(filename):
    """Serve a screenshot from the screenshots directory."""
    if session.get('role') not in ['admin', 'student']:
        return "Unauthorized", 401
    return send_from_directory(SCREENSHOTS_DIR, filename)


@app.route('/stop_exam')
def stop_exam():
    """Handle stopping the exam, writing the final report, and rendering submitted template."""
    student_data = session.get('student', {})
    regno = student_data.get('regno')
    
    if regno:
        # Save or update report one last time with current state
        save_or_update_report(regno)
        
    # Copy student info to pass to the template before clearing session
    student_info = student_data.copy() if student_data else None
    
    # Clear session to log student out
    session.clear()
    
    return render_template('submitted.html', student=student_info)


@app.route('/logout')
def logout():
    """Clear the session and redirect to login."""
    session.clear()
    return redirect(url_for('index'))


# Helper import for serving files safely
from flask import send_from_directory


# -------------------------
# Run App
# -------------------------
if __name__ == '__main__':
    app.run(debug=True, threaded=True)