"""Task 2 (인문논술 첨삭) 카드 렌더링

add_info에 '논제' 키가 존재하지만 '논제.문항'이 없는
인문논술 아이템을 HTML 카드로 변환한다.
"""
from __future__ import annotations

from typing import Any

from .base import escape_html
from .components import process_content_with_tags, render_list_items

# ---------------------------------------------------------------------------
# Task 2 전용 상수
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


def render_task2_card(
    idx: int,
    item: dict[str, Any],
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """Task 2 (인문논술 첨삭) JSONL 아이템을 HTML 카드로 렌더링"""
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
        f'<span class="header-actions"></span>'
        f"</div>"
    )

    # ── 1. 논제 섹션 ──
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

    # ── 2. 논제분석 섹션 ──
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

    # ── 3. 학생답안 섹션 ──
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

    # ── 4. 교사첨삭 섹션 ──
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
            html += render_list_items(
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
            html += render_list_items(
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
            html += render_list_items(
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

    # ── 5. 출처 정보 섹션 ──
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
