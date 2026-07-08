// ── Trial state (set by dashboard.js after loadTrialBanner) ──────────────────
let _trialExhausted = false;

function setTrialExhausted(val) {
    _trialExhausted = val;
}

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
    // Only enable upload if trial not exhausted
    if (!_trialExhausted) {
        document.getElementById('uploadBtn').disabled = false;
    }
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

    // Re-check trial status before uploading
    if (_trialExhausted) {
        showToast('Trial limit reached — please upgrade to continue.');
        return;
    }

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

        if (!upRes.ok) {
            const e = await upRes.json();
            // Handle trial exhaustion from server
            if (upRes.status === 402 && e.detail?.code === 'TRIAL_LIMIT_REACHED') {
                _trialExhausted = true;
                btn.disabled = true;
                btn.title = 'Trial limit reached';
                // Refresh banner to show exhausted state
                refreshTrialBanner();
                showTrialExhaustedModal();
                return;
            }
            throw new Error(e.detail?.message || e.detail || 'Upload failed');
        }

        const { job_id, trial } = await upRes.json();

        // 2. Update trial banner with fresh data from response
        if (trial) refreshTrialBannerFromData(trial);

        // 3. Save selected provider+model then queue
        btn.textContent = 'Queuing…';
        await saveSelectedModel();
        const anRes = await apiFetch('/analyse', 'POST', { job_id });
        if (!anRes.ok) { const e = await anRes.json(); throw new Error(e.detail || 'Queue failed'); }

        showToast('Job queued! Processing in background.');
        clearFile();
        await loadJobs();

    } catch (e) {
        showToast('Error: ' + e.message);
    } finally {
        if (!_trialExhausted) {
            btn.disabled = false;
            btn.textContent = 'Upload & Analyse';
        }
    }
}

// ── Trial banner refresh helpers ──────────────────────────────────────────────
async function refreshTrialBanner() {
    try {
        const res = await apiFetch('/user/trial-status', 'GET');
        if (!res.ok) return;
        const t = await res.json();
        refreshTrialBannerFromData(t);
    } catch (e) { /* silent */ }
}

function refreshTrialBannerFromData(t) {
    const pipsEl = document.getElementById('trialPips');
    const counterEl = document.getElementById('trialCounter');
    const exhaustedEl = document.getElementById('trialExhaustedMsg');
    const banner = document.getElementById('trialBanner');
    const uploadBtn = document.getElementById('uploadBtn');

    if (!banner) return;
    if (t.is_subscribed) return;

    const limit = t.uploads_limit || 3;
    const used = t.uploads_used || 0;
    const remaining = t.uploads_remaining ?? Math.max(0, limit - used);

    if (pipsEl) {
        pipsEl.innerHTML = Array.from({ length: limit }, (_, i) => {
            const cls = i < used ? 'used' : 'free';
            return `<div class="trial-pip ${cls}" title="${i < used ? 'Used' : 'Available'}"></div>`;
        }).join('');
    }
    if (counterEl) counterEl.innerHTML = `<strong>${remaining}</strong> of ${limit} free files left`;

    if (t.trial_exhausted || t.uploads_remaining === 0) {
        _trialExhausted = true;
        if (exhaustedEl) exhaustedEl.style.display = 'inline';
        if (uploadBtn) { uploadBtn.disabled = true; uploadBtn.title = 'Trial limit reached'; }
    }
}

function showTrialExhaustedModal() {
    // Inline toast-style notification directing to settings
    showToast('Trial limit reached — go to Settings to upgrade.');
    // Highlight settings button
    const settingsBtn = document.getElementById('settingsBtn');
    if (settingsBtn) {
        settingsBtn.style.outline = '2px solid #f97316';
        settingsBtn.style.outlineOffset = '2px';
        setTimeout(() => { settingsBtn.style.outline = ''; settingsBtn.style.outlineOffset = ''; }, 4000);
    }
}