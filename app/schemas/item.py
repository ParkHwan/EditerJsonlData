"""데이터 스키마"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ItemBase(BaseModel):
    """JSONL Row 기본 스키마 (Optimistic Locking용 version 포함)

    content는 dict 형태(구조화 데이터) 또는 str 형태(플레인 텍스트) 모두 허용.
    """

    content: dict[str, Any] | str = Field(..., description="실제 JSON 데이터")
    version: int = Field(1, description="Optimistic Locking 버전")
    modified_at: datetime | None = None
    modified_by: str | None = None

    model_config = {"extra": "ignore"}


class ItemUpdate(ItemBase):
    """Row 업데이트 요청"""

    pass


class ItemResponse(ItemBase):
    """Row 응답"""

    row_idx: int
    file_id: str


class LockResponse(BaseModel):
    """Lock 응답"""

    success: bool
    message: str
    remaining_seconds: int
