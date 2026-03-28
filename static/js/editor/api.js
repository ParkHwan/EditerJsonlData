/**
 * api.js - 네트워크 유틸리티 (csrfFetch, fetchWithRetry, showToast)
 */

export async function csrfFetch(url, options = {}) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
    const headers = options.headers || {};
    headers['X-CSRF-Token'] = csrfToken;
    return fetch(url, { ...options, headers, credentials: 'same-origin' });
}

export async function fetchWithRetry(url, options = {}, { maxRetries = 3, baseDelay = 1000 } = {}) {
    for (let attempt = 0; attempt <= maxRetries; attempt++) {
        const resp = await fetch(url, { credentials: 'same-origin', ...options });
        if (resp.status !== 429 || attempt === maxRetries) return resp;
        const retryAfter = resp.headers.get('Retry-After');
        const delay = retryAfter ? parseInt(retryAfter, 10) * 1000 : baseDelay * (2 ** attempt);
        await new Promise(r => setTimeout(r, delay));
    }
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
