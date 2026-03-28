/**
 * config.js - Jinja2 템플릿 변수 브릿지
 * window.EDITOR_CONFIG에서 값을 읽어 ES Module 상수로 제공
 */
const cfg = window.EDITOR_CONFIG || {};

export const FILE_ID          = cfg.fileId          || '';
export const EDIT_MODE        = cfg.editMode        || 'local';
export const CURRENT_USER_ID  = cfg.currentUserId   || '';
export const AUTO_SAVE_INTERVAL = cfg.autoSaveInterval || 30;
export const GCS_DATE         = cfg.gcsDate          || '';
export const GCS_TASK         = cfg.gcsTask          || '';
export const API_V1_STR       = cfg.apiV1Str         || '/api/v1';
