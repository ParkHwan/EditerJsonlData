/**
 * list.js - 리스트 CRUD (추가, 삭제, 이동, 토글, 재빌드)
 */
import { state } from './state.js';
import { escapeHtml, getNestedValue } from './utils.js';
import { TASK3_LIST_SCHEMAS, TASK2_LIST_SCHEMAS } from './schemas.js';
import { showToast } from './api.js';

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
        fieldsHtml += `
            <tr>
                <th>${escapeHtml(f)}</th>
                <td class="editable-value" contenteditable="true"
                    data-field="${escapeHtml(fieldPath)}"
                    data-editing="true"></td>
            </tr>`;
    });

    const itemHtml = `
        <div class="list-item-card theme-green" data-list-index="${newIdx}">
            <div class="list-item-header">
                <span class="list-item-idx">${newIdx + 1}</span>
                <span>${escapeHtml(schema.label)} #${newIdx + 1} (신규)</span>
                <button class="btn-list-delete" style="display:inline-block" onclick="deleteListItem('${escapeHtml(listPath)}', -1, this)">×</button>
            </div>
            <table class="info-table">${fieldsHtml}</table>
        </div>`;

    container.insertAdjacentHTML('beforeend', itemHtml);
    state.modifiedLists.add(listPath);
    showToast(`${schema.label} 항목 추가됨`, 'success');
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

export function moveListItem(listPath, index, direction) {
    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return;
    const container = card.querySelector(`[data-list-path="${listPath}"]`);
    if (!container) return;

    const items = [...container.querySelectorAll('.list-item-card:not(.pending-delete)')];
    const itemCard = items.find(c => parseInt(c.dataset.listIndex) === index);
    if (!itemCard) return;

    const currentIdx = items.indexOf(itemCard);

    if (direction === 'up' && currentIdx > 0) {
        container.insertBefore(itemCard, items[currentIdx - 1]);
    } else if (direction === 'down' && currentIdx < items.length - 1) {
        const next = items[currentIdx + 1];
        container.insertBefore(next, itemCard);
    }

    _renumberListItems(container);
    state.modifiedLists.add(listPath);
}

export function _renumberListItems(container) {
    const items = container.querySelectorAll('.list-item-card:not(.pending-delete)');
    items.forEach((card, i) => {
        const idx = card.querySelector('.list-item-idx');
        if (idx) idx.textContent = i + 1;
        card.dataset.listIndex = i;
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

        schema.fields.forEach(fieldKey => {
            const possiblePaths = [
                `${listPath}.${newIdx}.${fieldKey}`,
                `${listPath}.${itemCard.dataset.listIndex}.${fieldKey}`,
            ];
            let value = undefined;

            for (const fullPath of possiblePaths) {
                const el = itemCard.querySelector(`[data-field="${fullPath}"]`);
                if (!el) continue;

                if (el.dataset.useTextarea === 'true') {
                    const ta = el.querySelector('.inline-edit-ta');
                    if (ta) {
                        try { value = JSON.parse(ta.value.trim()); } catch { value = ta.value.trim(); }
                    }
                } else if (el.dataset.editing === 'true') {
                    value = el.textContent.trim();
                } else {
                    value = el.textContent.trim();
                }
                break;
            }

            if (value === undefined) {
                const origIdx = parseInt(itemCard.dataset.listIndex);
                const fullPath = `${listPath}.${origIdx}.${fieldKey}`;
                value = getNestedValue(state.rawEditData, fullPath);
            }

            if (value !== undefined) {
                obj[fieldKey] = value;
            }
        });

        result.push(obj);
    });

    return result;
}
