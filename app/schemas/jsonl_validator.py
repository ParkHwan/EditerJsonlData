"""JSONL 스키마 검증 모듈 (Phase 20.1)

TASK1/TASK2/TASK3 JSONL 구조를 docs/JSONL_SCHEMA.md 정의 기준으로 검증한다.
검증 결과는 errors(차단) + warnings(경고) 리스트로 반환.

사용처:
    - gcs_edit_service.update_row(): 변경 후 row 검증 (warning 로깅)
    - editor.py publish: 전체 row 일괄 검증 (error 시 발행 차단)
"""

from __future__ import annotations

from typing import Any

from app.core.logger import logger


class ValidationResult:
    """검증 결과를 담는 컨테이너"""

    __slots__ = ("errors", "warnings")

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def merge(self, other: ValidationResult) -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


# ──────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────

def _check_required_str(
    data: dict[str, Any], key: str, path: str, result: ValidationResult,
) -> None:
    if key not in data:
        result.add_error(f"{path}.{key}: 필수 필드 누락")
    elif not isinstance(data[key], str):
        result.add_error(
            f"{path}.{key}: string 타입이어야 합니다 "
            f"(현재: {type(data[key]).__name__})"
        )


def _check_required_field(
    data: dict[str, Any],
    key: str,
    expected_type: type | tuple[type, ...],
    path: str,
    result: ValidationResult,
) -> None:
    if key not in data:
        result.add_error(f"{path}.{key}: 필수 필드 누락")
    elif not isinstance(data[key], expected_type):
        type_names = (
            expected_type.__name__
            if isinstance(expected_type, type)
            else "/".join(t.__name__ for t in expected_type)
        )
        result.add_error(
            f"{path}.{key}: {type_names} 타입이어야 합니다 "
            f"(현재: {type(data[key]).__name__})"
        )


def _check_optional_field(
    data: dict[str, Any],
    key: str,
    expected_type: type | tuple[type, ...],
    path: str,
    result: ValidationResult,
) -> None:
    if key in data and data[key] is not None:
        if not isinstance(data[key], expected_type):
            type_names = (
                expected_type.__name__
                if isinstance(expected_type, type)
                else "/".join(t.__name__ for t in expected_type)
            )
            result.add_error(
                f"{path}.{key}: {type_names} 타입이어야 합니다 "
                f"(현재: {type(data[key]).__name__})"
            )


def _check_list_of_dicts(
    data: dict[str, Any],
    key: str,
    path: str,
    result: ValidationResult,
    *,
    required: bool = True,
) -> list[dict[str, Any]]:
    """list[dict] 타입 필드 검사. 반환: 검증된 리스트 (비어있으면 [])"""
    if key not in data:
        if required:
            result.add_error(f"{path}.{key}: 필수 필드 누락")
        return []

    val = data[key]
    if not isinstance(val, list):
        result.add_error(
            f"{path}.{key}: list 타입이어야 합니다 (현재: {type(val).__name__})"
        )
        return []

    for i, item in enumerate(val):
        if not isinstance(item, dict):
            result.add_error(
                f"{path}.{key}[{i}]: dict 타입이어야 합니다 "
                f"(현재: {type(item).__name__})"
            )
    return [item for item in val if isinstance(item, dict)]


def _check_list_of_type(
    data: dict[str, Any],
    key: str,
    item_type: type,
    path: str,
    result: ValidationResult,
    *,
    required: bool = True,
) -> None:
    """list 필드의 각 value가 특정 타입(int, str 등)인지 검사."""
    if key not in data:
        if required:
            result.add_error(f"{path}.{key}: 필수 필드 누락")
        return

    val = data[key]
    if not isinstance(val, list):
        result.add_error(
            f"{path}.{key}: list 타입이어야 합니다 (현재: {type(val).__name__})"
        )
        return

    type_name = item_type.__name__
    for i, item in enumerate(val):
        if item_type is int:
            if not isinstance(item, int) or isinstance(item, bool):
                result.add_error(
                    f"{path}.{key}[{i}]: {type_name} 타입이어야 합니다 "
                    f"(현재: {type(item).__name__})"
                )
        elif not isinstance(item, item_type):
            result.add_error(
                f"{path}.{key}[{i}]: {type_name} 타입이어야 합니다 "
                f"(현재: {type(item).__name__})"
            )


