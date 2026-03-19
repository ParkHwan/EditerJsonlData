# Phase 12: ZipStream 기반 실시간 스트리밍 다운로드

**작성일**: 2026-03-19  
**작성자**: AI Assistant + 사용자 협업  
**대상 프로젝트**: EditerJsonlData  
**환경**: Docker Compose (macOS Darwin 25.3.0)

---

## 1. 개요

GCS 날짜 폴더 내 대규모 파일(19,000+ PNG/PDF/JSONL 등)을 브라우저에서 ZIP으로 다운로드할 때 발생하던 **"Provisional headers are shown"** 문제를 해결하기 위해 `zipstream-ng` 라이브러리 기반의 **실시간 스트리밍 다운로드**를 구현.

### 설계 배경

| 검토 방안 | 결과 |
|-----------|------|
| In-memory ZIP (기존) | 19K 파일(~1GB) 시 서버 메모리 부족 + 브라우저 타임아웃 |
| Background ZIP Job + 다운로드 링크 | 구현 복잡, UX 분리 필요 |
| gcloud CLI 병렬 다운로드 | 사용자 환경 의존성 높음 |
| **ZipStream 실시간 스트리밍** | **채택** — 서버 메모리 ~20MB, 파일 수 무제한 |

### 핵심 결정 사항

- **하이브리드 전략**: 파일 수에 따라 자동 분기 (임계값 500개)
- **lazy generator**: iteration 시점에 1파일씩 GCS 다운로드 → 즉시 ZIP 스트림 전송
- **에러 허용**: 개별 파일 다운로드 실패 시 빈 파일로 대체 + 에러 매니페스트 추가

---

## 2. 아키텍처

```
[Browser]
  │
  ├─ downloadFolder() → GET /download-folder-info (사전 검증)
  │   └─ { file_count, total_size, total_size_display, streaming }
  │
  ├─ streaming=false → fetch() + blob → createObjectURL (소규모, <=500 파일)
  │
  └─ streaming=true → confirm() → window.location.href (대규모, >500 파일)
        │
[Nginx: /api/v1/gcs/download-folder]
  │  proxy_buffering off
  │  proxy_read_timeout 3600s
  │
[FastAPI: download_folder()]
  │
  ├─ <=500 files: download_folder_as_zip() → Response (Content-Length)
  │
  └─ >500 files: create_zip_stream() → StreamingResponse
       │
       ├─ all_files(): _lazy_blob() × N  →  GCS download → ZIP chunk → stream
       ├─ errors 존재 시: _DOWNLOAD_ERRORS.txt 추가
       └─ finalize(): central directory
```

### 서버 메모리 사용량

| 시나리오 | 메모리 사용량 |
|----------|-------------|
| 소규모 (<=500, in-memory) | 파일 총 크기만큼 |
| 대규모 (>500, streaming) | peak ~20MB (1파일씩 처리) |

---

## 3. 변경 파일 상세

### 3.1 의존성 추가

**`pyproject.toml`**
```toml
dependencies = [
    # ... 기존 ...
    "zipstream-ng>=1.8.0",
]
```

### 3.2 GCS 서비스 (`app/services/gcs_service.py`)

#### list_all_blobs() 수정

파일 사전 검증 API에서 총 용량 계산을 위해 `size` 필드 추가:

```python
files.append({
    "name": filename,
    "gcs_path": blob.name,
    "rel_path": rel_path,
    "size": blob.size or 0,  # 신규 추가
})
```

#### create_zip_stream() 신규 메서드

```python
def create_zip_stream(
    self,
    files: list[dict[str, Any]],
    errors: list[str] | None = None,
) -> ZipStream:
    """GCS 파일 목록으로 스트리밍 ZipStream 생성 (lazy loading)

    각 파일은 generator로 등록되어 iteration 시점에 GCS에서 다운로드된다.
    서버 메모리 사용량: peak ~20MB (한 번에 1파일만 메모리에 존재).
    """
    zs = ZipStream(compress_type=ZIP_STORED)

    def _lazy_blob(path: str, rel: str):
        """Generator: iteration 시점에 GCS 다운로드 실행"""
        try:
            blob = bucket.blob(path)
            yield blob.download_as_bytes()
        except Exception as exc:
            logger.warning("ZIP stream: GCS download failed %s: %s", path, exc)
            if errors is not None:
                errors.append(f"FAILED: {rel} ({exc})")
            yield b""

    for f in files:
        rel_path: str = f["rel_path"]
        ext = Path(rel_path).suffix.lower()
        compress = ZIP_STORED if ext in no_compress else ZIP_DEFLATED
        zs.add(
            _lazy_blob(f["gcs_path"], rel_path),
            arcname=rel_path,
            compress_type=compress,
        )
    return zs
```

