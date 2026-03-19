"""DuckDB 연결 관리자 (Phase 10: Embedded Lakehouse)

Thread-safe + Process-safe Singleton 패턴.
- Level 1 (threading.Lock): 프로세스 내 스레드 간 쓰기 직렬화
- Level 2 (fcntl.flock):    Gunicorn 멀티 워커(프로세스) 간 쓰기 직렬화
- 읽기: cursor()로 동시 읽기 (Lock 불필요)
- 종료: CHECKPOINT → close (WAL 플러시)
"""

from __future__ import annotations

import fcntl
import threading
from pathlib import Path
from typing import Any

import duckdb

from app.core.config import settings
from app.core.logger import logger

_DDL_SCHEMA = """
-- ENUM Types
CREATE TYPE IF NOT EXISTS file_status AS ENUM (
    'registered', 'editing', 'updated', 'completed'
);
CREATE TYPE IF NOT EXISTS event_type AS ENUM (
    'session_start', 'row_save', 'gcs_update',
    'session_discard', 'review_complete', 'bulk_register'
);

-- Sequences
CREATE SEQUENCE IF NOT EXISTS seq_file_registry START 1;
CREATE SEQUENCE IF NOT EXISTS seq_edit_events START 1;

-- Tables
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR PRIMARY KEY,
    display_name    VARCHAR NOT NULL,
    email           VARCHAR NOT NULL,
    password_hash   VARCHAR NOT NULL DEFAULT '',
    is_admin        BOOLEAN NOT NULL DEFAULT false,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    first_login_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_login_at   TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    login_count     INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS file_registry (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_file_registry'),
    gcs_path            VARCHAR NOT NULL UNIQUE,
    file_name           VARCHAR NOT NULL,
    task_id             VARCHAR NOT NULL,
    date_folder         VARCHAR NOT NULL,
    created_by          VARCHAR NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_modified_by    VARCHAR,
    last_modified_at    TIMESTAMPTZ,
    completed_by        VARCHAR,
    completed_at        TIMESTAMPTZ,
    status              file_status NOT NULL DEFAULT 'registered',
    content_stats       STRUCT(
                            total_rows INTEGER,
                            has_images BOOLEAN,
                            data_id_range VARCHAR
                        ),
    update_count        INTEGER DEFAULT 0,
    total_edit_sessions INTEGER DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS edit_events (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_edit_events'),
    file_id         INTEGER NOT NULL,
    gcs_path        VARCHAR NOT NULL,
    user_id         VARCHAR NOT NULL,
    display_name    VARCHAR NOT NULL,
    event_type      event_type NOT NULL,
    summary         VARCHAR,
    rows_affected   INTEGER DEFAULT 0,
    modified_fields VARCHAR[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS registry_sync (
    task_id         VARCHAR NOT NULL,
    date_folder     VARCHAR NOT NULL,
    last_synced_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_synced_date VARCHAR NOT NULL,
    file_count      INTEGER DEFAULT 0,
    PRIMARY KEY (task_id, date_folder)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_file_task      ON file_registry (task_id);
CREATE INDEX IF NOT EXISTS idx_file_status    ON file_registry (status);
CREATE INDEX IF NOT EXISTS idx_file_date      ON file_registry (date_folder);
CREATE INDEX IF NOT EXISTS idx_file_modified  ON file_registry (last_modified_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_task_date ON file_registry (task_id, date_folder);
CREATE INDEX IF NOT EXISTS idx_event_file     ON edit_events (file_id);
CREATE INDEX IF NOT EXISTS idx_event_user     ON edit_events (user_id);
CREATE INDEX IF NOT EXISTS idx_event_type     ON edit_events (event_type);
CREATE INDEX IF NOT EXISTS idx_event_time     ON edit_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_file_time ON edit_events (file_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_task      ON registry_sync (task_id);

-- Download History (Phase 12)
CREATE SEQUENCE IF NOT EXISTS seq_download_history START 1;
CREATE TABLE IF NOT EXISTS download_history (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_download_history'),
    user_id         VARCHAR NOT NULL,
    display_name    VARCHAR NOT NULL,
    task_id         VARCHAR NOT NULL,
    date_folder     VARCHAR NOT NULL,
    file_types      VARCHAR NOT NULL DEFAULT 'all',
    file_count      INTEGER NOT NULL DEFAULT 0,
    total_size      BIGINT NOT NULL DEFAULT 0,
    zip_size        BIGINT NOT NULL DEFAULT 0,
    zip_filename    VARCHAR NOT NULL,
    status          VARCHAR NOT NULL DEFAULT 'completed',
    error_message   VARCHAR,
    duration_ms     INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);
CREATE INDEX IF NOT EXISTS idx_dl_user     ON download_history (user_id);
CREATE INDEX IF NOT EXISTS idx_dl_task     ON download_history (task_id);
CREATE INDEX IF NOT EXISTS idx_dl_time     ON download_history (created_at DESC);
"""

