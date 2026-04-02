"""Task 3 (수리논술 첨삭) 카드 렌더링

add_info에 '논제.문항'이 존재하는 수리논술 아이템을 HTML 카드로 변환한다.
"""
from __future__ import annotations

from typing import Any

from .base import escape_html
from .components import process_content_with_tags, render_list_items

# ---------------------------------------------------------------------------
# Task 3 전용 상수
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


def render_task3_card(
    idx: int,
    item: dict[str, Any],
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """Task 3 (논술 첨삭) JSONL 아이템을 HTML 카드로 렌더링"""
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
            html += render_list_items(
                questions, "add_info.논제.문항.질문", schema, meta, comparison,
                gcs_image_base_url, field_styles=TASK3_FIELD_STYLES,
            )
            html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.논제.문항.질문')\">+ 질문 추가</button>"
            html += "</div></div>"

        html += "</div></div>"

    # ── 2. 논제분석 섹션 ──
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
                html += render_list_items(
                    ex_questions, "add_info.논제분석.예시답안.문항_질문",
                    schema, meta, comparison, gcs_image_base_url,
                    field_styles=TASK3_FIELD_STYLES,
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
                html += render_list_items(
                    cr_questions, "add_info.논제분석.평가기준.문항_질문",
                    schema, meta, comparison, gcs_image_base_url,
                    field_styles=TASK3_FIELD_STYLES,
                )
                html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.논제분석.평가기준.문항_질문')\">+ 평가기준 추가</button>"
                html += "</div></div>"

        html += "</div></div>"

    # ── 3. 학생답안 섹션 ──
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
            html += render_list_items(
                st_questions, "add_info.학생답안.문항_질문",
                schema, meta, comparison, gcs_image_base_url,
                field_styles=TASK3_FIELD_STYLES,
            )
            html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.학생답안.문항_질문')\">+ 학생답안 추가</button>"
            html += "</div></div>"

        html += "</div></div>"

    # ── 4. 교사첨삭 섹션 ──
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
            html += render_list_items(
                evals, "add_info.교사첨삭.평가",
                schema, meta, comparison, gcs_image_base_url,
                field_styles=TASK3_FIELD_STYLES,
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
            html += render_list_items(
                details, "add_info.교사첨삭.세부첨삭",
                schema, meta, comparison, gcs_image_base_url,
                field_styles=TASK3_FIELD_STYLES,
            )
            html += "<button class=\"btn-list-add\" onclick=\"addListItem('add_info.교사첨삭.세부첨삭')\">+ 세부첨삭 추가</button>"
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
        f' style="font-family:monospace;font-size:12px;">{escape_html(source_file)}</td></tr>'
    )
    html += f'<tr><th>data_source</th><td>{escape_html(data_source)}</td></tr>'
    html += f'<tr><th>category</th><td>{escape_html(cat_main)} · {escape_html(cat_sub)}</td></tr>'
    html += f'<tr><th>data_type</th><td>{escape_html(data_type)}</td></tr>'
    html += f'<tr><th>collected_date</th><td>{escape_html(collected_date)}</td></tr>'
    html += "</table></div></div>"

    html += "</div>"
    return html
