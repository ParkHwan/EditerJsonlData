# 개선사항 리포트 (2026-03-23)

> Phase 14: 파일 단위 Lock, 편집 키 CRUD, 다운로드 간소화, 편집 흐름 개선 기록

---

## IMP-06: 파일 단위 Lock (Row → File)

| 항목 | 내용 |
|---|---|
| **목적** | JSONL 파일 하나의 동시 편집을 방지하여 데이터 무결성 강화 |
| **작업** | Lock 범위를 data_id(row) 단위에서 파일(file_id) 단위로 변경 |

### 변경 내용

| 변경 | 이전 | 이후 |
|---|---|---|
| Redis key | `lock:{file_id}:{row_idx}` | `lock:{file_id}` |
| Lock 단위 | 행(row) 하나 | JSONL 파일 전체 |
| save 후 Lock | save_data 후 Lock 해제 | **save_data 후 Lock 유지** |
| WebSocket init | row별 Lock 배열 전송 | 단일 Lock 객체 전송 |

### 동작 흐름

```
사용자A "편집" 클릭 → 에디터 페이지 → 자동 Lock 획득
  → 편집/저장 반복 (Lock 유지)
  → "편집 종료" 클릭 → Lock 해제 + 파일목록 이동

사용자B 같은 파일 접근 → "OO님이 편집 중입니다. 열람만 가능합니다." 배너 표시
```

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/services/lock_service.py` | `_get_key` → `lock:{file_id}`, row_idx 파라미터 전부 제거 |
| `app/api/v1/endpoints/editor.py` | Lock 엔드포인트 경로에서 row_idx 제거, save_data 시 Lock 해제 로직 삭제 |
| `app/api/v1/endpoints/websocket.py` | init 시 단일 Lock 객체 전송, lock_change 브로드캐스트 단순화 |
| `app/services/draft_service.py` | `delete_all_drafts_for_file(file_id, user_id)` 메서드 추가 |
| `app/services/audit_service.py` | ActionType에 `edit_end`, `gcs_file_download`, `gcs_jsonl_download`, `gcs_folder_download` 추가 |

---

## IMP-07: 편집 키 생성/삭제

| 항목 | 내용 |
|---|---|
| **목적** | 편집 중 JSON 객체의 키를 동적으로 추가/삭제할 수 있도록 기능 확장 |
| **작업** | add_info.unit_meta, 문제, 풀이 섹션에 키 CRUD 기능 구현 |

### 대상 섹션

| 섹션 | JSON 경로 | 예시 |
|---|---|---|
| 단원 메타 | `add_info.unit_meta` | 단원명, 학년 등 |
| 문제 | `add_info.문제` | 단일지문, 보기, 선택지 등 |
| 풀이 | `add_info.풀이` | 힌트, 풀이과정, 정답 등 |

### 키 생성 흐름

```
편집 모드 → "키 추가" 버튼 → 커스텀 모달에서 키명 입력
  → 중복 검증 (기존 키 + 이미 추가된 키 교차 확인)
  → 새 행 추가 (녹색 배경) → 값 입력 → 저장 시 서버 반영
```

### 키 삭제 흐름

```
편집 모드 → 키 옆 "×" 버튼 → confirm() 확인
  → 행 제거 (UI) → 저장 시 서버에서 키 삭제 반영
```

### 검증 로직

- **중복 키 방지**: `sectionData[key]` 존재 여부 + `addedKeys` Map + `deletedKeys` Set 교차 확인
- **삭제 확인**: `confirm()` 다이얼로그로 사용자 의사 재확인
- **변경 추적**: `deletedKeys` (Set), `addedKeys` (Map) → `collectInlineChanges()`에서 반영

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/services/render_service.py` | `KEY_EDITABLE_SECTIONS` 정의, `data-section`/`data-key` 속성, 키 추가/삭제 버튼 렌더링 |
| `app/templates/editor.html` | `addKeyToSection()`, `deleteKeyFromSection()`, `showKeyInputDialog()` 구현, CSS 추가 |

---

## IMP-08: 다운로드 기능 간소화

| 항목 | 내용 |
|---|---|
| **목적** | 불필요한 전체 파일(PNG/PDF) 다운로드 제거, JSONL만 지원하여 성능 부하 감소 |
| **작업** | 기존 ZipStream 대규모 다운로드 제거, JSONL 개별/전체 다운로드만 유지 |

### 변경 내용

