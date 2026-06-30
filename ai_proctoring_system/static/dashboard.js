/* ============================================
   AI Proctoring System — Student Dashboard JS
   Client-Side Face Analysis via MediaPipe JS
   ============================================ */

let seconds = 0;
const video = document.getElementById('webcam');
const canvas = document.getElementById('capture-canvas');
const ctx = canvas.getContext('2d');
const cameraLoading = document.getElementById('camera-loading');

// ---- Tracking State (client-side) ----
let blink_counter = 0;
let closed_frames = 0;
let focus_frames = 0;
let total_frames = 0;
let look_away_count = 0;
let last_gaze = 'Center';
let isProcessing = false;
let faceMesh = null;
let faceMeshReady = false;

// ---- Landmark Indices (same as Python MediaPipe 468-point mesh) ----
const LEFT_EYE  = [33, 160, 158, 133, 153, 144];
const RIGHT_EYE = [362, 385, 387, 263, 373, 380];
const NOSE_TIP    = 1;
const LEFT_CORNER = 33;
const RIGHT_CORNER = 263;
const EAR_THRESHOLD = 0.20;

// ---- Eye Aspect Ratio ----
function eyeAspectRatio(landmarks, indices) {
    const p = indices.map(i => landmarks[i]);
    const dist = (a, b) => Math.sqrt(
        Math.pow(a.x - b.x, 2) + Math.pow(a.y - b.y, 2)
    );
    const A = dist(p[1], p[5]);
    const B = dist(p[2], p[4]);
    const C = dist(p[0], p[3]);
    if (C === 0) return 0.3;
    return (A + B) / (2.0 * C);
}

