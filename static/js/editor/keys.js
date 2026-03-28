/**
 * keys.js - 키 CRUD (추가, 삭제, 순서변경)
 */
import { state } from './state.js';
import { escapeHtml, getNestedValue } from './utils.js';
import { SPECIAL_KEY_TYPES } from './schemas.js';
import { showToast } from './api.js';
import { attachJsonValidator } from './validate.js';

export function moveKeyUp(sectionPath, key) {
    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return;
    const sectionBlock = card.querySelector(`[data-section="${sectionPath}"]`);
    if (!sectionBlock) return;
    const table = sectionBlock.querySelector('table');
    if (!table) return;
    const row = table.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
    if (!row || !row.previousElementSibling) return;
    table.insertBefore(row, row.previousElementSibling);
    trackSectionOrder(sectionPath, sectionBlock);
}

export function moveKeyDown(sectionPath, key) {
    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return;
    const sectionBlock = card.querySelector(`[data-section="${sectionPath}"]`);
    if (!sectionBlock) return;
    const table = sectionBlock.querySelector('table');
    if (!table) return;
    const row = table.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
    if (!row || !row.nextElementSibling) return;
    table.insertBefore(row.nextElementSibling, row);
    trackSectionOrder(sectionPath, sectionBlock);
}

export function trackSectionOrder(sectionPath, sectionBlock) {
    const table = sectionBlock.querySelector('table');
    if (!table) return;
    const keys = [];
    table.querySelectorAll('tr[data-key]').forEach(tr => {
        if (!tr.classList.contains('pending-delete')) {
            keys.push(tr.dataset.key);
        }
    });
    state.reorderedSections.set(sectionPath, keys);
}

export function showKeyInputDialog(message, onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'key-input-overlay';
    overlay.innerHTML = `
        <div class="key-input-dialog">
            <h4>${escapeHtml(message)}</h4>
            <input type="text" id="keyInputField" placeholder="키 이름 입력" autocomplete="off" />
            <div class="dialog-actions">
                <button class="btn-dialog-cancel" id="keyInputCancel">취소</button>
                <button class="btn-dialog-ok" id="keyInputOk">확인</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const input = overlay.querySelector('#keyInputField');
    const btnOk = overlay.querySelector('#keyInputOk');
    const btnCancel = overlay.querySelector('#keyInputCancel');

    input.focus();

    function submit() {
        const value = input.value.trim();
        overlay.remove();
        if (value) onConfirm(value);
    }

    function close() {
        overlay.remove();
    }

    btnOk.addEventListener('click', submit);
    btnCancel.addEventListener('click', close);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) close();
    });
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.isComposing) submit();
        if (e.key === 'Escape') close();
    });
}

export function addKeyToSection(sectionPath) {
    if (state.currentRowIdx === null || !state.rawEditData) return;

    showKeyInputDialog('추가할 키 이름을 입력하세요', (key) => {
        const sectionData = getNestedValue(state.rawEditData, sectionPath);
        if (sectionData && typeof sectionData === 'object' && key in sectionData && !state.deletedKeys.has(`${sectionPath}.${key}`)) {
            alert(`'${key}' 키가 이미 존재합니다. 다른 이름을 사용하세요.`);
            return;
        }

        if (state.addedKeys.has(`${sectionPath}.${key}`)) {
            alert(`'${key}' 키가 이미 추가되었습니다.`);
            return;
        }

        const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
        if (!card) return;
        const sectionBlock = card.querySelector(`[data-section="${sectionPath}"]`);
        if (!sectionBlock) return;
        const table = sectionBlock.querySelector('.info-table');
        if (!table) return;

        const fieldPath = `${sectionPath}.${key}`;
        const specialInfo = SPECIAL_KEY_TYPES[key];
        const tr = document.createElement('tr');
        tr.dataset.key = key;
        tr.className = 'new-key-row';

        if (specialInfo) {
            const origType = specialInfo.type === 'dict' ? 'dict' : 'array';
            const allowedAttr = specialInfo.allowedTypes ? ` data-allowed-types="${specialInfo.allowedTypes.join(',')}"` : '';
            const rows = Math.max(specialInfo.template.split('\n').length, 4);
            tr.innerHTML = `
                <th style="background-color:#757575;color:white;">
                    ${escapeHtml(key)}
                    <span class="special-key-badge">${escapeHtml(specialInfo.label)}</span>
                    <button class="btn-delete-key" onclick="deleteKeyFromSection('${sectionPath}', '${escapeHtml(key)}')" title="키 삭제">×</button>
                </th>
                <td class="editable-value" data-field="${escapeHtml(fieldPath)}" data-editing="true" data-use-textarea="true" style="background-color:#E8F5E9;">
                    <textarea class="inline-edit-ta" data-original-type="${origType}"${allowedAttr} rows="${rows}">${escapeHtml(specialInfo.template)}</textarea>
                </td>
            `;
            state.addedKeys.set(fieldPath, '');
            state.originalTypeMap[fieldPath] = origType;
        } else {
            tr.innerHTML = `
                <th style="background-color:#757575;color:white;">
                    ${escapeHtml(key)}
                    <button class="btn-delete-key" onclick="deleteKeyFromSection('${sectionPath}', '${escapeHtml(key)}')" title="키 삭제">×</button>
                </th>
                <td class="editable-value" contenteditable="true" data-field="${escapeHtml(fieldPath)}" data-editing="true" style="background-color:#E8F5E9;"></td>
            `;
            state.addedKeys.set(fieldPath, '');
            state.originalTypeMap[fieldPath] = 'string';
        }

        table.appendChild(tr);

        if (specialInfo) {
            const ta = tr.querySelector('.inline-edit-ta');
            if (ta) attachJsonValidator(ta);
        } else {
            const td = tr.querySelector('td[contenteditable="true"]');
            if (td) td.focus();
        }

        showToast(`'${key}' 키가 추가되었습니다.${specialInfo ? ` (타입: ${specialInfo.label})` : ''}`, 'success');
    });
}

export function deleteKeyFromSection(sectionPath, key) {
    if (state.currentRowIdx === null) return;

    if (!confirm(`'${key}' 키를 삭제하시겠습니까?\n이 작업은 저장 시 반영됩니다.`)) {
        return;
    }

    const fieldPath = `${sectionPath}.${key}`;
    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return;

    const sectionBlock = card.querySelector(`[data-section="${sectionPath}"]`);
    if (!sectionBlock) return;

    const row = sectionBlock.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
    if (row) {
        row.style.display = 'none';
        row.classList.add('pending-delete');
    }

    if (state.addedKeys.has(fieldPath)) {
        state.addedKeys.delete(fieldPath);
    } else {
        state.deletedKeys.add(fieldPath);
    }

    showToast(`'${key}' 키가 삭제 예정입니다. 저장 시 반영됩니다.`, 'info');
}
