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


def render_match_table(
    raw_val: Any,
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """매칭 리스트([[left], [right]])를 테이블로 렌더링"""
    try:
        data = raw_val
        if isinstance(raw_val, str):
            clean_val = raw_val.replace("\n", "\\n").replace("\r", "").strip()
            if clean_val.startswith("[["):
                data = json.loads(clean_val)

        if isinstance(data, list) and len(data) >= 2:
            left, right = data[0], data[1]
            html = '<table class="match-table">'

            for i in range(max(len(left), len(right))):
                l_txt = left[i] if i < len(left) else ""
                r_txt = right[i] if i < len(right) else ""

                l_html = process_content_with_tags(
                    l_txt, content_meta, comparison, gcs_image_base_url
                )
                r_html = process_content_with_tags(
                    r_txt, content_meta, comparison, gcs_image_base_url
                )

                html += f"<tr><td>{l_html}</td><td>{r_html}</td></tr>"

            html += "</table>"
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
                    html += f'<div class="meta-table-content">{text_content}</div>'
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

    meta = item.get("content_meta", {}) or {}
    add_info = item.get("add_info", {}) or {}

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
            elif isinstance(v, (list, str)) and "[[" in str(v):
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