# (필드명, 기대타입, 필수여부)
FieldSpec = tuple[str, type | tuple[type, ...], bool]


def _validate_list_dict_fields(
    items: list[dict[str, Any]],
    path: str,
    result: ValidationResult,
    field_specs: list[FieldSpec],
) -> None:
    """list[dict] 내 각 dict의 필수/선택 필드를 검증한다."""
    for i, item in enumerate(items):
        item_path = f"{path}[{i}]"
        for field_name, expected_type, is_required in field_specs:
            if is_required:
                _check_required_field(item, field_name, expected_type, item_path, result)
            else:
                _check_optional_field(item, field_name, expected_type, item_path, result)


# ──────────────────────────────────────────────
# 공통 최상위 필드 검증
# ──────────────────────────────────────────────

_TOP_LEVEL_REQUIRED_STR = [
    "data_id", "data_file", "data_title", "data_source",
    "category_main", "category_sub", "collected_date",
]


def _validate_top_level(row: dict[str, Any], result: ValidationResult) -> None:
    for key in _TOP_LEVEL_REQUIRED_STR:
        _check_required_str(row, key, "row", result)
    _check_required_field(row, "data_type", list, "row", result)


def _validate_content_meta(
    content_meta: dict[str, Any], result: ValidationResult,
) -> None:
    path = "content_meta"
    for tag_name, tag_data in content_meta.items():
        if not isinstance(tag_data, dict):
            result.add_error(f"{path}.{tag_name}: dict 타입이어야 합니다")
            continue

        tag_path = f"{path}.{tag_name}"
        _check_required_str(tag_data, "type", tag_path, result)
        _check_required_str(tag_data, "info", tag_path, result)
        _check_required_field(tag_data, "tag_properties", dict, tag_path, result)
        if isinstance(tag_data.get("tag_properties"), dict):
            tp = tag_data["tag_properties"]
            tp_path = f"{tag_path}.tag_properties"
            _check_list_of_type(tp, "page_num", int, tp_path, result, required=False)

        if "img_size" in tag_data and isinstance(tag_data["img_size"], dict):
            img_path = f"{tag_path}.img_size"
            for dim in ("channel", "height", "width"):
                _check_required_field(
                    tag_data["img_size"], dim, int, img_path, result,
                )

        if "bbox" in tag_data and isinstance(tag_data["bbox"], dict):
            bbox_path = f"{tag_path}.bbox"
            for coord in ("x1", "y1", "x2", "y2"):
                _check_required_field(
                    tag_data["bbox"], coord, (int, float), bbox_path, result,
                )


# ──────────────────────────────────────────────
# TASK1 검증
# ──────────────────────────────────────────────

def _validate_task1_add_info(
    add_info: dict[str, Any], result: ValidationResult,
) -> None:
    path = "add_info"
    _check_list_of_type(add_info, "page_num", int, path, result, required=True)
    _check_required_str(add_info, "source_file", path, result)

    if "book_meta" in add_info:
        bm = add_info["book_meta"]
        if not isinstance(bm, dict):
            result.add_error(f"{path}.book_meta: dict 타입이어야 합니다")
        else:
            bm_path = f"{path}.book_meta"
            for k in ("학교급", "과목", "학년", "학기", "도서시리즈"):
                _check_required_str(bm, k, bm_path, result)
            _check_required_field(bm, "연도", int, bm_path, result)

    if "unit_meta" in add_info:
        um = add_info["unit_meta"]
        if not isinstance(um, dict):
            result.add_error(f"{path}.unit_meta: dict 타입이어야 합니다")
        else:
            um_path = f"{path}.unit_meta"
            for k in ("유형", "학습파트", "대단원", "중단원"):
                _check_required_str(um, k, um_path, result)
            _check_optional_field(um, "소단원", str, um_path, result)

    if "문제" in add_info:
        q = add_info["문제"]
        if not isinstance(q, dict):
            result.add_error(f"{path}.문제: dict 타입이어야 합니다")
        else:
            q_path = f"{path}.문제"
            _check_required_str(q, "문제유형", q_path, result)
            _check_required_str(q, "단일질문", q_path, result)
            _check_optional_field(q, "보기", (list, dict), q_path, result)
            _check_optional_field(q, "선택지", dict, q_path, result)
            _check_optional_field(q, "매칭항목", list, q_path, result)
            _check_optional_field(q, "부가정보", dict, q_path, result)

    if "풀이" in add_info:
        if not isinstance(add_info["풀이"], dict):
            result.add_error(f"{path}.풀이: dict 타입이어야 합니다")

    _check_list_of_type(add_info, "add_images", str, path, result, required=False)


