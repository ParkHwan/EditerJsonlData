/**
 * save.js - collectInlineChanges, saveEdit, cancelEdit
 */
import { FILE_ID, API_V1_STR, EDIT_MODE } from './config.js';
import { state } from './state.js';
import {
    getNestedValue, setNestedValue, deepMerge,
    classifyValueType, escapeHtml,
} from './utils.js';
import { validateAllChanges, showSaveConfirm, extractFieldValue } from './validate.js';
import { _rebuildListFromDOM } from './list.js';
import { csrfFetch, showToast, showValidationPanel } from './api.js';
import { exitInlineEdit } from './edit.js';
import { selectItem } from './sidebar.js';

export function collectInlineChanges() {
    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return {};

    const changes = {};
    const modifiedTopKeys = new Set();
    const textareaCompleteValues = new Map();
    const editables = card.querySelectorAll('[data-field][data-editing="true"]');

    editables.forEach(el => {
        if (el.closest('.pending-delete')) return;

        const field = el.dataset.field;
        const origType = state.originalTypeMap[field] || 'string';

        const { value, error } = extractFieldValue(el);
        if (error || value === undefined) {
            return;
        }

        if (el.dataset.useTextarea === 'true' && (origType === 'dict' || origType === 'array')) {
            textareaCompleteValues.set(field, value);
        }

        const topKey = field.split('.')[0];
        modifiedTopKeys.add(topKey);
        setNestedValue(changes, field, value);
    });

    if (state.deletedKeys.size > 0 || state.addedKeys.size > 0 || state.reorderedSections.size > 0) {
        modifiedTopKeys.add('add_info');
    }

    for (const topKey of modifiedTopKeys) {
        if (state.rawEditData[topKey] && typeof state.rawEditData[topKey] === 'object' && !Array.isArray(state.rawEditData[topKey])) {
            const merged = JSON.parse(JSON.stringify(state.rawEditData[topKey]));
            if (changes[topKey] && typeof changes[topKey] === 'object') {
                deepMerge(merged, changes[topKey]);
            }
            changes[topKey] = merged;
        }
    }

    for (const [fieldPath, val] of textareaCompleteValues) {
        setNestedValue(changes, fieldPath, val);
    }

    for (const fieldPath of state.deletedKeys) {
        const parts = fieldPath.split('.');
        if (parts.length >= 3) {
            const topKey = parts[0];
            let obj = changes[topKey];
            if (!obj) continue;
            for (let i = 1; i < parts.length - 1; i++) {
                if (!obj[parts[i]]) break;
                obj = obj[parts[i]];
            }
            const lastKey = parts[parts.length - 1];
            if (obj && lastKey in obj) {
                delete obj[lastKey];
            }
        }
    }

    for (const [sectionPath, keyOrder] of state.reorderedSections) {
        const parts = sectionPath.split('.');
        let obj = changes;
        for (const p of parts) {
            if (!obj || !obj[p]) break;
            obj = obj[p];
        }
        if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
            const reordered = {};
            for (const k of keyOrder) {
                if (k in obj) reordered[k] = obj[k];
            }
            for (const k of Object.keys(obj)) {
                if (!(k in reordered)) reordered[k] = obj[k];
            }
            let target = changes;
            for (let i = 0; i < parts.length - 1; i++) {
                target = target[parts[i]];
            }
            target[parts[parts.length - 1]] = reordered;
        }
    }

    for (const listPath of state.modifiedLists) {
        const rebuilt = _rebuildListFromDOM(card, listPath);
        if (rebuilt !== null) {
            modifiedTopKeys.add(listPath.split('.')[0]);
            setNestedValue(changes, listPath, rebuilt);
        }
    }

    return changes;
}

