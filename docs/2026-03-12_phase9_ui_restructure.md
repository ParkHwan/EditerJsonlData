# Phase 9: UI 재구조화 + TASK 분류 + GCS Diff 뷰

## 1. 네비게이션 재구조화

| 변경 전 | 변경 후 |
|---|---|
| 파일 목록 (로컬 data/) | 파일 목록 (TASK 선택) |
| GCS 관리 | 수정 이력 (GCS 버전 Diff) |

- 로컬 파일 목록 페이지 제거
- "파일 목록" = TASK 선택 → 날짜 폴더 → 파일 목록 → 에디터
- "수정 이력" = GCS 버전 기반 Diff 뷰

## 2. TASK별 GCS 분류

| TASK ID | 이름 | GCS Prefix |
|---|---|---|
| task1 | 교재 | `manual/task1/YYYYMMDD/` |
| task2 | 인문논술 | `manual/task2/YYYYMMDD/` |
| task3 | 수리논술 | `manual/task3/YYYYMMDD/` |

- `config.py`의 `GCS_TASKS` dict로 관리
- `gcs_service._build_prefix(task_id=)` → TASK별 prefix 동적 결정
- 모든 browse 엔드포인트에 `?task=task1` 쿼리 파라미터 전달

## 3. 수정 이력 (GCS Versioning Diff)

GCS Object Versioning 활성화 상태에서 버전 비교 기능 제공.

### 엔드포인트

| 경로 | 설명 |
|---|---|
| `GET /gcs/history?task=` | 수정 이력 메인 페이지 (HTML) |
| `GET /gcs/versions/{gcs_path}` | 특정 파일의 버전 목록 (JSON) |
| `GET /gcs/diff?gcs_path=&gen_a=&gen_b=` | 두 버전 JSONL diff (JSON) |

### Diff 로직

1. `list_blobs(versions=True)` → 전체 버전 (current + noncurrent) 조회
2. 사용자가 2개 버전의 generation 선택
3. 각 버전의 JSONL 텍스트 다운로드
4. `data_id` 기준으로 행 매칭 → 추가/삭제/수정 판별
5. 수정된 행은 변경된 필드 목록 표시

### UI 구성

- TASK 필터 탭
- 수정된 파일 목록 (버전 2개 이상)
- 파일 클릭 → 버전 목록 패널 → 2개 선택 → Diff 뷰

## 변경된 파일

| 파일 | 변경 내용 |
|---|---|
| `app/core/config.py` | `GCS_TASKS` dict 추가 |
| `app/services/gcs_service.py` | `_build_prefix(task_id=)`, `list_blob_versions()`, `download_blob_version()`, `list_versioned_files()` |
| `app/api/v1/endpoints/gcs.py` | browse에 task 파라미터, `/history`, `/versions`, `/diff` 엔드포인트 |
| `app/api/v1/endpoints/files.py` | `list_files()` → TASK 선택 페이지로 변경 |
| `app/templates/base.html` | 네비게이션 바: "GCS 관리" → "수정 이력" |
| `app/templates/index.html` | TASK 선택 카드 UI |
| `app/templates/gcs_browse.html` | TASK별 날짜 폴더 (업로드 섹션 제거) |
| `app/templates/gcs_files.html` | task 파라미터 유지, 로컬 상태 열 제거 |
| `app/templates/gcs_history.html` | **신규** — 수정 이력 + Diff 뷰 |
