/**
 * utils.js - 순수 유틸리티 함수 (DOM/상태 의존 없음)
 */
import { SPECIAL_KEY_TYPES, CONTEXT_KEY_TYPES } from './schemas.js';

export function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

export function stripNewlineSymbol(text) {
    return text.replace(/↵\n?/g, '\n');
}

export function unescapeEditValue(text) {
    return text.replace(/\\(\\|n|t|r)/g, (_, ch) => {
        switch (ch) {
            case '\\': return '\\';
            case 'n':  return '\n';
            case 't':  return '\t';
            case 'r':  return '\r';
            default:   return _;
        }
    });
}

export function getNestedValue(obj, path) {
    const parts = path.split('.');
    let current = obj;
    for (const part of parts) {
        if (current === null || current === undefined) return undefined;
        current = current[part];
    }
    return current;
}

export function setNestedValue(obj, path, value) {
    const parts = path.split('.');
    let current = obj;
    for (let i = 0; i < parts.length - 1; i++) {
        const nextKey = parts[i + 1];
        const isNextNumeric = /^\d+$/.test(nextKey);
        if (!current[parts[i]] || typeof current[parts[i]] !== 'object') {
            current[parts[i]] = isNextNumeric ? [] : {};
        }
        current = current[parts[i]];
    }
    current[parts[parts.length - 1]] = value;
}

export function deepMerge(target, source) {
    for (const key of Object.keys(source)) {
        if (Array.isArray(source[key]) && Array.isArray(target[key])) {
            for (let i = 0; i < source[key].length; i++) {
                if (source[key][i] === undefined) continue;
                if (i < target[key].length &&
                    source[key][i] && typeof source[key][i] === 'object' &&
                    target[key][i] && typeof target[key][i] === 'object') {
                    deepMerge(target[key][i], source[key][i]);
                } else {
                    target[key][i] = source[key][i];
                }
            }
        } else if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key]) &&
            target[key] && typeof target[key] === 'object' && !Array.isArray(target[key])) {
            deepMerge(target[key], source[key]);
        } else {
            target[key] = source[key];
        }
    }
}

export function classifyValueType(val) {
    if (val === null || val === undefined) return 'null';
    if (Array.isArray(val)) return 'array';
    if (typeof val === 'object') return 'dict';
    if (typeof val === 'number') return 'number';
    return 'string';
}

export function getFieldKeyName(fieldPath) {
    const parts = fieldPath.split('.');
    return parts[parts.length - 1];
}

export function getAllowedTypesForField(fieldPath) {
    if (CONTEXT_KEY_TYPES[fieldPath]) {
        return CONTEXT_KEY_TYPES[fieldPath].allowedTypes;
    }
    const keyName = getFieldKeyName(fieldPath);
    const info = SPECIAL_KEY_TYPES[keyName];
    return info ? info.allowedTypes : null;
}