**핵심 설계**:
- `_lazy_blob()`: generator 함수로, ZipStream이 해당 파일을 처리할 때까지 GCS 다운로드를 지연
- `ZIP_STORED` vs `ZIP_DEFLATED`: 이미 압축된 파일(.png, .jpg, .pdf 등)은 재압축하지 않음
- 에러 발생 시 빈 bytes(`b""`)를 yield하여 ZIP 스트림 중단 방지

### 3.3 API 엔드포인트 (`app/api/v1/endpoints/gcs.py`)

#### _parse_extensions() 공통 헬퍼

```python
_DOWNLOAD_TYPE_MAP = {
    "jsonl": {".jsonl"},
    "data": {".jsonl", ".json", ".csv"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"},
    "pdf": {".pdf"},
}

def _parse_extensions(types: str) -> set[str] | None:
    """types 문자열을 확장자 set으로 변환. 'all'이면 None 반환."""
```

#### GET /download-folder-info (신규)

다운로드 전 사전 검증 API:

```python
@router.get("/download-folder-info")
@limiter.limit("30/minute")
async def download_folder_info(request, date_str, task, types, current_user):
    files = await gcs_service.list_all_blobs(...)
    total_size = sum(f.get("size", 0) for f in files)
    return {
        "file_count": len(files),
        "total_size": total_size,
        "total_size_display": gcs_service._human_size(total_size),
        "streaming": len(files) > MAX_ZIP_FILES,  # 500
    }
```

#### GET /download-folder (하이브리드)

```
파일 수 <= 500  →  in-memory ZIP  →  Response (Content-Length)
파일 수 >  500  →  ZipStream     →  StreamingResponse (스트리밍)
```

**스트리밍 모드 핵심 코드**:

```python
async def _stream_zip_with_manifest():
    def _sync_iter():
        yield from zs.all_files()     # lazy blob → GCS download → ZIP chunk
        if errors:
            manifest = f"# ZIP Download Errors ({len(errors)} failures)\n\n" + "\n".join(errors)
            zs.add(manifest.encode("utf-8"), arcname="_DOWNLOAD_ERRORS.txt")
        yield from zs.finalize()       # central directory 기록

    it = _sync_iter()
    while True:
        try:
            chunk = await asyncio.to_thread(next, it)  # sync → async bridge
            yield chunk
        except StopIteration:
            break
```

**async bridge 패턴**: `asyncio.to_thread(next, it)`로 동기 ZipStream iterator를 async generator로 변환하여 FastAPI의 `StreamingResponse`와 호환.

### 3.4 프론트엔드 (`app/templates/gcs_files.html`)

```javascript
async function downloadFolder(dateStr, taskId, types) {
    // 1단계: 사전 검증
    const infoResp = await fetch(`/api/v1/gcs/download-folder-info?${params}`);
    const info = await infoResp.json();

    // 2단계: 대규모 → 브라우저 네이티브 다운로드
    if (info.streaming) {
        const ok = confirm(`대규모 다운로드\n\n파일 수: ${info.file_count}개\n예상 용량: ${info.total_size_display}\n\n스트리밍 방식으로 다운로드합니다.`);
        if (!ok) return;
        window.location.href = `/api/v1/gcs/download-folder?${params}`;
        return;
    }

    // 3단계: 소규모 → fetch + blob (기존 방식)
    const resp = await fetch(`/api/v1/gcs/download-folder?${params}`);
    const blob = await resp.blob();
    // ... createObjectURL 다운로드
}
```

**설계 이유**: 대규모 스트리밍 시 `fetch().blob()`은 응답 전체를 JS 메모리에 버퍼링하므로, `window.location.href`로 브라우저 다운로드 매니저에 직접 위임.

### 3.5 Nginx 설정 (`nginx/nginx.conf`, `nginx/nginx-http-only.conf`)

```nginx
# ZIP 스트리밍 다운로드 (대규모 파일, 장시간 연결)
location /api/v1/gcs/download-folder {
    proxy_pass http://gunicorn;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    proxy_buffering off;           # Nginx 응답 버퍼링 비활성화
    proxy_connect_timeout 60s;
    proxy_send_timeout 3600s;      # 1시간 (대규모 전송용)
    proxy_read_timeout 3600s;      # 1시간
}
```

