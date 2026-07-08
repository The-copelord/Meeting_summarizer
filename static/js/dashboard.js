// ── Dashboard — upload page provider/model selectors ─────────────────────────
// Settings (API keys) now live at /settings as a separate page.
// This file handles the provider+model dropdowns on the main upload page.

let _providerModels = {};  // session cache: { groq: [...], openai: [...] }
const PROVIDER_COLORS = {
    groq: '#fa6800',
    anthropic: '#d97757',
    claude: '#a78bfa',
    openai: '#22c55e',
    together: '#3b82f6',
    mistral: '#f59e0b',
};

function updateProviderSelectColor(selectId) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    sel.style.color = PROVIDER_COLORS[sel.value] || 'var(--ink)';
}
async function loadUploadSelectors() {
    try {
        // Small delay ensures token variable is populated after login
        await new Promise(r => setTimeout(r, 50));

        const res = await apiFetch('/user/settings', 'GET');
        if (!res.ok) return;
        const data = await res.json();

        const provider = data.selected_provider || 'groq';
        const model = data.selected_model || '';

        const provSel = document.getElementById('uploadProvider');
        if (provSel) {
            provSel.value = provider;
            updateProviderSelectColor('uploadProvider');
        }

        // Always force-fetch — ignore session cache on initial page load
        // so models appear immediately without needing to switch provider
        delete _providerModels[provider];
        await loadProviderModels(provider, model, 'modelSelect');

        // Load trial usage banner
        loadTrialBanner(data.trial);
    } catch (e) { /* silent */ }
}

async function loadProviderModels(provider, selectedModel, selectId) {
    const sel = document.getElementById(selectId);
    if (!sel) return;

    // Use session cache
    if (_providerModels[provider]) {
        populateModelDropdown(selectId, _providerModels[provider], selectedModel);
        return;
    }

    sel.innerHTML = '<option value="">Loading...</option>';

    try {
        const res = await apiFetch(`/user/models/${provider}`, 'GET');
        if (!res.ok) {
            sel.innerHTML = `<option value="">No key for ${provider} — add in Settings</option>`;
            return;
        }
        const data = await res.json();
        if (data.error || !data.models || !data.models.length) {
            sel.innerHTML = `<option value="">No models — add ${provider} key in Settings</option>`;
            return;
        }
        _providerModels[provider] = data.models;
        populateModelDropdown(selectId, data.models, selectedModel);
    } catch (e) {
        sel.innerHTML = '<option value="">Failed to load models</option>';
    }
}

function populateModelDropdown(selectId, models, selectedModel) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    sel.innerHTML = models.map(m =>
        `<option value="${m}" ${m === selectedModel ? 'selected' : ''}>${m}</option>`
    ).join('');
}

async function onUploadProviderChange() {
    const prov = document.getElementById('uploadProvider')?.value || 'groq';
    updateProviderSelectColor('uploadProvider');   // ← add this
    await loadProviderModels(prov, null, 'modelSelect');
}

function loadTrialBanner(trial) {
    const banner = document.getElementById('trialBanner');
    const pipsEl = document.getElementById('trialPips');
    const counterEl = document.getElementById('trialCounter');
    const exhaustedEl = document.getElementById('trialExhaustedMsg');
    const subEl = document.getElementById('trialSubMsg');
    const uploadBtn = document.getElementById('uploadBtn');

    if (!banner || !trial) return;
    banner.style.display = 'flex';

    if (trial.is_subscribed) {
        pipsEl.innerHTML = '';
        counterEl.textContent = '';
        if (subEl) subEl.style.display = 'inline';
        return;
    }

    const limit = trial.uploads_limit || 3;
    const used = trial.uploads_used || 0;
    const remaining = trial.uploads_remaining ?? Math.max(0, limit - used);

    pipsEl.innerHTML = Array.from({ length: limit }, (_, i) => {
        const cls = i < used ? 'used' : 'free';
        return `<div class="trial-pip ${cls}" title="${i < used ? 'Used' : 'Available'}"></div>`;
    }).join('');

    counterEl.innerHTML = `<strong>${remaining}</strong> of ${limit} free files left`;

    if (trial.trial_exhausted) {
        if (exhaustedEl) exhaustedEl.style.display = 'inline';
        // Disable upload button and sync trial flag into upload.js
        if (uploadBtn) {
            uploadBtn.disabled = true;
            uploadBtn.title = 'Trial limit reached';
        }
        if (typeof setTrialExhausted === 'function') setTrialExhausted(true);
    }
}

// Save selected provider+model when user uploads
async function saveSelectedModel() {
    const provider = document.getElementById('uploadProvider')?.value || 'groq';
    const model = document.getElementById('modelSelect')?.value;
    if (!model) return;
    await apiFetch('/user/settings', 'PUT', {
        selected_provider: provider,
        selected_model: model,
    }).catch(() => { });
}