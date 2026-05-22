"""render 패키지 공용 컴포넌트

content_meta 태그 인라인 렌더링, 매칭 테이블, 보기/선택지,
리스트 아이템 카드 등 여러 task 렌더러에서 재사용되는 컴포넌트.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .base import (
    escape_html,
    escape_math_in_html,
    format_text_with_newlines,
    protect_math_expressions,
    render_image_tag,
    resolve_tags_in_html,
    restore_math_expressions,
    NEWLINE_SYMBOL,
)

# 메타 태그 참조 패턴(예: <tag_P001_01>) 전용.
# 과거에는 ``<[^>]+>`` 처럼 모든 HTML 토큰을 잡아 일반 텍스트의 ``<x`` 같은
# 부등호를 태그로 오해석하던 사고가 있었다(BUG-51 / LaTeX 깨짐 분석).
META_TAG_PATTERN = r"(<tag_[A-Za-z0-9_]+>)"

# ---------------------------------------------------------------------------
# 매칭 테이블 상수
# ---------------------------------------------------------------------------
MATCH_COLUMN_COLORS = [
    {"bg": "#EDE7F6", "border": "#B39DDB"},
    {"bg": "#FCE4EC", "border": "#F48FB1"},
    {"bg": "#E3F2FD", "border": "#90CAF9"},
    {"bg": "#FFF8E1", "border": "#FFD54F"},
]

MATCH_ROW_CONNECTORS = ["•", "•", "•"]

KOREAN_LABELS = [
    "ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ", "ㅂ", "ㅅ", "ㅇ",
    "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]


# ---------------------------------------------------------------------------
# 매칭 테이블 렌더링
# ---------------------------------------------------------------------------
def render_match_table(
    raw_val: Any,
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """매칭 중첩리스트를 인덱스 기반 매칭 테이블로 렌더링"""
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


# ---------------------------------------------------------------------------
# content_meta 인라인 렌더링
# ---------------------------------------------------------------------------
def render_meta_inline(
    meta_key: str,
    meta_value: Any,
    comparison: dict[str, str],
    content_meta: dict[str, Any] | None = None,
    gcs_image_base_url: str | None = None,
) -> str:
    """content_meta의 태그를 인라인으로 렌더링"""
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
            if file_name:
                return render_image_tag(file_name, comparison, gcs_image_base_url)
            return ""

        if meta_type == "table":
            html += '<div class="meta-type-badge">📊 Table</div>'
            html += _render_title_page()
            html += _img()
            if text_content:
                if isinstance(text_content, str) and "<table" in text_content:
                    resolved = resolve_tags_in_html(
                        text_content, content_meta, comparison, gcs_image_base_url,
                    )
                    # 표 셀 내부의 수식($...$ 등)이 raw HTML로 들어와도
                    # 브라우저가 부등호를 태그로 오인하지 않도록 entity화한다.
                    resolved = escape_math_in_html(resolved)
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


# ---------------------------------------------------------------------------
# content 태그 처리
# ---------------------------------------------------------------------------
def process_content_with_tags(
    content_text: Any,
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """content 내의 <태그>를 content_meta와 연결하여 렌더링"""
    if not isinstance(content_text, str):
        return format_text_with_newlines(str(content_text))

    text, math_placeholders = protect_math_expressions(content_text)

    parts = re.split(META_TAG_PATTERN, text)
    result = ""

    for part in parts:
        if re.match(META_TAG_PATTERN, part):
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
# 리스트 아이템 카드 렌더링 (Task2/Task3 공통)
# ---------------------------------------------------------------------------
def render_list_items(
    items: list[dict[str, Any]],
    base_path: str,
    schema: dict[str, Any],
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
    field_styles: dict[str, str] | None = None,
) -> str:
    """List[Dict] 구조를 list-item-card 컴포넌트로 렌더링

    field_styles가 None이면 빈 dict로 처리한다.
    """
    if field_styles is None:
        field_styles = {}

    theme = schema["theme"]
    label = schema["label"]
    fields: list[str] = schema["fields"]
    section_key = base_path.replace("add_info.", "")

    html = ""
    for i, item_data in enumerate(items):
        if not isinstance(item_data, dict):
            continue

        html += f'<div class="list-item-card theme-{theme}" data-list-index="{i}" data-list-path-ref="{escape_html(base_path)}">'
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
            f'<button class="btn-list-add-key"'
            f" onclick=\"addListItemKey(this, '{escape_html(base_path)}')\">+ 키</button>"
            f'<button class="btn-list-delete"'
            f" onclick=\"deleteListItem('{escape_html(base_path)}', {i}, this)\">×</button>"
            f'<button class="btn-list-move"'
            f" onclick=\"moveListItem('{escape_html(base_path)}', {i}, 'up', this)\">▲</button>"
            f'<button class="btn-list-move"'
            f" onclick=\"moveListItem('{escape_html(base_path)}', {i}, 'down', this)\">▼</button>"
        )
        html += "</div>"

        html += '<table class="info-table">'
        for field_key in fields:
            val = item_data.get(field_key)
            field_path = f"{base_path}.{i}.{field_key}"
            style_key = f"{section_key}.{field_key}"
            style_class = field_styles.get(style_key, "")

            td_cls = f"editable-value {style_class}" if style_class else "editable-value"
            html += f'<tr data-key="{escape_html(field_key)}"><th>{escape_html(field_key)}'
            html += (
                f'<button class="btn-delete-key" style="display:none;"'
                f" onclick=\"deleteListItemKey(this)\" title=\"키 삭제\">×</button>"
            )
            html += "</th>"
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