| 변경 | 이전 (Phase 12) | 이후 (Phase 14) |
|---|---|---|
| 다운로드 대상 | PNG, PDF, JSONL 등 전체 | **JSONL만** |
| 대규모 다운로드 | ZipStream 스트리밍 (19K+ 파일) | **제거** |
| JSONL 전체 다운로드 | 없음 (전체에 포함) | **비동기 in-memory ZIP** |
| 서버 디스크 버퍼링 | 있음 (임시 파일 생성) | **없음** (메모리에서 직접 ZIP) |

### 신규 엔드포인트

| 엔드포인트 | 설명 |
|---|---|
| `GET /api/v1/gcs/download-jsonl-info?date_str=&task=` | JSONL 파일 개수/크기 사전 조회 |
| `GET /api/v1/gcs/download-jsonl-all?date_str=&task=` | 전체 JSONL ZIP 다운로드 |

### 제거된 엔드포인트

- `GET /api/v1/gcs/download-folder-info` — 전체 파일 다운로드 정보
- `GET /api/v1/gcs/download-folder` — ZipStream 전체 다운로드

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/api/v1/endpoints/gcs.py` | `_DOWNLOAD_TYPE_MAP`, `_parse_extensions` 제거, `download-jsonl-info`/`download-jsonl-all` 신설 |
| `app/templates/gcs_files.html` | "전체 다운로드" 버튼 제거, "JSONL 전체 다운로드" 버튼으로 교체 |

---

## IMP-09: 편집 흐름 개선

| 항목 | 내용 |
|---|---|
| **목적** | 편집 시작/종료/취소 UX 개선 및 CSRF 토큰 만료 에러 해소 |
| **작업** | 자동 Lock 획득, 편집 종료 시 파일목록 이동, working copy 보존 |

### 편집 시작 버튼 제거

| 변경 | 이전 | 이후 |
|---|---|---|
| 진입 방식 | 파일목록 "편집" → 에디터 → **"편집 시작" 클릭** → Lock 획득 | 파일목록 "편집" → 에디터 → **자동 Lock 획득** |
| UI | "편집 시작/종료" 토글 버튼 | "편집 종료" 버튼만 (Lock 보유 시) |

### 편집 종료 동작

```
"편집 종료" 클릭
  → 편집 중인 행 있으면: "저장 후 종료" / "무시 후 종료" confirm
  → Lock 해제 (DELETE /editor/lock/{file_id})
  → working copy는 Redis에 보존 (삭제하지 않음)
  → /gcs/browse/{날짜}?task={task_id} 로 forward navigation
```

### 편집 취소 동작

```
"편집 취소" 클릭
  → confirm("모든 변경사항이 폐기됩니다")
  → POST /editor/discard/{file_id}
    → Redis working copy 삭제
    → Lock 해제
    → Draft 삭제
  → /gcs/browse/{날짜}?task={task_id} 로 이동
```

### working copy 재사용 (open-edit 개선)

| 변경 | 이전 | 이후 |
|---|---|---|
| 재진입 시 | **항상 GCS에서 새로 로드** (기존 수정사항 소실) | **is_loaded() 체크 → 기존 working copy 재사용** |
| 응답 | `message: "편집 세션 시작"` | `message: "편집 세션 복원"` (resumed=true) |

### CSRF 토큰 에러 해소

| 변경 | 이전 | 이후 |
|---|---|---|
| 페이지 이동 | 없음 (페이지에 머무름) → 브라우저 Back 시 캐시된 페이지의 CSRF 토큰 만료 | **forward navigation** (`window.location.href`) → 새 페이지에서 새 CSRF 토큰 발급 |

### task_id 전달

| 파일 | 변경 내용 |
|---|---|
| `app/api/v1/endpoints/files.py` | `gcs_path`에서 GCS_TASKS 매칭으로 `task_id` 추출, `gcs_task` 템플릿 변수 전달 |
| `app/templates/editor.html` | `GCS_TASK` JS 변수 추가, 편집 종료/취소 시 날짜폴더 URL에 task 파라미터 포함 |

### 한글 IME 수정 (키 생성)

| 변경 | 이전 | 이후 |
|---|---|---|
| 키 입력 | 브라우저 `prompt()` | **커스텀 HTML 모달** (`showKeyInputDialog`) |
| 한글 처리 | 마지막 글자 누락 (IME 조합 미완성) | `e.isComposing` 체크로 조합 중 submit 방지 |

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/api/v1/endpoints/gcs.py` | open-edit: `is_loaded()` 체크 → 기존 working copy 재사용, `resumed` 응답 필드 |
| `app/api/v1/endpoints/files.py` | `gcs_task` 템플릿 변수 추가 (gcs_path → task_id 매핑) |
| `app/templates/editor.html` | 자동 Lock, 편집 종료→파일목록 이동, 커스텀 키 입력 모달, CSS 정리 |

