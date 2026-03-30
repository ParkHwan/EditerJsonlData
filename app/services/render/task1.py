"""Task 1 (교재) 카드 렌더링

add_info에 '논제' 키가 없는 일반 교재 아이템을 HTML 카드로 변환한다.
"""
from __future__ import annotations

from typing import Any

from .base import escape_html, format_text_with_newlines
from .components import (
    process_content_with_tags,
    render_bogi,
    render_match_table,
)

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


def get_bg_colors() -> dict[str, dict[str, str]]:
    """키별 색상 맵 반환 (템플릿에서 참조용)"""
    return BG_COLORS


def render_task1_card(
    idx: int,
    item: dict[str, Any],
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """Task 1 (교재) JSONL 아이템을 HTML 카드로 렌더링"""
    add_info = item.get("add_info", {}) or {}
    meta = item.get("content_meta", {}) or {}

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
        f'<span class="header-actions"></span>'
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

    html += "</div>"
    return html
