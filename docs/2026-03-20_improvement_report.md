# 개선사항 리포트 (2026-03-20)

> GCP VM 배포 준비, 운영 최적화, GCS 동기화, 수식 렌더링 개선 기록

---

## IMP-01: GitHub 연동 및 버전 관리

| 항목 | 내용 |
|---|---|
| **목적** | 코드 이력 관리 및 협업 기반 마련 |
| **작업** | Git 초기화, `.gitignore` 구성, GitHub 원격 저장소 연결 |
| **저장소** | `https://github.com/ParkHwan/EditerJsonlData.git` |

### `.gitignore` 주요 항목

- DuckDB 파일: `data/*.duckdb`, `data/*.duckdb.*`
- 임시 다운로드: `data/gcs_dl_*`, `data/*.zip`
- Serena/Ruff 캐시: `.serena/`, `.ruff_cache/`

---

## IMP-02: Docker 이미지 최적화

| 항목 | 내용 |
|---|---|
| **목적** | 이미지 크기 최소화 및 빌드 속도 향상 |
| **작업** | `.dockerignore` 생성, `Dockerfile` 최적화, Python 버전 업그레이드 |

### 변경 내용

| 변경 | 이전 | 이후 |
|---|---|---|
| Python 버전 | 3.10-slim | **3.12-slim** |
| 패키지 매니저 | pip | **uv** (`ghcr.io/astral-sh/uv`) |
| 의존성 레이어 | `COPY . .` | `COPY pyproject.toml` → 설치 → `COPY app/` (캐시 활용) |
| 헬스체크 | 없음 | `HEALTHCHECK` 지시어 추가 |

### `.dockerignore` 주요 제외 대상

```
.git, .venv, __pycache__, tests, docs, .env*, data, docker-compose*.yml, nginx/, redis/
```

### 변경 파일

- `Dockerfile` — 최적화 재작성
- `.dockerignore` — 신규 생성
- `pyproject.toml` — `requires-python >= 3.12`, `target-version = py312`

---

## IMP-03: GCP Artifact Registry 기반 배포 파이프라인

| 항목 | 내용 |
|---|---|
| **목적** | 로컬 → GCP VM 배포 워크플로우 표준화 |
| **작업** | Artifact Registry 연동, docker-compose.prod.yml 수정 |

### 배포 흐름

```
로컬 빌드(buildx, linux/amd64)
  → Push to asia-northeast1-docker.pkg.dev/crowdworks-platform/editer-jsonl/web
    → VM에서 docker compose pull & up
```

### 변경 파일

- `docker-compose.prod.yml` — `build: .` → `image: ${DOCKER_IMAGE:-asia-northeast1-docker.pkg.dev/...}`
- `docker-compose.yml` — 로컬 개발용 이미지 참조 분리

---

## IMP-04: GCS 수동 동기화 API

| 항목 | 내용 |
|---|---|
| **목적** | 파일 업로드 후 즉시 반영 가능하도록 수동 동기화 메커니즘 추가 |
| **작업** | REST API 엔드포인트 + UI 버튼 + API 키 인증 |

### 신규 엔드포인트

```
POST /api/v1/gcs/sync?task={task_id}
```

| 파라미터 | 설명 |
|---|---|
| `task` (query, 필수) | 동기화할 task ID (`task1`, `task2`, `task3`) |
| `X-Sync-Key` (header, 선택) | API 키 인증 (세션 쿠키 대체) |

### 인증 방식 (이중 인증)

1. **세션 쿠키**: 브라우저에서 로그인한 사용자 (UI 동기화 버튼)
2. **API 키**: `X-Sync-Key` 헤더 (업로드 스크립트용, 세션 불필요)

```python
# hmac.compare_digest로 타이밍 공격 방지
if x_sync_key and settings.SYNC_API_KEY:
    if not hmac.compare_digest(x_sync_key, settings.SYNC_API_KEY):
        raise HTTPException(status_code=403)
```

### 동작 흐름

```
POST /api/v1/gcs/sync?task=task1
  ↓
1. registry_sync 테이블에서 해당 task 기록 삭제 (clear_sync_record)
2. GCS에서 list_date_folders → list_files (블로킹)
3. DuckDB file_registry에 upsert
4. JSON 응답: { folders_synced, files_synced }
```

### Rate Limit

- `5/minute` (남용 방지)

### 업로드 스크립트 호출 예시

```bash
curl -X POST "http://VM_IP/api/v1/gcs/sync?task=task1" \
  -H "X-Sync-Key: <SYNC_API_KEY>"
```

