/**
 * main.js - 편집기 진입점
 * 모든 모듈을 임포트하고, DOMContentLoaded 초기화 및 window 함수 노출
 */
import { FILE_ID, API_V1_STR, CURRENT_USER_ID } from './config.js';
import { state } from './state.js';
import { LockStatusManager, acquireFileLock, pauseEditing, updateFileLockUI, sendHeartbeat } from './lock.js';
import { filterSidebar, selectItem, navigatePrev, navigateNext } from './sidebar.js';
import { startRowEdit } from './edit.js';
import { saveEdit, cancelEdit, collectInlineChanges } from './save.js';
import { toggleSection, addListItem, deleteListItem, moveListItem } from './list.js';
import { addKeyToSection, deleteKeyFromSection, moveKeyUp, moveKeyDown } from './keys.js';
import { publishToGCS, discardWorkingCopy } from './gcs.js';
import { restoreDraft, discardDraft } from './draft.js';
import { showToast } from './api.js';

// ── render_service.py의 onclick 핸들러를 위해 window에 함수 노출 ──
window.startRowEdit       = startRowEdit;
window.saveEdit           = saveEdit;
window.cancelEdit         = cancelEdit;
window.collectInlineChanges = collectInlineChanges;
window.toggleSection      = toggleSection;
window.addListItem        = addListItem;
window.deleteListItem     = deleteListItem;
window.moveListItem       = moveListItem;
window.addKeyToSection    = addKeyToSection;
window.deleteKeyFromSection = deleteKeyFromSection;
window.moveKeyUp          = moveKeyUp;
window.moveKeyDown        = moveKeyDown;
window.pauseEditing       = pauseEditing;
window.publishToGCS       = publishToGCS;
window.discardWorkingCopy = discardWorkingCopy;
window.restoreDraft       = restoreDraft;
window.discardDraft       = discardDraft;
window.filterSidebar      = filterSidebar;
window.navigatePrev       = navigatePrev;
window.navigateNext       = navigateNext;
window.showToast          = showToast;

// ── beforeunload 가드 ──
function _beforeUnloadGuard(e) {
    if (state.currentRowIdx !== null || state.isFileLockedByMe) {
        e.preventDefault();
        e.returnValue = '';
    }
}
window._beforeUnloadGuard = _beforeUnloadGuard;
window.addEventListener('beforeunload', _beforeUnloadGuard);

// ── unload 시 Lock 해제 beacon ──
window.addEventListener('unload', () => {
    if (state.isFileLockedByMe) {
        navigator.sendBeacon(`${API_V1_STR}/editor/lock/${FILE_ID}/release-beacon`);
    }
});

// ── 키보드 단축키 ──
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.currentRowIdx !== null) {
        cancelEdit();
    }
});

document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        if (state.currentRowIdx !== null) {
            saveEdit();
        }
    }
});

// ── 초기화 ──
document.addEventListener('DOMContentLoaded', async () => {
    if (FILE_ID) {
        state.lockStatusManager = new LockStatusManager(FILE_ID, CURRENT_USER_ID);
        state.lockStatusManager.connect();

        await acquireFileLock();
    }

    const sidebarList = document.getElementById('sidebarList');
    if (sidebarList) {
        sidebarList.addEventListener('click', (e) => {
            const li = e.target.closest('.sidebar-item');
            if (!li) return;
            const rowIdx = parseInt(li.dataset.rowIdx, 10);
            const dataId = li.dataset.dataId || '';
            selectItem(li, rowIdx, dataId);
        });
    }

    const firstItem = document.querySelector('.sidebar-item');
    if (firstItem) {
        const rowIdx = parseInt(firstItem.dataset.rowIdx, 10);
        const dataId = firstItem.dataset.dataId || '';
        selectItem(firstItem, rowIdx, dataId);
    }
});
