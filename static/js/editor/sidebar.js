/**
 * sidebar.js - 사이드바 네비게이션, 카드 로드, 페이지네이션
 */
import { FILE_ID, API_V1_STR, GCS_DATE } from './config.js';
import { state } from './state.js';
import { showToast } from './api.js';

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

    const container = document.getElementById('cardContainer');
    container.innerHTML = '<div class="loading-spinner">로딩 중...</div>';

    try {
        const gcsParam = GCS_DATE ? `&gcs_date=${GCS_DATE}` : '';
        const resp = await fetch(
            `${API_V1_STR}/editor/card/${FILE_ID}?row_idx=${rowIdx}${gcsParam}`,
            { credentials: 'same-origin' }
        );
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const html = await resp.text();
        container.innerHTML = html;

        if (window.renderMathInElement && window.__katexOptions) {
            renderMathInElement(container, window.__katexOptions);
        }

        updatePagination(el);

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