# ──────────────────────────────────────────────
# TASK2 검증
# ──────────────────────────────────────────────

def _validate_task2_add_info(
    add_info: dict[str, Any], result: ValidationResult,
) -> None:
    path = "add_info"
    _check_required_str(add_info, "pairIDX", path, result)
    _check_required_str(add_info, "source_file", path, result)

    _check_required_field(add_info, "논제", dict, path, result)
    if isinstance(add_info.get("논제"), dict):
        t = add_info["논제"]
        t_path = f"{path}.논제"
        _check_required_str(t, "회차", t_path, result)
        _check_required_str(t, "출처", t_path, result)
        _check_required_str(t, "본문", t_path, result)
        _check_optional_field(t, "글자수", str, t_path, result)
        _check_optional_field(t, "유형", str, t_path, result)
        _check_optional_field(t, "제목", str, t_path, result)

    _check_required_field(add_info, "논제분석", dict, path, result)
    if isinstance(add_info.get("논제분석"), dict):
        a = add_info["논제분석"]
        a_path = f"{path}.논제분석"
        _check_required_str(a, "해설", a_path, result)
        _check_required_str(a, "예시답안", a_path, result)
        _check_optional_field(a, "평가기준", str, a_path, result)

    _check_required_str(add_info, "학생답안", path, result)

    _check_required_field(add_info, "교사첨삭", dict, path, result)
    if isinstance(add_info.get("교사첨삭"), dict):
        tc = add_info["교사첨삭"]
        tc_path = f"{path}.교사첨삭"

        items = _check_list_of_dicts(tc, "총평가", tc_path, result, required=True)
        if items:
            _validate_list_dict_fields(items, f"{tc_path}.총평가", result, [
                ("유형", str, True),
                ("내용", str, True),
                ("항목", str, False),
            ])

        items = _check_list_of_dicts(tc, "세부평가", tc_path, result, required=False)
        if items:
            _validate_list_dict_fields(items, f"{tc_path}.세부평가", result, [
                ("항목", str, False),
                ("기준", list, False),
                ("결과", str, False),
                ("원본기준", list, False),
            ])

        items = _check_list_of_dicts(tc, "세부첨삭", tc_path, result, required=True)
        if items:
            _validate_list_dict_fields(items, f"{tc_path}.세부첨삭", result, [
                ("원본", str, True),
                ("유형", list, True),
                ("내용", str, True),
            ])


# ──────────────────────────────────────────────
# TASK3 검증
# ──────────────────────────────────────────────

