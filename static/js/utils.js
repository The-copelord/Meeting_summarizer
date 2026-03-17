// ── State ─────────────────────────────────────────────────────────────────────
let token = localStorage.getItem('mm_token') || null;
let userEmail = localStorage.getItem('mm_email') || null;
let currentJobId = null;
let selectedFile = null;
let transcriptExpanded = false;
const API = '';  // same origin

// ── Helpers ───────────────────────────────────────────────────────────────────
async function apiFetch(path, method, body) {
    const opts = {
        method,
        headers: {
            'Content-Type': 'application/json',
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
    };
    if (body) opts.body = JSON.stringify(body);
    return fetch(API + path, opts);
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