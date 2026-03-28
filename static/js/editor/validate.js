/**
 * validate.js - JSON 검증, 변경사항 검증, diff 하이라이트, 저장 확인 모달
 */
import { state } from './state.js';
import { getNestedValue, classifyValueType, getAllowedTypesForField, escapeHtml } from './utils.js';

export function attachJsonValidator(ta) {
    let msgEl = ta.nextElementSibling;
    if (!msgEl || !msgEl.classList.contains('json-validation-msg')) {
        msgEl = document.createElement('div');
        msgEl.className = 'json-validation-msg';
        ta.parentNode.insertBefore(msgEl, ta.nextSibling);
    }
    const validate = () => {
        const text = ta.value.trim();
        if (!text) {
            ta.classList.remove('json-valid', 'json-invalid');
            msgEl.className = 'json-validation-msg warning';
            msgEl.textContent = '⚠ 값이 비어 있습니다';
            return;
        }
        try {
            const parsed = JSON.parse(text);
            const origType = ta.dataset.originalType || '';
            const newType = classifyValueType(parsed);
            const allowedRaw = ta.dataset.allowedTypes || '';
            const allowedTypes = allowedRaw ? allowedRaw.split(',') : [];

            if (origType && origType !== newType && origType !== 'null') {
                if (allowedTypes.length > 0 && allowedTypes.includes(newType)) {
                    ta.classList.remove('json-invalid');
                    ta.classList.add('json-valid');
                    msgEl.className = 'json-validation-msg warning';
                    msgEl.textContent = `⚠ 타입 변경 감지: ${origType} → ${newType} (허용된 타입: ${allowedTypes.join(', ')})`;
                } else {
                    ta.classList.remove('json-valid');
                    ta.classList.add('json-invalid');
                    msgEl.className = 'json-validation-msg invalid';
                    msgEl.textContent = `⛔ 타입 변경 감지: ${origType} → ${newType} (원본 구조를 유지하세요)`;
                }
            } else {
                ta.classList.remove('json-invalid');
                ta.classList.add('json-valid');
                msgEl.className = 'json-validation-msg valid';
                msgEl.textContent = '✓ JSON 유효';
            }
        } catch (e) {
            ta.classList.remove('json-valid');
            ta.classList.add('json-invalid');
            msgEl.className = 'json-validation-msg invalid';
            msgEl.textContent = `⛔ JSON 오류: ${e.message}`;
        }
    };
    ta.addEventListener('input', validate);
    validate();
}

export function validateAllChanges() {
    const card = document.querySelector(`.item[data-row-idx="${state.currentRowIdx}"]`);
    if (!card) return { valid: true, errors: [], warnings: [], changes: [] };

    const errors = [];
    const warnings = [];
    const changes = [];
    const editables = card.querySelectorAll('[data-field][data-editing="true"]');

    editables.forEach(el => {
        if (el.closest('.pending-delete')) return;

        const field = el.dataset.field;
        const origType = state.originalTypeMap[field] || 'string';
        const rawOriginal = getNestedValue(state.rawEditData, field);
        let newValue;

        if (el.dataset.useTextarea === 'true') {
            const ta = el.querySelector('.inline-edit-ta');
            const text = ta ? ta.value.trim() : '';

            if (!text && rawOriginal !== null && rawOriginal !== undefined && rawOriginal !== '') {
                warnings.push({ field, msg: '값이 비어 있습니다 (원래 값 존재)' });
            }

            if (origType === 'dict' || origType === 'array') {
                try {
                    newValue = JSON.parse(text);
                    const newType = classifyValueType(newValue);
                    if (origType !== newType && origType !== 'null') {
                        const allowed = getAllowedTypesForField(field);
                        if (!allowed || !allowed.includes(newType)) {
                            errors.push({ field, msg: `타입 변경 불가: ${origType} → ${newType}` });
                        }
                    }
                } catch (e) {
                    errors.push({ field, msg: `JSON 파싱 오류: ${e.message}` });
                    return;
                }
            } else {
                try { newValue = JSON.parse(text); } catch { newValue = text; }
            }
        } else {
            newValue = el.textContent.trim();
        }

        const fromStr = (rawOriginal !== null && rawOriginal !== undefined && typeof rawOriginal === 'object')
            ? JSON.stringify(rawOriginal, null, 2)
            : String(rawOriginal ?? '');
        const toStr = (newValue !== null && newValue !== undefined && typeof newValue === 'object')
            ? JSON.stringify(newValue, null, 2)
            : String(newValue ?? '');

        if (fromStr !== toStr) {
            changes.push({ field, from: fromStr, to: toStr });
        }
    });

    for (const fieldPath of state.deletedKeys) {
        changes.push({ field: fieldPath, from: '(존재)', to: '(삭제됨)' });
    }
    for (const [fieldPath] of state.addedKeys) {
        changes.push({ field: fieldPath, from: '(없음)', to: '(추가됨)' });
    }

    return {
        valid: errors.length === 0,
        errors,
        warnings,
        changes,
    };
}