// ---- Initialize MediaPipe FaceMesh in the browser ----
async function initFaceMesh() {
    if (typeof FaceMesh === 'undefined') {
        console.error('MediaPipe FaceMesh JS library not loaded from CDN.');
        return false;
    }

    try {
        if (cameraLoading) {
            cameraLoading.innerHTML = `
                <span style="width: 28px; height: 28px; border: 3px solid rgba(255,255,255,0.1); border-top-color: var(--primary); border-radius: 50%; animation: spin 1s linear infinite;"></span>
                <span>Loading AI model...</span>
                <span style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">This may take a few seconds on first load</span>
            `;
        }

        faceMesh = new FaceMesh({
            locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/face_mesh/${file}`
        });

        faceMesh.setOptions({
            maxNumFaces: 2,
            refineLandmarks: false,
            minDetectionConfidence: 0.5,
            minTrackingConfidence: 0.5
        });

        faceMesh.onResults(onFaceMeshResults);

        // Warm up with a tiny blank canvas
        const warmup = document.createElement('canvas');
        warmup.width = 64;
        warmup.height = 64;
        await faceMesh.send({ image: warmup });

        faceMeshReady = true;
        console.log('FaceMesh JS initialized successfully.');
        return true;
    } catch (err) {
        console.error('FaceMesh initialization failed:', err);
        return false;
    }
}

// ---- FaceMesh Results Callback ----
function onFaceMeshResults(results) {
    let status = 'No Face';
    let gaze = 'Center';
    let faces = 0;

    if (results.multiFaceLandmarks && results.multiFaceLandmarks.length > 0) {
        status = 'Focused';
        faces = results.multiFaceLandmarks.length;
        const lm = results.multiFaceLandmarks[0];

        // ---- Gaze Direction ----
        const nose  = lm[NOSE_TIP];
        const left  = lm[LEFT_CORNER];
        const right = lm[RIGHT_CORNER];
        const cx = (left.x + right.x) / 2;

        if (nose.x < cx - 0.02) {
            gaze = 'Right';   // mirror flipped
        } else if (nose.x > cx + 0.02) {
            gaze = 'Left';
        } else {
            gaze = 'Center';
        }

        // Count transitions away from center
        if ((gaze === 'Left' || gaze === 'Right') && last_gaze === 'Center') {
            look_away_count++;
        }
        last_gaze = gaze;

        // ---- Eye Aspect Ratio (blink / drowsy detection) ----
        const leftEAR  = eyeAspectRatio(lm, LEFT_EYE);
        const rightEAR = eyeAspectRatio(lm, RIGHT_EYE);
        const ear = (leftEAR + rightEAR) / 2.0;

        if (ear < EAR_THRESHOLD) {
            closed_frames++;
            if (closed_frames >= 8) {
                status = 'Drowsy';
            }
        } else {
            if (closed_frames >= 1 && closed_frames < 8) {
                blink_counter++;
            }
            closed_frames = 0;
        }

        // Multiple faces override
        if (faces > 1) {
            status = 'Multiple Faces';
        }
    }

    // ---- Focus Score ----
    total_frames++;
    if (status === 'Focused') {
        focus_frames++;
    }
    const focus = total_frames > 0 ? Math.round((focus_frames / total_frames) * 100) : 100;

    const data = {
        faces: faces,
        blinks: blink_counter,
        focus: focus,
        gaze: gaze,
        status: status,
        look_away_count: look_away_count,
        cheated: (look_away_count > 20 || blink_counter > 300)
    };

    updateStatsUI(data);
    sendStatsToServer(data);
    isProcessing = false;
}

// ---- Send lightweight JSON to server ----
let lastSendTime = 0;
function sendStatsToServer(data) {
    const now = Date.now();
    if (now - lastSendTime < 1000) return;   // throttle: once per second
    lastSendTime = now;

    fetch('/update_stats', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).catch(err => console.warn('Stats sync failed:', err));
}

// ---- Process loop via requestAnimationFrame ----
function processFrame() {
    if (!faceMeshReady || isProcessing || video.readyState < 2) {
        setTimeout(() => requestAnimationFrame(processFrame), 100);
        return;
    }
    isProcessing = true;
    faceMesh.send({ image: video }).catch(err => {
        console.warn('FaceMesh send error:', err);
        isProcessing = false;
    });
    // ~4 FPS
    setTimeout(() => requestAnimationFrame(processFrame), 250);
}

// ---- Update Dashboard UI ----
function updateStatsUI(data) {
    document.getElementById('faces').textContent = data.faces;
    document.getElementById('blinks').textContent = data.blinks;
    document.getElementById('focus').textContent = data.focus + '%';
    document.getElementById('focus-bar').style.width = data.focus + '%';
    document.getElementById('gaze').textContent = data.gaze;
    document.getElementById('status').textContent = data.status;

    // Status card colour
    const card = document.getElementById('statusCard');
    card.className = 'stat-card';
    if (data.status === 'Focused') {
        card.classList.add('success');
    } else if (data.status === 'Drowsy' || data.status === 'Multiple Faces') {
        card.classList.add('danger');
    } else {
        card.classList.add('warning');
    }

    // Focus bar colour
    const bar = document.getElementById('focus-bar');
    if (data.focus >= 70) {
        bar.style.background = 'var(--success)';
    } else if (data.focus >= 40) {
        bar.style.background = 'var(--warning)';
    } else {
        bar.style.background = 'var(--danger)';
    }
}

// ---- Timer ----
function updateTimer() {
    seconds++;
    const h = String(Math.floor(seconds / 3600)).padStart(2, '0');
    const m = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
    const s = String(seconds % 60).padStart(2, '0');
    document.getElementById('timer').textContent = h + ':' + m + ':' + s;
}

// ---- Download Report ----
function downloadReport() {
    window.location = '/export';
}

// ---- Stop Exam ----
function stopExam() {
    if (confirm('Are you sure you want to stop the exam? This will finish your session and submit your report.')) {
        if (window.captureInterval) {
            clearInterval(window.captureInterval);
        }
        if (video.srcObject) {
            video.srcObject.getTracks().forEach(t => t.stop());
        }
        window.location.href = '/stop_exam';
    }
}

// ---- Fallback: server-side frame processing ----
function captureAndSendFrame() {
    if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

        canvas.toBlob(blob => {
            if (!blob) return;
            const fd = new FormData();
            fd.append('frame', blob, 'frame.jpg');
            fetch('/process_frame', { method: 'POST', body: fd })
                .then(r => r.json())
                .then(data => { if (!data.error) updateStatsUI(data); })
                .catch(err => console.warn('Frame process failed:', err));
        }, 'image/jpeg', 0.7);
    }
}

// ---- Boot Sequence ----
const constraints = {
    video: { width: { ideal: 640 }, height: { ideal: 480 } }
};

navigator.mediaDevices.getUserMedia(constraints)
    .then(async stream => {
        console.log('Camera access granted.');
        video.addEventListener('loadedmetadata', () => {
            console.log('Video metadata:', video.videoWidth, 'x', video.videoHeight);
        });

        video.srcObject = stream;
        await video.play();

        // Try client-side MediaPipe first
        console.log('Initialising FaceMesh JS...');
        const ok = await initFaceMesh();

        if (cameraLoading) {
            cameraLoading.style.display = 'none';
        }

        if (ok) {
            console.log('Client-side face analysis active.');
            requestAnimationFrame(processFrame);
        } else {
            console.warn('FaceMesh JS unavailable — falling back to server-side.');
            window.captureInterval = setInterval(captureAndSendFrame, 500);
        }
    })
    .catch(err => {
        console.error('Camera access failed:', err);
        if (cameraLoading) {
            cameraLoading.innerHTML = `
                <span style="font-size: 24px;">🛑</span>
                <span style="font-weight: 600; color: var(--danger);">Webcam Access Failed</span>
                <span style="font-size: 12px; text-align: center; max-width: 280px; color: var(--text-muted); margin-top: 4px;">
                    ${err.name === 'NotReadableError'
                        ? 'Camera is already in use by another application (OBS, Zoom, etc.)'
                        : err.message || 'Please ensure camera is connected and permission granted.'}
                </span>
            `;
        }
    });

// Start timer
setInterval(updateTimer, 1000);