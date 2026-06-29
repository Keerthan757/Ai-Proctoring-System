/* ============================================
   AI Proctoring System — Admin Panel JS
   ============================================ */

let seconds = 0;

/**
 * Fetch proctoring stats from the backend and update admin panel UI.
 * Uses AbortController for a 3-second timeout to avoid hanging requests.
 */
async function updateStats() {
    try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 3000);

        const res = await fetch('/stats', { signal: controller.signal });
        clearTimeout(timeout);
        const data = await res.json();

        // Update stat values
        document.getElementById('admin-faces').textContent = data.faces;
        document.getElementById('admin-blinks').textContent = data.blinks;
        document.getElementById('admin-focus').textContent = data.focus + '%';
        document.getElementById('admin-gaze').textContent = data.gaze;
        document.getElementById('admin-status').textContent = data.status;

        // Focus bar
        const focusBar = document.getElementById('admin-focus-bar');
        if (focusBar) {
            focusBar.style.width = data.focus + '%';
            if (data.focus >= 70) {
                focusBar.style.background = 'var(--success)';
            } else if (data.focus >= 40) {
                focusBar.style.background = 'var(--warning)';
            } else {
                focusBar.style.background = 'var(--danger)';
            }
        }

        // Status card color
        const statusCard = document.getElementById('admin-status-card');
        if (statusCard) {
            statusCard.className = 'admin-stat-card';
            if (data.status === 'Focused') {
                statusCard.classList.add('accent-success');
            } else if (data.status === 'Drowsy' || data.status === 'Multiple Faces') {
                statusCard.classList.add('accent-danger');
            } else {
                statusCard.classList.add('accent-warning');
            }
        }

    } catch (err) {
        if (err.name !== 'AbortError') {
            console.warn('Stats fetch failed:', err);
        }
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

// Start intervals
setInterval(updateStats, 1000);
setInterval(updateTimer, 1000);
