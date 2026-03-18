// ── State ─────────────────────────────────────────────────────────────────────
let token = localStorage.getItem('mm_token') || null;
let userEmail = localStorage.getItem('mm_email') || null;
let currentJobId = null;
let selectedFile = null;
let transcriptExpanded = false;
const API = '';  // same origin

// ── Helpers ───────────────────────────────────────────────────────────────────
async function apiFetch(path, method, body) {
    // Read token fresh from localStorage every call — avoids stale null on login
    const _token = token || localStorage.getItem('mm_token');
    const opts = {
        method,
        headers: {
            'Content-Type': 'application/json',
            ...(_token ? { 'Authorization': `Bearer ${_token}` } : {}),
        },
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(API + path, opts);
    // Auto-logout on 401 — token expired or invalid
    if (res.status === 401 && _token) {
        _handleExpiredToken();
    }
    return res;
}

function _handleExpiredToken() {
    token = null;
    localStorage.removeItem('mm_token');
    localStorage.removeItem('mm_email');
    showToast('Session expired — please sign in again');
    setTimeout(() => {
        // Redirect to home which shows login page
        // Use replace so back button doesn't return to authenticated page
        window.location.replace('/');
    }, 1500);
}

function _scheduleAutoLogout() {
    const _token = token || localStorage.getItem('mm_token');
    if (!_token) return;
    try {
        // Decode JWT payload (middle part) without verification
        const payload = JSON.parse(atob(_token.split('.')[1]));
        if (!payload.exp) return;
        const expiresInMs = (payload.exp * 1000) - Date.now();
        if (expiresInMs <= 0) {
            // Already expired
            _handleExpiredToken();
            return;
        }
        // Set timer to auto-logout when token expires
        setTimeout(() => {
            showToast('Session expired — please sign in again');
            setTimeout(() => window.location.replace('/'), 1500);
        }, expiresInMs);
        console.log(`Token expires in ${Math.round(expiresInMs / 60000)} minutes`);
    } catch (e) { /* malformed token — ignore */ }
}

function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function fmtBytes(b) {
    return b < 1048576 ? (b / 1024).toFixed(1) + ' KB' : (b / 1048576).toFixed(1) + ' MB';
}

function fmtDate(iso) {
    return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 3000);
}

function copyEl(id, btn) {
    const el = document.getElementById(id);
    navigator.clipboard.writeText(el.textContent || '').then(() => {
        btn.textContent = 'Copied!'; btn.classList.add('copied');
        setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
    });
}

function showPage(name) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
}

function truncateFilename(name, maxChars) {
    if (!name || name.length <= maxChars) return name;
    return name.slice(0, maxChars) + '...';
}

// No-op — SSE removed, kept so other files don't break
function closeSse() { }