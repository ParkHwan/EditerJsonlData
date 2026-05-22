"""render 패키지 기본 유틸리티

HTML 이스케이프, 수식 보존, 이미지 처리, 태그 해석 등
모든 task 렌더러가 공유하는 저수준 함수를 제공한다.
"""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

NEWLINE_SYMBOL = '<span class="nl-symbol" title="줄바꿈 (\\n)">↵</span><br>'


# ---------------------------------------------------------------------------
# HTML 텍스트 유틸리티
# ---------------------------------------------------------------------------
def escape_html(text: Any) -> str:
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
    r"""수식 delimiter를 플레이스홀더로 치환하여 HTML escape 시 보존.

    지원 delimiter (우선순위 순):
    - ``\[...\]`` : display math
    - ``\(...\)`` : inline math
    - ``$$...$$`` : display math
    - ``$...$``   : inline math (single-line)

    더 긴 delimiter부터 매칭해야 ``$$x$$``가 ``$`` + ``$x$`` + ``$``로
    분해되는 사고를 막을 수 있다.
    """
    placeholders: dict[str, str] = {}
    counter = [0]

    def _replace(m: re.Match[str]) -> str:
        key = f"__MATH_PH_{counter[0]}__"
        placeholders[key] = m.group(0)
        counter[0] += 1
        return key

    text = re.sub(r"\\\[(.+?)\\\]", _replace, text, flags=re.DOTALL)
    text = re.sub(r"\\\((.+?)\\\)", _replace, text, flags=re.DOTALL)
    text = re.sub(r"\$\$(.+?)\$\$", _replace, text, flags=re.DOTALL)
    text = re.sub(r"\$(.+?)\$", _replace, text)
    return text, placeholders


def restore_math_expressions(html: str, placeholders: dict[str, str]) -> str:
    r"""플레이스홀더를 원래 수식으로 복원하되 HTML-safe로 처리한다.

    수식 내부의 ``<``, ``>`` 같은 토큰이 HTML 파서에 태그로 오해석되어
    DOM이 깨지는 것을 막기 위해 ``escape_html``을 적용해 entity로 치환한다.
    KaTeX ``auto-render``는 텍스트 노드를 스캔하며, entity는 브라우저 파싱
    단계에서 다시 ``<`` 문자로 디코딩되므로 KaTeX 입력에는 영향이 없다.
    백슬래시는 escape 대상이 아니므로 ``\\frac`` 등 TeX 명령어는 그대로
    보존된다.
    """
    for key, original in placeholders.items():
        html = html.replace(key, escape_html(original))
    return html


def escape_math_in_html(html_text: str) -> str:
    r"""이미 HTML 문자열인 텍스트 안의 수식 ``$...$`` / ``\(..\)`` 등만
    HTML-safe하게 변환한다.

    ``content_meta`` 의 ``text_content``가 이미 ``<table>...</table>`` 같은
    HTML 단편으로 들어오는 경우(`render_meta_inline`의 table 분기)
    ``process_content_with_tags`` 경로를 거치지 않으므로 별도 보호가 필요하다.
    """
    if not isinstance(html_text, str) or not html_text:
        return html_text

    def _esc(m: re.Match[str]) -> str:
        return escape_html(m.group(0))

    html_text = re.sub(r"\\\[(.+?)\\\]", _esc, html_text, flags=re.DOTALL)
    html_text = re.sub(r"\\\((.+?)\\\)", _esc, html_text, flags=re.DOTALL)
    html_text = re.sub(r"\$\$(.+?)\$\$", _esc, html_text, flags=re.DOTALL)
    html_text = re.sub(r"\$(.+?)\$", _esc, html_text)
    return html_text


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
    try:
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()
    except Exception:
        return None


def get_images_from_folder(
    images_folder: str | Path,
    jsonl_filename: str,
) -> dict[str, str]:
    """JSONL 파일명에 매칭되는 이미지 폴더 검색"""
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
# 이미지 태그 렌더링
# ---------------------------------------------------------------------------
def render_image_tag(
    fname_val: Any,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """이미지 파일명을 img 태그로 렌더링"""
    if not fname_val:
        return ""
    fname_str = str(fname_val)

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


def resolve_tags_in_html(
    html_text: str,
    content_meta: dict[str, Any] | None,
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """HTML 문자열 내의 <tag_...> 참조를 content_meta 이미지로 치환"""
    if not content_meta:
        return html_text

    def _replace_tag(match: re.Match[str]) -> str:
        tag_name = match.group(1)
        if tag_name in content_meta:
            meta_val = content_meta[tag_name]
            if isinstance(meta_val, dict) and meta_val.get("file_name"):
                return render_image_tag(
                    meta_val["file_name"], comparison, gcs_image_base_url,
                )
        return match.group(0)

    return re.sub(r"<(tag_[A-Za-z0-9_]+)>", _replace_tag, html_text)
