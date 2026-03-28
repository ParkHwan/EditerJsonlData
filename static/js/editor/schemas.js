/**
 * schemas.js - Task별 리스트 스키마 및 특수 키 정의
 */

export const TASK3_LIST_SCHEMAS = {
    "add_info.논제.문항.질문":               { fields: ["번호", "본문", "배점"], label: "질문" },
    "add_info.논제분석.예시답안.문항_질문":    { fields: ["번호", "답안"], label: "예시답안" },
    "add_info.논제분석.평가기준.문항_질문":    { fields: ["번호", "내용"], label: "평가기준" },
    "add_info.학생답안.문항_질문":            { fields: ["번호", "답안"], label: "학생답안" },
    "add_info.교사첨삭.평가":                { fields: ["평가유형","문항_질문_번호","항목","유형","기준","결과","내용","원본기준"], label: "평가" },
    "add_info.교사첨삭.세부첨삭":             { fields: ["문항_질문_번호","원본","유형","내용","첨삭본문이미지"], label: "세부첨삭" },
};

export const TASK2_LIST_SCHEMAS = {
    "add_info.교사첨삭.총평가":   { fields: ["항목", "유형", "내용"], label: "총평가" },
    "add_info.교사첨삭.세부평가": { fields: ["항목", "기준", "결과", "원본기준"], label: "세부평가" },
    "add_info.교사첨삭.세부첨삭": { fields: ["원본", "유형", "내용"], label: "세부첨삭" },
};

export const SPECIAL_KEY_TYPES = {
    '보기':     { type: 'array', allowedTypes: ['array', 'dict'], template: '[\n  "항목1",\n  "항목2",\n  "항목3"\n]', label: 'list 또는 dict' },
    '선택지':   { type: 'dict',  allowedTypes: ['dict'],          template: '{\n  "①": "선택지1",\n  "②": "선택지2",\n  "③": "선택지3",\n  "④": "선택지4"\n}', label: 'dict' },
    '매칭항목': { type: 'array', allowedTypes: ['array'],         template: '[\n  ["좌측1", "좌측2", "좌측3"],\n  ["우측1", "우측2", "우측3"]\n]', label: '중첩 list' },
};
