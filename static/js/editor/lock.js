/**
 * lock.js - 파일 잠금 관리 (LockStatusManager, acquireFileLock, heartbeat)
 */
import { FILE_ID, API_V1_STR, CURRENT_USER_ID, EDIT_MODE, GCS_DATE, GCS_TASK } from './config.js';
import { state } from './state.js';
import { csrfFetch, showToast } from './api.js';

export function updateFileLockUI() {
    const banner = document.getElementById('fileLockBanner');
    const text = document.getElementById('fileLockText');
    const btn = document.getElementById('btnFileLock');

    banner.classList.remove('locked-by-me', 'locked-by-other', 'unlocked');

    if (state.isFileLockedByMe) {
        banner.classList.add('locked-by-me');
        text.textContent = '🔒 편집 모드 — 편집을 중단하려면 일시중단을 눌러주세요.';
        btn.textContent = '⏸️ 편집 일시중단';
        btn.className = 'btn-file-lock end';
        btn.style.display = '';
        btn.disabled = false;
    } else if (state.fileLockOwner) {
        banner.classList.add('locked-by-other');
        text.textContent = `🔒 ${state.fileLockOwner}님이 편집 중입니다. 열람만 가능합니다.`;
        btn.style.display = 'none';
    } else {
        banner.classList.add('unlocked');
        text.textContent = '🔒 편집 잠금 획득 중...';
        btn.style.display = 'none';
    }

    document.querySelectorAll('.btn-edit').forEach(b => {
        b.disabled = !state.isFileLockedByMe;
    });
}

export async function acquireFileLock() {
    if (state.isFileLockedByMe) return;

    try {
        const resp = await csrfFetch(`${API_V1_STR}/editor/lock/${FILE_ID}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        if (resp.status === 401) {
            window.location.href = `${API_V1_STR}/view/login`;
            return;
        }
        if (!resp.ok) {
            const err = await resp.json();
            showToast(err.detail || '편집 잠금 실패', 'error');
            return;
        }

        state.isFileLockedByMe = true;
        state.fileLockOwner = CURRENT_USER_ID;
        updateFileLockUI();

        state.heartbeatInterval = setInterval(sendHeartbeat, 30000);
    } catch (e) {
        showToast('편집 잠금 획득 실패: ' + e.message, 'error');
    }
}

export async function sendHeartbeat() {
    try {
        const resp = await fetch(`${API_V1_STR}/editor/lock/${FILE_ID}/heartbeat`, {
            method: 'POST',
            credentials: 'same-origin'
        });
        if (!resp.ok) {
            showToast('Lock 연장 실패 - 편집을 종료해 주세요', 'warning');
        }
    } catch (e) { /* silent */ }
}

export async function pauseEditing() {
    if (state.currentRowIdx !== null) {
        const doSave = confirm(
            '편집 중인 항목이 있습니다.\n\n'
            + '확인 → 현재 변경사항을 저장 후 나가기\n'
            + '취소 → 변경사항 무시 후 나가기'
        );
        // saveEdit / cancelEdit는 main.js에서 window에 노출되므로 window를 통해 호출
        if (doSave) {
            await window.saveEdit();
        } else {
            await window.cancelEdit();
        }
    }

    if (state.heartbeatInterval) {
        clearInterval(state.heartbeatInterval);
        state.heartbeatInterval = null;
    }
    if (state.lockStatusManager) {
        state.lockStatusManager.disconnect();
    }

    window.removeEventListener('beforeunload', window._beforeUnloadGuard);

    if (EDIT_MODE === 'gcs') {
        const taskParam = GCS_TASK ? `?task=${GCS_TASK}` : '';
        const backUrl = GCS_DATE
            ? `${API_V1_STR}/gcs/browse/${GCS_DATE}${taskParam}`
            : `${API_V1_STR}/gcs/browse${taskParam}`;
        window.location.href = backUrl;
    } else {
        window.location.href = `${API_V1_STR}/view/files`;
    }
}

export class LockStatusManager {
    constructor(fileId, currentUserId) {
        this.fileId = fileId;
        this.currentUserId = currentUserId;
        this.ws = null;
        this.reconnectTimer = null;
        this.pingInterval = null;
    }

    get wsUrl() {
        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${location.host}${API_V1_STR}/ws/lock-status/${this.fileId}`;
    }

    connect() {
        if (this.ws) return;

        this.ws = new WebSocket(this.wsUrl);

        this.ws.onopen = () => {
            this._startPing();
        };

        this.ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'init' || data.type === 'lock_change') {
                    if (data.locked_by) {
                        state.fileLockOwner = data.locked_by;
                        state.isFileLockedByMe = (data.locked_by === this.currentUserId);
                        if (state.isFileLockedByMe && !state.heartbeatInterval) {
                            state.heartbeatInterval = setInterval(sendHeartbeat, 30000);
                        }
                    } else {
                        state.fileLockOwner = null;
                        state.isFileLockedByMe = false;
                        acquireFileLock();
                    }
                    updateFileLockUI();
                }
            } catch (e) { /* silent */ }
        };

        this.ws.onclose = () => {
            this._stopPing();
            this.ws = null;
            this.reconnectTimer = setTimeout(() => this.connect(), 3000);
        };

        this.ws.onerror = () => {
            if (this.ws) this.ws.close();
        };
    }

    _startPing() {
        this._stopPing();
        this.pingInterval = setInterval(() => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);
    }

    _stopPing() {
        if (this.pingInterval) {
            clearInterval(this.pingInterval);
            this.pingInterval = null;
        }
    }

    disconnect() {
        this._stopPing();
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
