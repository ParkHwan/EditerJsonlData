"""FileService 단위 테스트

- LineIndex 구축 및 Random Access
- Row 읽기 / 쓰기 (Optimistic Locking)
- Atomic Write + Backup
- 페이지네이션
- 타입 검증 (Phase 5)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.file_service import FileService, LineIndex


# ---------------------------------------------------------------------------
# LineIndex Tests
# ---------------------------------------------------------------------------
class TestLineIndex:
    """LineIndex 바이트 오프셋 인덱서 테스트"""

    @pytest.mark.asyncio
    async def test_build_index(self, sample_jsonl: Path) -> None:
        idx = LineIndex()
        await idx.build(sample_jsonl)
        assert idx.total_lines == 3
        assert len(idx.offsets) == 3

    @pytest.mark.asyncio
    async def test_stale_detection(self, sample_jsonl: Path) -> None:
        idx = LineIndex()
        await idx.build(sample_jsonl)
        assert not idx.is_stale(sample_jsonl)

        with open(sample_jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps({"content": {"q": "4"}, "version": 1}) + "\n")

        assert idx.is_stale(sample_jsonl)

    def test_invalidate(self) -> None:
        idx = LineIndex()
        idx.offsets = [0, 100, 200]
        idx.total_lines = 3
        idx.file_mtime = 12345.0
        idx.invalidate()
        assert idx.offsets == []
        assert idx.total_lines == 0
        assert idx.file_mtime == 0.0


# ---------------------------------------------------------------------------
# FileService Tests
# ---------------------------------------------------------------------------
class TestFileService:
    """FileService CRUD 및 Atomic Write 테스트"""

    def _make_svc(self, tmp_data_dir: Path) -> FileService:
        svc = FileService()
        svc.data_dir = tmp_data_dir
        svc.backup_dir = tmp_data_dir / "backups"
        return svc

    @pytest.mark.asyncio
    async def test_get_total_rows(self, tmp_data_dir: Path, sample_jsonl: Path) -> None:
        svc = self._make_svc(tmp_data_dir)
        total = await svc.get_total_rows("sample")
        assert total == 3

    @pytest.mark.asyncio
    async def test_get_row(self, tmp_data_dir: Path, sample_jsonl: Path) -> None:
        svc = self._make_svc(tmp_data_dir)
        item = await svc.get_row("sample", 0)
        assert item.content["question"] == "문제 1"
        assert item.version == 1

        item2 = await svc.get_row("sample", 2)
        assert item2.content["question"] == "문제 3"

    @pytest.mark.asyncio
    async def test_get_row_out_of_range(self, tmp_data_dir: Path, sample_jsonl: Path) -> None:
        svc = self._make_svc(tmp_data_dir)
        with pytest.raises(HTTPException) as exc_info:
            await svc.get_row("sample", 99)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_rows_paginated(self, tmp_data_dir: Path, sample_jsonl: Path) -> None:
        svc = self._make_svc(tmp_data_dir)
        items, total = await svc.get_rows_paginated("sample", page=1, per_page=2)
        assert total == 3
        assert len(items) == 2
        assert items[0]["content"]["question"] == "문제 1"

        items2, _ = await svc.get_rows_paginated("sample", page=2, per_page=2)
        assert len(items2) == 1
        assert items2[0]["content"]["question"] == "문제 3"

    @pytest.mark.asyncio
    async def test_update_row_atomic(self, tmp_data_dir: Path, sample_jsonl: Path) -> None:
        """Atomic Write + Optimistic Locking (changes dict 방식)"""
        svc = self._make_svc(tmp_data_dir)

        changes = {"content": {"question": "수정된 문제", "answer": "수정된 답변"}}
        result = await svc.update_row_atomic("sample", 0, changes, version=1, user_id="test_user")

        assert result["version"] == 2
        assert result["content"]["question"] == "수정된 문제"
        assert result["modified_by"] == "test_user"

        updated_item = await svc.get_row("sample", 0)
        assert updated_item.content["question"] == "수정된 문제"
        assert updated_item.version == 2

    @pytest.mark.asyncio
    async def test_optimistic_lock_conflict(self, tmp_data_dir: Path, sample_jsonl: Path) -> None:
        """버전 불일치 시 409 Conflict"""
        svc = self._make_svc(tmp_data_dir)

        changes = {"content": {"question": "충돌 테스트"}}
        with pytest.raises(HTTPException) as exc_info:
            await svc.update_row_atomic("sample", 0, changes, version=999, user_id="user_a")
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_backup_created(self, tmp_data_dir: Path, sample_jsonl: Path) -> None:
        svc = self._make_svc(tmp_data_dir)

        changes = {"content": {"question": "백업 테스트"}}
        await svc.update_row_atomic("sample", 0, changes, version=1, user_id="test_user")

        backups = list((tmp_data_dir / "backups").glob("sample_*.jsonl.bak"))
        assert len(backups) >= 1

    @pytest.mark.asyncio
    async def test_list_files(self, tmp_data_dir: Path, sample_jsonl: Path) -> None:
        svc = self._make_svc(tmp_data_dir)
        files = svc.list_files()
        assert len(files) == 1
        assert files[0]["name"] == "sample.jsonl"

    # --- Phase 5: 타입 검증 테스트 ---

    @pytest.mark.asyncio
    async def test_type_validation_dict_to_string_rejected(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """dict → string 타입 변환 차단 (422)"""
        svc = self._make_svc(tmp_data_dir)

        changes = {"content": "이것은 문자열입니다"}
        with pytest.raises(HTTPException) as exc_info:
            await svc.update_row_atomic("sample", 0, changes, version=1, user_id="user_a")
        assert exc_info.value.status_code == 422
        assert "타입 불일치" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_type_validation_dict_to_dict_allowed(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """dict → dict 타입 변환 허용"""
        svc = self._make_svc(tmp_data_dir)

        changes = {"content": {"question": "새 문제", "answer": "새 답변"}}
        result = await svc.update_row_atomic("sample", 0, changes, version=1, user_id="user_a")
        assert result["content"]["question"] == "새 문제"

    @pytest.mark.asyncio
    async def test_non_editable_field_ignored(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """편집 불가 필드(data_id)는 무시됨"""
        svc = self._make_svc(tmp_data_dir)

        changes = {
            "content": {"question": "수정 가능", "answer": "ok"},
            "data_id": "HACKED_ID",
        }
        result = await svc.update_row_atomic("sample", 0, changes, version=1, user_id="user_a")
        assert result.get("data_id") != "HACKED_ID"

    @pytest.mark.asyncio
    async def test_update_preserves_non_edited_fields(
        self, tmp_data_dir: Path
    ) -> None:
        """편집하지 않은 필드는 원본 그대로 보존"""
        data_dir = tmp_data_dir
        file_path = data_dir / "rich.jsonl"
        row = {
            "data_id": "EBS_001",
            "data_file": "test.pdf",
            "content": {"question": "Q1", "answer": "A1"},
            "content_meta": {"tag": "math"},
            "add_info": {"level": 3},
            "version": 1,
        }
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        svc = FileService()
        svc.data_dir = data_dir
        svc.backup_dir = data_dir / "backups"

        changes = {"content": {"question": "Q_UPDATED", "answer": "A1"}}
        result = await svc.update_row_atomic("rich", 0, changes, version=1, user_id="editor")

        assert result["data_id"] == "EBS_001"
        assert result["data_file"] == "test.pdf"
        assert result["content_meta"] == {"tag": "math"}
        assert result["add_info"] == {"level": 3}
        assert result["content"]["question"] == "Q_UPDATED"
