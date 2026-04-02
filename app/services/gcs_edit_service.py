"""GCS 파일 Redis 기반 편집 서비스

GCS JSONL을 로컬 파일 없이 직접 편집하기 위한 서비스.
전체 흐름:
    1. load_from_gcs()  → GCS JSONL → Redis Hash (행별 저장)
    2. get_row()         → Redis에서 단일 행 읽기
    3. update_row()      → Redis에서 행 업데이트 (변경분만 병합)
    4. publish_to_gcs()  → Redis 전체 행 → JSONL 재구성 → GCS 업데이트
    5. discard()         → Redis 작업 사본 삭제 (편집 취소)

Redis 키 구조:
    gcs_wc:{file_id}:rows  → Hash { "0": json_str, "1": json_str, ... }
    gcs_wc:{file_id}:meta  → Hash { gcs_path, date_str, total_rows, loaded_at }
    gcs_wc:{file_id}:idx   → Hash { "0": "data_id|pair_idx", ... }  (사이드바 경량 인덱스)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from app.core.logger import logger
from app.db.redis_client import get_redis_client
from app.schemas.jsonl_validator import ValidationResult, validate_row_safe
from app.services.gcs_service import gcs_service

WORKING_COPY_PREFIX = "gcs_wc"
WORKING_COPY_TTL = 86400  # 24시간

# 내부 메타 키 — strip 대상에서 제외
_INTERNAL_KEYS = {"_version", "_last_edited_by", "_last_edited_at", "row_idx", "file_id"}


def _is_empty(value: Any) -> bool:
    """빈 값 판별: None, "", [], {} → True"""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def _strip_empty_values(data: Any) -> Any:
    """dict/list를 재귀 순회하며 빈 값인 키를 제거한다.

    - dict: 빈 값인 키 제거, 내부 dict/list는 재귀 정리 후 결과가 비면 제거
    - list: 각 요소를 재귀 정리, 빈 요소는 유지 (인덱스 보존)
    - 내부 메타 키(_version 등)는 건드리지 않는다.
    """
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            if k in _INTERNAL_KEYS:
                cleaned[k] = v
                continue
            stripped = _strip_empty_values(v)
            if not _is_empty(stripped):
                cleaned[k] = stripped
        return cleaned
    if isinstance(data, list):
        return [_strip_empty_values(item) for item in data]
    return data


# ──────────────────────────────────────────────
# 스키마 인식 Strip: 필수 키 보존, 선택 키 빈값 제거
# ──────────────────────────────────────────────
# SchemaNode 규약
#   "_R": frozenset(...)  — 해당 레벨 필수 키 집합
#   "_R": "*"             — 해당 레벨 모든 키 필수 (동적 태그명 등)
#   "key": sub_schema     — 하위 dict 구조 스키마
#   "*": sub_schema       — 와일드카드: 이름과 무관하게 모든 키에 적용
#   "_ITEM": sub_schema   — list[dict] 각 항목에 적용할 스키마

_SchemaNode = dict[str, Any]

# -- content_meta 내부 태그 --
_CM_TAG_SCHEMA: _SchemaNode = {
    "_R": frozenset({"type", "info", "tag_properties"}),
    "tag_properties": {"_R": frozenset()},
    "img_size": {"_R": frozenset({"channel", "height", "width"})},
    "bbox": {"_R": frozenset({"x1", "y1", "x2", "y2"})},
}

_CONTENT_META_SCHEMA: _SchemaNode = {
    "_R": "*",
    "*": _CM_TAG_SCHEMA,
}

# -- TASK1 add_info --
_TASK1_ADD_INFO_SCHEMA: _SchemaNode = {
    "_R": frozenset({"page_num", "source_file", "book_meta", "unit_meta"}),
    "book_meta": {
        "_R": frozenset({"학교급", "과목", "학년", "학기", "도서시리즈", "연도"}),
    },
    "unit_meta": {
        "_R": frozenset({"유형", "학습파트", "대단원", "중단원"}),
    },
    "문제": {
        "_R": frozenset({"문제유형", "단일질문"}),
        "부가정보": {"_R": frozenset()},
    },
    "풀이": {"_R": frozenset()},
}

# -- TASK2 add_info --
_TASK2_ADD_INFO_SCHEMA: _SchemaNode = {
    "_R": frozenset({"pairIDX", "source_file", "논제", "논제분석", "학생답안", "교사첨삭"}),
    "논제": {"_R": frozenset({"회차", "출처", "본문"})},
    "논제분석": {"_R": frozenset({"해설", "예시답안"})},
    "교사첨삭": {
        "_R": frozenset({"총평가", "세부첨삭"}),
        "총평가": {"_ITEM": {"_R": frozenset({"유형", "내용"})}},
        "세부평가": {"_ITEM": {"_R": frozenset()}},
        "세부첨삭": {"_ITEM": {"_R": frozenset({"원본", "유형", "내용"})}},
    },
}

# -- TASK3 add_info --
_TASK3_ADD_INFO_SCHEMA: _SchemaNode = {
    "_R": frozenset({"pairIDX", "source_file", "논제", "논제분석", "학생답안", "교사첨삭"}),
    "논제": {
        "_R": frozenset({"회차", "출처", "제시문", "문항"}),
        "문항": {
            "_R": frozenset({"질문"}),
            "질문": {"_ITEM": {"_R": frozenset({"번호", "본문"})}},
        },
    },
    "논제분석": {
        "_R": frozenset(),
        "예시답안": {
            "_R": frozenset(),
            "문항_질문": {"_ITEM": {"_R": frozenset()}},
        },
        "평가기준": {
            "_R": frozenset(),
            "문항_질문": {"_ITEM": {"_R": frozenset()}},
        },
    },
    "학생답안": {
        "_R": frozenset({"문항_질문"}),
        "문항_질문": {"_ITEM": {"_R": frozenset({"번호", "답안"})}},
    },
    "교사첨삭": {
        "_R": frozenset({"세부첨삭"}),
        "평가": {"_ITEM": {"_R": frozenset()}},
        "세부첨삭": {
            "_ITEM": {
                "_R": frozenset({"문항_질문_번호", "원본", "유형", "내용"}),
            },
        },
    },
}

# -- 최상위 스키마 (TASK별) --
_TOP_REQUIRED = frozenset({
    "data_id", "data_file", "data_title", "data_source",
    "category_main", "category_sub", "data_type", "collected_date",
    "content",
})

_TOP_SCHEMA_TASK1: _SchemaNode = {
    "_R": _TOP_REQUIRED,
    "content_meta": _CONTENT_META_SCHEMA,
    "add_info": _TASK1_ADD_INFO_SCHEMA,
}

_TOP_SCHEMA_TASK2: _SchemaNode = {
    "_R": _TOP_REQUIRED | frozenset({"add_info"}),
    "content_meta": _CONTENT_META_SCHEMA,
    "add_info": _TASK2_ADD_INFO_SCHEMA,
}

_TOP_SCHEMA_TASK3: _SchemaNode = {
    "_R": _TOP_REQUIRED | frozenset({"add_info"}),
    "content_meta": _CONTENT_META_SCHEMA,
    "add_info": _TASK3_ADD_INFO_SCHEMA,
}


def _apply_sub(value: Any, sub_schema: _SchemaNode | None) -> Any:
    """하위 스키마를 value에 적용한다."""
    if sub_schema is None:
        return value

    if "_ITEM" in sub_schema and isinstance(value, list):
        item_schema: _SchemaNode = sub_schema["_ITEM"]
        processed: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                stripped = _strip_with_schema(item, item_schema)
                if stripped:
                    processed.append(stripped)
            else:
                processed.append(item)
        return processed

    if isinstance(value, dict):
        return _strip_with_schema(value, sub_schema)

    return value


def _strip_with_schema(
    data: dict[str, Any], schema: _SchemaNode,
) -> dict[str, Any]:
    """스키마 기반 재귀 strip — 필수 키 보존, 선택 키 빈값 제거."""
    required: frozenset[str] | str = schema.get("_R", frozenset())
    all_required = required == "*"

    cleaned: dict[str, Any] = {}
    for k, v in data.items():
        if k in _INTERNAL_KEYS:
            cleaned[k] = v
            continue

        sub = schema.get(k) or schema.get("*")
        is_req = all_required or (
            isinstance(required, frozenset) and k in required
        )

        if is_req:
            cleaned[k] = _apply_sub(v, sub)
        else:
            stripped = _apply_sub(v, sub) if sub else _strip_empty_values(v)
            if not _is_empty(stripped):
                cleaned[k] = stripped

    return cleaned


def strip_row(row: dict[str, Any]) -> dict[str, Any]:
    """JSONL row를 TASK 유형에 맞는 스키마로 strip한다.

    - 필수 필드: 빈 값이어도 보존
    - 선택 필드: 빈 값이면 제거
    """
    if not isinstance(row, dict):
        return row

    add_info = row.get("add_info")
    if isinstance(add_info, dict) and "논제" in add_info:
        topic = add_info.get("논제")
        if isinstance(topic, dict) and "문항" in topic:
            schema = _TOP_SCHEMA_TASK3
        else:
            schema = _TOP_SCHEMA_TASK2
    else:
        schema = _TOP_SCHEMA_TASK1

    return _strip_with_schema(row, schema)


PIPELINE_CHUNK_SIZE = 500

EDITABLE_FIELDS = {"content", "content_meta", "add_info"}


class GCSEditService:
    """GCS 파일의 Redis 기반 편집 서비스"""

    def _rows_key(self, file_id: str) -> str:
        return f"{WORKING_COPY_PREFIX}:{file_id}:rows"

    def _meta_key(self, file_id: str) -> str:
        return f"{WORKING_COPY_PREFIX}:{file_id}:meta"

    def _idx_key(self, file_id: str) -> str:
        return f"{WORKING_COPY_PREFIX}:{file_id}:idx"

    @staticmethod
    def _build_idx_value(row: dict[str, Any]) -> str:
        """data_id와 pair_idx를 파이프로 결합한 인덱스 값 생성."""
        data_id = row.get("data_id", "")
        add_info = row.get("add_info")
        pair_idx = ""
        if isinstance(add_info, dict):
            pair_idx = add_info.get("pairIDX", "")
        return f"{data_id}|{pair_idx}"

    async def is_loaded(self, file_id: str) -> bool:
        """파일이 Redis working copy에 로드되어 있는지 확인"""
        redis = await get_redis_client()
        return await redis.exists(self._meta_key(file_id)) > 0

    async def load_from_gcs(self, file_id: str, gcs_path: str, date_str: str) -> int:
        """GCS JSONL을 Redis working copy로 로드

        Returns:
            로드된 총 행 수
        """
        redis = await get_redis_client()
        rows_key = self._rows_key(file_id)
        meta_key = self._meta_key(file_id)

        def _download_text() -> str:
            blob = gcs_service.bucket.blob(gcs_path)
            return blob.download_as_text(encoding="utf-8")

        raw_text = await asyncio.to_thread(_download_text)

        def _parse_lines() -> tuple[list[str], dict[str, str]]:
            parsed: list[str] = []
            idx_map: dict[str, str] = {}
            for i, line in enumerate(raw_text.strip().split("\n")):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if "_version" not in row:
                    row["_version"] = 1
                idx_val = GCSEditService._build_idx_value(row)
                idx_map[str(len(parsed))] = idx_val
                parsed.append(json.dumps(row, ensure_ascii=False))
            return parsed, idx_map

        parsed_lines, idx_map = await asyncio.to_thread(_parse_lines)
        idx_key = self._idx_key(file_id)

        await redis.delete(rows_key, idx_key)
        for chunk_start in range(0, len(parsed_lines), PIPELINE_CHUNK_SIZE):
            chunk = parsed_lines[chunk_start:chunk_start + PIPELINE_CHUNK_SIZE]
            pipe = redis.pipeline()
            for offset, json_str in enumerate(chunk):
                key_str = str(chunk_start + offset)
                await pipe.hset(rows_key, key_str, json_str)
                if key_str in idx_map:
                    await pipe.hset(idx_key, key_str, idx_map[key_str])
            await pipe.execute()

        await redis.hset(meta_key, mapping={
            "gcs_path": gcs_path,
            "date_str": date_str,
            "total_rows": str(len(parsed_lines)),
            "loaded_at": datetime.now(tz=timezone.utc).isoformat(),
        })

        await redis.expire(rows_key, WORKING_COPY_TTL)
        await redis.expire(meta_key, WORKING_COPY_TTL)
        await redis.expire(idx_key, WORKING_COPY_TTL)

        logger.info(f"GCS → Redis: {file_id} ({len(parsed_lines)} rows) from {gcs_path}")
        return len(parsed_lines)

    async def get_meta(self, file_id: str) -> dict[str, str] | None:
        """working copy 메타데이터 조회"""
        redis = await get_redis_client()
        meta = await redis.hgetall(self._meta_key(file_id))
        return meta if meta else None

    async def get_row(self, file_id: str, row_idx: int) -> dict[str, Any]:
        """Redis에서 단일 행 읽기"""
        redis = await get_redis_client()
        raw = await redis.hget(self._rows_key(file_id), str(row_idx))
        if raw is None:
            raise KeyError(f"Row {row_idx} not found in working copy: {file_id}")
        row = json.loads(raw)
        row["row_idx"] = row_idx
        row["file_id"] = file_id
        return row

    async def update_row(
        self,
        file_id: str,
        row_idx: int,
        changes: dict[str, Any],
        version: int,
        user_id: str,
    ) -> dict[str, Any]:
        """Redis에서 행 업데이트 (변경분만 병합, Optimistic Locking)"""
        redis = await get_redis_client()
        rows_key = self._rows_key(file_id)

        raw = await redis.hget(rows_key, str(row_idx))
        if raw is None:
            raise KeyError(f"Row {row_idx} not found in working copy: {file_id}")

        row = json.loads(raw)
        current_version = row.get("_version", 1)

        if current_version != version:
            raise ValueError(
                f"Version conflict: expected {version}, got {current_version}"
            )

        for key, value in changes.items():
            if key in EDITABLE_FIELDS:
                row[key] = value

        row["_version"] = current_version + 1
        row["_last_edited_by"] = user_id
        row["_last_edited_at"] = datetime.now(tz=timezone.utc).isoformat()

        await redis.hset(rows_key, str(row_idx), json.dumps(row, ensure_ascii=False))

        if "add_info" in changes:
            idx_val = self._build_idx_value(row)
            await redis.hset(self._idx_key(file_id), str(row_idx), idx_val)

        await redis.expire(rows_key, WORKING_COPY_TTL)
        await redis.expire(self._meta_key(file_id), WORKING_COPY_TTL)
        await redis.expire(self._idx_key(file_id), WORKING_COPY_TTL)

        logger.info(f"Redis row updated: {file_id}[{row_idx}] by {user_id}")
        return row

    async def get_data_id_list(self, file_id: str) -> list[dict[str, Any]]:
        """모든 행의 data_id 목록 반환 (사이드바용).

        경량 인덱스 Hash를 우선 사용하여 전체 row 파싱을 회피한다.
        인덱스가 없으면 rows Hash에서 폴백.
        """
        redis = await get_redis_client()
        idx_key = self._idx_key(file_id)
        idx_data = await redis.hgetall(idx_key)

        if idx_data:
            items: list[dict[str, Any]] = []
            for idx_str in sorted(idx_data.keys(), key=int):
                parts = idx_data[idx_str].split("|", 1)
                data_id = parts[0] if parts[0] else f"row_{idx_str}"
                pair_idx = parts[1] if len(parts) > 1 else ""
                items.append({
                    "row_idx": int(idx_str),
                    "data_id": data_id,
                    "pair_idx": pair_idx,
                })
            return items

        all_rows = await redis.hgetall(self._rows_key(file_id))
        items = []
        for idx_str in sorted(all_rows.keys(), key=int):
            row = json.loads(all_rows[idx_str])
            add_info = row.get("add_info")
            pair_idx = ""
            if isinstance(add_info, dict):
                pair_idx = add_info.get("pairIDX", "")
            items.append({
                "row_idx": int(idx_str),
                "data_id": row.get("data_id", f"row_{idx_str}"),
                "pair_idx": pair_idx,
            })
        return items

    async def publish_to_gcs(self, file_id: str) -> str:
        """Redis의 모든 행을 JSONL로 재구성 → GCS 덮어쓰기

        Returns:
            업로드된 GCS 경로
        """
        redis = await get_redis_client()
        meta = await redis.hgetall(self._meta_key(file_id))
        if not meta:
            raise KeyError(f"Working copy not found: {file_id}")

        gcs_path = meta["gcs_path"]
        all_rows = await redis.hgetall(self._rows_key(file_id))

        def _build_jsonl(raw_rows: dict[str, str]) -> tuple[str, int]:
            result_lines: list[str] = []
            for idx_str in sorted(raw_rows.keys(), key=int):
                row = json.loads(raw_rows[idx_str])
                row.pop("_version", None)
                row.pop("_last_edited_by", None)
                row.pop("_last_edited_at", None)
                row = strip_row(row)
                result_lines.append(json.dumps(row, ensure_ascii=False))
            return "\n".join(result_lines) + "\n", len(result_lines)

        jsonl_content, line_count = await asyncio.to_thread(
            _build_jsonl, all_rows,
        )

        def _upload() -> None:
            blob = gcs_service.bucket.blob(gcs_path)
            blob.upload_from_string(jsonl_content, content_type="application/json")

        await asyncio.to_thread(_upload)
        logger.info(f"Redis → GCS updated: {file_id} ({line_count} rows) → {gcs_path}")

        await gcs_service.invalidate_cache()
        return gcs_path

    async def discard(self, file_id: str) -> None:
        """Redis working copy 삭제 (편집 취소)"""
        redis = await get_redis_client()
        await redis.delete(
            self._rows_key(file_id),
            self._meta_key(file_id),
            self._idx_key(file_id),
        )
        logger.info(f"Working copy discarded: {file_id}")

    async def list_active_sessions(self) -> list[dict[str, str]]:
        """Redis에 현재 로드된 모든 GCS 편집 세션의 메타 정보 반환"""
        redis = await get_redis_client()
        sessions: list[dict[str, str]] = []
        async for key in redis.scan_iter(
            match=f"{WORKING_COPY_PREFIX}:*:meta", count=100
        ):
            meta = await redis.hgetall(key)
            if meta:
                file_id = key.split(":")[1] if ":" in key else ""
                sessions.append({
                    "file_id": file_id,
                    "gcs_path": meta.get("gcs_path", ""),
                    "date_str": meta.get("date_str", ""),
                    "total_rows": meta.get("total_rows", "0"),
                    "loaded_at": meta.get("loaded_at", ""),
                })
        return sessions

    async def refresh_ttl(self, file_id: str) -> None:
        """working copy TTL 갱신 (편집 활동 시)"""
        redis = await get_redis_client()
        await redis.expire(self._rows_key(file_id), WORKING_COPY_TTL)
        await redis.expire(self._meta_key(file_id), WORKING_COPY_TTL)
        await redis.expire(self._idx_key(file_id), WORKING_COPY_TTL)

    async def validate_all_rows(
        self, file_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """전체 row에 대한 JSONL 스키마 검증을 수행한다.

        Returns:
            {"errors": [...], "warnings": [...]}
            각 항목은 {"row_idx": int, "messages": list[str]} 형태.
        """
        redis = await get_redis_client()
        all_rows = await redis.hgetall(self._rows_key(file_id))

        error_items: list[dict[str, Any]] = []
        warning_items: list[dict[str, Any]] = []

        for idx_str in sorted(all_rows.keys(), key=int):
            row = strip_row(json.loads(all_rows[idx_str]))
            vr = validate_row_safe(row)
            if vr.errors:
                error_items.append(
                    {"row_idx": int(idx_str), "messages": vr.errors}
                )
            if vr.warnings:
                warning_items.append(
                    {"row_idx": int(idx_str), "messages": vr.warnings}
                )

        return {"errors": error_items, "warnings": warning_items}


gcs_edit_service = GCSEditService()
