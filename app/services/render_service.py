"""JSONL 콘텐츠 렌더링 서비스

PM 스크립트(JJIn_last.py)의 렌더링 로직을 서비스 계층으로 포팅.
- 수식(KaTeX) 보존
- content_meta 태그 인라인 렌더링
- 이미지 base64 변환 (로컬) / GCS 프록시 URL (Phase 6-1)
- 매칭 테이블 / 보기 / 선택지 렌더링
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from app.core.config import settings

# ---------------------------------------------------------------------------
# 키별 색상 정의 (키 배경 / 값 배경)
# ---------------------------------------------------------------------------
BG_COLORS: dict[str, dict[str, str]] = {
    "공통질문": {"key": "#C62828", "value": "#FFEBEE"},
    "공통지문": {"key": "#EF6C00", "value": "#FFF3E0"},
    "단일질문": {"key": "#1565C0", "value": "#E3F2FD"},
    "단일지문": {"key": "#AD1457", "value": "#FCE4EC"},
    "보기":     {"key": "#00838F", "value": "#E0F7FA"},
    "선택지":   {"key": "#6A1B9A", "value": "#F3E5F5"},
    "정답":     {"key": "#2E7D32", "value": "#E8F5E9"},
    "해설":     {"key": "#F57F17", "value": "#FFF9C4"},
    "힌트":     {"key": "#F9A825", "value": "#FFFDE7"},
}

KOREAN_LABELS = [
    "ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ", "ㅂ", "ㅅ", "ㅇ",
    "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]

# ---------------------------------------------------------------------------
# Task 3 (논술 첨삭) 전용 상수
# ---------------------------------------------------------------------------
TASK3_LIST_SCHEMAS: dict[str, dict[str, Any]] = {
    "논제.문항.질문": {
        "fields": ["번호", "본문", "배점"],
        "theme": "blue",
        "label": "질문",
    },
    "논제분석.예시답안.문항_질문": {
        "fields": ["번호", "답안"],
        "theme": "green",
        "label": "예시답안",
    },
    "논제분석.평가기준.문항_질문": {
        "fields": ["번호", "내용"],
        "theme": "orange",
        "label": "평가기준",
    },
    "학생답안.문항_질문": {
        "fields": ["번호", "답안"],
        "theme": "orange",
        "label": "학생답안",
    },
    "교사첨삭.평가": {
        "fields": [
            "평가유형", "문항_질문_번호", "항목", "유형",
            "기준", "결과", "내용", "원본기준",
        ],
        "theme": "purple",
        "label": "평가",
    },
    "교사첨삭.세부첨삭": {
        "fields": ["문항_질문_번호", "원본", "유형", "내용", "첨삭본문이미지"],
        "theme": "pink",
        "label": "세부첨삭",
    },
}

TASK3_FIELD_STYLES: dict[str, str] = {
    "교사첨삭.세부첨삭.원본": "field-highlight-yellow",
    "교사첨삭.세부첨삭.내용": "field-highlight-blue",
}

# ---------------------------------------------------------------------------
# Task 2 (인문논술 첨삭) 전용 상수
# ---------------------------------------------------------------------------
TASK2_LIST_SCHEMAS: dict[str, dict[str, Any]] = {
    "교사첨삭.총평가": {
        "fields": ["항목", "유형", "내용"],
        "theme": "purple",
        "label": "총평가",
    },
    "교사첨삭.세부평가": {
        "fields": ["항목", "기준", "결과", "원본기준"],
        "theme": "teal",
        "label": "세부평가",
    },
    "교사첨삭.세부첨삭": {
        "fields": ["원본", "유형", "내용"],
        "theme": "amber",
        "label": "세부첨삭",
    },
}

TASK2_FIELD_STYLES: dict[str, str] = {
    "교사첨삭.세부첨삭.원본": "field-highlight-yellow",
    "교사첨삭.세부첨삭.내용": "field-highlight-blue",
}


def detect_task_type(add_info: dict[str, Any]) -> str:
    """add_info 구조로 Task 유형을 판별한다.

    Returns:
        "task3" — 논제.문항이 존재 (수리논술)
        "task2" — 논제가 존재하지만 문항 없음 (인문논술)
        "task1" — 논제 키 자체가 없음 (교재)
    """
    if not isinstance(add_info, dict) or "논제" not in add_info:
        return "task1"
    topic = add_info.get("논제")
    if isinstance(topic, dict) and "문항" in topic:
        return "task3"
    return "task2"


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


# ---------------------------------------------------------------------------
# HTML 텍스트 유틸리티
# ---------------------------------------------------------------------------
def escape_html(text: Any) -> str:
    """HTML 특수문자 이스케이프"""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def protect_math_expressions(text: str) -> tuple[str, dict[str, str]]:
    """수식($...$)을 플레이스홀더로 치환하여 HTML escape 시 보존.

    $...$만 매칭하고 $$는 인접한 인라인 수식의 경계로 취급한다.
    KaTeX 클라이언트가 $/$$ 구분자를 직접 처리하므로 서버에서는
    HTML 이스케이프 방지 용도로만 사용한다.
    """
    placeholders: dict[str, str] = {}
    counter = [0]

    def _replace(m: re.Match[str]) -> str:
        key = f"__MATH_PH_{counter[0]}__"
        placeholders[key] = m.group(0)
        counter[0] += 1
        return key

    text = re.sub(r"\$(.+?)\$", _replace, text)

    return text, placeholders


def restore_math_expressions(html: str, placeholders: dict[str, str]) -> str:
    r"""플레이스홀더를 원래 $...$ 수식으로 복원.

    KaTeX auto-render가 클라이언트에서 $...$ 구분자를 직접 파싱하므로
    서버에서는 원본 그대로 복원한다.
    """
    for key, original in placeholders.items():
        html = html.replace(key, original)
    return html


NEWLINE_SYMBOL = '<span class="nl-symbol" title="줄바꿈 (\\n)">↵</span><br>'


def format_text_with_newlines(text: Any) -> str:
    """줄바꿈 + 수식 보존을 함께 처리"""
    if text is None:
        return ""
    text = str(text)

    text, placeholders = protect_math_expressions(text)
    html = escape_html(text).replace("\n", NEWLINE_SYMBOL)
    html = restore_math_expressions(html, placeholders)

    return html


# ---------------------------------------------------------------------------
# 이미지 유틸리티
# ---------------------------------------------------------------------------
def get_image_base64(image_path: str | Path) -> str | None:
    """이미지를 base64로 인코딩"""
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return None


def get_images_from_folder(
    images_folder: str | Path,
    jsonl_filename: str,
) -> dict[str, str]:
    """JSONL 파일명에 매칭되는 이미지 폴더 검색

    예: EHT_3001_수능완성.jsonl → images/EHT_3001/ 폴더 탐색
    """
    images_folder = Path(images_folder)
    if not images_folder.exists():
        return {}

    jsonl_stem = Path(jsonl_filename).stem
    parts = jsonl_stem.split("_")

    if len(parts) >= 2:
        jsonl_prefix = f"{parts[0]}_{parts[1]}"
    else:
        return {}

    target_folder = images_folder / jsonl_prefix
    if not target_folder.exists():
        return {}

    images_dict: dict[str, str] = {}
    for root_dir, _dirs, files in os.walk(target_folder):
        for file in files:
            if Path(file).suffix.lower() in IMAGE_EXTENSIONS:
                images_dict[file] = os.path.join(root_dir, file)

    return images_dict


# ---------------------------------------------------------------------------
# content_meta 태그 렌더링
# ---------------------------------------------------------------------------
def _resolve_tags_in_html(
    html_text: str,
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """HTML 문자열 내의 <tag_...> 참조를 content_meta의 이미지로 치환한다.

    일반 HTML 태그(<table>, <tr> 등)는 보존하고,
    content_meta에 키가 존재하는 tag_ 접두어 참조만 이미지로 변환한다.
    """
    if not content_meta:
        return html_text

    def _replace_tag(match: re.Match[str]) -> str:
        tag_name = match.group(1)
        if tag_name in content_meta:
            meta_val = content_meta[tag_name]
            if isinstance(meta_val, dict) and meta_val.get("file_name"):
                return _render_image_tag(
                    meta_val["file_name"], comparison, gcs_image_base_url,
                )
        return match.group(0)

    return re.sub(r"<(tag_[A-Za-z0-9_]+)>", _replace_tag, html_text)


def _render_image_tag(
    fname_val: Any,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """이미지 파일명을 img 태그로 렌더링

    gcs_image_base_url이 제공되면 GCS 프록시 URL로 직접 서빙하고,
    없으면 로컬 파일을 base64로 인코딩하여 인라인 삽입한다.
    """
    if not fname_val:
        return ""

    fname_str = str(fname_val)

    # GCS 프록시 모드: file_name 상대 경로를 프록시 URL로 변환
    if gcs_image_base_url:
        img_url = f"{gcs_image_base_url}/{fname_str}"
        alt_text = escape_html(os.path.basename(fname_str))
        return (
            '<div class="meta-image">'
            f'<img src="{escape_html(img_url)}" loading="lazy" '
            f'alt="{alt_text}" '
            "onerror=\"this.style.display='none';"
            "this.nextElementSibling.style.display='block';\" />"
            f'<span class="error-msg" style="display:none;">'
            f"⚠️ GCS 이미지 로드 실패: {alt_text}</span>"
            "</div>"
        )

    # 로컬 base64 모드 (기존 방식)
    fname = os.path.basename(fname_str)
    img_path = comparison.get(fname)
    if img_path:
        img_64 = get_image_base64(img_path)
        if img_64:
            return (
                '<div class="meta-image">'
                f'<img src="data:image/png;base64,{img_64}" />'
                "</div>"
            )
    return f'<div class="error-msg">⚠️ 이미지 파일 찾을 수 없음: {fname}</div>'


MATCH_COLUMN_COLORS = [
    {"bg": "#EDE7F6", "border": "#B39DDB"},  # 보라
    {"bg": "#FCE4EC", "border": "#F48FB1"},  # 분홍
    {"bg": "#E3F2FD", "border": "#90CAF9"},  # 파랑
    {"bg": "#FFF8E1", "border": "#FFD54F"},  # 노랑
]

MATCH_ROW_CONNECTORS = ["•", "•", "•"]


def render_match_table(
    raw_val: Any,
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """매칭 중첩리스트를 인덱스 기반 매칭 테이블로 렌더링

    지원 구조:
      - [[left], [right]]           — 2열 매칭
      - [[col1], [col2], [col3]]    — 3열 매칭
      - [[col1], [col2], [col3], [col4]] — 4열 매칭
    각 내부 리스트의 같은 인덱스끼리 매칭됨을 시각적으로 표현한다.
    """
    try:
        data = raw_val
        if isinstance(raw_val, str):
            clean_val = raw_val.replace("\n", "\\n").replace("\r", "").strip()
            if clean_val.startswith("[["):
                data = json.loads(clean_val)

        if (
            isinstance(data, list)
            and len(data) >= 2
            and all(isinstance(col, list) for col in data)
        ):
            num_cols = len(data)
            max_rows = max(len(col) for col in data)
            col_width_pct = int(90 / num_cols)
            connector_width_pct = int(10 / max(num_cols - 1, 1))

            html = '<table class="match-table-multi">'

            # 헤더
            html += "<thead><tr>"
            for ci in range(num_cols):
                colors = MATCH_COLUMN_COLORS[ci % len(MATCH_COLUMN_COLORS)]
                html += (
                    f'<th class="match-col-header" '
                    f'style="width:{col_width_pct}%;background:{colors["bg"]};">'
                    f"그룹 {ci + 1}</th>"
                )
                if ci < num_cols - 1:
                    html += (
                        f'<th class="match-connector-header" '
                        f'style="width:{connector_width_pct}%;"></th>'
                    )
            html += "</tr></thead>"

            # 본문
            html += "<tbody>"
            for ri in range(max_rows):
                html += "<tr>"
                for ci in range(num_cols):
                    colors = MATCH_COLUMN_COLORS[ci % len(MATCH_COLUMN_COLORS)]
                    cell_val = data[ci][ri] if ri < len(data[ci]) else ""
                    cell_html = process_content_with_tags(
                        str(cell_val), content_meta, comparison, gcs_image_base_url
                    )
                    html += (
                        f'<td class="match-cell" '
                        f'style="background:{colors["bg"]};'
                        f'border-color:{colors["border"]};">'
                        f'<span class="match-idx">({ri + 1})</span>'
                        f"{cell_html}</td>"
                    )
                    if ci < num_cols - 1:
                        connector = MATCH_ROW_CONNECTORS[
                            ci % len(MATCH_ROW_CONNECTORS)
                        ]
                        html += (
                            f'<td class="match-connector">'
                            f'<span class="match-arrow">{connector} ─ {connector}</span>'
                            f"</td>"
                        )
                html += "</tr>"

            html += "</tbody></table>"
            return html
    except Exception:
        pass

    return format_text_with_newlines(raw_val)


def render_meta_inline(
    meta_key: str,
    meta_value: Any,
    comparison: dict[str, str],
    content_meta: dict[str, Any] | None = None,
    gcs_image_base_url: str | None = None,
) -> str:
    """content_meta의 태그를 인라인으로 렌더링

    file_name 키가 존재하면 이미지를 렌더링한다 (type 무관).
    """
    html = '<div class="meta-inline">'
    html += f'<div class="meta-key">🏷️ {escape_html(meta_key)}</div>'

    if isinstance(meta_value, dict):
        meta_type = meta_value.get("type", "")
        text_content = meta_value.get("text", "")
        title_content = meta_value.get("title", "")

        page_num = ""
        tag_props = meta_value.get("tag_properties")
        if isinstance(tag_props, dict):
            page_num = tag_props.get("page_num", "")

        file_name = meta_value.get("file_name", "")

        def _render_title_page() -> str:
            out = ""
            if title_content:
                out += (
                    '<div class="meta-title"><strong>Title:</strong> '
                    f"{format_text_with_newlines(title_content)}</div>"
                )
            if page_num:
                out += (
                    '<div class="meta-pagenum"><strong>Page:</strong> '
                    f"{escape_html(page_num)}</div>"
                )
            return out

        def _img() -> str:
            """file_name이 존재하면 이미지 렌더링"""
            if file_name:
                return _render_image_tag(file_name, comparison, gcs_image_base_url)
            return ""

        # ── 타입별 처리 ──
        if meta_type == "table":
            html += '<div class="meta-type-badge">📊 Table</div>'
            html += _render_title_page()
            html += _img()

            if text_content:
                if isinstance(text_content, str) and "<table" in text_content:
                    resolved = _resolve_tags_in_html(
                        text_content, content_meta, comparison, gcs_image_base_url,
                    )
                    html += f'<div class="meta-table-content">{resolved}</div>'
                elif isinstance(text_content, (list, str)) and "[[" in str(text_content):
                    html += render_match_table(
                        text_content, content_meta, comparison, gcs_image_base_url
                    )
                else:
                    html += (
                        f'<div class="meta-text">'
                        f"{format_text_with_newlines(text_content)}"
                        f"</div>"
                    )

        elif meta_type == "image":
            html += '<div class="meta-type-badge">🖼️ Image</div>'
            html += _render_title_page()
            html += _img()
            if text_content:
                html += (
                    f'<div class="meta-text"><strong>Text:</strong> '
                    f"{format_text_with_newlines(text_content)}</div>"
                )

        elif meta_type == "chart":
            html += '<div class="meta-type-badge">📈 Chart</div>'
            html += _render_title_page()
            html += _img()
            if text_content:
                html += (
                    f'<div class="meta-text"><strong>Text:</strong> '
                    f"{format_text_with_newlines(text_content)}</div>"
                )

        else:
            # 기타 타입 — file_name이 있으면 이미지 렌더링
            html += _render_title_page()
            html += _img()
            if text_content:
                if isinstance(text_content, (list, str)) and "[[" in str(text_content):
                    html += render_match_table(
                        text_content, content_meta, comparison, gcs_image_base_url
                    )
                else:
                    html += (
                        f'<div class="meta-text">'
                        f"{format_text_with_newlines(text_content)}"
                        f"</div>"
                    )

    html += "</div>"
    return html


def process_content_with_tags(
    content_text: Any,
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """content 내의 <태그>를 content_meta와 연결하여 렌더링

    수식($...$, $$...$$)은 먼저 플레이스홀더로 보존하여
    <...> 태그 매칭과 충돌하지 않도록 한다.
    """
    if not isinstance(content_text, str):
        return format_text_with_newlines(str(content_text))

    # Step 1: 수식 보존
    text, math_placeholders = protect_math_expressions(content_text)

    # Step 2: <태그> 분리 후 렌더링
    tag_pattern = r"(<[^>]+>)"
    parts = re.split(tag_pattern, text)
    result = ""

    for part in parts:
        if re.match(r"<[^>]+>", part):
            tag_name = part[1:-1]
            if content_meta and tag_name in content_meta:
                result += render_meta_inline(
                    tag_name, content_meta[tag_name], comparison,
                    content_meta, gcs_image_base_url,
                )
            else:
                result += f'<span class="tag-placeholder">{escape_html(part)}</span>'
        else:
            escaped = escape_html(part).replace("\n", NEWLINE_SYMBOL)
            result += f"<span>{escaped}</span>"

    # Step 3: 수식 복원
    result = restore_math_expressions(result, math_placeholders)

    return result


# ---------------------------------------------------------------------------
# 보기 / 선택지 렌더링
# ---------------------------------------------------------------------------
def render_bogi(
    value: Any,
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """'보기' 키: list → ㄱ,ㄴ,ㄷ 순번 / dict → 키-값 쌍"""
    if isinstance(value, list):
        html = ""
        for i, item in enumerate(value):
            label = KOREAN_LABELS[i] if i < len(KOREAN_LABELS) else str(i + 1)
            rendered = process_content_with_tags(
                str(item), content_meta, comparison, gcs_image_base_url
            )
            html += (
                f'<div class="bogi-item"><strong>{label}.</strong> '
                f"{rendered}</div>"
            )
        return html

    if isinstance(value, dict):
        html = ""
        for k, v in value.items():
            rendered = process_content_with_tags(
                str(v), content_meta, comparison, gcs_image_base_url
            )
            html += (
                f'<div class="bogi-item">'
                f"<strong>{escape_html(str(k))}.</strong> "
                f"{rendered}</div>"
            )
        return html

    return process_content_with_tags(
        str(value), content_meta, comparison, gcs_image_base_url
    )


# ---------------------------------------------------------------------------
# 아이템 렌더링 (단일 Row → HTML 카드)
# ---------------------------------------------------------------------------
def render_item_card(
    idx: int,
    item: dict[str, Any],
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """단일 JSONL 아이템을 HTML 카드로 렌더링

    gcs_image_base_url이 제공되면 이미지를 GCS 프록시를 통해 직접 서빙한다.
    """
    if "error" in item:
        return (
            '<div class="item">'
            '<div class="header" style="background:#f44336;">'
            f"No. {idx} - 파싱 오류</div>"
            f'<div class="error-msg">❌ {escape_html(str(item["error"]))}</div>'
            "</div>"
        )

    add_info = item.get("add_info", {}) or {}
    task_type = detect_task_type(add_info)
    if task_type == "task3":
        return render_task3_card(idx, item, comparison, gcs_image_base_url)
    if task_type == "task2":
        return render_task2_card(idx, item, comparison, gcs_image_base_url)

    meta = item.get("content_meta", {}) or {}

    # 유형 확인 (개념 여부)
    problem_type = ""
    is_concept = False
    problem_section = add_info.get("문제")
    if isinstance(problem_section, dict):
        problem_type = problem_section.get("유형", "")
        is_concept = problem_type == "개념"

    item_class = "item type-concept" if is_concept else "item"
    if is_concept:
        type_badge = '<span class="type-badge concept">📚 개념</span>'
    elif problem_type:
        type_badge = f'<span class="type-badge other">{escape_html(problem_type)}</span>'
    else:
        type_badge = ""

    html = f'<div class="{item_class}" data-row-idx="{idx - 1}">'
    html += (
        f'<div class="header">'
        f'<span class="header-left">No. {idx} | ID: {escape_html(item.get("data_id", ""))}{type_badge}</span>'
        f'<span class="inline-edit-status"></span>'
        f'<span class="header-actions">'
        f'<button class="btn-edit" onclick="startRowEdit(\'{escape_html(item.get("data_id", ""))}\', {idx - 1})">편집</button>'
        f"</span>"
        f"</div>"
    )

    # ── Content 섹션 ──
    content_html = ""
    content = item.get("content")
    if isinstance(content, dict):
        for ck, cv in content.items():
            rendered = process_content_with_tags(
                cv, meta, comparison, gcs_image_base_url
            )
            content_html += (
                f'<div class="content-row">'
                f'<strong>[{escape_html(ck)}]</strong> '
                f'<span class="editable-value" data-field="content.{escape_html(ck)}">{rendered}</span>'
                f'</div>'
            )
    elif isinstance(content, str):
        rendered = process_content_with_tags(
            content, meta, comparison, gcs_image_base_url
        )
        content_html += (
            f'<div class="content-row">'
            f'<strong>[content]</strong> '
            f'<span class="editable-value" data-field="content">{rendered}</span>'
            f'</div>'
        )
    elif isinstance(content, list):
        for i, c_item in enumerate(content):
            content_html += (
                f'<div class="content-row">'
                f'<strong>[content_{i}]</strong> '
                f'<span class="editable-value" data-field="content.{i}">{format_text_with_newlines(c_item)}</span>'
                f'</div>'
            )

    if is_concept and content_html:
        html += f'<div class="content-section">{content_html}</div>'
    else:
        html += content_html

    # ── 소스 메타 정보 ──
    source_file = add_info.get("source_file", "")
    page_num = add_info.get("page_num", "")
    html += (
        f'<div class="meta-info">'
        f'📄 Source: <span class="editable-value" data-field="add_info.source_file">{escape_html(source_file)}</span> | '
        f'📖 Page: <span class="editable-value" data-field="add_info.page_num">{escape_html(page_num)}</span>'
        f'</div>'
    )

    # ── 상세 정보 테이블 (book_meta / unit_meta / 문제 / 풀이) ──
    KEY_EDITABLE_SECTIONS = {"unit_meta", "문제", "풀이"}

    for section in ["book_meta", "unit_meta", "문제", "풀이"]:
        section_data = add_info.get(section)
        if section_data is None:
            continue

        if not isinstance(section_data, dict):
            html += (
                f'<div class="error-msg">'
                f"⚠️ {section} 섹션이 딕셔너리가 아님: {type(section_data).__name__}"
                f"</div>"
            )
            continue

        is_key_editable = section in KEY_EDITABLE_SECTIONS
        section_path = f"add_info.{section}"
        section_attr = f' data-section="{section_path}"' if is_key_editable else ""
        html += f'<div class="section-block"{section_attr}>'
        html += '<table class="info-table">'

        for k, v in section_data.items():
            colors = BG_COLORS.get(k, {"key": "#757575", "value": "#ffffff"})
            key_color = colors["key"]
            value_color = colors["value"]

            row_attr = f' data-key="{escape_html(k)}"' if is_key_editable else ""
            html += f'<tr{row_attr}><th style="background-color: {key_color};">'
            if is_key_editable:
                html += (
                    f'<span class="move-key-wrap" style="display:none;">'
                    f'<button class="btn-move-key" '
                    f"onclick=\"moveKeyUp('{section_path}', '{escape_html(k)}')\" "
                    f'title="위로 이동">▲</button>'
                    f'<button class="btn-move-key" '
                    f"onclick=\"moveKeyDown('{section_path}', '{escape_html(k)}')\" "
                    f'title="아래로 이동">▼</button>'
                    f"</span>"
                )
            html += escape_html(k)
            if is_key_editable:
                html += (
                    f'<button class="btn-delete-key" style="display:none;" '
                    f"onclick=\"deleteKeyFromSection('{section_path}', '{escape_html(k)}')\" "
                    f'title="키 삭제">×</button>'
                )
            html += "</th>"
            html += f'<td style="background-color: {value_color};" class="editable-value" data-field="{section_path}.{escape_html(k)}">'

            if k == "선택지" and isinstance(v, dict):
                for ok, ov in v.items():
                    rendered = process_content_with_tags(
                        ov, meta, comparison, gcs_image_base_url
                    )
                    html += f"<div><strong>{escape_html(ok)}</strong> {rendered}</div>"
            elif k == "보기":
                html += render_bogi(v, meta, comparison, gcs_image_base_url)
            elif k == "매칭항목" or (
                isinstance(v, (list, str)) and "[[" in str(v)
            ):
                html += render_match_table(
                    v, meta, comparison, gcs_image_base_url
                )
            else:
                html += process_content_with_tags(
                    v, meta, comparison, gcs_image_base_url
                )

            html += "</td></tr>"

        html += "</table>"
        if is_key_editable:
            html += (
                f'<button class="btn-add-key" style="display:none;" '
                f"onclick=\"addKeyToSection('{section_path}')\">"
                f"+ 키 추가</button>"
            )
        html += "</div>"

    html += "</div>"  # .item 닫기
    return html


def get_bg_colors() -> dict[str, dict[str, str]]:
    """키별 색상 맵 반환 (템플릿에서 참조용)"""
    return BG_COLORS


# ---------------------------------------------------------------------------
# Task 3 (논술 첨삭) 렌더링
# ---------------------------------------------------------------------------
def _render_list_items(
    items: list[dict[str, Any]],
    base_path: str,
    schema: dict[str, Any],
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
    field_styles: dict[str, str] | None = None,
) -> str:
    """List[Dict] 구조를 list-item-card 컴포넌트로 렌더링

    base_path 예시: "add_info.교사첨삭.세부첨삭"
    각 항목의 필드 경로: "{base_path}.{i}.{field_key}"
    field_styles: 필드별 CSS 클래스 매핑 (None이면 TASK3_FIELD_STYLES 사용)
    """
    if field_styles is None:
        field_styles = TASK3_FIELD_STYLES

    theme = schema["theme"]
    label = schema["label"]
    fields: list[str] = schema["fields"]
    section_key = base_path.replace("add_info.", "")

    html = ""
    for i, item_data in enumerate(items):
        if not isinstance(item_data, dict):
            continue

        html += f'<div class="list-item-card theme-{theme}" data-list-idx="{i}">'
        html += '<div class="list-item-header">'
        html += f'<span class="list-item-idx">{i + 1}</span>'
        html += f'{escape_html(label)} [{i}]'

        if "평가유형" in fields:
            eval_type = item_data.get("평가유형", "")
            sentiment = item_data.get("유형", "")
            if eval_type:
                html += (
                    f'<span class="eval-type-badge {escape_html(str(eval_type))}"'
                    f' style="margin-left:auto">{escape_html(str(eval_type))}</span>'
                )
            if sentiment:
                html += f'<span class="sentiment-badge {escape_html(str(sentiment))}">{escape_html(str(sentiment))}</span>'
        elif "첨삭본문이미지" in fields:
            edit_type = item_data.get("유형", "")
            if edit_type:
                html += (
                    f'<span class="detail-edit-type"'
                    f' style="margin-left:auto">{escape_html(str(edit_type))}</span>'
                )
        elif "기준" in fields and "결과" in fields:
            result = item_data.get("결과", "")
            if result:
                html += (
                    f'<span class="eval-grade {escape_html(str(result))}"'
                    f' style="margin-left:auto">{escape_html(str(result))}</span>'
                )
        elif "항목" in fields and "유형" in fields and "기준" not in fields:
            sentiment = item_data.get("유형", "")
            if sentiment:
                html += (
                    f'<span class="sentiment-badge {escape_html(str(sentiment))}"'
                    f' style="margin-left:auto">{escape_html(str(sentiment))}</span>'
                )

        html += (
            f'<button class="btn-list-delete"'
            f" onclick=\"deleteListItem('{escape_html(base_path)}', {i})\">×</button>"
            f'<button class="btn-list-move"'
            f" onclick=\"moveListItem('{escape_html(base_path)}', {i}, 'up')\">▲</button>"
            f'<button class="btn-list-move"'
            f" onclick=\"moveListItem('{escape_html(base_path)}', {i}, 'down')\">▼</button>"
        )
        html += "</div>"

        html += '<table class="info-table">'
        for field_key in fields:
            val = item_data.get(field_key)
            field_path = f"{base_path}.{i}.{field_key}"
            style_key = f"{section_key}.{field_key}"
            style_class = field_styles.get(style_key, "")

            td_cls = f"editable-value {style_class}" if style_class else "editable-value"
            html += f'<tr><th>{escape_html(field_key)}</th>'
            html += f'<td class="{td_cls}" data-field="{escape_html(field_path)}" style="white-space:pre-wrap;">'

            if val is None or val == "":
                html += '<span style="color:#999">(없음)</span>'
            elif isinstance(val, (list, dict)):
                html += format_text_with_newlines(
                    json.dumps(val, ensure_ascii=False)
                )
            else:
                html += process_content_with_tags(
                    str(val), content_meta, comparison, gcs_image_base_url
                )

            html += "</td></tr>"
        html += "</table>"
        html += "</div>"

    return html


def render_task3_card(
    idx: int,
    item: dict[str, Any],
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """Task 3 (논술 첨삭) JSONL 아이템을 HTML 카드로 렌더링

    add_info에 '논제' 키가 존재하면 이 함수가 호출된다.
    """
    meta = item.get("content_meta", {}) or {}
    add_info = item.get("add_info", {}) or {}
    pair_idx = add_info.get("pairIDX", "")

    html = f'<div class="item" data-row-idx="{idx - 1}">'

    pair_badge = (
        f'<span class="pair-idx-badge">{escape_html(pair_idx)}</span>'
        if pair_idx
        else ""
    )
    html += (
        f'<div class="header">'
        f'<span class="header-left">'
        f"No. {idx} | ID: {escape_html(item.get('data_id', ''))}{pair_badge}"
        f"</span>"
        f'<span class="inline-edit-status"></span>'
        f'<span class="header-actions">'
        f"<button class=\"btn-edit\" onclick=\"startRowEdit("
        f"'{escape_html(item.get('data_id', ''))}', {idx - 1})\">편집</button>"
        f"</span></div>"
    )

    # ── 1. 논제 섹션 ──────────────────────────────────────────────
    topic = add_info.get("논제", {})
    if isinstance(topic, dict):
        munhang = topic.get("문항", {}) if isinstance(topic.get("문항"), dict) else {}
        questions = munhang.get("질문", []) if isinstance(munhang.get("질문"), list) else []

        html += '<div class="section-block section-논제" data-section="add_info.논제">'
        html += (
            '<div class="section-header" onclick="toggleSection(this)">'
            '<span class="section-icon">📋</span> 논제'
            f'<span class="section-count">({len(questions)}개 질문)</span>'
            '<span class="toggle-icon">▼</span></div>'
        )
        html += '<div class="section-body">'

        html += '<table class="info-table">'
        for key in ("회차", "출처", "제시문"):
            val = topic.get(key, "")
            field_path = f"add_info.논제.{key}"
            rendered = (
                process_content_with_tags(str(val), meta, comparison, gcs_image_base_url)
                if val
                else '<span style="color:#999">(없음)</span>'
            )
            html += (
                f'<tr><th>{escape_html(key)}</th>'
                f'<td class="editable-value" data-field="{escape_html(field_path)}"'
                f' style="white-space:pre-wrap;">{rendered}</td></tr>'
            )
        html += "</table>"

        jimun = munhang.get("지문")
        jimun_rendered = (
            process_content_with_tags(str(jimun), meta, comparison, gcs_image_base_url)
            if jimun
            else '<span style="color:#999">(없음)</span>'
        )
        html += '<table class="info-table" style="margin-top:8px">'
        html += (
            '<tr><th>문항.지문</th>'
            '<td class="editable-value" data-field="add_info.논제.문항.지문"'
            f' style="white-space:pre-wrap;">{jimun_rendered}</td></tr>'
        )
        html += "</table>"

        if questions:
            schema = TASK3_LIST_SCHEMAS["논제.문항.질문"]
            html += '<div class="sub-section">'
            html += (
                '<div class="sub-section-header">📝 문항 · 질문'
                f' <span style="color:#999;font-weight:400;margin-left:4px">({len(questions)}개)</span></div>'
            )
            html += f'<div class="sub-section-body" data-list-path="add_info.논제.문항.질문">'
            html += _render_list_items(
                questions, "add_info.논제.문항.질문", schema, meta, comparison, gcs_image_base_url
            )
            html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.논제.문항.질문')\">+ 질문 추가</button>"
            html += "</div></div>"

        html += "</div></div>"

    # ── 2. 논제분석 섹션 ──────────────────────────────────────────
    analysis = add_info.get("논제분석", {})
    if isinstance(analysis, dict):
        html += '<div class="section-block section-논제분석" data-section="add_info.논제분석">'
        html += (
            '<div class="section-header" onclick="toggleSection(this)">'
            '<span class="section-icon">🔬</span> 논제분석'
            '<span class="toggle-icon">▼</span></div>'
        )
        html += '<div class="section-body">'

        haeseol = analysis.get("해설")
        rendered_h = (
            process_content_with_tags(str(haeseol), meta, comparison, gcs_image_base_url)
            if haeseol
            else '<span style="color:#999">(없음)</span>'
        )
        html += '<table class="info-table">'
        html += (
            '<tr><th>해설</th>'
            '<td class="editable-value" data-field="add_info.논제분석.해설"'
            f' style="white-space:pre-wrap;">{rendered_h}</td></tr>'
        )
        html += "</table>"

        example = analysis.get("예시답안", {})
        if isinstance(example, dict):
            ex_questions = example.get("문항_질문", [])
            if isinstance(ex_questions, list) and ex_questions:
                schema = TASK3_LIST_SCHEMAS["논제분석.예시답안.문항_질문"]
                html += '<div class="sub-section">'
                html += (
                    '<div class="sub-section-header">✏️ 예시답안 · 문항_질문'
                    f' <span style="color:#999;font-weight:400;margin-left:4px">({len(ex_questions)}개)</span></div>'
                )
                html += '<div class="sub-section-body" data-list-path="add_info.논제분석.예시답안.문항_질문">'
                html += _render_list_items(
                    ex_questions, "add_info.논제분석.예시답안.문항_질문",
                    schema, meta, comparison, gcs_image_base_url,
                )
                html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.논제분석.예시답안.문항_질문')\">+ 예시답안 추가</button>"
                html += "</div></div>"

        criteria = analysis.get("평가기준", {})
        if isinstance(criteria, dict):
            cr_questions = criteria.get("문항_질문", [])
            if isinstance(cr_questions, list) and cr_questions:
                schema = TASK3_LIST_SCHEMAS["논제분석.평가기준.문항_질문"]
                html += '<div class="sub-section">'
                html += (
                    '<div class="sub-section-header">📊 평가기준 · 문항_질문'
                    f' <span style="color:#999;font-weight:400;margin-left:4px">({len(cr_questions)}개)</span></div>'
                )
                html += '<div class="sub-section-body" data-list-path="add_info.논제분석.평가기준.문항_질문">'
                html += _render_list_items(
                    cr_questions, "add_info.논제분석.평가기준.문항_질문",
                    schema, meta, comparison, gcs_image_base_url,
                )
                html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.논제분석.평가기준.문항_질문')\">+ 평가기준 추가</button>"
                html += "</div></div>"

        html += "</div></div>"

    # ── 3. 학생답안 섹션 ──────────────────────────────────────────
    student = add_info.get("학생답안", {})
    if isinstance(student, dict):
        st_questions = student.get("문항_질문", []) if isinstance(student.get("문항_질문"), list) else []

        html += '<div class="section-block section-학생답안" data-section="add_info.학생답안">'
        html += (
            '<div class="section-header" onclick="toggleSection(this)">'
            '<span class="section-icon">✍️</span> 학생답안'
            f'<span class="section-count">({len(st_questions)}개 답안)</span>'
            '<span class="toggle-icon">▼</span></div>'
        )
        html += '<div class="section-body">'

        if st_questions:
            schema = TASK3_LIST_SCHEMAS["학생답안.문항_질문"]
            html += '<div class="sub-section">'
            html += (
                '<div class="sub-section-header">📝 문항_질문'
                f' <span style="color:#999;font-weight:400;margin-left:4px">({len(st_questions)}개)</span></div>'
            )
            html += '<div class="sub-section-body" data-list-path="add_info.학생답안.문항_질문">'
            html += _render_list_items(
                st_questions, "add_info.학생답안.문항_질문",
                schema, meta, comparison, gcs_image_base_url,
            )
            html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.학생답안.문항_질문')\">+ 학생답안 추가</button>"
            html += "</div></div>"

        html += "</div></div>"

    # ── 4. 교사첨삭 섹션 ──────────────────────────────────────────
    teacher = add_info.get("교사첨삭", {})
    if isinstance(teacher, dict):
        evals = teacher.get("평가", []) if isinstance(teacher.get("평가"), list) else []
        details = teacher.get("세부첨삭", []) if isinstance(teacher.get("세부첨삭"), list) else []

        html += '<div class="section-block section-교사첨삭" data-section="add_info.교사첨삭">'
        html += (
            '<div class="section-header" onclick="toggleSection(this)">'
            '<span class="section-icon">🖊️</span> 교사첨삭'
            f'<span class="section-count">(평가 {len(evals)}건 · 세부첨삭 {len(details)}건)</span>'
            '<span class="toggle-icon">▼</span></div>'
        )
        html += '<div class="section-body">'

        if evals:
            schema = TASK3_LIST_SCHEMAS["교사첨삭.평가"]
            html += '<div class="sub-section">'
            html += (
                '<div class="sub-section-header">📋 평가'
                f' <span style="color:#999;font-weight:400;margin-left:4px">({len(evals)}개)</span></div>'
            )
            html += '<div class="sub-section-body" data-list-path="add_info.교사첨삭.평가">'
            html += _render_list_items(
                evals, "add_info.교사첨삭.평가",
                schema, meta, comparison, gcs_image_base_url,
            )
            html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.교사첨삭.평가')\">+ 평가 추가</button>"
            html += "</div></div>"

        if details:
            schema = TASK3_LIST_SCHEMAS["교사첨삭.세부첨삭"]
            html += '<div class="sub-section">'
            html += (
                '<div class="sub-section-header">✏️ 세부첨삭'
                f' <span style="color:#999;font-weight:400;margin-left:4px">({len(details)}개)</span></div>'
            )
            html += '<div class="sub-section-body" data-list-path="add_info.교사첨삭.세부첨삭">'
            html += _render_list_items(
                details, "add_info.교사첨삭.세부첨삭",
                schema, meta, comparison, gcs_image_base_url,
            )
            html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.교사첨삭.세부첨삭')\">+ 세부첨삭 추가</button>"
            html += "</div></div>"

        html += "</div></div>"

    # ── 5. 출처 정보 섹션 ─────────────────────────────────────────
    source_file = add_info.get("source_file", "")
    data_source = item.get("data_source", "")
    cat_main = item.get("category_main", "")
    cat_sub = item.get("category_sub", "")
    raw_dtype = item.get("data_type", "")
    data_type = ", ".join(raw_dtype) if isinstance(raw_dtype, list) else str(raw_dtype)
    collected_date = item.get("collected_date", "")

    html += '<div class="section-block section-source">'
    html += (
        '<div class="section-header">'
        '<span class="section-icon">📁</span> 출처 정보</div>'
    )
    html += '<div class="section-body"><table class="info-table">'
    html += (
        '<tr><th>source_file</th>'
        '<td class="editable-value" data-field="add_info.source_file"'
        f' style="font-family:monospace;font-size:12px;">{escape_html(source_file)}</td></tr>'
    )
    html += f'<tr><th>data_source</th><td>{escape_html(data_source)}</td></tr>'
    html += f'<tr><th>category</th><td>{escape_html(cat_main)} · {escape_html(cat_sub)}</td></tr>'
    html += f'<tr><th>data_type</th><td>{escape_html(data_type)}</td></tr>'
    html += f'<tr><th>collected_date</th><td>{escape_html(collected_date)}</td></tr>'
    html += "</table></div></div>"

    html += "</div>"
    return html


# ---------------------------------------------------------------------------
# Task 2 (인문논술 첨삭) 렌더링
# ---------------------------------------------------------------------------
def render_task2_card(
    idx: int,
    item: dict[str, Any],
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """Task 2 (인문논술 첨삭) JSONL 아이템을 HTML 카드로 렌더링

    add_info에 '논제' 키가 존재하지만 '논제.문항'이 없으면 이 함수가 호출된다.
    """
    meta = item.get("content_meta", {}) or {}
    add_info = item.get("add_info", {}) or {}
    pair_idx = add_info.get("pairIDX", "")

    html = f'<div class="item" data-row-idx="{idx - 1}">'

    pair_badge = (
        f'<span class="pair-idx-badge">{escape_html(pair_idx)}</span>'
        if pair_idx
        else ""
    )
    html += (
        f'<div class="header">'
        f'<span class="header-left">'
        f"No. {idx} | ID: {escape_html(item.get('data_id', ''))}{pair_badge}"
        f"</span>"
        f'<span class="inline-edit-status"></span>'
        f'<span class="header-actions">'
        f"<button class=\"btn-edit\" onclick=\"startRowEdit("
        f"'{escape_html(item.get('data_id', ''))}', {idx - 1})\">편집</button>"
        f"</span></div>"
    )

    # ── 1. 논제 섹션 ──────────────────────────────────────────────
    topic = add_info.get("논제", {})
    if isinstance(topic, dict):
        topic_fields = ["회차", "출처", "글자수", "제목", "본문"]
        html += '<div class="section-block section-논제" data-section="add_info.논제">'
        html += (
            '<div class="section-header" onclick="toggleSection(this)">'
            '<span class="section-icon">📋</span> 논제'
            f'<span class="section-count">({len(topic_fields)}개 필드)</span>'
            '<span class="toggle-icon">▼</span></div>'
        )
        html += '<div class="section-body"><table class="info-table">'
        for key in topic_fields:
            val = topic.get(key, "")
            field_path = f"add_info.논제.{key}"
            rendered = (
                process_content_with_tags(
                    str(val), meta, comparison, gcs_image_base_url
                )
                if val
                else '<span style="color:#999">(없음)</span>'
            )
            html += (
                f'<tr><th>{escape_html(key)}</th>'
                f'<td class="editable-value" data-field="{escape_html(field_path)}"'
                f' style="white-space:pre-wrap;">{rendered}</td></tr>'
            )
        html += "</table></div></div>"

    # ── 2. 논제분석 섹션 ──────────────────────────────────────────
    analysis = add_info.get("논제분석", {})
    if isinstance(analysis, dict):
        html += '<div class="section-block section-논제분석" data-section="add_info.논제분석">'
        html += (
            '<div class="section-header" onclick="toggleSection(this)">'
            '<span class="section-icon">🔍</span> 논제분석'
            '<span class="section-count">(2개 필드)</span>'
            '<span class="toggle-icon">▼</span></div>'
        )
        html += '<div class="section-body"><table class="info-table">'

        for key in ("해설", "예시답안"):
            val = analysis.get(key, "")
            field_path = f"add_info.논제분석.{key}"
            rendered = (
                process_content_with_tags(
                    str(val), meta, comparison, gcs_image_base_url
                )
                if val
                else '<span style="color:#999">(없음)</span>'
            )
            html += (
                f'<tr><th>{escape_html(key)}</th>'
                f'<td class="editable-value" data-field="{escape_html(field_path)}"'
                f' style="white-space:pre-wrap;">{rendered}</td></tr>'
            )

        html += "</table></div></div>"

    # ── 3. 학생답안 섹션 ──────────────────────────────────────────
    student_answer = add_info.get("학생답안", "")
    html += '<div class="section-block section-학생답안" data-section="add_info.학생답안">'
    html += (
        '<div class="section-header" onclick="toggleSection(this)">'
        '<span class="section-icon">✍️</span> 학생답안'
        '<span class="section-count">(단일 텍스트)</span>'
        '<span class="toggle-icon">▼</span></div>'
    )
    html += '<div class="section-body"><table class="info-table">'

    if isinstance(student_answer, str):
        rendered_sa = (
            process_content_with_tags(
                student_answer, meta, comparison, gcs_image_base_url
            )
            if student_answer
            else '<span style="color:#999">(없음)</span>'
        )
        html += (
            '<tr><th style="background:#fff3e0;color:#e65100;">답안</th>'
            '<td class="editable-value" data-field="add_info.학생답안"'
            f' style="white-space:pre-wrap;background:#fffbf0;">{rendered_sa}</td></tr>'
        )
    elif isinstance(student_answer, dict):
        for sk, sv in student_answer.items():
            field_path = f"add_info.학생답안.{sk}"
            rendered_sv = (
                process_content_with_tags(
                    str(sv), meta, comparison, gcs_image_base_url
                )
                if sv
                else '<span style="color:#999">(없음)</span>'
            )
            html += (
                f'<tr><th>{escape_html(sk)}</th>'
                f'<td class="editable-value" data-field="{escape_html(field_path)}"'
                f' style="white-space:pre-wrap;">{rendered_sv}</td></tr>'
            )

    html += "</table></div></div>"

    # ── 4. 교사첨삭 섹션 ──────────────────────────────────────────
    teacher = add_info.get("교사첨삭", {})
    if isinstance(teacher, dict):
        chong_eval = (
            teacher.get("총평가", [])
            if isinstance(teacher.get("총평가"), list)
            else []
        )
        detail_eval = (
            teacher.get("세부평가", [])
            if isinstance(teacher.get("세부평가"), list)
            else []
        )
        detail_edit = (
            teacher.get("세부첨삭", [])
            if isinstance(teacher.get("세부첨삭"), list)
            else []
        )

        total_cnt = len(chong_eval) + len(detail_eval) + len(detail_edit)
        html += '<div class="section-block section-교사첨삭" data-section="add_info.교사첨삭">'
        html += (
            '<div class="section-header" onclick="toggleSection(this)">'
            '<span class="section-icon">📝</span> 교사첨삭'
            f'<span class="section-count">'
            f'(총평가 {len(chong_eval)}건 · 세부평가 {len(detail_eval)}건 · 세부첨삭 {len(detail_edit)}건)'
            f'</span>'
            '<span class="toggle-icon">▼</span></div>'
        )
        html += '<div class="section-body">'

        if chong_eval:
            schema = TASK2_LIST_SCHEMAS["교사첨삭.총평가"]
            html += '<div class="sub-section">'
            html += (
                '<div class="sub-section-header">📊 총평가'
                f' <span style="color:#999;font-weight:400;margin-left:4px">'
                f'({len(chong_eval)}개)</span></div>'
            )
            html += (
                '<div class="sub-section-body"'
                ' data-list-path="add_info.교사첨삭.총평가">'
            )
            html += _render_list_items(
                chong_eval,
                "add_info.교사첨삭.총평가",
                schema,
                meta,
                comparison,
                gcs_image_base_url,
                field_styles=TASK2_FIELD_STYLES,
            )
            html += (
                "<button class=\"btn-list-add\""
                " onclick=\"addListItem('add_info.교사첨삭.총평가')\">"
                "+ 총평가 추가</button>"
            )
            html += "</div></div>"

        if detail_eval:
            schema = TASK2_LIST_SCHEMAS["교사첨삭.세부평가"]
            html += '<div class="sub-section">'
            html += (
                '<div class="sub-section-header">📈 세부평가'
                f' <span style="color:#999;font-weight:400;margin-left:4px">'
                f'({len(detail_eval)}개)</span></div>'
            )
            html += (
                '<div class="sub-section-body"'
                ' data-list-path="add_info.교사첨삭.세부평가">'
            )
            html += _render_list_items(
                detail_eval,
                "add_info.교사첨삭.세부평가",
                schema,
                meta,
                comparison,
                gcs_image_base_url,
                field_styles=TASK2_FIELD_STYLES,
            )
            html += (
                "<button class=\"btn-list-add\""
                " onclick=\"addListItem('add_info.교사첨삭.세부평가')\">"
                "+ 세부평가 추가</button>"
            )
            html += "</div></div>"

        if detail_edit:
            schema = TASK2_LIST_SCHEMAS["교사첨삭.세부첨삭"]
            html += '<div class="sub-section">'
            html += (
                '<div class="sub-section-header">✍️ 세부첨삭'
                f' <span style="color:#999;font-weight:400;margin-left:4px">'
                f'({len(detail_edit)}개)</span></div>'
            )
            html += (
                '<div class="sub-section-body"'
                ' data-list-path="add_info.교사첨삭.세부첨삭">'
            )
            html += _render_list_items(
                detail_edit,
                "add_info.교사첨삭.세부첨삭",
                schema,
                meta,
                comparison,
                gcs_image_base_url,
                field_styles=TASK2_FIELD_STYLES,
            )
            html += (
                "<button class=\"btn-list-add\""
                " onclick=\"addListItem('add_info.교사첨삭.세부첨삭')\">"
                "+ 세부첨삭 추가</button>"
            )
            html += "</div></div>"

        html += "</div></div>"

    # ── 5. 출처 정보 섹션 ─────────────────────────────────────────
    source_file = add_info.get("source_file", "")
    data_source = item.get("data_source", "")
    cat_main = item.get("category_main", "")
    cat_sub = item.get("category_sub", "")
    raw_dtype = item.get("data_type", "")
    data_type = ", ".join(raw_dtype) if isinstance(raw_dtype, list) else str(raw_dtype)
    collected_date = item.get("collected_date", "")

    html += '<div class="section-block section-source">'
    html += (
        '<div class="section-header">'
        '<span class="section-icon">📁</span> 출처 정보</div>'
    )
    html += '<div class="section-body"><table class="info-table">'
    html += (
        '<tr><th>pairIDX</th>'
        f'<td style="font-family:monospace;font-size:12px;">'
        f'{escape_html(pair_idx)}</td></tr>'
    )
    html += (
        '<tr><th>source_file</th>'
        '<td class="editable-value" data-field="add_info.source_file"'
        f' style="font-family:monospace;font-size:12px;">'
        f'{escape_html(source_file)}</td></tr>'
    )
    html += f'<tr><th>data_source</th><td>{escape_html(data_source)}</td></tr>'
    html += (
        f'<tr><th>category</th>'
        f'<td>{escape_html(cat_main)} · {escape_html(cat_sub)}</td></tr>'
    )
    html += f'<tr><th>data_type</th><td>{escape_html(data_type)}</td></tr>'
    html += f'<tr><th>collected_date</th><td>{escape_html(collected_date)}</td></tr>'
    html += "</table></div></div>"

    html += "</div>"
    return html
