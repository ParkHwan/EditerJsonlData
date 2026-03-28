"""애플리케이션 설정 (Phase 7: 환경 분리)

ENVIRONMENT 환경 변수로 로컬/운영 동작을 제어한다.
- local: 개발 모드 (DEBUG, 단일 워커, Redis Standalone)
- production: 운영 모드 (INFO 로깅, 멀티 워커, Redis Sentinel, HTTPS)
"""

import multiprocessing
import os

from pydantic_settings import BaseSettings

_env_file = os.getenv("ENV_FILE", ".env")


class Settings(BaseSettings):
    PROJECT_NAME: str = "Editer Jsonl"
    API_V1_STR: str = "/api/v1"

    # Environment (Phase 7)
    ENVIRONMENT: str = "local"
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    WORKERS: int = 1
    ALLOWED_HOSTS: str = "*"
    GUNICORN_TIMEOUT: int = 120

    # Security
    SECRET_KEY: str = "changethis-in-production-secret-key-12345"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 2  # 48 hours

    # Session
    SESSION_TTL_HOURS: int = 48
    SESSION_COOKIE_NAME: str = "session_id"
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: str = "lax"

    # 이메일 도메인 제한
    ALLOWED_EMAIL_DOMAIN: str = "crowdworks.kr"

    # Redis (Sentinel)
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_SERVICE_NAME: str = "mymaster"

    # Filesystem
    DATA_DIR: str = "data"
    BACKUP_DIR: str = "data/backups"

    # Rate Limiting
    RATE_LIMIT_DEFAULT: str = "300/minute"
    RATE_LIMIT_WRITE: str = "30/minute"

    # Audit Logging (Phase 3)
    AUDIT_DIR: str = "data/audit"
    AUDIT_RETENTION_DAYS: int = 90

    # Draft / Auto-save (Phase 3)
    DRAFT_TTL_SECONDS: int = 1800
    DRAFT_AUTO_SAVE_INTERVAL: int = 30

    # Health Check (Phase 4)
    DISK_USAGE_WARNING_PCT: float = 90.0

    # GCS (Phase 6)
    GCS_PROJECT_ID: str = "crowdworks-platform"
    GCS_BUCKET_NAME: str = "de-download-service-storage"
    GCS_PREFIX: str = "manual"
    GCS_LOCATION: str = "asia-northeast1"
    GCS_CREDENTIALS_PATH: str = ""

    # DuckDB (Phase 10)
    DUCKDB_PATH: str = "data/editor.duckdb"

    # Sync API Key (스크립트에서 세션 없이 동기화 호출용)
    SYNC_API_KEY: str = ""

    # TASK별 GCS prefix 매핑 (Phase 9)
    GCS_TASKS: dict[str, dict[str, str]] = {
        "task1": {"name": "교재", "prefix": "manual/PROJ-14768/TASK1"},    
        "task2": {"name": "인문논술", "prefix": "manual/PROJ-14768/TASK2"},
        "task3": {"name": "수리논술", "prefix": "manual/PROJ-14768/TASK3"},
    }

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def effective_workers(self) -> int:
        """운영 환경에서 WORKERS=0이면 CPU 코어 기반 자동 결정"""
        if self.WORKERS > 0:
            return self.WORKERS
        return multiprocessing.cpu_count() * 2 + 1

    class Config:
        case_sensitive = True
        env_file = _env_file


settings = Settings()
