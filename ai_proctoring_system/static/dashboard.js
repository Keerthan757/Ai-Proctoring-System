/* ============================================
   AI Proctoring System — Student Dashboard JS
   ============================================ */

let seconds = 0;
const video = document.getElementById('webcam');
const canvas = document.getElementById('capture-canvas');
const ctx = canvas.getContext('2d');

const cameraLoading = document.getElementById('camera-loading');

// Request user camera access with robust fallback constraints
const constraints = {
    video: {
        width: { ideal: 640 },
        height: { ideal: 480 }
    }
};

navigator.mediaDevices.getUserMedia(constraints)
    .then(stream => {
        console.log("Camera access granted, initializing video tracks...");

        const startCapturing = () => {
            if (cameraLoading) {
                cameraLoading.style.display = 'none';
            }
            if (!window.captureInterval) {
                console.log("Starting frame capture...");
                window.captureInterval = setInterval(captureAndSendFrame, 500); // 2 frames per second
            }
        };

        // Event listeners to hide loading overlay and start capturing
        video.addEventListener('loadedmetadata', () => {
            console.log("Video metadata loaded. Resolution:", video.videoWidth, "x", video.videoHeight);
            startCapturing();
        });
        video.addEventListener('playing', () => {
            console.log("Video stream is now playing.");
            startCapturing();
        });

        // Assign stream
        video.srcObject = stream;
        
        // Force play to ensure browser plays it
        video.play()
            .then(() => {
                console.log("Webcam video.play() resolved successfully.");
                startCapturing();
            })
            .catch(err => {
                console.warn("Video play promise was blocked or failed, waiting for metadata:", err);
            });

        // Immediate fallback check
        if (video.readyState >= 2) { // HAVE_CURRENT_DATA or higher
            startCapturing();
        }
    })
    .catch(err => {
        console.error("Camera access failed:", err);
        if (cameraLoading) {
            cameraLoading.innerHTML = `
                <span style="font-size: 24px;">🛑</span>
                <span style="font-weight: 600; color: var(--danger);">Webcam Access Failed</span>
                <span style="font-size: 12px; text-align: center; max-width: 280px; color: var(--text-muted); margin-top: 4px;">
                    ${err.name === "NotReadableError" ? "Camera is already in use by another application (OBS, Zoom, etc.)" : err.message || "Please ensure your camera is connected and you have granted permission."}
                </span>
            `;
        }
        alert("Camera Access Required: Please allow camera access in your browser to take the proctored exam.");
    });

/**
 * Capture frame from local video, convert to blob, and send to server.
 */
function captureAndSendFrame() {
    if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        
        // Convert to JPEG with 70% quality for small payload size
        canvas.toBlob(blob => {
            if (!blob) return;
            const formData = new FormData();
            formData.append('frame', blob, 'frame.jpg');
            
            fetch('/process_frame', {
                method: 'POST',
                body: formData
            })
            .then(res => res.json())
            .then(data => {
                if (data.error) return;
                updateStatsUI(data);
            })
            .catch(err => console.warn('Frame process failed:', err));
        }, 'image/jpeg', 0.7);
    }
}

/**
 * Update UI with proctoring stats from backend data
 */
function updateStatsUI(data) {
    document.getElementById('faces').textContent = data.faces;
    document.getElementById('blinks').textContent = data.blinks;
    document.getElementById('focus').textContent = data.focus + '%';
    document.getElementById('focus-bar').style.width = data.focus + '%';
    document.getElementById('gaze').textContent = data.gaze;
    document.getElementById('status').textContent = data.status;

    // Status card color
    const statusCard = document.getElementById('statusCard');
    statusCard.className = 'stat-card'; // reset
    if (data.status === 'Focused') {
        statusCard.classList.add('success');
    } else if (data.status === 'Drowsy' || data.status === 'Multiple Faces') {
        statusCard.classList.add('danger');
    } else {
        statusCard.classList.add('warning');
    }

    // Focus bar color
    const focusBar = document.getElementById('focus-bar');
    if (data.focus >= 70) {
        focusBar.style.background = 'var(--success)';
    } else if (data.focus >= 40) {
        focusBar.style.background = 'var(--warning)';
    } else {
        focusBar.style.background = 'var(--danger)';
    }
}

/**
 * Increment and display the session timer in HH:MM:SS format.
 */
function updateTimer() {
    seconds++;
    const hrs  = String(Math.floor(seconds / 3600)).padStart(2, '0');
    const mins = String(Math.floor((seconds % 3600) / 60)).padStart(2, '0');
    const secs = String(seconds % 60).padStart(2, '0');
    document.getElementById('timer').textContent = hrs + ':' + mins + ':' + secs;
}

/**
 * Trigger CSV report download.
 */
function downloadReport() {
    window.location = '/export';
}

/**
 * Stop the exam and redirect to backend stop_exam route.
 */
function stopExam() {
    if (confirm("Are you sure you want to stop the exam? This will finish your session and submit your report.")) {
        // Stop the frame capture interval
        if (window.captureInterval) {
            clearInterval(window.captureInterval);
            window.captureInterval = null;
        }
        // Stop all webcam video tracks
        if (video.srcObject) {
            video.srcObject.getTracks().forEach(track => track.stop());
        }
        // Redirect to /stop_exam
        window.location.href = '/stop_exam';
    }
}

// Start Timer Interval
setInterval(updateTimer, 1000);