```python
import requests

resp = requests.post(
    "http://VM_IP/api/v1/gcs/sync",
    params={"task": "task1"},
    headers={"X-Sync-Key": "<SYNC_API_KEY>"},
)
print(resp.json())
```

### UI 동기화 버튼

- GCS 브라우저 페이지(`gcs_browse.html`) 상단에 "GCS 동기화" 버튼 추가
- 클릭 → `POST /api/v1/gcs/sync` 호출 → 결과 alert → 페이지 새로고침

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/api/v1/endpoints/gcs.py` | `POST /sync` 엔드포인트 추가, 이중 인증 (세션/API 키) |
| `app/services/metadata_service.py` | `clear_sync_record()` 메서드 추가 |
| `app/templates/gcs_browse.html` | 동기화 버튼 UI + JavaScript |
| `app/core/config.py` | `SYNC_API_KEY` 설정 추가 |
| `.env.production` | `SYNC_API_KEY` 값 설정 |

---

## IMP-05: MathJax → KaTeX 수식 렌더링 마이그레이션

| 항목 | 내용 |
|---|---|
| **목적** | 수식 렌더링 속도 개선 및 `$...$` 구분자 안정적 지원 |
| **작업** | MathJax 3 → KaTeX 0.16.21 교체 |

### 성능 비교

| 항목 | MathJax 3 | KaTeX |
|---|---|---|
| 렌더링 속도 | 수백ms~ | 수ms |
| 번들 크기 | ~700KB+ | ~300KB |
| SSR 지원 | 제한적 | 완전 |
| 에러 처리 | 페이지 중단 가능 | `throwOnError: false`로 안전 표시 |

### KaTeX 설정 (참조: `jsonl-editor_전체.html`)

```javascript
window.__katexOptions = {
    delimiters: [
        {left: '$$', right: '$$', display: true},
        {left: '$', right: '$', display: false},
        {left: '\\[', right: '\\]', display: true},
        {left: '\\(', right: '\\)', display: false}
    ],
    throwOnError: false,
    errorColor: '#cc0000',
    strict: false,
    trust: false,
    macros: {
        "\\textrm": "\\mathrm",
        "\\textbf": "\\mathbf"
    }
};
```

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/templates/base.html` | MathJax CDN → KaTeX CDN (CSS + JS + auto-render), 전역 옵션 설정 |
| `app/templates/editor.html` | `MathJax.typesetPromise()` → `renderMathInElement()` (2곳) |
| `app/services/render_service.py` | 주석 업데이트 (MathJax → KaTeX) |

### `$...$` 에러 원인 참고

교육용 수학 콘텐츠에서 인접한 수식이 `$69$$= 92$` 형태로 이어지면 `$$`가 디스플레이 수학 모드로 오인식되어 "Missing open brace for subscript" 에러 발생.
KaTeX의 `throwOnError: false` + `strict: false` 설정으로 에러 시 빨간 텍스트로 표시되며 페이지는 정상 동작.

---

## 변경 파일 전체 요약

| 파일 | BUG | IMP | 설명 |
|---|---|---|---|
| `Dockerfile` | 02 | 02 | 사용자 제거, Python 3.12, uv, 레이어 최적화 |
| `.dockerignore` | | 02 | 신규 생성 |
| `pyproject.toml` | | 02 | Python 3.12 타겟 |
| `.gitignore` | | 01 | DuckDB, 임시파일 제외 |
| `docker-compose.prod.yml` | 04 | 03 | HTTP-only, Artifact Registry 이미지 |
| `docker-compose.yml` | | 03 | 로컬 이미지 분리 |
| `.env.production` | 05, 06, 07 | 04 | WORKERS, 쿠키, GCS 경로, SYNC_API_KEY |
| `app/main.py` | 08 | | Path import |
| `app/core/config.py` | | 04 | SYNC_API_KEY |
| `app/api/v1/endpoints/gcs.py` | | 04 | sync 엔드포인트 |
| `app/services/metadata_service.py` | | 04 | clear_sync_record |
| `app/templates/gcs_browse.html` | | 04 | 동기화 버튼 |
| `app/templates/base.html` | | 05 | KaTeX CDN |
| `app/templates/editor.html` | | 05 | renderMathInElement |
| `app/services/render_service.py` | | 05 | 주석 수정 |
| `nginx/nginx-http-only.conf` | 04 | | HTTP-only 설정 |
| `redis/sentinel.conf` | 03 | | Sentinel 설정 |
