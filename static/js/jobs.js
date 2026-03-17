// ── Jobs list ─────────────────────────────────────────────────────────────────
async function loadJobs() {
    try {
        const res = await apiFetch('/jobs', 'GET');
        if (!res.ok) return;
        const jobs = await res.json();
        renderJobs(jobs);
    } catch (e) { /* silent */ }
}

function renderJobs(jobs) {
    const el = document.getElementById('jobsList');
    if (!jobs.length) {
        el.innerHTML = '<div class="empty-jobs">No jobs yet. Upload a recording to get started.</div>';
        return;
    }
    el.innerHTML = jobs.map(j => `
    <div class="job-card ${j.job_id === currentJobId ? 'active-job' : ''}" onclick="viewJob('${j.job_id}')">
      <span class="job-status-dot dot-${j.status}"></span>
      <div class="job-info">
        <div class="job-filename">${esc(j.original_filename || j.file_path.split(/[\\\/]/).pop())}</div>
        <div class="job-meta">${j.job_id.slice(0, 8)} · ${fmtDate(j.created_at)}</div>
      </div>
      <span class="job-status-badge badge-${j.status}">${j.status}</span>
      ${j.status === 'done' ? `<button class="job-view-btn" onclick="event.stopPropagation();viewJob('${j.job_id}')">View →</button>` : ''}
    </div>
  `).join('');
}

// Navigate to the dedicated job page
function viewJob(jobId) {
    window.location.href = `/job/${jobId}`;
}

// ── DB Polling ────────────────────────────────────────────────────────────────
let pollInterval = null;

function startJobPolling() {
    stopJobPolling();
    pollInterval = setInterval(async () => {
        await loadJobs();
        const indicator = document.getElementById('pollIndicator');
        if (indicator) indicator.textContent = 'Updated ' + new Date().toLocaleTimeString();
    }, 5000);
}

function stopJobPolling() {
    if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
}

// Keep these as no-ops so other files don't break
function closeSse() { }