**핵심 설정**:
- `proxy_buffering off`: Nginx가 전체 응답을 버퍼링하지 않고 즉시 클라이언트에 전달
- `timeout 3600s`: 19K+ 파일(~1GB) 전송에 수십 분 소요 → 1시간 타임아웃

---

## 4. 에러 처리 전략

### 4.1 개별 파일 다운로드 실패

```
_lazy_blob() 실행 중 Exception 발생
  → logger.warning 기록
  → errors 리스트에 실패 경로 + 원인 추가
  → yield b"" (빈 파일로 대체)
  → ZIP 스트림 계속 진행 (중단하지 않음)
```

### 4.2 에러 매니페스트

스트리밍 완료 후 에러가 1건이라도 있으면 `_DOWNLOAD_ERRORS.txt` 파일이 ZIP에 포함:

```
# ZIP Download Errors (3 failures)

FAILED: TASK1/20250301/image_001.png (NotFound: 404 ...)
FAILED: TASK1/20250301/data_042.jsonl (ServiceUnavailable: 503 ...)
FAILED: TASK1/20250301/scan_007.pdf (Timeout: ...)
```

### 4.3 클라이언트 안내

- 소규모: HTTP 에러 코드로 직접 피드백 (400, 404, 502)
- 대규모: `confirm()` 다이얼로그로 파일 수/용량 사전 고지 + 토스트 메시지

---

## 5. 성능 특성

| 항목 | 소규모 모드 | 대규모 모드 |
|------|-----------|-----------|
| 임계값 | <=500 파일 | >500 파일 |
| ZIP 방식 | zipfile (in-memory) | zipstream-ng (streaming) |
| 서버 메모리 | 파일 총 크기 | peak ~20MB |
| Content-Length | 제공 (진행률 표시) | 미제공 |
| 다운로드 방식 | fetch + blob | 브라우저 네이티브 |
| GCS 동시성 | asyncio.gather (병렬) | 순차 (lazy generator) |
| 에러 처리 | HTTP 에러 코드 | 빈 파일 대체 + 매니페스트 |
| 예상 시간 (19K 파일) | N/A (메모리 부족) | 30-60분 |

---

## 6. 향후 개선 가능 사항 (선택)

1. **병렬 pre-fetch**: `asyncio.Queue` 기반으로 다음 N개 파일을 사전 다운로드하여 처리 속도 향상
2. **Content-Length 사전 계산**: `ZIP_STORED` 전용 시 파일 크기 합산으로 다운로드 진행률 표시 가능
3. **gcloud CLI 복사 버튼**: 대규모 다운로드 시 `gcloud storage cp -r` 명령어를 클립보드에 복사하는 UX 옵션
4. **분할 다운로드**: 파일 수가 극단적(50K+)일 경우 날짜/서브폴더 단위 분할 다운로드 제안

---

## 7. 테스트 및 검증

### 7.1 의존성 설치

```bash
uv pip install "zipstream-ng>=1.8.0"
```

### 7.2 zipstream-ng lazy loading 검증

```python
# generator 기반 lazy loading 확인
from zipstream import ZipStream, ZIP_STORED

zs = ZipStream(compress_type=ZIP_STORED)

call_count = 0
def lazy_gen():
    global call_count
    call_count += 1
    yield b"hello"

zs.add(lazy_gen(), arcname="test.txt")
assert call_count == 0  # add 시점에는 미실행

list(zs)  # iteration 시점에 실행
assert call_count == 1  # 확인: lazy loading 동작
```

### 7.3 에러 매니페스트 검증

```python
# all_files() → 에러 매니페스트 → finalize() 순서 확인
zs = ZipStream(compress_type=ZIP_STORED)
zs.add(b"ok", arcname="good.txt")

data1 = b"".join(zs.all_files())
zs.add(b"error manifest", arcname="_ERRORS.txt")
data2 = b"".join(zs.finalize())

final_zip = data1 + data2  # 완전한 ZIP 파일
```

### 7.4 정적 분석

```bash
pyright app/services/gcs_service.py app/api/v1/endpoints/gcs.py  # 타입 체크
ruff check app/services/gcs_service.py app/api/v1/endpoints/gcs.py  # 린트
```

---

## 8. 요약

Phase 12에서 `zipstream-ng` 기반 실시간 스트리밍 다운로드를 구현하여:

- **19,000+ 파일(~1GB)도 안정적으로 ZIP 다운로드** 가능 (서버 메모리 ~20MB)
- **소규모/대규모 자동 분기**로 최적의 UX 제공
- **에러 허용 설계**로 일부 파일 실패에도 다운로드 계속 진행
- **Nginx 스트리밍 설정**으로 장시간 연결 안정성 확보
