"""render 패키지 공개 API

외부에서는 이 모듈만 import 하면 된다.
기존 render_service.py의 모든 공개 함수/상수를 동일한 이름으로 re-export 한다.
"""
from __future__ import annotations

from typing import Any

from .base import (
    escape_html,
    format_text_with_newlines,
    get_image_base64,
    get_images_from_folder,
    protect_math_expressions,
    restore_math_expressions,
)
from .components import (
    process_content_with_tags,
    render_bogi,
    render_list_items,
    render_match_table,
    render_meta_inline,
)
from .task1 import (
    BG_COLORS,
    detect_task_type,
    get_bg_colors,
    render_task1_card,
)
from .task2 import (
    TASK2_FIELD_STYLES,
    TASK2_LIST_SCHEMAS,
    render_task2_card,
)
from .task3 import (
    TASK3_FIELD_STYLES,
    TASK3_LIST_SCHEMAS,
    render_task3_card,
)


def render_item_card(
    idx: int,
    item: dict[str, Any],
    comparison: dict[str, str],
    gcs_image_base_url: str | None = None,
) -> str:
    """단일 JSONL 아이템을 HTML 카드로 렌더링 (dispatch 함수)"""
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
    return render_task1_card(idx, item, comparison, gcs_image_base_url)


__all__ = [
    "render_item_card",
    "render_task1_card",
    "render_task2_card",
    "render_task3_card",
    "detect_task_type",
    "get_bg_colors",
    "get_images_from_folder",
    "get_image_base64",
    "escape_html",
    "format_text_with_newlines",
    "protect_math_expressions",
    "restore_math_expressions",
    "process_content_with_tags",
    "render_bogi",
    "render_list_items",
    "render_match_table",
    "render_meta_inline",
    "BG_COLORS",
    "TASK2_LIST_SCHEMAS",
    "TASK2_FIELD_STYLES",
    "TASK3_LIST_SCHEMAS",
    "TASK3_FIELD_STYLES",
]
