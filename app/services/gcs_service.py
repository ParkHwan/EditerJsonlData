"""GCS 연동 서비스 (Phase 6 / 6-1)

Google Cloud Storage의 JSONL 파일을 로컬로 다운로드하거나,
편집 완료된 파일을 GCS에 업로드하는 기능을 제공한다.
GCS 이미지 경로 매핑을 위한 파일별 메타데이터 관리 포함.

Bucket 구조:
    gs://de-download-service-storage/manual/PROJ-14768/TASK{N}/YYYYMMDD/filename.jsonl
    gs://de-download-service-storage/manual/PROJ-14768/TASK{N}/YYYYMMDD/images/...
"""

from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError, NotFound
from zipstream import ZIP_DEFLATED, ZIP_STORED, ZipStream

from app.core.config import settings
from app.core.logger import logger

GCS_CACHE_TTL = 1800  # 30분


class GCSService:
    """Google Cloud Storage 파일 관리 서비스

    - GCS 버킷 내 JSONL 파일 목록 조회
    - GCS → 로컬(data/) 다운로드
    - 로컬(data/) → GCS 업로드
    - 날짜별 prefix 기반 탐색 (manual/YYYYMMDD/)
    """

    def __init__(self) -> None:
        self._client: storage.Client | None = None
        self._bucket: storage.Bucket | None = None

    @property
    def client(self) -> storage.Client:
        if self._client is None:
            kwargs: dict[str, Any] = {"project": settings.GCS_PROJECT_ID}
            creds = self._resolve_credentials()
            if creds is not None:
                kwargs["credentials"] = creds
            self._client = storage.Client(**kwargs)
            logger.info(
                f"GCS client initialized: project={settings.GCS_PROJECT_ID}"
            )
        return self._client

    @property
    def bucket(self) -> storage.Bucket:
        if self._bucket is None:
            self._bucket = self.client.bucket(settings.GCS_BUCKET_NAME)
        return self._bucket

    def _resolve_credentials(self) -> Any:
        """GCS 자격증명 탐색 (우선순위대로 시도)

        1) settings.GCS_CREDENTIALS_PATH (명시적 지정)
        2) .config/gcs-credentials.json (프로젝트 내 서비스 계정 키)
        3) None → google-cloud-storage 기본 탐색 (GOOGLE_APPLICATION_CREDENTIALS, gcloud ADC 등)
        """
        from google.oauth2 import service_account

        # 1) 환경 변수로 명시 지정
        if settings.GCS_CREDENTIALS_PATH:
            path = Path(settings.GCS_CREDENTIALS_PATH)
            if path.exists():
                logger.info(f"GCS credentials: {path} (env)")
                return service_account.Credentials.from_service_account_file(str(path))

        # 2) 프로젝트 내 .security/gcs-credentials.json
        project_creds = Path(".security/gcs-credentials.json")
        if project_creds.exists():
            logger.info(f"GCS credentials: {project_creds} (project .security)")
            return service_account.Credentials.from_service_account_file(str(project_creds))

        # 3) 기본 탐색 (ADC, 메타데이터 서버 등)
        logger.info("GCS credentials: default (ADC / metadata server)")
        return None

    def _build_prefix(self, date_str: str = "", task_id: str = "") -> str:
        """GCS prefix 생성

        task_id가 지정되면 TASK별 prefix 사용, 아니면 기본 GCS_PREFIX 사용.
        """
        if task_id and task_id in settings.GCS_TASKS:
            base = settings.GCS_TASKS[task_id]["prefix"].rstrip("/")
        else:
            base = settings.GCS_PREFIX.rstrip("/")
        if date_str:
            return f"{base}/{date_str}/"
        return f"{base}/"

    # ------------------------------------------------------------------
    # Redis 캐시 헬퍼
    # ------------------------------------------------------------------
    async def _get_cache(self, cache_key: str) -> Any | None:
        """Redis에서 캐시 데이터 조회"""
        try:
            from app.api.deps import get_redis_client

            redis = await get_redis_client()
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass
        return None

    async def _set_cache(self, cache_key: str, data: Any, ttl: int = GCS_CACHE_TTL) -> None:
        """Redis에 캐시 데이터 저장"""
        try:
            from app.api.deps import get_redis_client

            redis = await get_redis_client()
            await redis.set(cache_key, json.dumps(data, ensure_ascii=False), ex=ttl)
        except Exception:
            pass

    async def invalidate_cache(self, pattern: str = "gcs_cache:*") -> int:
        """캐시 무효화 (패턴 기반 삭제)"""
        try:
            from app.api.deps import get_redis_client

            redis = await get_redis_client()
            keys = []
            async for key in redis.scan_iter(match=pattern, count=100):
                keys.append(key)
            if keys:
                await redis.delete(*keys)
            return len(keys)
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # 날짜 폴더 목록 조회
    # ------------------------------------------------------------------
    async def list_date_folders(self, task_id: str = "") -> list[dict[str, str]]:
        """GCS 하위의 날짜 폴더(YYYYMMDD) 목록 조회 (30분 캐시)"""
        cache_key = f"gcs_cache:folders:{task_id or '_all'}"
        cached = await self._get_cache(cache_key)
        if cached is not None:
            return cached

        def _list() -> list[dict[str, str]]:
            prefix = self._build_prefix(task_id=task_id)
            iterator = self.client.list_blobs(
                self.bucket,
                prefix=prefix,
                delimiter="/",
            )
            list(iterator)

            folders: list[dict[str, str]] = []
            for p in sorted(iterator.prefixes, reverse=True):
                folder_name = p.rstrip("/").split("/")[-1]
                if folder_name.isdigit() and len(folder_name) == 8:
                    try:
                        dt = datetime.strptime(folder_name, "%Y%m%d")
                        display = dt.strftime("%Y-%m-%d")
                    except ValueError:
                        display = folder_name
                    folders.append({
                        "name": folder_name,
                        "display": display,
                        "prefix": p,
                    })
            return folders

        result = await asyncio.to_thread(_list)
        await self._set_cache(cache_key, result)
        return result

    # ------------------------------------------------------------------
    # 파일 목록 조회
    # ------------------------------------------------------------------
    async def list_files(
        self, date_str: str = "", task_id: str = ""
    ) -> list[dict[str, Any]]:
        """특정 날짜 폴더 내 JSONL 파일 목록 반환 (30분 캐시)

        Args:
            date_str: YYYYMMDD 형식 날짜. 빈 문자열이면 전체 검색.
            task_id: TASK ID (task1/task2/task3).
        """
        cache_key = f"gcs_cache:files:{task_id or '_all'}:{date_str or '_root'}"
        cached = await self._get_cache(cache_key)
        if cached is not None:
            return cached

        def _list() -> list[dict[str, Any]]:
            prefix = self._build_prefix(date_str, task_id=task_id)
            blobs = self.client.list_blobs(self.bucket, prefix=prefix)

            files: list[dict[str, Any]] = []
            for blob in blobs:
                if not blob.name.endswith(".jsonl"):
                    continue
                filename = blob.name.split("/")[-1]
                if not filename:
                    continue
                files.append({
                    "name": filename,
                    "gcs_path": blob.name,
                    "size_bytes": blob.size or 0,
                    "size_display": self._human_size(blob.size or 0),
                    "updated": (
                        blob.updated.strftime("%Y-%m-%d %H:%M")
                        if blob.updated
                        else ""
                    ),
                    "date_folder": date_str,
                })
            return sorted(files, key=lambda x: x["name"])

        result = await asyncio.to_thread(_list)
        await self._set_cache(cache_key, result)
        return result

    async def list_all_blobs(
        self, date_str: str, task_id: str = "", extensions: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """날짜 폴더 내 파일 목록 반환

        Args:
            extensions: 허용할 확장자 집합 (예: {".jsonl", ".png"}).
                        None이면 모든 파일 반환.

        Returns:
            [{"name", "gcs_path", "rel_path", "size"}, ...]
        """

        def _list() -> list[dict[str, str]]:
            prefix = self._build_prefix(date_str, task_id=task_id)
            blobs = self.client.list_blobs(self.bucket, prefix=prefix)
            files: list[dict[str, Any]] = []
            for blob in blobs:
                filename = blob.name.split("/")[-1]
                if not filename:
                    continue
                if extensions is not None:
                    ext = Path(filename).suffix.lower()
                    if ext not in extensions:
                        continue
                rel_path = blob.name[len(prefix):]
                files.append({
                    "name": filename,
                    "gcs_path": blob.name,
                    "rel_path": rel_path,
                    "size": blob.size or 0,
                })
            return sorted(files, key=lambda x: x["rel_path"])

        return await asyncio.to_thread(_list)

    # ------------------------------------------------------------------
    # GCS → 로컬 다운로드
    # ------------------------------------------------------------------
    async def download_to_local(
        self, gcs_path: str, overwrite: bool = False
    ) -> Path:
        """GCS 파일을 로컬 data/ 디렉터리로 다운로드

        Args:
            gcs_path: GCS 내 전체 경로 (e.g. manual/20260311/data.jsonl)
            overwrite: 기존 파일 덮어쓰기 여부

        Returns:
            로컬 저장 경로
        """
        filename = gcs_path.split("/")[-1]
        local_path = Path(settings.DATA_DIR) / filename

        if local_path.exists() and not overwrite:
            logger.info(f"File already exists locally: {local_path}")
            return local_path

        def _download() -> None:
            blob = self.bucket.blob(gcs_path)
            if not blob.exists():
                raise NotFound(f"GCS blob not found: {gcs_path}")
            local_path.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local_path))
            logger.info(f"Downloaded GCS → local: {gcs_path} → {local_path}")

        await asyncio.to_thread(_download)
        return local_path

    async def download_blob_bytes(self, gcs_path: str) -> bytes:
        """GCS blob을 메모리(bytes)로 다운로드

        blob.exists() 사전 확인 없이 바로 다운로드 시도하여 API 호출을 절반으로 줄인다.
        존재하지 않는 blob은 GCS 클라이언트가 NotFound를 발생시킨다.
        """

        def _download() -> bytes:
            blob = self.bucket.blob(gcs_path)
            return blob.download_as_bytes()

        return await asyncio.to_thread(_download)

    _NO_COMPRESS_EXTS: frozenset[str] = frozenset(
        {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".gz", ".bz2"}
    )

    async def download_blobs_concurrent(
        self,
        files: list[dict[str, str]],
        max_concurrent: int = 20,
    ) -> list[tuple[str, bytes]]:
        """여러 GCS blob을 병렬 다운로드하여 (rel_path, bytes) 리스트 반환"""
        sem = asyncio.Semaphore(max_concurrent)

        async def _dl(f: dict[str, str]) -> tuple[str, bytes]:
            async with sem:
                data = await self.download_blob_bytes(f["gcs_path"])
                return f["rel_path"], data

        return list(await asyncio.gather(*[_dl(f) for f in files]))

    async def download_folder_as_zip(
        self,
        date_str: str,
        task_id: str = "",
        extensions: set[str] | None = None,
    ) -> tuple[bytes, str]:
        """날짜 폴더 내 파일을 ZIP으로 묶어 bytes 반환 (소규모 전용)

        Args:
            extensions: 허용할 확장자 집합. None이면 전체 파일.

        Returns:
            (zip_bytes, zip_filename) 튜플
        """
        import zipfile

        files = await self.list_all_blobs(date_str, task_id=task_id, extensions=extensions)
        if not files:
            raise NotFound(f"No files found in folder: {date_str}")

        results = await self.download_blobs_concurrent(files)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel_path, data in results:
                ext = Path(rel_path).suffix.lower()
                compress = (
                    zipfile.ZIP_STORED
                    if ext in self._NO_COMPRESS_EXTS
                    else zipfile.ZIP_DEFLATED
                )
                zf.writestr(rel_path, data, compress_type=compress)

        task_label = task_id or "all"
        zip_filename = f"{task_label}_{date_str}.zip"
        return buf.getvalue(), zip_filename

    def create_zip_stream(
        self,
        files: list[dict[str, Any]],
        errors: list[str] | None = None,
        prefetch_workers: int = 20,
    ) -> ZipStream:
        """GCS 파일 목록으로 스트리밍 ZipStream 생성 (병렬 pre-fetch)

        ThreadPoolExecutor로 최대 prefetch_workers개 파일을 동시에 다운로드하고,
        ZipStream iteration 순서에 맞춰 버퍼에서 꺼내 ZIP에 추가한다.

        기존 순차 방식 대비 ~10-20x 빠름 (네트워크 I/O 병렬화).
        서버 메모리 사용량: peak ~ prefetch_workers × avg_file_size.

        Args:
            files: list_all_blobs() 반환값
            errors: 다운로드 실패 파일 경로를 수집할 리스트 (None이면 무시)
            prefetch_workers: 동시 다운로드 스레드 수
        """
        import threading
        from collections.abc import Generator
        from concurrent.futures import ThreadPoolExecutor

        zs = ZipStream(compress_type=ZIP_STORED)
        bucket = self.bucket
        no_compress = self._NO_COMPRESS_EXTS

        buffer: dict[int, bytes] = {}
        buffer_cond = threading.Condition()
        buffer_sem = threading.Semaphore(prefetch_workers)

        def _download_one(idx: int) -> None:
            buffer_sem.acquire()
            f = files[idx]
            try:
                data = bucket.blob(f["gcs_path"]).download_as_bytes()
            except Exception as exc:
                logger.warning(
                    "ZIP prefetch failed %s: %s", f["gcs_path"], exc,
                )
                if errors is not None:
                    errors.append(f"FAILED: {f['rel_path']} ({exc})")
                data = b""
            with buffer_cond:
                buffer[idx] = data
                buffer_cond.notify_all()

        def _prefetch_all() -> None:
            with ThreadPoolExecutor(max_workers=prefetch_workers) as pool:
                pool.map(_download_one, range(len(files)))

        threading.Thread(target=_prefetch_all, daemon=True).start()

        for i, f in enumerate(files):
            rel_path: str = f["rel_path"]
            ext = Path(rel_path).suffix.lower()
            compress = (
                ZIP_STORED if ext in no_compress else ZIP_DEFLATED
            )

            def _prefetched(idx: int = i) -> Generator[bytes, None, None]:
                with buffer_cond:
                    while idx not in buffer:
                        buffer_cond.wait()
                    data = buffer.pop(idx)
                buffer_sem.release()
                yield data

            zs.add(
                _prefetched(),
                arcname=rel_path,
                compress_type=compress,
            )

        return zs

    # ------------------------------------------------------------------
    # 디스크 기반 폴더 다운로드 + ZIP (방식 B)
    # ------------------------------------------------------------------
    async def download_folder_to_disk(
        self,
        date_str: str,
        task_id: str = "",
        extensions: set[str] | None = None,
        max_concurrent: int = 30,
    ) -> tuple[Path, list[dict[str, Any]], list[str]]:
        """GCS 폴더 전체를 서버 디스크에 병렬 다운로드

        Returns:
            (tmp_dir, files, errors) — tmp_dir은 사용 후 호출자가 정리
        """
        import tempfile

        files = await self.list_all_blobs(date_str, task_id=task_id, extensions=extensions)
        if not files:
            raise NotFound(f"No files found in folder: {date_str}")

        tmp_dir = Path(tempfile.mkdtemp(prefix="gcs_dl_", dir=settings.DATA_DIR))
        errors: list[str] = []
        sem = asyncio.Semaphore(max_concurrent)

        async def _dl(f: dict[str, Any]) -> None:
            async with sem:
                rel_path: str = f["rel_path"]
                local_path = tmp_dir / rel_path
                local_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    data = await self.download_blob_bytes(f["gcs_path"])
                    local_path.write_bytes(data)
                except Exception as exc:
                    logger.warning("Disk download failed %s: %s", f["gcs_path"], exc)
                    errors.append(f"FAILED: {rel_path} ({exc})")

        await asyncio.gather(*[_dl(f) for f in files])
        return tmp_dir, files, errors

    async def create_zip_on_disk(
        self,
        tmp_dir: Path,
        files: list[dict[str, Any]],
        zip_filename: str,
        errors: list[str] | None = None,
    ) -> Path:
        """디스크에 다운로드된 파일들을 ZIP으로 압축

        Returns:
            생성된 ZIP 파일 경로
        """
        import zipfile

        zip_path = Path(settings.DATA_DIR) / zip_filename
        no_compress = self._NO_COMPRESS_EXTS

        def _build_zip() -> None:
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in files:
                    rel_path: str = f["rel_path"]
                    local_file = tmp_dir / rel_path
                    if not local_file.exists():
                        continue
                    ext = Path(rel_path).suffix.lower()
                    compress = (
                        zipfile.ZIP_STORED if ext in no_compress
                        else zipfile.ZIP_DEFLATED
                    )
                    zf.write(local_file, arcname=rel_path, compress_type=compress)

                if errors:
                    manifest = (
                        f"# Download Errors ({len(errors)} failures)\n\n"
                        + "\n".join(errors)
                    )
                    zf.writestr("_DOWNLOAD_ERRORS.txt", manifest)

        await asyncio.to_thread(_build_zip)
        return zip_path

    @staticmethod
    def cleanup_path(path: Path) -> None:
        """임시 디렉터리 또는 파일 안전 삭제"""
        import shutil

        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.is_file():
                path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Cleanup failed %s: %s", path, exc)

    # ------------------------------------------------------------------
    # 로컬 → GCS 업로드
    # ------------------------------------------------------------------
    async def upload_from_local(
        self, file_id: str, date_str: str = ""
    ) -> str:
        """로컬 data/ 파일을 GCS에 업로드

        Args:
            file_id: 파일명 (확장자 제외)
            date_str: 업로드 대상 날짜 폴더 (YYYYMMDD).
                      빈 문자열이면 오늘 날짜 사용.

        Returns:
            업로드된 GCS 경로
        """
        local_path = Path(settings.DATA_DIR) / f"{file_id}.jsonl"
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        if not date_str:
            date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d")

        gcs_path = f"{settings.GCS_PREFIX.rstrip('/')}/{date_str}/{file_id}.jsonl"

        def _upload() -> None:
            blob = self.bucket.blob(gcs_path)
            blob.upload_from_filename(
                str(local_path), content_type="application/json"
            )
            logger.info(f"Uploaded local → GCS: {local_path} → gs://{settings.GCS_BUCKET_NAME}/{gcs_path}")

        await asyncio.to_thread(_upload)
        return gcs_path

    # ------------------------------------------------------------------
    # GCS 파일 존재 여부 확인
    # ------------------------------------------------------------------
    async def blob_exists(self, gcs_path: str) -> bool:
        """GCS blob 존재 여부 확인"""

        def _check() -> bool:
            blob = self.bucket.blob(gcs_path)
            return blob.exists()

        return await asyncio.to_thread(_check)

    # ------------------------------------------------------------------
    # GCS 파일 메타데이터 조회
    # ------------------------------------------------------------------
    async def get_blob_metadata(self, gcs_path: str) -> dict[str, Any]:
        """GCS blob 메타데이터 반환"""

        def _meta() -> dict[str, Any]:
            blob = self.bucket.blob(gcs_path)
            blob.reload()
            return {
                "name": blob.name,
                "size_bytes": blob.size or 0,
                "size_display": self._human_size(blob.size or 0),
                "content_type": blob.content_type,
                "updated": (
                    blob.updated.isoformat() if blob.updated else None
                ),
                "md5_hash": blob.md5_hash,
                "crc32c": blob.crc32c,
            }

        return await asyncio.to_thread(_meta)

    # ------------------------------------------------------------------
    # 연결 테스트
    # ------------------------------------------------------------------
    async def check_connection(self) -> dict[str, Any]:
        """GCS 연결 상태 확인 (Health Check 용)"""

        def _check() -> dict[str, Any]:
            try:
                bucket = self.client.get_bucket(settings.GCS_BUCKET_NAME)
                return {
                    "ok": True,
                    "bucket": bucket.name,
                    "location": bucket.location,
                    "project": settings.GCS_PROJECT_ID,
                }
            except GoogleCloudError as e:
                return {"ok": False, "error": str(e)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        return await asyncio.to_thread(_check)

    # ------------------------------------------------------------------
    # 파일별 GCS 메타데이터 관리 (Phase 6-1)
    # ------------------------------------------------------------------
    @property
    def _metadata_path(self) -> Path:
        return Path(settings.DATA_DIR) / ".gcs_metadata.json"

    def _load_metadata(self) -> dict[str, Any]:
        if self._metadata_path.exists():
            try:
                return json.loads(self._metadata_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_metadata(self, metadata: dict[str, Any]) -> None:
        self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
        self._metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def save_file_metadata(
        self, file_id: str, date_str: str, gcs_path: str
    ) -> None:
        """GCS 다운로드 시 파일 → 날짜 폴더 매핑 저장"""
        metadata = self._load_metadata()
        metadata[file_id] = {
            "date_str": date_str,
            "gcs_path": gcs_path,
            "downloaded_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        self._save_metadata(metadata)
        logger.info(f"GCS metadata saved: {file_id} → {date_str}")

    def get_file_metadata(self, file_id: str) -> dict[str, Any] | None:
        """파일의 GCS 메타데이터 조회 (date_str 등)"""
        metadata = self._load_metadata()
        return metadata.get(file_id)

    def get_date_str_for_file(self, file_id: str) -> str | None:
        """파일에 연결된 GCS 날짜 폴더(YYYYMMDD) 반환"""
        meta = self.get_file_metadata(file_id)
        if meta:
            return meta.get("date_str")
        return None

    # ------------------------------------------------------------------
    # GCS Object Versioning (Phase 9)
    # ------------------------------------------------------------------
    async def list_blob_versions(
        self, gcs_path: str
    ) -> list[dict[str, Any]]:
        """특정 파일의 모든 버전 (current + noncurrent) 조회"""

        def _list() -> list[dict[str, Any]]:
            blobs = self.client.list_blobs(
                self.bucket, prefix=gcs_path, versions=True
            )
            versions: list[dict[str, Any]] = []
            for blob in blobs:
                if blob.name != gcs_path:
                    continue
                versions.append({
                    "generation": blob.generation,
                    "size_bytes": blob.size or 0,
                    "size_display": self._human_size(blob.size or 0),
                    "updated": (
                        blob.updated.strftime("%Y-%m-%d %H:%M:%S")
                        if blob.updated
                        else ""
                    ),
                    "is_live": not blob.time_deleted,
                })
            return sorted(versions, key=lambda v: v["generation"], reverse=True)

        return await asyncio.to_thread(_list)

    async def download_blob_version(
        self, gcs_path: str, generation: int
    ) -> str:
        """특정 버전의 blob 텍스트 내용을 다운로드"""

        def _download() -> str:
            blob = self.bucket.blob(gcs_path, generation=generation)
            return blob.download_as_text(encoding="utf-8")

        return await asyncio.to_thread(_download)

    async def list_versioned_files(
        self, task_id: str = ""
    ) -> list[dict[str, Any]]:
        """버전이 2개 이상인 파일 목록 (= 수정된 적 있는 파일)"""

        def _list() -> list[dict[str, Any]]:
            prefix = self._build_prefix(task_id=task_id)
            blobs = self.client.list_blobs(
                self.bucket, prefix=prefix, versions=True
            )

            version_counts: dict[str, dict[str, Any]] = {}
            for blob in blobs:
                if not blob.name.endswith(".jsonl"):
                    continue
                key = blob.name
                if key not in version_counts:
                    version_counts[key] = {
                        "gcs_path": key,
                        "name": key.split("/")[-1],
                        "count": 0,
                        "latest_updated": "",
                        "latest_size": "",
                    }
                version_counts[key]["count"] += 1
                updated_str = (
                    blob.updated.strftime("%Y-%m-%d %H:%M")
                    if blob.updated
                    else ""
                )
                if not blob.time_deleted:
                    version_counts[key]["latest_updated"] = updated_str
                    version_counts[key]["latest_size"] = self._human_size(
                        blob.size or 0
                    )

            return sorted(
                [v for v in version_counts.values() if v["count"] >= 2],
                key=lambda x: x["latest_updated"],
                reverse=True,
            )

        return await asyncio.to_thread(_list)

    # ------------------------------------------------------------------
    # 유틸리티
    # ------------------------------------------------------------------
    @staticmethod
    def _human_size(size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"


gcs_service = GCSService()