def _validate_task3_add_info(
    add_info: dict[str, Any], result: ValidationResult,
) -> None:
    path = "add_info"
    _check_required_str(add_info, "pairIDX", path, result)
    _check_required_str(add_info, "source_file", path, result)

    _check_required_field(add_info, "논제", dict, path, result)
    if isinstance(add_info.get("논제"), dict):
        t = add_info["논제"]
        t_path = f"{path}.논제"
        _check_required_str(t, "회차", t_path, result)
        _check_required_str(t, "출처", t_path, result)
        _check_required_str(t, "제시문", t_path, result)
        _check_required_field(t, "문항", dict, t_path, result)
        if isinstance(t.get("문항"), dict):
            m = t["문항"]
            m_path = f"{t_path}.문항"
            _check_optional_field(m, "지문", str, m_path, result)
            items = _check_list_of_dicts(m, "질문", m_path, result, required=True)
            if items:
                _validate_list_dict_fields(items, f"{m_path}.질문", result, [
                    ("번호", str, True),
                    ("본문", str, True),
                    ("배점", str, False),
                ])

    _check_required_field(add_info, "논제분석", dict, path, result)
    if isinstance(add_info.get("논제분석"), dict):
        a = add_info["논제분석"]
        a_path = f"{path}.논제분석"
        _check_optional_field(a, "해설", str, a_path, result)
        if isinstance(a.get("예시답안"), dict):
            items = _check_list_of_dicts(
                a["예시답안"], "문항_질문",
                f"{a_path}.예시답안", result, required=False,
            )
            if items:
                _validate_list_dict_fields(items, f"{a_path}.예시답안.문항_질문", result, [
                    ("번호", str, False),
                    ("답안", str, False),
                ])
        if isinstance(a.get("평가기준"), dict):
            items = _check_list_of_dicts(
                a["평가기준"], "문항_질문",
                f"{a_path}.평가기준", result, required=False,
            )
            if items:
                _validate_list_dict_fields(items, f"{a_path}.평가기준.문항_질문", result, [
                    ("번호", str, False),
                    ("내용", str, False),
                ])

    _check_required_field(add_info, "학생답안", dict, path, result)
    if isinstance(add_info.get("학생답안"), dict):
        items = _check_list_of_dicts(
            add_info["학생답안"], "문항_질문",
            f"{path}.학생답안", result, required=True,
        )
        if items:
            _validate_list_dict_fields(items, f"{path}.학생답안.문항_질문", result, [
                ("번호", str, True),
                ("답안", str, True),
            ])

    _check_required_field(add_info, "교사첨삭", dict, path, result)
    if isinstance(add_info.get("교사첨삭"), dict):
        tc = add_info["교사첨삭"]
        tc_path = f"{path}.교사첨삭"

        items = _check_list_of_dicts(tc, "평가", tc_path, result, required=False)
        if items:
            _validate_list_dict_fields(items, f"{tc_path}.평가", result, [
                ("평가유형", str, False),
                ("문항_질문_번호", str, False),
                ("항목", str, False),
                ("유형", str, False),
                ("기준", list, False),
                ("결과", str, False),
                ("내용", str, False),
                ("원본기준", (str, list), False),
            ])

        items = _check_list_of_dicts(tc, "세부첨삭", tc_path, result, required=True)
        if items:
            _validate_list_dict_fields(items, f"{tc_path}.세부첨삭", result, [
                ("문항_질문_번호", str, True),
                ("원본", str, True),
                ("유형", str, True),
                ("내용", str, True),
                ("첨삭본문이미지", str, False),
            ])


# ──────────────────────────────────────────────
# Task 판별 (render/task1.py의 detect_task_type 호환)
# ──────────────────────────────────────────────

def _detect_task_type(add_info: Any) -> str:
    if not isinstance(add_info, dict) or "논제" not in add_info:
        return "task1"
    topic = add_info.get("논제")
    if isinstance(topic, dict) and "문항" in topic:
        return "task3"
    return "task2"


# ──────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────

def validate_row(row: dict[str, Any]) -> ValidationResult:
    """JSONL row를 TASK 유형에 맞게 검증한다.

    Returns:
        ValidationResult (errors: 차단, warnings: 경고)
    """
    result = ValidationResult()

    if not isinstance(row, dict):
        result.add_error("row: dict 타입이어야 합니다")
        return result

    _validate_top_level(row, result)

    add_info = row.get("add_info")
    task_type = _detect_task_type(add_info) if isinstance(add_info, dict) else "task1"

    if task_type == "task1":
        _check_required_field(row, "content", (str, dict), "row", result)
    else:
        _check_optional_field(row, "content", str, "row", result)

    cm = row.get("content_meta")
    if cm is not None:
        if isinstance(cm, dict):
            _validate_content_meta(cm, result)
        else:
            result.add_error("content_meta: dict 타입이어야 합니다")

    if add_info is not None:
        if not isinstance(add_info, dict):
            result.add_error("add_info: dict 타입이어야 합니다")
            return result

        if task_type == "task1":
            _validate_task1_add_info(add_info, result)
        elif task_type == "task2":
            _validate_task2_add_info(add_info, result)
        else:
            _validate_task3_add_info(add_info, result)

    return result


def validate_row_safe(row: dict[str, Any]) -> ValidationResult:
    """예외를 발생시키지 않는 안전한 검증. 내부 오류는 warning으로 처리."""
    try:
        return validate_row(row)
    except Exception as e:
        logger.warning("JSONL 스키마 검증 중 예외: %s", e)
        result = ValidationResult()
        result.add_warning(f"검증 중 예외 발생: {e}")
        return result
