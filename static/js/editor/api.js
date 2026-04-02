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

function _escapeHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/**
 * 서버 검증 결과(errors/warnings)를 오버레이 패널로 표시한다.
 * @param {Object} opts
 * @param {string}  opts.title - 패널 제목
 * @param {Array}   opts.errors   - [{row_idx?, messages[]?} | string]
 * @param {Array}   opts.warnings - [{row_idx?, messages[]?} | string]
 * @param {string}  [opts.summary] - 요약 문구
 * @returns {void}  errors/warnings 모두 비어있으면 아무것도 하지 않는다.
 */
export function showValidationPanel({ title, errors = [], warnings = [], summary = '' }) {
    if (errors.length === 0 && warnings.length === 0) return;

    const existing = document.querySelector('.validation-panel-overlay');
    if (existing) existing.remove();

    const renderItems = (items, level) => {
        return items.map(item => {
            if (typeof item === 'string') {
                return `<div class="change-item ${level}">
                    <div class="change-detail">${_escapeHtml(item)}</div>
                </div>`;
            }
            const rowLabel = item.row_idx != null ? `Row ${item.row_idx}` : '';
            const msgs = Array.isArray(item.messages) ? item.messages : [String(item)];
            return msgs.map(m =>
                `<div class="change-item ${level}">
                    ${rowLabel ? `<div class="change-field">${level === 'error' ? '⛔' : '⚠'} ${_escapeHtml(rowLabel)}</div>` : ''}
                    <div class="change-detail">${_escapeHtml(m)}</div>
                </div>`
            ).join('');
        }).join('');
    };

    let body = '';
    if (summary) {
        body += `<p class="validation-summary">${_escapeHtml(summary)}</p>`;
    }
    if (errors.length > 0) {
        body += `<h4 class="validation-section-title error-title">오류 (${errors.length}건) — 수정 필요</h4>`;
        body += renderItems(errors, 'error');
    }
    if (warnings.length > 0) {
        body += `<h4 class="validation-section-title warning-title">경고 (${warnings.length}건)</h4>`;
        body += renderItems(warnings, 'warning');
    }

    const overlay = document.createElement('div');
    overlay.className = 'validation-panel-overlay';
    overlay.innerHTML = `
        <div class="validation-panel-box">
            <h3>${_escapeHtml(title)}</h3>
            <div class="validation-panel-body">${body}</div>
            <div class="save-confirm-actions">
                <button class="btn btn-cancel" id="validationPanelClose">확인</button>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    overlay.querySelector('#validationPanelClose').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
}
