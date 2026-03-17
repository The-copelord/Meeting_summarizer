// ── Dashboard / Settings ─────────────────────────────────────────────────────

async function loadSettings() {
    try {
        const res = await apiFetch('/user/settings', 'GET');
        if (!res.ok) return;
        const data = await res.json();

        // Populate settings form fields
        const groqEl = document.getElementById('settingsGroqKey');
        const anthEl = document.getElementById('settingsAnthropicKey');
        if (groqEl) groqEl.value = data.groq_api_key || '';
        if (anthEl) anthEl.value = data.anthropic_api_key || '';

        // Populate both model selects with available models
        const models = data.available_models || [];
        const current = data.selected_model || 'llama-3.3-70b-versatile';
        populateModelSelect('modelSelect', models, current);
        populateModelSelect('settingsModel', models, current);

    } catch (e) { /* silent — user may not be logged in yet */ }
}

function populateModelSelect(selectId, models, selectedModel) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    sel.innerHTML = models.map(m =>
        `<option value="${m}" ${m === selectedModel ? 'selected' : ''}>${m}</option>`
    ).join('');
}

async function saveSettings() {
    const groq_api_key = document.getElementById('settingsGroqKey')?.value.trim() || '';
    const anthropic_api_key = document.getElementById('settingsAnthropicKey')?.value.trim() || '';
    const selected_model = document.getElementById('settingsModel')?.value || 'llama-3.3-70b-versatile';

    const btn = document.querySelector('#page-settings .btn-action');
    if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

    const res = await apiFetch('/user/settings', 'PUT', {
        groq_api_key,
        anthropic_api_key,
        selected_model,
    });

    if (btn) { btn.disabled = false; btn.textContent = 'Save Settings'; }

    if (res.ok) {
        // Sync the main page model selector
        populateModelSelect('modelSelect', [], selected_model);
        const mainSel = document.getElementById('modelSelect');
        if (mainSel && mainSel.querySelector(`option[value="${selected_model}"]`)) {
            mainSel.value = selected_model;
        }
        showToast('Settings saved');
        showPage('app');
    } else {
        const err = await res.json().catch(() => ({}));
        showToast('Error: ' + (err.detail || 'Could not save settings'));
    }
}

// Toggle password field visibility
function toggleKeyVisibility(inputId, btn) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';
    btn.textContent = isHidden ? 'Hide' : 'Show';
}