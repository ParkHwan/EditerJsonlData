"""구조화 로깅 설정 (Phase 7: 환경별 동적 로그 레벨)

- 로컬 개발: DEBUG 레벨, 읽기 쉬운 포맷
- 운영 환경: INFO 레벨, JSON 포맷 옵션
"""

import json
import logging
import sys
from datetime import datetime, timezone

from app.core.config import settings

_LOG_LEVEL = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)


class JSONFormatter(logging.Formatter):
    """운영 환경용 JSON 로그 포맷터"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


logger = logging.getLogger("editer-jsonl")
logger.setLevel(_LOG_LEVEL)

if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(_LOG_LEVEL)

    if settings.is_production:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    logger.addHandler(handler)
