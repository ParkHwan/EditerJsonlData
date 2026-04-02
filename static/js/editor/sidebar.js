/**
 * sidebar.js - 사이드바 네비게이션, 카드 로드, 페이지네이션, 검증 실패 하이라이트
 */
import { FILE_ID, API_V1_STR, GCS_DATE } from './config.js';
import { state } from './state.js';
import { showToast, fetchWithRetry } from './api.js';
import { escapeHtml } from './utils.js';

export function filterSidebar(query) {
    const q = (query || '').toLowerCase();
    document.querySelectorAll('.sidebar-item').forEach(item => {
        const dataId = (item.dataset.dataId || '').toLowerCase();
        const pairIdx = (item.dataset.pairIdx || '').toLowerCase();
        item.style.display = (!q || dataId.includes(q) || pairIdx.includes(q)) ? '' : 'none';
    });
}

export async function selectItem(el, rowIdx, dataId) {
    if (state.currentRowIdx !== null) {
        await window.cancelEdit();
    }

    state.selectedRowIdx = rowIdx;
    document.querySelectorAll('.sidebar-item').forEach(i => i.classList.remove('active'));
    if (el && el.classList) el.classList.add('active');

    const placeholder = document.getElementById('detailPlaceholder');
    const detailContent = document.getElementById('detailContent');
    const pagination = document.getElementById('cardPagination');
    const titleEl = document.getElementById('detailTitle');

    if (placeholder) placeholder.style.display = 'none';
    if (detailContent) detailContent.style.display = '';
    if (pagination) pagination.style.display = '';
    if (titleEl) titleEl.textContent = dataId || `#${rowIdx + 1}`;

    const container = document.getElementById('cardContainer');
    container.innerHTML = '<div class="loading-spinner">로딩 중...</div>';

    try {
        const gcsParam = GCS_DATE ? `?gcs_date=${GCS_DATE}` : '';
        const resp = await fetchWithRetry(
            `${API_V1_STR}/editor/card/${FILE_ID}/${rowIdx}${gcsParam}`
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const html = await resp.text();
        container.innerHTML = html;

        if (window.renderMathInElement && window.__katexOptions) {
            renderMathInElement(container, window.__katexOptions);
        }

        showRowValidationBanner(rowIdx);
        updatePagination(el);

        const toolbarActions = document.getElementById('toolbarEditActions');
        if (toolbarActions) {
            const escaped = escapeHtml(dataId);
            toolbarActions.innerHTML =
                `<button class="btn btn-edit" onclick="startRowEdit('${escaped}', ${rowIdx})">편집</button>`;
        }

        document.querySelectorAll('.btn-edit').forEach(b => {
            b.disabled = !state.isFileLockedByMe;
        });
    } catch (e) {
        container.innerHTML = `<div class="error-msg">카드 로딩 실패: ${e.message}</div>`;
    }
}

export function updatePagination(currentEl) {
    const items = [...document.querySelectorAll('.sidebar-item')].filter(
        i => i.style.display !== 'none'
    );
    const idx = items.indexOf(currentEl);
    const total = items.length;

    const prevBtn = document.getElementById('btnPagePrev');
    const nextBtn = document.getElementById('btnPageNext');
    const indicator = document.getElementById('pageIndicator');

    if (prevBtn) prevBtn.disabled = (idx <= 0);
    if (nextBtn) nextBtn.disabled = (idx >= total - 1);
    if (indicator) indicator.textContent = `${idx + 1} / ${total}`;
}

export async function navigatePrev() {
    const items = [...document.querySelectorAll('.sidebar-item')].filter(
        i => i.style.display !== 'none'
    );
    const currentActive = document.querySelector('.sidebar-item.active');
    const idx = items.indexOf(currentActive);
    if (idx > 0) {
        const prev = items[idx - 1];
        const rowIdx = parseInt(prev.dataset.rowIdx, 10);
        const dataId = prev.dataset.dataId || '';
        selectItem(prev, rowIdx, dataId);
        prev.scrollIntoView({ block: 'nearest' });
    }
}

export async function navigateNext() {
    const items = [...document.querySelectorAll('.sidebar-item')].filter(
        i => i.style.display !== 'none'
    );
    const currentActive = document.querySelector('.sidebar-item.active');
    const idx = items.indexOf(currentActive);
    if (idx < items.length - 1) {
        const next = items[idx + 1];
        const rowIdx = parseInt(next.dataset.rowIdx, 10);
        const dataId = next.dataset.dataId || '';
        selectItem(next, rowIdx, dataId);
        next.scrollIntoView({ block: 'nearest' });
    }
}

/**
 * 검증 실패 row를 사이드바에 하이라이트하고 필터 토글 버튼을 표시한다.
 * @param {Array<{row_idx: number, messages: string[]}>} errorItems
 */
export function highlightValidationErrors(errorItems) {
    clearValidationHighlights();

    if (!errorItems || errorItems.length === 0) return;

    state.validationErrors = errorItems;
    const errorIdxSet = new Set(errorItems.map(e => e.row_idx));

    document.querySelectorAll('.sidebar-item').forEach(item => {
        const rowIdx = parseInt(item.dataset.rowIdx, 10);
        if (errorIdxSet.has(rowIdx)) {
            item.classList.add('validation-error');
            const badge = document.createElement('span');
            badge.className = 'validation-error-badge';
            badge.textContent = '!';
            badge.title = '스키마 검증 실패';
            item.appendChild(badge);
        }
    });

    _ensureFilterToggle(errorItems.length);
}

/**
 * 검증 실패 하이라이트 및 필터를 모두 제거한다.
 */
export function clearValidationHighlights() {
    state.validationErrors = [];
    state.validationFilterActive = false;

    document.querySelectorAll('.sidebar-item.validation-error').forEach(item => {
        item.classList.remove('validation-error');
    });
    document.querySelectorAll('.validation-error-badge').forEach(b => b.remove());

    const toggle = document.getElementById('validationFilterToggle');
    if (toggle) toggle.remove();

    document.querySelectorAll('.sidebar-item').forEach(item => {
        item.removeAttribute('data-validation-hidden');
        if (item.style.display === 'none' && !item.dataset.searchHidden) {
            item.style.display = '';
        }
    });
}

/**
 * 검증 실패 필터 토글 버튼을 사이드바 검색 영역 아래에 생성한다.
 */
function _ensureFilterToggle(errorCount) {
    if (document.getElementById('validationFilterToggle')) return;

    const searchWrap = document.querySelector('.sidebar-search');
    if (!searchWrap) return;

    const toggle = document.createElement('button');
    toggle.id = 'validationFilterToggle';
    toggle.className = 'btn-validation-filter';
    toggle.innerHTML = `<span class="vf-icon">⛔</span> 검증 실패 <strong>${errorCount}</strong>건만 보기`;
    toggle.addEventListener('click', () => _toggleValidationFilter(toggle));
    searchWrap.after(toggle);
}

function _toggleValidationFilter(btn) {
    state.validationFilterActive = !state.validationFilterActive;
    const errorIdxSet = new Set(state.validationErrors.map(e => e.row_idx));

    document.querySelectorAll('.sidebar-item').forEach(item => {
        const rowIdx = parseInt(item.dataset.rowIdx, 10);
        if (state.validationFilterActive) {
            if (!errorIdxSet.has(rowIdx)) {
                item.style.display = 'none';
                item.dataset.validationHidden = 'true';
            }
        } else {
            if (item.dataset.validationHidden) {
                item.style.display = '';
                delete item.dataset.validationHidden;
            }
        }
    });

    if (state.validationFilterActive) {
        btn.classList.add('active');
        btn.innerHTML = `<span class="vf-icon">✕</span> 전체 목록 보기`;
    } else {
        const cnt = state.validationErrors.length;
        btn.classList.remove('active');
        btn.innerHTML = `<span class="vf-icon">⛔</span> 검증 실패 <strong>${cnt}</strong>건만 보기`;
    }
}

/**
 * 현재 선택된 row에 검증 에러가 있으면 카드 상단에 배너를 표시한다.
 */
export function showRowValidationBanner(rowIdx) {
    const existing = document.getElementById('rowValidationBanner');
    if (existing) existing.remove();

    if (!state.validationErrors || state.validationErrors.length === 0) return;

    const entry = state.validationErrors.find(e => e.row_idx === rowIdx);
    if (!entry || !entry.messages || entry.messages.length === 0) return;

    const container = document.getElementById('cardContainer');
    if (!container) return;

    const banner = document.createElement('div');
    banner.id = 'rowValidationBanner';
    banner.className = 'row-validation-banner';

    const msgs = entry.messages.map(m =>
        `<div class="rvb-msg">⛔ ${escapeHtml(m)}</div>`
    ).join('');

    banner.innerHTML = `
        <div class="rvb-header">
            <span class="rvb-title">스키마 검증 실패 (Row ${rowIdx})</span>
            <button class="rvb-close" title="닫기">&times;</button>
        </div>
        <div class="rvb-body">${msgs}</div>`;

    container.prepend(banner);

    banner.querySelector('.rvb-close').addEventListener('click', () => banner.remove());
}
