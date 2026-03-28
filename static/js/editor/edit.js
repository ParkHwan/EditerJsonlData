/**
 * edit.js - 인라인 편집 진입/종료, startRowEdit, enterInlineEdit
 */
import { FILE_ID, API_V1_STR } from './config.js';
import { state } from './state.js';
import { getNestedValue, classifyValueType, getAllowedTypesForField, escapeHtml } from './utils.js';
import { attachJsonValidator } from './validate.js';
import { checkDraft, startAutoSave } from './draft.js';

export async function startRowEdit(dataId, rowIdx) {
    if (state.currentRowIdx !== null) {
        await window.cancelEdit();
    }

    try {
        const resp = await fetch(`${API_V1_STR}/editor/data/${FILE_ID}/${rowIdx}`, {
            credentials: 'same-origin'
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();

        state.rawEditData = data.row || data;
        state.currentVersion = data.version || null;
        state.currentRowIdx = rowIdx;
        state.currentDataId = dataId;

        const card = document.querySelector(`.item[data-row-idx="${rowIdx}"]`);
        if (card) {
            enterInlineEdit(card);
            await checkDraft(rowIdx, card);
            startAutoSave();
        }
    } catch (e) {
        window.showToast('편집 데이터 로드 실패: ' + e.message, 'error');
    }
}

export function enterInlineEdit(card) {
    card.classList.add('inline-editing');

    const editables = card.querySelectorAll('.editable-value[data-field]');
    editables.forEach(el => {
        const field = el.dataset.field;

        state.originalHtmlMap[field] = el.innerHTML;

        const rawValue = getNestedValue(state.rawEditData, field);
        const valueType = classifyValueType(rawValue);
        state.originalTypeMap[field] = valueType;

        if (valueType === 'dict' || valueType === 'array') {
            el.contentEditable = 'false';
            el.dataset.editing = 'true';
            el.dataset.useTextarea = 'true';

            const jsonStr = JSON.stringify(rawValue, null, 2);
            const rows = Math.max(jsonStr.split('\n').length + 1, 4);
            const allowedTypes = getAllowedTypesForField(field);
            const allowedAttr = allowedTypes ? ` data-allowed-types="${allowedTypes.join(',')}"` : '';

            el.innerHTML = `<textarea class="inline-edit-ta" data-original-type="${valueType}"${allowedAttr} rows="${rows}">${escapeHtml(jsonStr)}</textarea>`;

            const ta = el.querySelector('.inline-edit-ta');
            if (ta) attachJsonValidator(ta);
        } else {
            el.contentEditable = 'true';
            el.dataset.editing = 'true';

            const text = el.textContent;
            if (typeof rawValue === 'string' && rawValue !== text) {
                el.dataset.hasEscapes = 'true';
            }
            el.dataset.originalDisplay = text;
        }
    });

    const contentMetaEl = card.querySelector('[data-field="content_meta"]');
    if (!contentMetaEl && state.rawEditData.content_meta) {
        const metaDiv = document.createElement('div');
        metaDiv.id = 'inlineContentMeta';
        metaDiv.className = 'inline-meta-section';
        metaDiv.innerHTML = `
            <h4>content_meta (JSON 편집)</h4>
            <div class="editable-value" data-field="content_meta" data-editing="true" data-use-textarea="true">
                <textarea class="inline-edit-ta" data-original-type="dict" rows="8">${escapeHtml(JSON.stringify(state.rawEditData.content_meta, null, 2))}</textarea>
            </div>
        `;
        const header = card.querySelector('.header');
        if (header && header.parentNode) {
            header.parentNode.insertBefore(metaDiv, header.nextSibling);
        }
        state.originalTypeMap['content_meta'] = 'dict';
        state.originalHtmlMap['content_meta'] = '';
        const ta = metaDiv.querySelector('.inline-edit-ta');
        if (ta) attachJsonValidator(ta);
    }

    card.querySelectorAll('.btn-add-key').forEach(b => b.style.display = 'inline-block');
    card.querySelectorAll('.btn-delete-key').forEach(b => b.style.display = 'inline-block');
    card.querySelectorAll('.move-key-wrap').forEach(w => w.style.display = 'inline-flex');

    card.querySelectorAll('.btn-list-add').forEach(b => b.style.display = 'inline-block');
    card.querySelectorAll('.btn-list-delete').forEach(b => b.style.display = 'inline-block');
    card.querySelectorAll('.btn-list-move').forEach(b => b.style.display = 'inline-block');

    document.querySelectorAll('.btn-edit').forEach(b => b.disabled = true);

    const actions = card.querySelector('.header-actions');
    if (actions) {
        actions.dataset.originalHtml = actions.innerHTML;
    }
    actions.innerHTML = `
        <span class="autosave-indicator" id="autosaveIndicator"></span>
        <button class="btn-inline-cancel" onclick="cancelEdit()">취소</button>
        <button class="btn-inline-save" onclick="saveEdit()">저장</button>
    `;

    const statusEl = card.querySelector('.inline-edit-status');
    if (statusEl) statusEl.textContent = '편집 중';
}

export async function exitInlineEdit() {
    if (state.autoSaveInterval) {
        clearInterval(state.autoSaveInterval);
        state.autoSaveInterval = null;
    }

    state.currentRowIdx = null;
    state.currentVersion = null;
    state.rawEditData = null;
    state.originalHtmlMap = {};
    state.originalTypeMap = {};
    state.pendingDraftData = null;
    state.deletedKeys.clear();
    state.addedKeys.clear();
    state.reorderedSections.clear();
    state.modifiedLists.clear();
}