export async function saveEdit() {
    if (state.currentRowIdx === null) return;

    const validation = validateAllChanges();
    if (validation.errors.length > 0) {
        showToast(`JSON 유효성 오류 ${validation.errors.length}건 — 수정 후 저장하세요`, 'error');
    }
    const confirmed = await showSaveConfirm(validation);
    if (!confirmed) return;

    const changes = collectInlineChanges();

    try {
        const resp = await csrfFetch(`${API_V1_STR}/editor/data/${FILE_ID}/${state.currentRowIdx}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                changes: changes,
                version: state.currentVersion
            })
        });

        if (!resp.ok) {
            const err = await resp.json();
            showToast(err.detail || '저장 실패', 'error');
            return;
        }

        const result = await resp.json();
        const serverWarnings = result.validation_warnings || [];

        if (EDIT_MODE === 'gcs') {
            showToast('저장 완료! (Redis에 임시 저장됨 — "GCS 파일 업데이트"로 최종 반영)', 'success');
        } else {
            showToast('저장 완료!', 'success');
        }

        if (serverWarnings.length > 0) {
            showValidationPanel({
                title: `스키마 검증 경고 (Row ${state.currentRowIdx})`,
                warnings: serverWarnings,
            });
        }
        const savedRowIdx = state.currentRowIdx;
        const savedDataId = state.currentDataId;
        await exitInlineEdit();

        const activeItem = document.querySelector('.sidebar-item.active');
        if (activeItem) {
            selectItem(activeItem, savedRowIdx, savedDataId);
        }

    } catch (e) {
        showToast('저장 중 오류: ' + e.message, 'error');
    }
}

export async function cancelEdit() {
    if (state.currentRowIdx === null) return;

    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (card) {
        const editables = card.querySelectorAll('.editable-value[data-editing="true"]');
        editables.forEach(el => {
            const field = el.dataset.field;
            if (state.originalHtmlMap[field] !== undefined) {
                el.innerHTML = state.originalHtmlMap[field];
            }
            el.contentEditable = 'false';
            el.removeAttribute('data-editing');
            el.removeAttribute('data-use-textarea');
            el.removeAttribute('data-has-escapes');
        });

        const metaSection = document.getElementById('inlineContentMeta');
        if (metaSection) metaSection.remove();

        const banner = document.getElementById('draftBanner');
        if (banner) banner.remove();

        const actions = card.querySelector('.header-actions');
        if (actions && actions.dataset.originalHtml !== undefined) {
            actions.innerHTML = actions.dataset.originalHtml;
        }

        card.querySelectorAll('.pending-delete').forEach(row => {
            row.style.display = '';
            row.classList.remove('pending-delete');
        });

        card.querySelectorAll('.new-key-row').forEach(row => {
            row.remove();
        });

        if (state.modifiedLists.size > 0) {
            state.modifiedLists.clear();
            await exitInlineEdit();
            const activeItem = document.querySelector('.sidebar-item.active');
            if (activeItem) {
                const rowIdx = parseInt(activeItem.dataset.rowIdx, 10);
                const dataId = activeItem.dataset.dataId || '';
                selectItem(activeItem, rowIdx, dataId);
            }
            return;
        }

        for (const [sectionPath] of state.reorderedSections) {
            const sectionBlock = card.querySelector(`[data-section="${sectionPath}"]`);
            if (!sectionBlock) continue;
            const table = sectionBlock.querySelector('table');
            if (!table) continue;
            const origData = getNestedValue(state.rawEditData, sectionPath);
            if (!origData || typeof origData !== 'object') continue;
            const origKeys = Object.keys(origData);
            for (const k of origKeys) {
                const row = table.querySelector(`tr[data-key="${CSS.escape(k)}"]`);
                if (row) row.parentNode.appendChild(row);
            }
        }

        card.querySelectorAll('.btn-add-key').forEach(b => b.style.display = 'none');
        card.querySelectorAll('.btn-delete-key').forEach(b => b.style.display = 'none');
        card.querySelectorAll('.move-key-wrap').forEach(w => w.style.display = 'none');

        card.querySelectorAll('.btn-list-add').forEach(b => b.style.display = 'none');
        card.querySelectorAll('.btn-list-delete').forEach(b => b.style.display = 'none');
        card.querySelectorAll('.btn-list-move').forEach(b => b.style.display = 'none');
        card.querySelectorAll('.btn-list-add-key').forEach(b => b.style.display = 'none');

        card.classList.remove('inline-editing');
        const statusEl = card.querySelector('.inline-edit-status');
        if (statusEl) statusEl.textContent = '';

        if (window.renderMathInElement && window.__katexOptions) {
            renderMathInElement(card, window.__katexOptions);
        }

        const toolbarActions = document.getElementById('toolbarEditActions');
        if (toolbarActions) {
            const activeItem = document.querySelector('.sidebar-item.active');
            if (activeItem) {
                const rid = parseInt(activeItem.dataset.rowIdx, 10);
                const did = escapeHtml(activeItem.dataset.dataId || '');
                toolbarActions.innerHTML =
                    `<button class="btn btn-edit" onclick="startRowEdit('${did}', ${rid})">편집</button>`;
            } else {
                toolbarActions.innerHTML = '';
            }
        }

        document.querySelectorAll('.btn-edit').forEach(b => {
            b.disabled = !state.isFileLockedByMe;
        });
    }

    await exitInlineEdit();
}
