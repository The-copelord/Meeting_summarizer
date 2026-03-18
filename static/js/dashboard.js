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