# Phase 8: GCS Redis 기반 직접 편집

## 개요

GCS에 있는 JSONL 파일을 **로컬 다운로드 없이** Redis를 통해 직접 편집하는 아키텍처.

- 편집 중인 데이터는 Redis에만 존재 (로컬 파일 I/O 없음)
- 사용자가 명시적으로 "GCS에 발행"을 클릭해야 GCS에 반영
- 편집 취소 시 변경사항 폐기 (GCS 원본 무영향)

## 데이터 흐름

```
[GCS JSONL] ─── open-edit ───▶ [Redis Hash (행별)] ─── 편집 ───▶ [Redis Hash]
                                                                       │
                                                           publish ◀───┘
                                                               │
                                                    [GCS JSONL 덮어쓰기]
```

### 1단계: GCS → Redis 로드
- 엔드포인트: `POST /api/v1/gcs/open-edit`
- 요청: `{ gcs_path, date_str }`
- 동작: GCS에서 JSONL 텍스트 다운로드 → 행별 파싱 → Redis Hash 저장

### 2단계: 편집 (Redis)
- 기존 에디터와 동일한 UI/UX
- `GET /api/v1/editor/data/{file_id}/{row_idx}` → Redis에서 읽기
- `PUT /api/v1/editor/data/{file_id}/{row_idx}` → Redis에 저장 (GCS 미반영)
- Optimistic Locking 유지 (`_version` 필드)

### 3단계: GCS 발행
- 엔드포인트: `POST /api/v1/editor/publish/{file_id}`
- 동작: Redis의 모든 행 → JSONL 재구성 → GCS 업로드 (덮어쓰기)
- 발행 시 `_version`, `_last_edited_by`, `_last_edited_at` 내부 필드 제거

### 4단계: 편집 취소
- 엔드포인트: `POST /api/v1/editor/discard/{file_id}`
- 동작: Redis working copy 삭제 → GCS 브라우저로 리다이렉트

## Redis 키 구조

| 키 | 타입 | 설명 |
|---|---|---|
| `gcs_wc:{file_id}:rows` | Hash | `{ "0": json, "1": json, ... }` |
| `gcs_wc:{file_id}:meta` | Hash | `{ gcs_path, date_str, total_rows, loaded_at }` |

- TTL: 24시간 (편집 활동 시 자동 갱신)

## 모드 판별 로직

| 계층 | 방식 |
|---|---|
| 서버 (editor.py) | `gcs_edit_service.is_loaded(file_id)` → Redis에 working copy 존재 여부 |
| 서버 (files.py) | URL 파라미터 `?mode=gcs` |
| 클라이언트 (JS) | `EDIT_MODE` 변수 (`'gcs'` / `'local'`) |

## 변경된 파일

| 파일 | 변경 내용 |
|---|---|
| `app/services/gcs_edit_service.py` | **신규** — GCSEditService (Redis CRUD + GCS 발행) |
| `app/api/v1/endpoints/editor.py` | GCS/로컬 이중 모드 분기, publish/discard 엔드포인트, auto-upload 제거 |
| `app/api/v1/endpoints/gcs.py` | `/open-edit` 엔드포인트 추가 |
| `app/api/v1/endpoints/files.py` | `mode=gcs` 파라미터, GCS 모드 시 Redis 조회 |
| `app/templates/editor.html` | GCS 발행/취소 버튼, EDIT_MODE 변수, 모드별 토스트 메시지 |
| `app/templates/gcs_files.html` | openEditor() → /open-edit API 사용 (로컬 다운로드 제거) |

## 이전 방식과 비교

| 항목 | Phase 7 (이전) | Phase 8 (현재) |
|---|---|---|
| 편집 저장소 | 로컬 파일 (data/*.jsonl) | Redis Hash |
| GCS 반영 시점 | 매 행 저장마다 auto-upload | 사용자가 "GCS에 발행" 클릭 시 |
| 로컬 파일 필요 | 필수 (다운로드 후 편집) | 불필요 |
| 편집 취소 | 없음 (이미 GCS에 반영) | 가능 (Redis 삭제) |
| 기존 로컬 편집 | 동일 | 완전 호환 유지 |
