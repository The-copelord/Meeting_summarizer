// ── Auth ──────────────────────────────────────────────────────────────────────
let authMode = 'login';

function switchTab(mode) {
    authMode = mode;
    document.getElementById('tab-login').classList.toggle('active', mode === 'login');
    document.getElementById('tab-signup').classList.toggle('active', mode === 'signup');
    document.getElementById('authBtn').textContent = mode === 'login' ? 'Sign In' : 'Create Account';
    document.getElementById('authError').classList.remove('visible');
}

async function doAuth() {
    const email = document.getElementById('authEmail').value.trim();
    const password = document.getElementById('authPassword').value;
    const errEl = document.getElementById('authError');
    const btn = document.getElementById('authBtn');

    if (!email || !password) { showAuthError('Please fill in all fields.'); return; }

    btn.disabled = true;
    btn.textContent = authMode === 'login' ? 'Signing in…' : 'Creating account…';
    errEl.classList.remove('visible');

    try {
        if (authMode === 'signup') {
            const res = await apiFetch('/user/signup', 'POST', { email, password });
            if (!res.ok) { const e = await res.json(); showAuthError(e.detail || 'Signup failed'); return; }
            showToast('Account created! Please sign in.');
            switchTab('login');
            return;
        }

        const res = await apiFetch('/user/login', 'POST', { email, password });
        if (!res.ok) { const e = await res.json(); showAuthError(e.detail || 'Login failed'); return; }
        const data = await res.json();
        token = data.access_token;
        userEmail = email;
        localStorage.setItem('mm_token', token);
        localStorage.setItem('mm_email', email);
        enterApp();

    } catch (e) {
        showAuthError('Network error. Is the server running?');
    } finally {
        btn.disabled = false;
        btn.textContent = authMode === 'login' ? 'Sign In' : 'Create Account';
    }
}

function showAuthError(msg) {
    const el = document.getElementById('authError');
    el.textContent = msg;
    el.classList.add('visible');
}

function enterApp() {
    document.getElementById('userBadge').classList.add('visible');
    document.getElementById('userEmail').textContent = userEmail || '';
    document.getElementById('logoutBtn').style.display = 'inline-block';
    document.getElementById('settingsBtn').style.display = 'inline-block';
    showPage('app');
    loadJobs();
    loadSettings();
    startJobPolling();
}

function logout() {
    token = null; userEmail = null;
    localStorage.removeItem('mm_token');
    localStorage.removeItem('mm_email');
    stopJobPolling();
    closeSse();
    document.getElementById('userBadge').classList.remove('visible');
    document.getElementById('logoutBtn').style.display = 'none';
    document.getElementById('settingsBtn').style.display = 'none';
    showPage('auth');
}