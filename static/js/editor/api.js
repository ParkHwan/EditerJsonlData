/**
 * api.js - 네트워크 유틸리티 (csrfFetch, showToast)
 */

export async function csrfFetch(url, options = {}) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const headers = options.headers || {};
    headers['X-CSRF-Token'] = csrfToken;
    return fetch(url, { ...options, headers, credentials: 'same-origin' });
}

export function showToast(msg, type) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const el = document.createElement('div');
    el.className = `toast ${type || 'info'}`;
    el.textContent = msg;
    document.body.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
        el.classList.remove('show');
        setTimeout(() => el.remove(), 300);
    }, 3000);
}