export function highlightDiff(oldStr, newStr, side) {
    const oldWords = String(oldStr).split(/(\s+)/);
    const newWords = String(newStr).split(/(\s+)/);
    let result = '';

    if (side === 'old') {
        for (let i = 0; i < oldWords.length; i++) {
            if (i >= newWords.length || oldWords[i] !== newWords[i]) {
                result += `<span class="hl-removed">${escapeHtml(oldWords[i])}</span>`;
            } else {
                result += escapeHtml(oldWords[i]);
            }
        }
    } else {
        for (let i = 0; i < newWords.length; i++) {
            if (i >= oldWords.length || newWords[i] !== oldWords[i]) {
                result += `<span class="hl-added">${escapeHtml(newWords[i])}</span>`;
            } else {
                result += escapeHtml(newWords[i]);
            }
        }
    }
    return result;
}

export function showSaveConfirm(validation) {
    return new Promise(resolve => {
        const { errors, warnings, changes } = validation;

        if (errors.length === 0 && warnings.length === 0 && changes.length === 0) {
            resolve(true);
            return;
        }

        const overlay = document.createElement('div');
        overlay.className = 'save-confirm-overlay';

        let itemsHtml = '';

        errors.forEach(e => {
            itemsHtml += `
                <div class="change-item error">
                    <div class="change-field">⛔ ${escapeHtml(e.field)}</div>
                    <div class="change-detail">${escapeHtml(e.msg)}</div>
                </div>`;
        });

        warnings.forEach(w => {
            itemsHtml += `
                <div class="change-item warning">
                    <div class="change-field">⚠ ${escapeHtml(w.field)}</div>
                    <div class="change-detail">${escapeHtml(w.msg)}</div>
                </div>`;
        });

        changes.forEach(c => {
            itemsHtml += `
                <div class="change-item modified">
                    <div class="change-field">${escapeHtml(c.field)}</div>
                    <div class="change-diff-split">
                        <div class="change-diff-col old-val">
                            <span class="change-diff-label old">이전</span><br>
                            ${highlightDiff(c.from, c.to, 'old')}
                        </div>
                        <div class="change-diff-col new-val">
                            <span class="change-diff-label new">변경</span><br>
                            ${highlightDiff(c.from, c.to, 'new')}
                        </div>
                    </div>
                </div>`;
        });

        overlay.innerHTML = `
            <div class="save-confirm-box">
                <h3>변경사항 확인</h3>
                ${itemsHtml}
                <div class="save-confirm-actions">
                    <button class="btn btn-cancel" id="saveConfirmCancel">취소</button>
                    <button class="btn btn-save" id="saveConfirmOk" ${errors.length > 0 ? 'disabled' : ''}>
                        ${errors.length > 0 ? '오류 수정 필요' : '저장'}
                    </button>
                </div>
            </div>`;

        document.body.appendChild(overlay);

        overlay.querySelector('#saveConfirmOk').addEventListener('click', () => {
            overlay.remove();
            resolve(true);
        });
        overlay.querySelector('#saveConfirmCancel').addEventListener('click', () => {
            overlay.remove();
            resolve(false);
        });
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) { overlay.remove(); resolve(false); }
        });
    });
}
