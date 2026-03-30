/**
 * draft.js - 초안 자동저장, 복원, 삭제
 */
import { FILE_ID, API_V1_STR, AUTO_SAVE_INTERVAL } from './config.js';
import { state } from './state.js';
import { csrfFetch, showToast } from './api.js';

export async function checkDraft(rowIdx, card) {
    try {
        const resp = await fetch(`${API_V1_STR}/editor/draft/${FILE_ID}/${rowIdx}`, {
            credentials: 'same-origin'
        });
        if (!resp.ok) return;

        const data = await resp.json();
        if (data.exists && data.draft) {
            state.pendingDraftData = data.draft;

            const savedAt = new Date(data.draft.saved_at).toLocaleString('ko-KR');
            const banner = document.createElement('div');
            banner.id = 'draftBanner';
            banner.className = 'draft-banner-inline';
            banner.innerHTML = `
                <span>이전에 저장하지 않은 편집 내용이 있습니다. (${savedAt})</span>
                <div>
                    <button class="btn-inline-save" onclick="restoreDraft()" style="font-size:12px;padding:3px 10px;">복원</button>
                    <button class="btn-inline-cancel" onclick="discardDraft()" style="font-size:12px;padding:3px 10px;">무시</button>
                </div>
            `;
            const cardContainer = document.getElementById('cardContainer');
            if (cardContainer) {
                cardContainer.parentNode.insertBefore(banner, cardContainer);
            } else {
                card.appendChild(banner);
            }
        }
    } catch (e) { /* silent */ }
}

export function restoreDraft() {
    if (!state.pendingDraftData || state.currentRowIdx === null) return;

    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return;

    const changes = state.pendingDraftData.content;
    if (changes && typeof changes === 'object') {
        applyChangesToCard(card, changes, '');
    }

    const banner = document.getElementById('draftBanner');
    if (banner) banner.remove();
    state.pendingDraftData = null;
    showToast('이전 편집 내용이 복원되었습니다', 'success');
}

export function applyChangesToCard(card, obj, prefix) {
    for (const [key, value] of Object.entries(obj)) {
        const field = prefix ? `${prefix}.${key}` : key;

        if (value && typeof value === 'object' && !Array.isArray(value)) {
            applyChangesToCard(card, value, field);
        } else {
            const el = card.querySelector(`[data-field="${field}"][data-editing="true"]`);
            if (el) {
                if (el.dataset.useTextarea === 'true') {
                    const ta = el.querySelector('.inline-edit-ta');
                    if (ta) ta.value = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
                } else {
                    let displayVal = typeof value === 'string' ? value : JSON.stringify(value);
                    if (el.dataset.hasEscapes === 'true' && typeof value === 'string') {
                        displayVal = value
                            .replace(/\\/g, '\\\\')
                            .replace(/\n/g, '\\n')
                            .replace(/\t/g, '\\t')
                            .replace(/\r/g, '\\r');
                    }
                    el.textContent = displayVal;
                }
            }
        }
    }
}

export async function discardDraft() {
    if (state.currentRowIdx === null) return;

    try {
        await csrfFetch(`${API_V1_STR}/editor/draft/${FILE_ID}/${state.currentRowIdx}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' }
        });
    } catch (e) { /* silent */ }

    const banner = document.getElementById('draftBanner');
    if (banner) banner.remove();
    state.pendingDraftData = null;
    showToast('이전 편집 내용을 무시했습니다', 'info');
}

export function startAutoSave() {
    if (state.autoSaveInterval) clearInterval(state.autoSaveInterval);
    state.autoSaveInterval = setInterval(autoSaveDraft, AUTO_SAVE_INTERVAL * 1000);
}

export async function autoSaveDraft() {
    if (state.currentRowIdx === null || state.currentVersion === null) return;

    const indicator = document.getElementById('autosaveIndicator');
    if (indicator) {
        indicator.className = 'autosave-indicator saving';
        indicator.textContent = '저장 중...';
    }

    try {
        const changes = window.collectInlineChanges();
        const resp = await csrfFetch(`${API_V1_STR}/editor/draft/${FILE_ID}/${state.currentRowIdx}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: changes,
                version: state.currentVersion
            })
        });

        if (resp.ok && indicator) {
            const now = new Date().toLocaleTimeString('ko-KR');
            indicator.className = 'autosave-indicator saved';
            indicator.textContent = `자동 저장됨 (${now})`;
        } else if (indicator) {
            indicator.className = 'autosave-indicator';
            indicator.textContent = '자동 저장 실패';
        }
    } catch (e) {
        if (indicator) {
            indicator.className = 'autosave-indicator';
            indicator.textContent = '자동 저장 실패';
        }
    }
}
