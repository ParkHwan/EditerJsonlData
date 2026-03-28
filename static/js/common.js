/**
 * 공통 유틸리티 — base.html에서 분리됨
 *
 * 모든 페이지(파일 목록, 에디터, 히스토리, 관리자 등)에서 공유한다.
 * <script src="/static/js/common.js"></script> 로 로드.
 */

// ── CSRF 토큰 유틸리티 ──
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

function csrfFetch(url, options = {}) {
    const csrfToken = getCsrfToken();
    const headers = options.headers || {};

    if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
    }

    return fetch(url, {
        ...options,
        headers: headers,
        credentials: 'same-origin'
    });
}

// ── Toast 알림 ──
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ── 로그아웃 ──
async function handleLogout() {
    try {
        await csrfFetch('/api/v1/auth/logout', { method: 'POST' });
    } catch (e) { /* silent */ }
    window.location.href = '/api/v1/view/login';
}
