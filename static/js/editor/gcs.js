/**
 * gcs.js - GCS 배포(publish) 및 편집 취소(discard)
 */
import { FILE_ID, API_V1_STR, EDIT_MODE, GCS_DATE, GCS_TASK } from './config.js';
import { state } from './state.js';
import { csrfFetch, showToast } from './api.js';
import { updateFileLockUI } from './lock.js';

export async function publishToGCS() {
    if (EDIT_MODE !== 'gcs') return;

    if (state.currentRowIdx !== null) {
        showToast('편집 중인 행을 먼저 저장하거나 취소해주세요.', 'warning');
        return;
    }

    if (!confirm('편집한 내용을 GCS 파일에 업데이트하시겠습니까?\n기존 GCS 파일이 덮어쓰기됩니다.')) {
        return;
    }

    const btn = document.querySelector('.btn-gcs-publish');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ 업데이트 중...'; }

    try {
        const resp = await csrfFetch(`${API_V1_STR}/editor/publish/${FILE_ID}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await resp.json();
        if (resp.ok) {
            state.isFileLockedByMe = false;
            state.fileLockOwner = null;
            updateFileLockUI();
            if (state.heartbeatInterval) { clearInterval(state.heartbeatInterval); state.heartbeatInterval = null; }
            showToast(data.message || 'GCS 파일 업데이트 완료!', 'success');
        } else {
            showToast(data.detail || 'GCS 파일 업데이트 실패', 'error');
        }
    } catch (e) {
        showToast('GCS 업데이트 오류: ' + e.message, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'GCS 파일 업데이트'; }
    }
}

export async function discardWorkingCopy() {
    if (EDIT_MODE !== 'gcs') return;

    if (state.currentRowIdx !== null) {
        showToast('편집 중인 행을 먼저 저장하거나 취소해주세요.', 'warning');
        return;
    }

    if (!confirm('편집 세션을 취소하시겠습니까?\n모든 변경사항이 폐기되며 GCS 원본에는 영향이 없습니다.')) {
        return;
    }

    try {
        const resp = await csrfFetch(`${API_V1_STR}/editor/discard/${FILE_ID}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await resp.json();
        if (resp.ok) {
            state.isFileLockedByMe = false;
            state.fileLockOwner = null;
            if (state.heartbeatInterval) { clearInterval(state.heartbeatInterval); state.heartbeatInterval = null; }
            if (state.lockStatusManager) { state.lockStatusManager.disconnect(); }
            window.removeEventListener('beforeunload', window._beforeUnloadGuard);
            showToast(data.message || '편집 세션 취소됨', 'success');
            const taskParam = GCS_TASK ? `?task=${GCS_TASK}` : '';
            const backUrl = GCS_DATE
                ? `${API_V1_STR}/gcs/browse/${GCS_DATE}${taskParam}`
                : `${API_V1_STR}/gcs/browse${taskParam}`;
            setTimeout(() => { window.location.href = backUrl; }, 1000);
        } else {
            showToast(data.detail || '취소 실패', 'error');
        }
    } catch (e) {
        showToast('취소 오류: ' + e.message, 'error');
    }
}
