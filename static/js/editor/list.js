/**
 * list.js - 리스트 CRUD (추가, 삭제, 이동, 토글, 재빌드)
 */
import { state } from './state.js';
import { escapeHtml, getNestedValue, stripNewlineSymbol } from './utils.js';
import { TASK3_LIST_SCHEMAS, TASK2_LIST_SCHEMAS, getListFieldType } from './schemas.js';
import { showToast } from './api.js';
import { attachJsonValidator } from './validate.js';

export function toggleSection(header) {
    const body = header.nextElementSibling;
    if (!body) return;
    const icon = header.querySelector('.toggle-icon');
    if (body.style.display === 'none') {
        body.style.display = '';
        if (icon) icon.textContent = '▼';
    } else {
        body.style.display = 'none';
        if (icon) icon.textContent = '▶';
    }
}

function _showListKeyInputDialog(message, onConfirm) {
    const overlay = document.createElement('div');
    overlay.className = 'key-input-overlay';
    overlay.innerHTML = `
        <div class="key-input-dialog">
            <h4>${escapeHtml(message)}</h4>
            <input type="text" id="listKeyInputField" placeholder="키 이름 입력" autocomplete="off" />
            <div class="dialog-actions">
                <button class="btn-dialog-cancel" id="listKeyInputCancel">취소</button>
                <button class="btn-dialog-ok" id="listKeyInputOk">확인</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    const input = overlay.querySelector('#listKeyInputField');
    const btnOk = overlay.querySelector('#listKeyInputOk');
    const btnCancel = overlay.querySelector('#listKeyInputCancel');

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

function _buildFieldHtml(fieldKey, fieldPath, listPath) {
    const ft = getListFieldType(listPath, fieldKey);
    if (ft && (ft.type === 'array' || ft.type === 'dict')) {
        const rows = Math.max((ft.template || '').split('\n').length, 4);
        return `
            <tr data-key="${escapeHtml(fieldKey)}">
                <th>${escapeHtml(fieldKey)} <span class="special-key-badge">${ft.type}</span>
                    <button class="btn-delete-key" style="display:inline-block"
                        onclick="deleteListItemKey(this)" title="키 삭제">×</button>
                </th>
                <td class="editable-value" data-field="${escapeHtml(fieldPath)}"
                    data-editing="true" data-use-textarea="true">
                    <textarea class="inline-edit-ta" data-original-type="${ft.type}" rows="${rows}">${escapeHtml(ft.template || '')}</textarea>
                </td>
            </tr>`;
    }
    return `
        <tr data-key="${escapeHtml(fieldKey)}">
            <th>${escapeHtml(fieldKey)}
                <button class="btn-delete-key" style="display:inline-block"
                    onclick="deleteListItemKey(this)" title="키 삭제">×</button>
            </th>
            <td class="editable-value" contenteditable="true"
                data-field="${escapeHtml(fieldPath)}"
                data-editing="true"></td>
        </tr>`;
}

export function addListItem(listPath) {
    const schema = TASK3_LIST_SCHEMAS[listPath] || TASK2_LIST_SCHEMAS[listPath];
    if (!schema) return;

    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return;
    const container = card.querySelector(`[data-list-path="${listPath}"]`);
    if (!container) return;

    const visibleItems = container.querySelectorAll('.list-item-card:not(.pending-delete)');
    const newIdx = visibleItems.length;

    let fieldsHtml = '';
    schema.fields.forEach(f => {
        const fieldPath = `${listPath}.${newIdx}.${f}`;
        fieldsHtml += _buildFieldHtml(f, fieldPath, listPath);
    });

    const itemHtml = `
        <div class="list-item-card theme-green" data-list-index="${newIdx}" data-list-path-ref="${escapeHtml(listPath)}">
            <div class="list-item-header">
                <span class="list-item-idx">${newIdx + 1}</span>
                <span>${escapeHtml(schema.label)} #${newIdx + 1} (신규)</span>
                <button class="btn-list-add-key" style="display:inline-block"
                    onclick="addListItemKey(this, '${escapeHtml(listPath)}')" title="키 추가">+ 키</button>
                <button class="btn-list-delete" style="display:inline-block"
                    onclick="deleteListItem('${escapeHtml(listPath)}', -1, this)">×</button>
            </div>
            <table class="info-table">${fieldsHtml}</table>
        </div>`;

    container.insertAdjacentHTML('beforeend', itemHtml);

    const addedCard = container.lastElementChild;
    addedCard.querySelectorAll('.inline-edit-ta').forEach(ta => attachJsonValidator(ta));

    state.modifiedLists.add(listPath);
    showToast(`${schema.label} 항목 추가됨`, 'success');
}

export function addListItemKey(btnEl, listPath) {
    const itemCard = btnEl.closest('.list-item-card');
    if (!itemCard) return;
    const table = itemCard.querySelector('.info-table');
    if (!table) return;
    const idx = itemCard.dataset.listIndex;

    _showListKeyInputDialog('추가할 키 이름을 입력하세요', (keyName) => {
        const existing = table.querySelector(`tr[data-key="${CSS.escape(keyName)}"]`);
        if (existing && !existing.classList.contains('pending-delete')) {
            alert(`'${keyName}' 키가 이미 존재합니다.`);
            return;
        }

        const fieldPath = `${listPath}.${idx}.${keyName}`;
        const html = _buildFieldHtml(keyName, fieldPath, listPath);
        table.insertAdjacentHTML('beforeend', html);

        const newRow = table.lastElementChild;
        const ta = newRow.querySelector('.inline-edit-ta');
        if (ta) attachJsonValidator(ta);

        state.modifiedLists.add(listPath);
        showToast(`'${keyName}' 키가 추가되었습니다`, 'success');
    });
}

export function deleteListItemKey(btnEl) {
    const row = btnEl.closest('tr[data-key]');
    if (!row) return;
    const keyName = row.dataset.key;
    if (!confirm(`'${keyName}' 키를 삭제하시겠습니까?`)) return;

    const itemCard = row.closest('.list-item-card');
    row.classList.add('pending-delete');
    row.style.display = 'none';

    if (itemCard) {
        const listPath = itemCard.dataset.listPathRef;
        if (listPath) state.modifiedLists.add(listPath);
    }
    showToast(`'${keyName}' 키가 삭제 예정입니다`, 'info');
}

export function deleteListItem(listPath, index, btnEl) {
    const card = btnEl ? btnEl.closest('.list-item-card') : null;
    if (!card) return;

    if (!confirm('이 항목을 삭제하시겠습니까?')) return;

    card.classList.add('pending-delete');
    card.style.display = 'none';
    state.modifiedLists.add(listPath);

    const container = card.closest(`[data-list-path="${listPath}"]`);
    if (container) _renumberListItems(container);
}

export function moveListItem(listPath, index, direction, btnEl) {
    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return;
    const container = card.querySelector(`[data-list-path="${listPath}"]`);
    if (!container) return;

    const items = [...container.querySelectorAll('.list-item-card:not(.pending-delete)')];
    const itemCard = btnEl
        ? btnEl.closest('.list-item-card')
        : items.find(c => parseInt(c.dataset.listIndex) === index);
    if (!itemCard) return;

    const currentIdx = items.indexOf(itemCard);

    let moved = false;
    if (direction === 'up' && currentIdx > 0) {
        container.insertBefore(itemCard, items[currentIdx - 1]);
        moved = true;
    } else if (direction === 'down' && currentIdx < items.length - 1) {
        const next = items[currentIdx + 1];
        container.insertBefore(next, itemCard);
        moved = true;
    }

    if (moved) {
        container.querySelectorAll('.list-item-card').forEach(c => c.classList.remove('list-item-moved'));
        itemCard.classList.add('list-item-moved');
        itemCard.style.animation = 'none';
        void itemCard.offsetHeight;
        itemCard.style.animation = '';
    }

    _renumberListItems(container);
    state.modifiedLists.add(listPath);
}

export function _renumberListItems(container) {
    const items = container.querySelectorAll('.list-item-card:not(.pending-delete)');
    items.forEach((card, i) => {
        const idx = card.querySelector('.list-item-idx');
        if (idx) idx.textContent = i + 1;
    });
}

export function _rebuildListFromDOM(card, listPath) {
    const container = card.querySelector(`[data-list-path="${listPath}"]`);
    if (!container) return null;

    const schema = TASK3_LIST_SCHEMAS[listPath] || TASK2_LIST_SCHEMAS[listPath];
    if (!schema) return null;

    const result = [];
    const items = container.querySelectorAll('.list-item-card:not(.pending-delete)');

    items.forEach((itemCard, newIdx) => {
        const obj = {};
        const origIdx = parseInt(itemCard.dataset.listIndex);

        const allKeys = new Set(schema.fields);
        itemCard.querySelectorAll('tr[data-key]:not(.pending-delete)').forEach(tr => {
            allKeys.add(tr.dataset.key);
        });

        allKeys.forEach(fieldKey => {
            const possiblePaths = [
                `${listPath}.${newIdx}.${fieldKey}`,
                `${listPath}.${origIdx}.${fieldKey}`,
            ];
            let value = undefined;

            for (const fullPath of possiblePaths) {
                const el = itemCard.querySelector(`[data-field="${CSS.escape(fullPath)}"]`);
                if (!el) continue;
                if (el.closest('.pending-delete')) continue;

                if (el.dataset.useTextarea === 'true') {
                    const ta = el.querySelector('.inline-edit-ta');
                    if (ta) {
                        try { value = JSON.parse(ta.value.trim()); } catch { value = ta.value.trim(); }
                    }
                } else {
                    let raw = stripNewlineSymbol(el.textContent).trim();
                    if (raw === '(없음)') raw = '';
                    value = raw;
                }
                break;
            }

            if (value === undefined) {
                const fullPath = `${listPath}.${origIdx}.${fieldKey}`;
                value = getNestedValue(state.rawEditData, fullPath);
            }

            if (value !== undefined) {
                obj[fieldKey] = value;
            }
        });

        const deletedKeys = new Set();
        itemCard.querySelectorAll('tr.pending-delete[data-key]').forEach(tr => {
            deletedKeys.add(tr.dataset.key);
        });
        deletedKeys.forEach(k => delete obj[k]);

        result.push(obj);
    });

    return result;
}
