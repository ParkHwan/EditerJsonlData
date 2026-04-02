/**
 * state.js - 편집기 전역 상태 관리
 * 모든 모듈이 이 객체를 import하여 공유 상태에 접근
 */
export const state = {
    currentRowIdx: null,
    currentVersion: null,
    currentDataId: null,
    selectedRowIdx: null,

    heartbeatInterval: null,
    autoSaveInterval: null,
    pendingDraftData: null,

    rawEditData: null,
    originalHtmlMap: {},
    originalTypeMap: {},

    fileLockOwner: null,
    isFileLockedByMe: false,
    lockStatusManager: null,

    deletedKeys: new Set(),
    addedKeys: new Map(),
    reorderedSections: new Map(),
    modifiedLists: new Set(),

    validationErrors: [],
    validationFilterActive: false,
};