_DDL_VIEWS = """
CREATE OR REPLACE VIEW task_progress AS
SELECT
    task_id,
    COUNT(*)                                           AS total_files,
    COUNT(*) FILTER (WHERE status = 'completed')       AS completed_files,
    COUNT(*) FILTER (WHERE status = 'editing')         AS editing_files,
    COUNT(*) FILTER (WHERE status = 'updated')         AS updated_files,
    COUNT(*) FILTER (WHERE status = 'registered')      AS registered_files,
    ROUND(
        COUNT(*) FILTER (WHERE status = 'completed') * 100.0
        / NULLIF(COUNT(*), 0), 1
    )                                                  AS completion_rate,
    SUM(content_stats.total_rows)                      AS total_rows_all,
    SUM(update_count)                                  AS total_updates_all
FROM file_registry
GROUP BY task_id
ORDER BY task_id;

CREATE OR REPLACE VIEW user_activity AS
SELECT
    u.user_id,
    u.display_name,
    u.login_count,
    COUNT(e.id) FILTER (WHERE e.event_type = 'row_save')       AS total_saves,
    COUNT(e.id) FILTER (WHERE e.event_type = 'gcs_update')     AS total_updates,
    COUNT(e.id) FILTER (WHERE e.event_type = 'review_complete') AS total_reviews,
    COALESCE(SUM(e.rows_affected), 0)                          AS total_rows_affected,
    COUNT(DISTINCT e.gcs_path)                                  AS unique_files_edited,
    MIN(e.created_at)                                           AS first_activity,
    MAX(e.created_at)                                           AS last_activity
FROM users u
LEFT JOIN edit_events e ON u.user_id = e.user_id
GROUP BY u.user_id, u.display_name, u.login_count
ORDER BY total_saves DESC;

CREATE OR REPLACE VIEW daily_activity AS
SELECT
    CAST(e.created_at AS DATE)                               AS activity_date,
    COUNT(DISTINCT e.user_id)                                AS active_users,
    COUNT(*) FILTER (WHERE e.event_type = 'row_save')       AS saves,
    COUNT(*) FILTER (WHERE e.event_type = 'gcs_update')     AS updates,
    COUNT(*) FILTER (WHERE e.event_type = 'review_complete') AS reviews,
    COALESCE(SUM(e.rows_affected), 0)                       AS rows_affected
FROM edit_events e
GROUP BY CAST(e.created_at AS DATE)
ORDER BY activity_date DESC;

CREATE OR REPLACE VIEW file_edit_history AS
SELECT
    f.gcs_path,
    f.file_name,
    f.task_id,
    f.status,
    f.created_by,
    f.created_at,
    f.last_modified_by,
    f.last_modified_at,
    f.completed_by,
    f.completed_at,
    f.update_count,
    e.event_type,
    e.user_id            AS event_user,
    e.display_name       AS event_user_name,
    e.summary            AS event_summary,
    e.modified_fields    AS event_modified_fields,
    e.created_at         AS event_time
FROM file_registry f
LEFT JOIN edit_events e ON f.id = e.file_id
ORDER BY f.gcs_path, e.created_at DESC;

CREATE OR REPLACE VIEW hourly_heatmap AS
SELECT
    EXTRACT(DOW FROM created_at)   AS day_of_week,
    EXTRACT(HOUR FROM created_at)  AS hour_of_day,
    COUNT(*)                       AS event_count,
    COUNT(DISTINCT user_id)        AS unique_users
FROM edit_events
GROUP BY EXTRACT(DOW FROM created_at), EXTRACT(HOUR FROM created_at)
ORDER BY day_of_week, hour_of_day;
"""


