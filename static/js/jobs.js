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
    <div class="job-card ${j.job_id === currentJobId ? 'active-job' : ''}" onclick="viewJob('${j.job_id}', '${esc(j.original_filename || j.job_id)}')">
      <span class="job-status-dot dot-${j.status}"></span>
      <div class="job-info">
        <div class="job-filename">${esc(j.original_filename || j.file_path.split(/[\\\/]/).pop())}</div>
        <div class="job-meta">${j.job_id.slice(0, 8)} · ${fmtDate(j.created_at)}</div>
      </div>
      <span class="job-status-badge badge-${j.status}">${j.status}</span>
      ${j.status === 'done' ? `<button class="job-view-btn" onclick="event.stopPropagation();viewJob('${j.job_id}', '${esc(j.original_filename || j.job_id)}')">View →</button>` : ''}
    </div>
  `).join('');
}

// ── SSE ───────────────────────────────────────────────────────────────────────
let sseSource = null;

function startJobPolling() {
    // No-op — polling replaced by SSE per job
}

function stopJobPolling() {
    closeSse();
}

function closeSse() {
    if (sseSource) { sseSource.close(); sseSource = null; }
}

function watchJobWithSse(jobId) {
    closeSse();

    sseSource = new EventSource(`/job-stream/${jobId}?token=${encodeURIComponent(token)}`);

    sseSource.addEventListener('status', async e => {
        document.getElementById('processingStatus').textContent = e.data;
        document.getElementById('pollIndicator').textContent = 'Status: ' + e.data;
        await loadJobs();
    });

    sseSource.addEventListener('done', async () => {
        closeSse();
        await loadJobs();
        fetchAndRenderSummary(currentJobId || jobId);
        document.getElementById('pollIndicator').textContent = 'Done ✓';
    });

    sseSource.addEventListener('error', async e => {
        closeSse();
        await loadJobs();
        document.getElementById('processingStatus').textContent = 'error';
        document.getElementById('pollIndicator').textContent = 'Job failed';
        showToast('Job failed: ' + (e.data || 'Unknown error'));
    });

    sseSource.onerror = () => {
        closeSse();
    };
}