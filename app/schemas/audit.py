"""감사 로그 스키마"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class AuditLog(BaseModel):
    """감사 로그 엔트리"""

    id: str
    timestamp: datetime
    user_id: str
    display_name: str
    action: Literal[
        "login",
        "logout",
        "view",
        "edit_start",
        "edit_save",
        "edit_cancel",
        "download",
        "rollback",
    ]
    file_id: str | None = None
    row_idx: int | None = None
    ip_address: str = ""
    user_agent: str = ""
    changes: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
