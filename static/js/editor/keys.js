/**
 * keys.js - 키 CRUD (추가, 삭제, 순서변경)
 */
import { state } from './state.js';
import { escapeHtml, getNestedValue } from './utils.js';
import { SPECIAL_KEY_TYPES, getSpecialKeyInfo } from './schemas.js';
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

export function showKeyInputDialog(message, sectionPath, onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'key-input-overlay';
    overlay.innerHTML = `
        <div class="key-input-dialog">
            <h4>${escapeHtml(message)}</h4>
            <input type="text" id="keyInputField" placeholder="키 이름 입력" autocomplete="off" />
            <div id="keyTypeSelector" style="margin-top:8px;">
                <label style="font-size:13px;color:#555;display:block;margin-bottom:4px;">값 타입:</label>
                <select id="keyTypeSelect" style="width:100%;padding:6px 8px;border:1px solid #ccc;border-radius:4px;font-size:13px;">
                    <option value="string">문자열 (string)</option>
                    <option value="dict">딕셔너리 (dict)</option>
                    <option value="array">배열 (array)</option>
                </select>
                <div id="keyTypeHint" style="font-size:11px;color:#999;margin-top:4px;"></div>
            </div>
            <div class="dialog-actions">
                <button class="btn-dialog-cancel" id="keyInputCancel">취소</button>
                <button class="btn-dialog-ok" id="keyInputOk">확인</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const input = overlay.querySelector('#keyInputField');
    const typeSelect = overlay.querySelector('#keyTypeSelect');
    const typeHint = overlay.querySelector('#keyTypeHint');
    const btnOk = overlay.querySelector('#keyInputOk');
    const btnCancel = overlay.querySelector('#keyInputCancel');

    input.addEventListener('input', () => {
        const key = input.value.trim();
        const spec = getSpecialKeyInfo(key, sectionPath);
        if (spec) {
            typeSelect.value = spec.type;
            typeHint.textContent = `자동 감지: ${spec.label} (${spec.allowedTypes.join(', ')})`;
            typeHint.style.color = '#1565C0';
        } else {
            typeHint.textContent = '';
        }
    });

    input.focus();

    function submit() {
        const value = input.value.trim();
        const selectedType = typeSelect.value;
        overlay.remove();
        if (value) onConfirm(value, selectedType);
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

const DEFAULT_TEMPLATES = {
    dict: '{\n  "키": "값"\n}',
    array: '[\n  "항목1",\n  "항목2"\n]',
};

export function addKeyToSection(sectionPath) {
    if (state.currentRowIdx === null || !state.rawEditData) return;

    showKeyInputDialog('추가할 키 이름을 입력하세요', sectionPath, (key, selectedType) => {
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
        const specialInfo = getSpecialKeyInfo(key, sectionPath);

        const useTextarea = specialInfo
            ? true
            : (selectedType === 'dict' || selectedType === 'array');

        const origType = specialInfo
            ? specialInfo.type
            : selectedType;

        const template = specialInfo
            ? specialInfo.template
            : (DEFAULT_TEMPLATES[selectedType] || '');

        const allowedTypes = specialInfo
            ? specialInfo.allowedTypes
            : (selectedType === 'dict' ? ['dict'] : selectedType === 'array' ? ['array'] : null);

        const tr = document.createElement('tr');
        tr.dataset.key = key;
        tr.className = 'new-key-row';

        if (useTextarea) {
            const allowedAttr = allowedTypes ? ` data-allowed-types="${allowedTypes.join(',')}"` : '';
            const rows = Math.max(template.split('\n').length, 4);
            const badge = specialInfo ? specialInfo.label : (selectedType === 'dict' ? 'dict' : 'array');
            tr.innerHTML = `
                <th style="background-color:#757575;color:white;">
                    ${escapeHtml(key)}
                    <span class="special-key-badge">${escapeHtml(badge)}</span>
                    <button class="btn-delete-key" onclick="deleteKeyFromSection('${sectionPath}', '${escapeHtml(key)}')" title="키 삭제">×</button>
                </th>
                <td class="editable-value" data-field="${escapeHtml(fieldPath)}" data-editing="true" data-use-textarea="true" style="background-color:#E8F5E9;">
                    <textarea class="inline-edit-ta" data-original-type="${origType}"${allowedAttr} rows="${rows}">${escapeHtml(template)}</textarea>
                </td>
            `;
            state.originalTypeMap[fieldPath] = origType;
        } else {
            tr.innerHTML = `
                <th style="background-color:#757575;color:white;">
                    ${escapeHtml(key)}
                    <button class="btn-delete-key" onclick="deleteKeyFromSection('${sectionPath}', '${escapeHtml(key)}')" title="키 삭제">×</button>
                </th>
                <td class="editable-value" contenteditable="true" data-field="${escapeHtml(fieldPath)}" data-editing="true" style="background-color:#E8F5E9;"></td>
            `;
            state.originalTypeMap[fieldPath] = 'string';
        }

        state.addedKeys.set(fieldPath, '');
        table.appendChild(tr);

        if (useTextarea) {
            const ta = tr.querySelector('.inline-edit-ta');
            if (ta) attachJsonValidator(ta);
        } else {
            const td = tr.querySelector('td[contenteditable="true"]');
            if (td) td.focus();
        }

        const typeLabel = specialInfo ? specialInfo.label : selectedType;
        showToast(`'${key}' 키가 추가되었습니다. (타입: ${typeLabel})`, 'success');
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
