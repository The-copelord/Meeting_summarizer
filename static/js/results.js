// ── Results view ──────────────────────────────────────────────────────────────
async function viewJob(jobId, filename) {
    currentJobId = jobId;
    const el = document.getElementById('resultsJobId');
    el.textContent = truncateFilename(filename || ('Job ' + jobId.slice(0, 8) + '…'), 12);
    el.setAttribute('data-fullname', filename);
    showPage('results');

    const res = await apiFetch(`/get_status/${jobId}`, 'GET');
    if (!res.ok) return;
    const { status } = await res.json();

    if (status === 'done') {
        fetchAndRenderSummary(jobId);
    } else {
        document.getElementById('processingCard').style.display = 'block';
        document.getElementById('resultsContent').style.display = 'none';
        document.getElementById('processingStatus').textContent = status;
        watchJobWithSse(jobId);
    }
}

async function fetchAndRenderSummary(jobId) {
    const res = await apiFetch(`/get_summary?job_id=${jobId}`, 'GET');
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById('processingCard').style.display = 'none';
    document.getElementById('resultsContent').style.display = 'block';

    const s = data.summary || {};
    document.getElementById('overviewText').textContent = s.overview || 'No overview available.';

    renderList('listKeyPoints', s.key_points);
    renderList('listDecisions', s.decisions);
    renderList('listActions', s.action_items);
    renderList('listNextSteps', s.next_steps);

    const chunkEl = document.getElementById('chunkList');
    const chunks = s.chunk_summaries || [];
    chunkEl.innerHTML = chunks.length
        ? chunks.map((c, i) => `<div class="chunk-item"><div class="chunk-num">Segment ${i + 1}</div><div class="chunk-text">${esc(c)}</div></div>`).join('')
        : '<p class="empty-list">No segment summaries available.</p>';

    const wasExpanded = transcriptExpanded;
    const transcriptBox = document.getElementById('transcriptBox');
    transcriptBox.textContent = data.transcript || 'No transcript available.';
    if (!wasExpanded) {
        transcriptBox.style.maxHeight = '380px';
        document.getElementById('expandToggle').textContent = 'Show full transcript ↓';
    } else {
        transcriptBox.style.maxHeight = 'none';
        document.getElementById('expandToggle').textContent = 'Collapse ↑';
    }
}

function renderList(id, items) {
    const el = document.getElementById(id);
    el.innerHTML = (items && items.length)
        ? items.map(i => `<li>${esc(i)}</li>`).join('')
        : '<li class="empty-list" style="background:none;padding-left:0">None noted</li>';
}

function toggleTranscript() {
    transcriptExpanded = !transcriptExpanded;
    const el = document.getElementById('transcriptBox');
    const btn = document.getElementById('expandToggle');
    el.style.maxHeight = transcriptExpanded ? 'none' : '380px';
    btn.textContent = transcriptExpanded ? 'Collapse ↑' : 'Show full transcript ↓';
}