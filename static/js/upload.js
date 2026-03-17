// ── File selection ────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');

    dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', e => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
    });
    fileInput.addEventListener('change', () => {
        if (fileInput.files[0]) setFile(fileInput.files[0]);
    });
});

function setFile(f) {
    selectedFile = f;
    document.getElementById('badgeName').textContent = f.name;
    document.getElementById('badgeSize').textContent = fmtBytes(f.size);
    document.getElementById('fileBadge').classList.add('visible');
    document.getElementById('uploadBtn').disabled = false;
    document.getElementById('clearFileBtn').style.display = 'inline-block';
}

function clearFile() {
    selectedFile = null;
    document.getElementById('fileInput').value = '';
    document.getElementById('fileBadge').classList.remove('visible');
    document.getElementById('uploadBtn').disabled = true;
    document.getElementById('clearFileBtn').style.display = 'none';
}

// ── Upload + Queue ────────────────────────────────────────────────────────────
async function doUploadAndAnalyse() {
    if (!selectedFile) return;
    const btn = document.getElementById('uploadBtn');
    btn.disabled = true;
    btn.textContent = 'Uploading…';

    try {
        // 1. Upload
        const form = new FormData();
        form.append('file', selectedFile);
        const upRes = await fetch(`${API}/uploadfile`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: form,
        });
        if (!upRes.ok) { const e = await upRes.json(); throw new Error(e.detail || 'Upload failed'); }
        const { job_id } = await upRes.json();

        // 2. Queue
        btn.textContent = 'Queuing…';
        const anRes = await apiFetch('/analyse', 'POST', { job_id });
        if (!anRes.ok) { const e = await anRes.json(); throw new Error(e.detail || 'Queue failed'); }

        showToast('Job queued! Processing in background.');
        clearFile();
        await loadJobs();
        watchJobWithSse(job_id);

    } catch (e) {
        showToast('Error: ' + e.message);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Upload & Analyse';
    }
}