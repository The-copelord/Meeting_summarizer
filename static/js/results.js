// results.js — SPA results view removed.
// Results are now served at /job/{job_id} via job.html.
// This file is kept as a stub so index.html script tag doesn't 404.

function toggleTranscript() {
    transcriptExpanded = !transcriptExpanded;
    const el = document.getElementById('transcriptBox');
    const btn = document.getElementById('expandToggle');
    if (el) el.style.maxHeight = transcriptExpanded ? 'none' : '380px';
    if (btn) btn.textContent = transcriptExpanded ? 'Collapse ↑' : 'Show full transcript ↓';
}

function renderList(id, items) {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = (items && items.length)
        ? items.map(i => `<li>${esc(i)}</li>`).join('')
        : '<li class="empty-list" style="background:none;padding-left:0">None noted</li>';
}