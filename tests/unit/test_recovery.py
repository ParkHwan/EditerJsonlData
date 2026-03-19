"""복구 및 Atomic Write 안전성 테스트 (Phase 5)

- Atomic Write 중 실패 시 원본 보존
- 백업 파일 무결성 확인
- 백업에서 원본 복원 시뮬레이션
- 임시 파일 잔존 방지
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.services.file_service import FileService


class TestAtomicWriteSafety:
    """Atomic Write 실패 시 데이터 보존 확인"""

    def _make_svc(self, tmp_data_dir: Path) -> FileService:
        svc = FileService()
        svc.data_dir = tmp_data_dir
        svc.backup_dir = tmp_data_dir / "backups"
        return svc

    @pytest.mark.asyncio
    async def test_original_preserved_on_write_failure(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """os.replace 실패 시 원본 파일 무손상"""
        svc = self._make_svc(tmp_data_dir)

        original_row = await svc.get_row_raw("sample", 0)
        original_question = original_row["content"]["question"]

        with patch("os.replace", side_effect=OSError("Disk full")):
            with pytest.raises(HTTPException) as exc_info:
                await svc.update_row_atomic(
                    "sample", 0,
                    {"content": {"question": "절대 저장 안 됨"}},
                    version=1,
                    user_id="user_a",
                )
            assert exc_info.value.status_code == 500

        svc._invalidate_index("sample")
        preserved_row = await svc.get_row_raw("sample", 0)
        assert preserved_row["content"]["question"] == original_question

    @pytest.mark.asyncio
    async def test_temp_file_cleaned_on_failure(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """실패 후 임시 파일(.tmp)이 제거됨"""
        svc = self._make_svc(tmp_data_dir)

        with patch("os.replace", side_effect=OSError("Disk full")):
            with pytest.raises(HTTPException):
                await svc.update_row_atomic(
                    "sample", 0,
                    {"content": {"question": "임시"}},
                    version=1,
                    user_id="user_a",
                )

        tmp_files = list(tmp_data_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    @pytest.mark.asyncio
    async def test_version_not_incremented_on_failure(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """실패 시 버전이 증가하지 않음"""
        svc = self._make_svc(tmp_data_dir)

        with patch("os.replace", side_effect=OSError("Disk full")):
            with pytest.raises(HTTPException):
                await svc.update_row_atomic(
                    "sample", 0,
                    {"content": {"question": "fail"}},
                    version=1,
                    user_id="u",
                )

        svc._invalidate_index("sample")
        row = await svc.get_row_raw("sample", 0)
        assert row["version"] == 1


class TestBackupIntegrity:
    """백업 파일 생성 및 무결성"""

    def _make_svc(self, tmp_data_dir: Path) -> FileService:
        svc = FileService()
        svc.data_dir = tmp_data_dir
        svc.backup_dir = tmp_data_dir / "backups"
        return svc

    @pytest.mark.asyncio
    async def test_backup_matches_original(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """백업 파일의 내용이 수정 전 원본과 일치"""
        svc = self._make_svc(tmp_data_dir)

        with open(sample_jsonl, "r", encoding="utf-8") as f:
            original_content = f.read()

        await svc.update_row_atomic(
            "sample", 0,
            {"content": {"question": "수정됨", "answer": "답변"}},
            version=1,
            user_id="u",
        )

        backups = sorted((tmp_data_dir / "backups").glob("sample_*.jsonl.bak"))
        assert len(backups) >= 1

        with open(backups[0], "r", encoding="utf-8") as f:
            backup_content = f.read()

        assert backup_content == original_content

    @pytest.mark.asyncio
    async def test_multiple_saves_create_multiple_backups(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """연속 저장 시 각각 백업 생성"""
        svc = self._make_svc(tmp_data_dir)

        await svc.update_row_atomic(
            "sample", 0,
            {"content": {"question": "v2"}},
            version=1,
            user_id="u",
        )

        import asyncio
        await asyncio.sleep(1.1)

        await svc.update_row_atomic(
            "sample", 0,
            {"content": {"question": "v3"}},
            version=2,
            user_id="u",
        )

        backups = list((tmp_data_dir / "backups").glob("sample_*.jsonl.bak"))
        assert len(backups) >= 2

    @pytest.mark.asyncio
    async def test_backup_can_restore_original(
        self, tmp_data_dir: Path, sample_jsonl: Path
    ) -> None:
        """백업에서 원본 복원 시뮬레이션"""
        svc = self._make_svc(tmp_data_dir)

        await svc.update_row_atomic(
            "sample", 0,
            {"content": {"question": "수정된 데이터"}},
            version=1,
            user_id="u",
        )

        backups = sorted((tmp_data_dir / "backups").glob("sample_*.jsonl.bak"))
        assert len(backups) >= 1

        import shutil
        shutil.copy2(backups[0], sample_jsonl)
        svc._invalidate_index("sample")

        restored = await svc.get_row_raw("sample", 0)
        assert restored["content"]["question"] == "문제 1"
        assert restored["version"] == 1


class TestFileCorruptionRecovery:
    """파일 손상 상황 복구"""

    @pytest.mark.asyncio
    async def test_corrupted_row_returns_error(self, tmp_data_dir: Path) -> None:
        """손상된 JSON Row 읽기 시 에러 필드 반환"""
        bad_file = tmp_data_dir / "bad.jsonl"
        with open(bad_file, "w", encoding="utf-8") as f:
            f.write("THIS IS NOT JSON\n")
            f.write(json.dumps({"content": {"q": "ok"}, "version": 1}) + "\n")

        svc = FileService()
        svc.data_dir = tmp_data_dir
        svc.backup_dir = tmp_data_dir / "backups"

        raw = await svc.get_row_raw("bad", 0)
        assert "error" in raw

        good = await svc.get_row_raw("bad", 1)
        assert good["content"]["q"] == "ok"

    @pytest.mark.asyncio
    async def test_empty_file_returns_zero_rows(self, tmp_data_dir: Path) -> None:
        """빈 JSONL 파일 → 0 rows"""
        empty_file = tmp_data_dir / "empty.jsonl"
        empty_file.touch()

        svc = FileService()
        svc.data_dir = tmp_data_dir
        svc.backup_dir = tmp_data_dir / "backups"

        total = await svc.get_total_rows("empty")
        assert total == 0