---

## IMP-10: Starlette 1.0.0 호환성 + DuckDB 버전 고정

| 항목 | 내용 |
|---|---|
| **목적** | 의존성 업데이트로 인한 호환성 문제 사전 방지 |
| **작업** | TemplateResponse API 변경 대응, DuckDB 버전 핀 |

### Starlette TemplateResponse 변경

| 변경 | 이전 (Starlette <1.0) | 이후 (Starlette 1.0.0) |
|---|---|---|
| 시그니처 | `TemplateResponse("template.html", {"request": request, ...})` | `TemplateResponse(request, "template.html", {...})` |

### DuckDB 버전 핀

```toml
# pyproject.toml
duckdb>=1.5.0,<1.6.0  # 이전: duckdb>=1.2.0
```

> DB 파일 포맷이 메이저/마이너 버전 간 호환되지 않으므로 범위 제한 필수

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/api/v1/endpoints/files.py` | `TemplateResponse(request, template, context)` 형태로 7개 호출 수정 |
| `app/api/v1/endpoints/gcs.py` | 동일 수정 (해당 호출부) |
| `pyproject.toml` | `duckdb>=1.5.0,<1.6.0`, `requires-python>=3.12`, `target-version=py312` |

---

## IMP-11: editor.html UI 복원

| 항목 | 내용 |
|---|---|
| **목적** | Phase 14 개발 과정에서 누락/변경된 CSS 및 템플릿 블록 복원 |
| **작업** | content_full 블록, flexbox 속성, 인라인 편집 CSS 복원 |

### 변경 내용

| 항목 | 이전 (누락) | 복원 |
|---|---|---|
| 템플릿 블록 | `{% block content %}` (max-width: 1200px) | `{% block content_full %}` (전체 너비) |
| detail-panel | 속성 누락 | `min-height: 0`, `overflow-x: hidden` 추가 |
| #cardContainer | 속성 누락 | `overflow-x: hidden`, `word-break: break-word` 추가 |
| 인라인 편집 CSS | 일부 누락 | `.item.inline-editing` 녹색 테두리, `td.editable-value[contenteditable]` display:table-cell 등 복원 |

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/templates/editor.html` | 전체 너비 블록 복원, flexbox 속성 추가, 인라인 편집 CSS 복원 |

---

## 변경 파일 전체 요약

| 파일 | IMP | 설명 |
|---|---|---|
| `app/services/lock_service.py` | 06 | 파일 단위 Lock, row_idx 제거 |
| `app/api/v1/endpoints/editor.py` | 06 | Lock 엔드포인트 단순화, save 후 Lock 유지 |
| `app/api/v1/endpoints/websocket.py` | 06 | 단일 Lock 객체 전송 |
| `app/services/draft_service.py` | 06 | `delete_all_drafts_for_file()` 추가 |
| `app/services/audit_service.py` | 06 | ActionType 확장 |
| `app/services/render_service.py` | 07 | 키 CRUD 버튼 렌더링 |
| `app/api/v1/endpoints/gcs.py` | 08, 09, 10 | JSONL 다운로드 간소화, open-edit 재사용, TemplateResponse |
| `app/templates/gcs_files.html` | 08 | JSONL 다운로드 UI |
| `app/api/v1/endpoints/files.py` | 09, 10 | task_id 전달, TemplateResponse |
| `app/templates/editor.html` | 06, 07, 09, 11 | Lock UI, 키 CRUD, 편집 흐름, CSS 복원 |
| `pyproject.toml` | 10 | DuckDB 버전 핀, Python 3.12 |

### Git 커밋 이력

| 커밋 | IMP | 설명 |
|---|---|---|
| `eaf9b1c` | 06, 07, 11 | feat: 파일 단위 Lock + 편집 키 생성/삭제 + UI 복원 |
| `bb6abfc` | 08 | feat: 다운로드 간소화 — JSONL 개별/전체 다운로드만 유지 |
| `4f6c84c` | 10 | fix: Starlette 1.0.0 TemplateResponse API 호환성 + DuckDB 버전 고정 |
| `60d3280` | 09 | fix: 편집 종료/취소 흐름 개선 + 한글 키 입력 IME 수정 |