class DuckDBClient:
    """Thread-safe + Process-safe DuckDB Singleton"""

    _conn: duckdb.DuckDBPyConnection | None = None
    _thread_lock: threading.Lock = threading.Lock()
    _lock_file_path: str = ""

    @classmethod
    def get_connection(cls) -> duckdb.DuckDBPyConnection:
        if cls._conn is None:
            db_path = Path(settings.DUCKDB_PATH)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            cls._lock_file_path = str(db_path) + ".lock"
            cls._conn = duckdb.connect(str(db_path))
            cls._init_schema()
            logger.info("DuckDB connected: %s", db_path)
        return cls._conn

    @classmethod
    def _init_schema(cls) -> None:
        conn = cls._conn
        if conn is None:
            return
        for statement in _DDL_SCHEMA.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except duckdb.CatalogException:
                    pass

        cls._migrate_users_table(conn)

        for statement in _DDL_VIEWS.strip().split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception as e:
                    logger.warning("DuckDB view creation warning: %s", e)
        logger.info("DuckDB schema initialized")

    @classmethod
    def _migrate_users_table(cls, conn: duckdb.DuckDBPyConnection) -> None:
        """기존 users 테이블에 누락된 컬럼이 있으면 ALTER TABLE로 추가."""
        migrations = [
            ("email", "ALTER TABLE users ADD COLUMN email VARCHAR DEFAULT ''"),
            ("password_hash", "ALTER TABLE users ADD COLUMN password_hash VARCHAR DEFAULT ''"),
            ("is_admin", "ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT false"),
            ("is_active", "ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT true"),
        ]
        for col_name, ddl in migrations:
            try:
                conn.execute(f"SELECT {col_name} FROM users LIMIT 0")
            except Exception:
                try:
                    conn.execute(ddl)
                    logger.info("Migrated users table: added column '%s'", col_name)
                except Exception as e:
                    logger.warning("Migration failed for column '%s': %s", col_name, e)

    @classmethod
    def get_read_cursor(cls) -> duckdb.DuckDBPyConnection:
        return cls.get_connection().cursor()

    @classmethod
    def execute_write(
        cls, sql: str, params: list[Any] | None = None
    ) -> duckdb.DuckDBPyConnection:
        with cls._thread_lock:
            fd = open(cls._lock_file_path, "w")  # noqa: SIM115
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
                conn = cls.get_connection()
                return conn.execute(sql, params or [])
            finally:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                fd.close()

    @classmethod
    def execute_write_many(cls, statements: list[tuple[str, list[Any]]]) -> None:
        with cls._thread_lock:
            fd = open(cls._lock_file_path, "w")  # noqa: SIM115
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
                conn = cls.get_connection()
                conn.execute("BEGIN TRANSACTION")
                try:
                    for sql, params in statements:
                        conn.execute(sql, params)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                fd.close()

    @classmethod
    def close(cls) -> None:
        if cls._conn:
            try:
                cls._conn.execute("CHECKPOINT")
            except Exception:
                pass
            cls._conn.close()
            cls._conn = None
            logger.info("DuckDB closed (CHECKPOINT completed)")
