"""DuckDB 메타데이터 서비스 (Phase 10)

file_registry, edit_events, users 테이블에 대한 CRUD 메서드.
모든 쓰기 작업은 DuckDBClient.execute_write_many()를 통해
트랜잭션으로 묶여 ACID가 보장된다.

FastAPI async 컨텍스트에서 asyncio.to_thread()로 동기 호출을 래핑.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.logger import logger
from app.db.duckdb_client import DuckDBClient


def _extract_task_id(gcs_path: str) -> str:
    """GCS 경로에서 task_id를 추출.
    예: manual/PROJ-14768/TASK1/20260313/file.jsonl → task1
    """
    for tid, info in settings.GCS_TASKS.items():
        if gcs_path.startswith(info["prefix"]):
            return tid
    return "unknown"


def _extract_date_folder(gcs_path: str) -> str:
    """GCS 경로에서 YYYYMMDD 날짜 폴더를 추출."""
    match = re.search(r"/(\d{8})/", gcs_path)
    return match.group(1) if match else ""


class MetadataService:
    """DuckDB 기반 파일 메타데이터 / 이벤트 서비스"""

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    def _upsert_user_sync(self, user_id: str, display_name: str) -> None:
        DuckDBClient.execute_write_many([
            (
                """
                INSERT INTO users (user_id, display_name, email, first_login_at, last_login_at, login_count)
                VALUES (?, ?, '', now(), now(), 1)
                ON CONFLICT (user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    last_login_at = now(),
                    login_count = users.login_count + 1
                """,
                [user_id, display_name],
            ),
        ])

    async def upsert_user(self, user_id: str, display_name: str) -> None:
        await asyncio.to_thread(self._upsert_user_sync, user_id, display_name)

    def _register_user_sync(
        self,
        user_id: str,
        display_name: str,
        email: str,
        password_hash: str,
        is_admin: bool = False,
    ) -> None:
        """관리자가 신규 사용자 등록 (INSERT or UPDATE)"""
        DuckDBClient.execute_write_many([
            (
                """
                INSERT INTO users (user_id, display_name, email, password_hash, is_admin, is_active,
                                   first_login_at, last_login_at, login_count)
                VALUES (?, ?, ?, ?, ?, true, now(), now(), 0)
                ON CONFLICT (user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    email = excluded.email,
                    password_hash = excluded.password_hash,
                    is_admin = excluded.is_admin,
                    is_active = true
                """,
                [user_id, display_name, email, password_hash, is_admin],
            ),
        ])

    async def register_user(
        self,
        user_id: str,
        display_name: str,
        email: str,
        password_hash: str,
        is_admin: bool = False,
    ) -> None:
        await asyncio.to_thread(
            self._register_user_sync, user_id, display_name, email, password_hash, is_admin
        )

    def _get_user_by_email_sync(self, email: str) -> dict[str, Any] | None:
        """이메일로 사용자 조회"""
        cursor = DuckDBClient.get_read_cursor()
        row = cursor.execute(
            """
            SELECT user_id, display_name, email, password_hash, is_admin, is_active, login_count
            FROM users WHERE email = ?
            """,
            [email],
        ).fetchone()
        cursor.close()
        if not row:
            return None
        return {
            "user_id": row[0],
            "display_name": row[1],
            "email": row[2],
            "password_hash": row[3],
            "is_admin": row[4],
            "is_active": row[5],
            "login_count": row[6],
        }

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_user_by_email_sync, email)

    def _get_display_name_sync(self, user_id: str) -> str | None:
        cursor = DuckDBClient.get_read_cursor()
        row = cursor.execute(
            "SELECT display_name FROM users WHERE user_id = ?",
            [user_id],
        ).fetchone()
        cursor.close()
        return row[0] if row else None

    async def get_display_name(self, user_id: str) -> str | None:
        """user_id로 display_name 조회."""
        return await asyncio.to_thread(
            self._get_display_name_sync, user_id
        )

    def _increment_login_count_sync(self, user_id: str) -> None:
        """로그인 횟수 증가 + 마지막 로그인 시각 갱신"""
        DuckDBClient.execute_write_many([
            (
                "UPDATE users SET login_count = login_count + 1, last_login_at = now() WHERE user_id = ?",
                [user_id],
            ),
        ])

    async def increment_login_count(self, user_id: str) -> None:
        await asyncio.to_thread(self._increment_login_count_sync, user_id)

    def _list_users_sync(self) -> list[dict[str, Any]]:
        """전체 사용자 목록"""
        cursor = DuckDBClient.get_read_cursor()
        rows = cursor.execute(
            """
            SELECT user_id, display_name, email, is_admin, is_active, login_count,
                   first_login_at, last_login_at
            FROM users ORDER BY display_name
            """
        ).fetchall()
        cursor.close()
        return [
            {
                "user_id": r[0],
                "display_name": r[1],
                "email": r[2],
                "is_admin": r[3],
                "is_active": r[4],
                "login_count": r[5],
                "first_login_at": str(r[6]) if r[6] else "",
                "last_login_at": str(r[7]) if r[7] else "",
            }
            for r in rows
        ]

    async def list_users(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_users_sync)

    def _update_user_active_sync(self, user_id: str, is_active: bool) -> None:
        """사용자 활성/비활성 토글"""
        DuckDBClient.execute_write_many([
            ("UPDATE users SET is_active = ? WHERE user_id = ?", [is_active, user_id]),
        ])

    async def update_user_active(self, user_id: str, is_active: bool) -> None:
        await asyncio.to_thread(self._update_user_active_sync, user_id, is_active)

    def _update_user_password_sync(self, user_id: str, password_hash: str) -> None:
        """비밀번호 변경"""
        DuckDBClient.execute_write_many([
            ("UPDATE users SET password_hash = ? WHERE user_id = ?", [password_hash, user_id]),
        ])

    async def update_user_password(self, user_id: str, password_hash: str) -> None:
        await asyncio.to_thread(self._update_user_password_sync, user_id, password_hash)

    def _delete_user_sync(self, user_id: str) -> None:
        """사용자 삭제"""
        DuckDBClient.execute_write_many([
            ("DELETE FROM users WHERE user_id = ?", [user_id]),
        ])

    async def delete_user(self, user_id: str) -> None:
        await asyncio.to_thread(self._delete_user_sync, user_id)

    # ------------------------------------------------------------------
    # File Registry
    # ------------------------------------------------------------------
    def _get_or_create_file_sync(
        self,
        gcs_path: str,
        user_id: str,
        total_rows: int = 0,
    ) -> int:
        """file_registry에 파일이 없으면 INSERT, 있으면 id 반환."""
        cursor = DuckDBClient.get_read_cursor()
        result = cursor.execute(
            "SELECT id FROM file_registry WHERE gcs_path = ?", [gcs_path]
        ).fetchone()
        cursor.close()

        if result:
            return int(result[0])

        file_name = gcs_path.rsplit("/", 1)[-1] if "/" in gcs_path else gcs_path
        task_id = _extract_task_id(gcs_path)
        date_folder = _extract_date_folder(gcs_path)

        row = DuckDBClient.execute_write(
            """
            INSERT INTO file_registry (
                gcs_path, file_name, task_id, date_folder,
                created_by, status,
                content_stats, update_count, total_edit_sessions
            ) VALUES (
                ?, ?, ?, ?,
                ?, 'registered',
                {'total_rows': ?, 'has_images': false, 'data_id_range': ''},
                0, 0
            )
            RETURNING id
            """,
            [gcs_path, file_name, task_id, date_folder, user_id, total_rows],
        ).fetchone()

        return int(row[0]) if row else 0

    async def get_or_create_file(
        self, gcs_path: str, user_id: str, total_rows: int = 0
    ) -> int:
        return await asyncio.to_thread(
            self._get_or_create_file_sync, gcs_path, user_id, total_rows
        )

    def _update_file_status_sync(
        self,
        gcs_path: str,
        *,
        status: str | None = None,
        modified_by: str | None = None,
        completed_by: str | None = None,
        increment_update_count: bool = False,
        increment_edit_sessions: bool = False,
        total_rows: int | None = None,
    ) -> None:
        parts: list[str] = ["updated_at = now()"]
        params: list[Any] = []

        if status:
            parts.append("status = ?::file_status")
            params.append(status)
        if modified_by:
            parts.append("last_modified_by = ?")
            params.append(modified_by)
            parts.append("last_modified_at = now()")
        if completed_by:
            parts.append("completed_by = ?")
            params.append(completed_by)
            parts.append("completed_at = now()")
        if increment_update_count:
            parts.append("update_count = update_count + 1")
        if increment_edit_sessions:
            parts.append("total_edit_sessions = total_edit_sessions + 1")
        if total_rows is not None:
            parts.append(
                "content_stats = struct_pack("
                "total_rows := ?, "
                "has_images := COALESCE(content_stats.has_images, false), "
                "data_id_range := COALESCE(content_stats.data_id_range, '')"
                ")"
            )
            params.append(total_rows)

        params.append(gcs_path)
        sql = f"UPDATE file_registry SET {', '.join(parts)} WHERE gcs_path = ?"
        DuckDBClient.execute_write(sql, params)

    async def update_file_status(
        self,
        gcs_path: str,
        **kwargs: Any,
    ) -> None:
        await asyncio.to_thread(
            self._update_file_status_sync, gcs_path, **kwargs
        )

    # ------------------------------------------------------------------
    # Edit Events
    # ------------------------------------------------------------------
    def _record_event_sync(
        self,
        file_id: int,
        gcs_path: str,
        user_id: str,
        display_name: str,
        event_type: str,
        summary: str = "",
        rows_affected: int = 0,
        modified_fields: list[str] | None = None,
    ) -> None:
        fields_param = modified_fields if modified_fields else []
        DuckDBClient.execute_write(
            """
            INSERT INTO edit_events (
                file_id, gcs_path, user_id, display_name,
                event_type, summary, rows_affected, modified_fields
            ) VALUES (?, ?, ?, ?, ?::event_type, ?, ?, ?)
            """,
            [
                file_id, gcs_path, user_id, display_name,
                event_type, summary, rows_affected, fields_param,
            ],
        )

    async def record_event(
        self,
        file_id: int,
        gcs_path: str,
        user_id: str,
        display_name: str,
        event_type: str,
        summary: str = "",
        rows_affected: int = 0,
        modified_fields: list[str] | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._record_event_sync,
            file_id, gcs_path, user_id, display_name,
            event_type, summary, rows_affected, modified_fields,
        )

    # ------------------------------------------------------------------
    # Combined: 편집 세션 시작
    # ------------------------------------------------------------------
    async def on_session_start(
        self,
        gcs_path: str,
        user_id: str,
        display_name: str,
        total_rows: int,
    ) -> int:
        """편집 세션 시작 시 file_registry UPSERT + edit_events INSERT"""
        file_id = await self.get_or_create_file(gcs_path, user_id, total_rows)

        await self.update_file_status(
            gcs_path,
            status="editing",
            modified_by=user_id,
            increment_edit_sessions=True,
            total_rows=total_rows,
        )
        await self.record_event(
            file_id=file_id,
            gcs_path=gcs_path,
            user_id=user_id,
            display_name=display_name,
            event_type="session_start",
            summary=f"{total_rows} rows loaded",
            rows_affected=total_rows,
        )
        logger.info("DuckDB: session_start recorded for %s by %s", gcs_path, user_id)
        return file_id

    # ------------------------------------------------------------------
    # Combined: 행 저장
    # ------------------------------------------------------------------
    async def on_row_save(
        self,
        gcs_path: str,
        user_id: str,
        display_name: str,
        row_idx: int,
        changed_fields: list[str],
    ) -> None:
        """행 저장 시 file_registry UPDATE + edit_events INSERT"""
        file_id = await self.get_or_create_file(gcs_path, user_id)

        await self.update_file_status(
            gcs_path, modified_by=user_id
        )
        await self.record_event(
            file_id=file_id,
            gcs_path=gcs_path,
            user_id=user_id,
            display_name=display_name,
            event_type="row_save",
            summary=f"row {row_idx}: {', '.join(changed_fields)}",
            rows_affected=1,
            modified_fields=changed_fields,
        )

    # ------------------------------------------------------------------
    # Combined: GCS 업데이트
    # ------------------------------------------------------------------
    async def on_gcs_update(
        self,
        gcs_path: str,
        user_id: str,
        display_name: str,
        total_rows: int,
    ) -> None:
        """GCS 파일 업데이트 시 file_registry UPDATE + edit_events INSERT"""
        file_id = await self.get_or_create_file(gcs_path, user_id)

        await self.update_file_status(
            gcs_path,
            status="updated",
            modified_by=user_id,
            increment_update_count=True,
        )
        await self.record_event(
            file_id=file_id,
            gcs_path=gcs_path,
            user_id=user_id,
            display_name=display_name,
            event_type="gcs_update",
            summary=f"{total_rows} rows published to GCS",
            rows_affected=total_rows,
        )
        logger.info("DuckDB: gcs_update recorded for %s by %s", gcs_path, user_id)

    # ------------------------------------------------------------------
    # Combined: 편집 취소
    # ------------------------------------------------------------------
    async def on_session_discard(
        self,
        gcs_path: str,
        user_id: str,
        display_name: str,
    ) -> None:
        """편집 취소 시 file_registry UPDATE + edit_events INSERT"""
        file_id = await self.get_or_create_file(gcs_path, user_id)

        cursor = DuckDBClient.get_read_cursor()
        result = cursor.execute(
            "SELECT update_count FROM file_registry WHERE gcs_path = ?",
            [gcs_path],
        ).fetchone()
        cursor.close()

        prev_status = "updated" if (result and result[0] > 0) else "registered"
        await self.update_file_status(gcs_path, status=prev_status)
        await self.record_event(
            file_id=file_id,
            gcs_path=gcs_path,
            user_id=user_id,
            display_name=display_name,
            event_type="session_discard",
        )

    # ------------------------------------------------------------------
    # 조회: TASK 진행률
    # ------------------------------------------------------------------
    def _get_task_progress_sync(self) -> list[dict[str, Any]]:
        cursor = DuckDBClient.get_read_cursor()
        result = cursor.execute("SELECT * FROM task_progress").fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()
        return [dict(zip(columns, row)) for row in result]

    async def get_task_progress(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_task_progress_sync)

    # ------------------------------------------------------------------
    # 조회: 파일 이력
    # ------------------------------------------------------------------
    def _get_file_events_sync(
        self, gcs_path: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        cursor = DuckDBClient.get_read_cursor()
        result = cursor.execute(
            """
            SELECT e.event_type, e.user_id, e.display_name,
                   e.summary, e.rows_affected, e.modified_fields, e.created_at
            FROM edit_events e
            WHERE e.gcs_path = ?
            ORDER BY e.created_at DESC
            LIMIT ?
            """,
            [gcs_path, limit],
        ).fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()
        return [dict(zip(columns, row)) for row in result]

    async def get_file_events(
        self, gcs_path: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_file_events_sync, gcs_path, limit)

    # ------------------------------------------------------------------
    # 조회: 파일 메타
    # ------------------------------------------------------------------
    def _get_file_meta_sync(self, gcs_path: str) -> dict[str, Any] | None:
        cursor = DuckDBClient.get_read_cursor()
        result = cursor.execute(
            "SELECT * FROM file_registry WHERE gcs_path = ?", [gcs_path]
        ).fetchone()
        if not result:
            cursor.close()
            return None
        columns = [desc[0] for desc in cursor.description]
        cursor.close()
        return dict(zip(columns, result))

    async def get_file_meta(self, gcs_path: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_file_meta_sync, gcs_path)

    # ------------------------------------------------------------------
    # GCS → DuckDB 동기화 (일별 1회)
    # ------------------------------------------------------------------
    def _needs_sync_today(self, task_id: str, date_folder: str) -> bool:
        """오늘 이미 동기화했는지 확인"""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        cursor = DuckDBClient.get_read_cursor()
        result = cursor.execute(
            "SELECT last_synced_date FROM registry_sync WHERE task_id = ? AND date_folder = ?",
            [task_id, date_folder],
        ).fetchone()
        cursor.close()
        if result and result[0] == today:
            return False
        return True

    async def needs_sync_today(self, task_id: str, date_folder: str) -> bool:
        return await asyncio.to_thread(self._needs_sync_today, task_id, date_folder)

    def _sync_files_from_gcs_sync(
        self,
        task_id: str,
        date_folder: str,
        gcs_files: list[dict[str, Any]],
    ) -> int:
        """GCS 파일 목록을 file_registry에 일괄 동기화.

        - 새 파일: INSERT (status='registered')
        - 기존 파일: size가 변경된 경우만 updated_at 갱신
        """
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        synced_count = 0

        existing: dict[str, dict[str, Any]] = {}
        cursor = DuckDBClient.get_read_cursor()
        rows = cursor.execute(
            "SELECT gcs_path, content_stats.total_rows AS total_rows FROM file_registry WHERE task_id = ? AND date_folder = ?",
            [task_id, date_folder],
        ).fetchall()
        cursor.close()
        for row in rows:
            existing[row[0]] = {"total_rows": row[1]}

        statements: list[tuple[str, list[Any]]] = []

        for f in gcs_files:
            gcs_path = f["gcs_path"]
            file_name = f["name"]
            size_bytes = f.get("size_bytes", 0)

            if gcs_path not in existing:
                statements.append((
                    """
                    INSERT INTO file_registry (
                        gcs_path, file_name, task_id, date_folder,
                        created_by, status,
                        content_stats, update_count, total_edit_sessions
                    ) VALUES (
                        ?, ?, ?, ?,
                        'system', 'registered',
                        {'total_rows': 0, 'has_images': false, 'data_id_range': ''},
                        0, 0
                    )
                    """,
                    [gcs_path, file_name, task_id, date_folder],
                ))
                synced_count += 1
            else:
                statements.append((
                    "UPDATE file_registry SET updated_at = now() WHERE gcs_path = ?",
                    [gcs_path],
                ))

        statements.append((
            """
            INSERT INTO registry_sync (task_id, date_folder, last_synced_at, last_synced_date, file_count)
            VALUES (?, ?, now(), ?, ?)
            ON CONFLICT (task_id, date_folder) DO UPDATE SET
                last_synced_at = now(),
                last_synced_date = excluded.last_synced_date,
                file_count = excluded.file_count
            """,
            [task_id, date_folder, today, len(gcs_files)],
        ))

        if statements:
            DuckDBClient.execute_write_many(statements)

        logger.info(
            "DuckDB sync: task=%s date=%s total=%d new=%d",
            task_id, date_folder, len(gcs_files), synced_count,
        )
        return synced_count

    async def sync_files_from_gcs(
        self,
        task_id: str,
        date_folder: str,
        gcs_files: list[dict[str, Any]],
    ) -> int:
        return await asyncio.to_thread(
            self._sync_files_from_gcs_sync, task_id, date_folder, gcs_files
        )

    # ------------------------------------------------------------------
    # DuckDB 기반 파일 목록 조회 (GCS 대체)
    # ------------------------------------------------------------------
    def _list_files_by_folder_sync(
        self, task_id: str, date_folder: str
    ) -> list[dict[str, Any]]:
        cursor = DuckDBClient.get_read_cursor()
        rows = cursor.execute(
            """
            SELECT
                f.gcs_path, f.file_name, f.status::VARCHAR AS status,
                f.created_by, f.created_at,
                f.last_modified_by,
                u.display_name AS last_modified_display_name,
                f.last_modified_at,
                f.content_stats.total_rows AS total_rows,
                f.update_count, f.total_edit_sessions, f.updated_at
            FROM file_registry f
            LEFT JOIN users u ON f.last_modified_by = u.user_id
            WHERE f.task_id = ? AND f.date_folder = ?
            ORDER BY f.file_name
            """,
            [task_id, date_folder],
        ).fetchall()
        columns = [desc[0] for desc in cursor.description]
        cursor.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            d = dict(zip(columns, row))
            d["name"] = d["file_name"]
            d["date_folder"] = date_folder
            d["status"] = d.get("status") or ""
            d["last_modified_by"] = d.get("last_modified_display_name") or d.get("last_modified_by") or ""
            d["update_count"] = d.get("update_count") or 0
            d["total_edit_sessions"] = d.get("total_edit_sessions") or 0
            if d.get("created_at"):
                d["created_at"] = str(d["created_at"])
            if d.get("last_modified_at"):
                d["last_modified_at"] = str(d["last_modified_at"])
            if d.get("updated_at"):
                d["updated_at"] = str(d["updated_at"])
            result.append(d)
        return result

    async def list_files_by_folder(
        self, task_id: str, date_folder: str
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._list_files_by_folder_sync, task_id, date_folder
        )

    # ------------------------------------------------------------------
    # DuckDB 기반 날짜 폴더 목록 (GCS 대체)
    # ------------------------------------------------------------------
    def _list_date_folders_sync(self, task_id: str) -> list[dict[str, str]]:
        cursor = DuckDBClient.get_read_cursor()
        rows = cursor.execute(
            """
            SELECT DISTINCT date_folder, COUNT(*) AS file_count
            FROM file_registry
            WHERE task_id = ?
            GROUP BY date_folder
            ORDER BY date_folder DESC
            """,
            [task_id],
        ).fetchall()
        cursor.close()

        folders: list[dict[str, str]] = []
        for row in rows:
            date_str = row[0]
            file_count = row[1]
            try:
                dt = datetime.strptime(date_str, "%Y%m%d")
                display = dt.strftime("%Y-%m-%d")
            except ValueError:
                display = date_str
            folders.append({
                "name": date_str,
                "display": display,
                "file_count": str(file_count),
            })
        return folders

    async def list_date_folders(self, task_id: str) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._list_date_folders_sync, task_id)

    # ------------------------------------------------------------------
    # 날짜 폴더 동기화 (GCS에서 폴더 목록 가져와 registry_sync에 등록)
    # ------------------------------------------------------------------
    def _needs_folder_sync_today(self, task_id: str) -> bool:
        """해당 TASK의 폴더 목록이 오늘 동기화되었는지 확인"""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        cursor = DuckDBClient.get_read_cursor()
        result = cursor.execute(
            "SELECT COUNT(*) FROM registry_sync WHERE task_id = ? AND last_synced_date = ?",
            [task_id, today],
        ).fetchone()
        cursor.close()
        return result is None or result[0] == 0

    async def needs_folder_sync_today(self, task_id: str) -> bool:
        return await asyncio.to_thread(self._needs_folder_sync_today, task_id)

    def _has_any_folders(self, task_id: str) -> bool:
        """해당 TASK에 등록된 폴더가 있는지 확인"""
        cursor = DuckDBClient.get_read_cursor()
        result = cursor.execute(
            "SELECT COUNT(DISTINCT date_folder) FROM file_registry WHERE task_id = ?",
            [task_id],
        ).fetchone()
        cursor.close()
        return result is not None and result[0] > 0

    async def has_any_folders(self, task_id: str) -> bool:
        return await asyncio.to_thread(self._has_any_folders, task_id)

    def _clear_sync_record_sync(self, task_id: str) -> int:
        """해당 TASK의 동기화 기록을 삭제하여 다음 접근 시 GCS 재동기화를 강제한다."""
        conn = DuckDBClient.get_connection()
        result = conn.execute(
            "DELETE FROM registry_sync WHERE task_id = ? RETURNING task_id",
            [task_id],
        ).fetchall()
        return len(result)

    async def clear_sync_record(self, task_id: str) -> int:
        return await asyncio.to_thread(self._clear_sync_record_sync, task_id)

    # ------------------------------------------------------------------
    # 다운로드 이력 (Phase 12)
    # ------------------------------------------------------------------
    def _record_download_sync(
        self,
        user_id: str,
        display_name: str,
        task_id: str,
        date_folder: str,
        file_types: str,
        file_count: int,
        total_size: int,
        zip_size: int,
        zip_filename: str,
        status: str,
        error_message: str | None,
        duration_ms: int,
    ) -> None:
        DuckDBClient.execute_write(
            """INSERT INTO download_history
            (user_id, display_name, task_id, date_folder, file_types,
             file_count, total_size, zip_size, zip_filename,
             status, error_message, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                user_id, display_name, task_id, date_folder, file_types,
                file_count, total_size, zip_size, zip_filename,
                status, error_message, duration_ms,
            ],
        )

    async def record_download(
        self,
        user_id: str,
        display_name: str,
        task_id: str,
        date_folder: str,
        file_types: str,
        file_count: int,
        total_size: int,
        zip_size: int,
        zip_filename: str,
        status: str = "completed",
        error_message: str | None = None,
        duration_ms: int = 0,
    ) -> None:
        await asyncio.to_thread(
            self._record_download_sync,
            user_id, display_name, task_id, date_folder, file_types,
            file_count, total_size, zip_size, zip_filename,
            status, error_message, duration_ms,
        )


metadata_service = MetadataService()
