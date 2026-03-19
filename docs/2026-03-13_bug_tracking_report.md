# BUG 추적 리포트 — Phase 10.x (DuckDB 도입 이후)

**작성일**: 2026-03-13 (최종 갱신: 2026-03-14)  
**작성자**: AI Assistant + 사용자 협업  
**대상 프로젝트**: EditerJsonlData  
**환경**: Docker Compose (macOS Darwin 25.3.0)

---

## 목차

1. [BUG 요약 타임라인](#1-bug-요약-타임라인)
2. [BUG-01: 파일목록 상태/수정이력/최종수정자 미표시](#2-bug-01-파일목록-상태수정이력최종수정자-미표시)
3. [BUG-02: Diff 뷰에서 미수정 필드까지 변경 표시](#3-bug-02-diff-뷰에서-미수정-필드까지-변경-표시)
4. [BUG-03: TASK 재선택 시 GCS 전체 재조회 (딜레이 재발)](#4-bug-03-task-재선택-시-gcs-전체-재조회-딜레이-재발)
5. [BUG-04: DuckDB current_timestamp Binder Error](#5-bug-04-duckdb-current_timestamp-binder-error)
6. [BUG-05: DuckDB 파일 손상 (MarkBlockAsModified)](#6-bug-05-duckdb-파일-손상-markblockasmodified)
7. [BUG-06: pytz 미설치로 DuckDB TIMESTAMPTZ 반환 실패](#7-bug-06-pytz-미설치로-duckdb-timestamptz-반환-실패)
8. [교훈 및 재발 방지](#8-교훈-및-재발-방지)

---

## 1. BUG 요약 타임라인

| 순서 | 버그 코드 | 에러 메시지 (요약) | 영향 범위 | 발견일 | 상태 |
|:---:|:---:|---|---|:---:|:---:|
| 1 | BUG-01 | 파일목록에서 상태/수정이력/최종수정자가 전부 `-`로 표시 | gcs_files.html, metadata_service.py | 03-13 | **해결** |
| 2 | BUG-02 | Diff 뷰에서 중첩 JSON의 상위 객체 전체가 변경으로 표시 | gcs_history.html, gcs.py | 03-13 | **해결** |
| 3 | BUG-03 | TASK 재선택 시 GCS API를 다시 호출하여 로딩 지연 | gcs.py | 03-13 | **해결** |
| 4 | BUG-04 | `Binder Error: Table does not have a column "current_timestamp"` | metadata_service.py | 03-13 | **해결** |
| 5 | BUG-05 | `MarkBlockAsModified called with already modified block` | data/editor.duckdb | 03-13 | **해결** |
| 6 | BUG-06 | `ModuleNotFoundError: No module named 'pytz'` | Docker image, duckdb | 03-14 | **해결** |

---

## 2. BUG-01: 파일목록 상태/수정이력/최종수정자 미표시

### 증상

파일목록 페이지(`gcs_files.html`)에서 모든 파일의 상태, 수정이력, 최종수정자 컬럼이 `-`로 표시됨.
실제로 편집한 파일도 동일하게 빈 값.

### 근본 원인 (복합)

1. **DuckDB SQL의 `current_timestamp` 오인식 (→ BUG-04)**: `INSERT ... ON CONFLICT DO UPDATE SET updated_at = current_timestamp`에서 DuckDB가 `current_timestamp`를 컬럼명으로 해석하여 `users`, `registry_sync` 테이블 INSERT가 전부 실패
2. **Jinja2 `|default()` vs `or` 차이**: `|default('-')`는 `None` 값을 처리하지 못해 `"None"` 문자열 렌더링
3. **`last_modified_by`가 user_id 반환**: `display_name`이 아닌 `user_id`를 표시하고 있었음
4. **pytz 미설치 (→ BUG-06)**: DuckDB `TIMESTAMPTZ` 반환 시 `pytz` 필요 → 예외 발생 → GCS fallback

### 수정 파일 및 내용

| 파일 | 수정 내용 |
|------|----------|
| `app/services/metadata_service.py` | `current_timestamp` → `now()` (6곳), `LEFT JOIN users`로 display_name 조회, Python-side `None` 방어 |
| `app/templates/gcs_files.html` | `\|default('-')` → `or '-'`, `update_count` 표시 로직 수정 |
| `pyproject.toml` | `"pytz>=2024.1"` 추가 |

### 검증 방법

```bash
# HTTP API로 파일목록 호출
curl -b cookies.txt "http://localhost:8000/api/v1/gcs/browse/20260311?task=task1"
# 응답 HTML에서 status-badge 확인
grep 'status-badge' response.html
# 결과: status-editing(편집중), status-registered(미편집) 정상 표시
```

---

## 3. BUG-02: Diff 뷰에서 미수정 필드까지 변경 표시

### 증상

버전 비교(Diff) 뷰에서 `add_info.unit_meta.중단원` 하나만 수정했는데, `add_info` 객체 전체가 "변경됨"으로 하이라이트됨.

### 근본 원인

기존 Diff 로직이 **최상위 필드 단위**로만 비교했기 때문에, 중첩 객체 내부의 leaf 변경이 상위 객체 전체 변경으로 감지됨.

### 수정 파일 및 내용

| 파일 | 수정 내용 |
|------|----------|
| `app/api/v1/endpoints/gcs.py` | `_deep_diff()` 재귀 함수 추가 — 중첩 dict/list를 leaf까지 탐색하여 정확한 변경 경로 반환 |
| `app/templates/gcs_history.html` | `renderDeepDiff()` JS 함수 추가 — `path`, `old`, `new` 테이블로 변경점만 표시 |

### _deep_diff 알고리즘

```python
def _deep_diff(old, new, path="") -> list[dict]:
    """중첩 구조를 재귀적으로 비교하여 변경된 leaf 노드만 반환"""
    if isinstance(old, dict) and isinstance(new, dict):
        for key in (old.keys() | new.keys()):
            _deep_diff(old.get(key), new.get(key), f"{path}.{key}")
    elif isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            _deep_diff(old[i] if i < len(old) else None,
                       new[i] if i < len(new) else None, f"{path}[{i}]")
    elif old != new:
        changes.append({"path": path, "old": old, "new": new})
```

### 검증 방법

수정 이력 페이지에서 버전 비교 시, `add_info.unit_meta.중단원` 변경 사항만 테이블에 표시됨을 확인.

---

## 4. BUG-03: TASK 재선택 시 GCS 전체 재조회 (딜레이 재발)

### 증상

파일 편집 후 TASK 선택 화면으로 돌아가 다시 같은 TASK를 선택하면, DuckDB에 데이터가 있음에도 GCS API를 전체 호출하여 수 초의 딜레이 발생.

### 근본 원인

`gcs_browse` 엔드포인트가 `has_any_folders(task)` 대신 매번 GCS를 조회하는 이전 로직이 남아있었음.

### 수정 파일 및 내용

| 파일 | 수정 내용 |
|------|----------|
| `app/api/v1/endpoints/gcs.py` | DuckDB-first 패턴 적용: `has_any_folders()` → 즉시 DB 서빙, `needs_folder_sync_today()` → 백그라운드 sync |

### 수정 후 성능

| 시점 | 응답 시간 |
|------|----------|
| DuckDB-first (2차 접근) | **~15ms** |
| GCS 직접 조회 (이전) | **2~5초** |

---

## 5. BUG-04: DuckDB current_timestamp Binder Error

### 증상

DuckDB `INSERT ... ON CONFLICT DO UPDATE SET` 구문에서 `current_timestamp`를 사용하면 쿼리가 조용히 실패. 서버 로그에는 별도 에러 없이 데이터 미입력.

### 근본 원인

DuckDB의 `ON CONFLICT DO UPDATE SET` 절 내에서 `current_timestamp`가 SQL 키워드/함수가 아닌 **컬럼명으로 해석**됨.

```sql
-- 실패하는 코드
INSERT INTO users (...) VALUES (...)
ON CONFLICT DO UPDATE SET updated_at = current_timestamp
-- Binder Error: Table "users" does not have a column named "current_timestamp"
```

### 수정 내용

```sql
-- 수정된 코드 (now() 함수 사용)
INSERT INTO users (...) VALUES (...)
ON CONFLICT DO UPDATE SET updated_at = now()
```

### 영향 범위

`metadata_service.py` 내 6곳:
- `_upsert_user_sync` (2곳)
- `_update_file_status_sync` (2곳)
- `_sync_files_from_gcs_sync` (2곳)

---

## 6. BUG-05: DuckDB 파일 손상 (MarkBlockAsModified)

### 증상

DuckDB 쿼리 실행 시 `duckdb::InternalException: MarkBlockAsModified called with already modified block id 22` 에러 발생.

### 근본 원인

동시 접근 또는 비정상 종료로 인한 DuckDB 데이터 파일 손상.

### 수정 방법

```bash
# 1. 기존 DB 백업
cp data/editor.duckdb data/editor.duckdb.bak

# 2. 손상 파일 제거
rm data/editor.duckdb data/editor.duckdb.lock data/editor.duckdb.wal

# 3. 서버 재시작 → 스키마 자동 재생성
docker compose restart web
```

### 재발 방지

- DuckDBClient에 `fcntl.flock` 기반 2단계 잠금 적용
- 서버 종료 시 `CHECKPOINT` 실행으로 WAL 플러시
- Docker 볼륨 `./data:/app/data`로 DB 영속성 확보

---

## 7. BUG-06: pytz 미설치로 DuckDB TIMESTAMPTZ 반환 실패

### 증상

파일목록 페이지에서 상태/수정이력/최종수정자가 모두 `-`로 표시. 서버 로그:

```
DuckDB file listing failed, falling back to GCS: Invalid Input Error:
Required module 'pytz' failed to import, due to the following Python exception:
ModuleNotFoundError: No module named 'pytz'
```

### 근본 원인

DuckDB가 `TIMESTAMPTZ` 타입 컬럼을 Python 객체로 변환할 때 `pytz` 라이브러리가 필수.
Docker 이미지에 해당 패키지가 포함되지 않았음.

### 왜 발견이 늦었나

1. `try/except`로 감싸진 DuckDB 호출이 예외를 잡아 **GCS fallback**으로 빠짐
2. GCS fallback은 파일 목록만 반환하고 메타데이터(상태/수정자)는 없음
3. 표면적으로는 파일 목록이 표시되지만 메타데이터가 전부 `-`
4. BUG-01, BUG-04의 수정 사항이 실제로 적용되었음에도, `pytz` 부재로 쿼리 자체가 실행되지 않았음

### 수정 파일 및 내용

| 파일 | 수정 내용 |
|------|----------|
| `pyproject.toml` | `"pytz>=2024.1"` 의존성 추가 |

### 적용 방법

```bash
docker compose build web && docker compose up -d
```

### 검증 결과

```
# 수정 전 (GCS fallback)
<span class="status-badge">-</span>

# 수정 후 (DuckDB 정상 서빙)
<span class="status-badge status-editing">편집중</span>
<span class="status-badge status-registered">미편집</span>
```

응답 시간: **15ms** (DuckDB-first, GCS 호출 없음)

---

## 8. 교훈 및 재발 방지

### 의존성 관리

| 교훈 | 대응 |
|------|------|
| DuckDB의 `TIMESTAMPTZ` → Python 변환에 `pytz` 필수 | `pyproject.toml`에 명시적 추가 |
| Docker 재빌드 없이도 코드 변경 반영 (`--reload` + 볼륨 마운트) | 새 패키지 추가 시 반드시 `docker compose build` 필요 |
| 의존성 누락은 런타임에서만 발견 가능 | CI/CD에 `pip check` 또는 통합 테스트 추가 권장 |

### DuckDB 특이사항

| 교훈 | 대응 |
|------|------|
| `UPSERT` 구문에서 `current_timestamp` → 컬럼명으로 오인 | `now()` 함수 사용 |
| 동시 접근/비정상 종료로 파일 손상 가능 | `fcntl.flock` + 종료 시 `CHECKPOINT` |
| 단일 프로세스 전용 (read_only 모드도 lock 경합) | 외부 도구로 DB 직접 접근 금지 |

### 에러 핸들링

| 교훈 | 대응 |
|------|------|
| `try/except` 내 fallback이 근본 에러를 숨김 | 로그 레벨을 `WARNING` → `ERROR`로 상향, 에러 메시지 상세화 |
| Jinja2 `\|default()` ≠ `or` (None 처리 차이) | Python 서비스 레이어에서 `None` 방어 후 템플릿 전달 |
