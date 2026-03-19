# Phase 10: DuckDB 영구 메타데이터 저장소 — 구현 보고서

> 작성일: 2026-03-13  
> 패턴: Embedded Lakehouse + Log-centric Audit

---

## 1. 개요

기존 Redis 휘발성 저장소에만 의존하던 편집 메타데이터(생성자, 수정자, 완료자, 이력)를
DuckDB 임베디드 데이터베이스로 영구 보존하는 Phase 10을 구현하였다.

### 도입 목적

| 목적 | 설명 |
|------|------|
| 핵심 메타데이터 영구 보존 | 생성자, 수정자, 최종 완료자를 Redis TTL과 무관하게 보존 |
| 분석 쿼리 기반 | TASK 진행률, 사용자 활동, 일별 통계를 SQL로 즉시 조회 |
| 향후 확장성 | JSONL 원본 직접 분석, Parquet 내보내기, Polars 연동 |

### 저장소 역할 분리 (최종)

| 저장소 | 역할 | 변경 여부 |
|--------|------|----------|
| Redis | 편집 세션, 행 잠금, 드래프트, GCS 캐시 | 변경 없음 |
| DuckDB | 파일 카탈로그, 이벤트 요약, 사용자 프로필, 분석 뷰 | **신규** |
| GCS | JSONL 원본, 버전 히스토리, 이미지 | 변경 없음 |
| JSONL 감사 로그 | 상세 행위 기록 (diff, IP, User-Agent) | 변경 없음 (유지) |

---

## 2. 스키마

### 2.1 ENUM 타입

```sql
file_status: 'registered' | 'editing' | 'updated' | 'completed'
event_type:  'session_start' | 'row_save' | 'gcs_update' | 'session_discard' | 'review_complete' | 'bulk_register'
```

### 2.2 테이블 3개

| 테이블 | 역할 | PK |
|--------|------|-----|
| `users` | 사용자 영구 프로필 (로그인 시 UPSERT) | `user_id VARCHAR` |
| `file_registry` | 파일 생명주기 카탈로그 | `id INTEGER (SEQUENCE)` |
| `edit_events` | 경량 편집 이벤트 로그 | `id INTEGER (SEQUENCE)` |

### 2.3 분석 VIEW 5개

| VIEW | 용도 |
|------|------|
| `task_progress` | TASK별 파일 수, 완료율, 총 행 수 |
| `user_activity` | 사용자별 저장/업데이트/검수 횟수, 편집 파일 수 |
| `daily_activity` | 일별 활성 사용자, 저장/업데이트/검수 건수 |
| `file_edit_history` | 파일별 편집 이벤트 타임라인 |
| `hourly_heatmap` | 시간대별 작업 패턴 (요일 × 시간) |

### 2.4 DuckDB 네이티브 타입 활용

| 타입 | 적용 위치 | 효과 |
|------|----------|------|
| `ENUM` | status, event_type | 2~5x 압축률 향상, 잘못된 값 방지 |
| `STRUCT` | file_registry.content_stats | 데이터 카탈로그 차원 (total_rows, has_images, data_id_range) |
| `VARCHAR[]` (LIST) | edit_events.modified_fields | 변경 필드 구조화 저장, `list_contains()` 쿼리 가능 |
| `TIMESTAMPTZ` | 전체 timestamp 컬럼 | UTC 명시적 타임존 |

---

## 3. 구현 파일

### 3.1 신규 파일

| 파일 | 역할 |
|------|------|
| `app/db/duckdb_client.py` | DuckDB Singleton 연결 관리 + 2단계 Lock + CHECKPOINT + 스키마 자동 초기화 |
| `app/services/metadata_service.py` | file_registry / edit_events / users CRUD + asyncio.to_thread() 래핑 |

### 3.2 수정 파일

| 파일 | 변경 내용 |
|------|----------|
| `app/core/config.py` | `DUCKDB_PATH = "data/editor.duckdb"` 설정 추가 |
| `app/main.py` | lifespan에 DuckDB startup/shutdown 추가 |
| `app/api/v1/endpoints/auth.py` | 로그인 시 `metadata_service.upsert_user()` 호출 |
| `app/api/v1/endpoints/gcs.py` | open-edit 시 `metadata_service.on_session_start()` 호출, logger import 추가 |
| `app/api/v1/endpoints/editor.py` | 행 저장/GCS 업데이트/편집 취소 시 각각 DuckDB 이벤트 기록 |
| `pyproject.toml` | `duckdb>=1.2.0` 의존성 추가 |

---

## 4. 데이터 기록 시점

| 사용자 액션 | DuckDB 기록 |
|------------|------------|
| 로그인 | `users` UPSERT (display_name, login_count, last_login_at) |
| GCS 파일 열기 (open-edit) | `file_registry` UPSERT (status='editing') + `edit_events`(session_start) |
| 행 저장 | `file_registry` UPDATE (last_modified_by/at) + `edit_events`(row_save) |
| GCS 파일 업데이트 | `file_registry` UPDATE (status='updated', update_count++) + `edit_events`(gcs_update) |
| 편집 취소 | `file_registry` UPDATE (status 복원) + `edit_events`(session_discard) |

---

## 5. 안전성 설계

### 5.1 Non-blocking

모든 DuckDB 호출은 `try/except`로 감싸져 있어 DuckDB 장애 시에도 기존 Redis/GCS 기능에 영향 없음.

```python
try:
    await metadata_service.on_row_save(...)
except Exception as e:
    logger.warning("DuckDB row_save record failed (non-blocking): %s", e)
```

### 5.2 2단계 Lock

| Level | 메커니즘 | 보호 범위 |
|-------|---------|----------|
| 1 | `threading.Lock` | 프로세스 내 스레드 간 |
| 2 | `fcntl.flock()` | Gunicorn 멀티 워커(프로세스) 간 |

Lock 파일: `data/editor.duckdb.lock`

### 5.3 CHECKPOINT

`DuckDBClient.close()` 호출 시 `CHECKPOINT` → `close` 순서 실행.
WAL에 남은 데이터를 메인 `.duckdb` 파일로 완전 플러시.

### 5.4 Lifespan 연동

```
Startup:  Redis connect → DuckDB init (스키마 자동 생성)
Shutdown: DuckDB CHECKPOINT+close → Redis close
```

---

## 6. 의존성

```
duckdb==1.5.0  (설치됨)
```

---

## 7. DB 파일 위치

```
data/editor.duckdb       ← 메인 DB 파일 (서버 시작 시 자동 생성)
data/editor.duckdb.lock  ← fcntl 파일 락 (쓰기 직렬화)
data/editor.duckdb.wal   ← WAL 파일 (DuckDB 자동 관리)
```

---

## 8. 검증 결과

- DuckDB 스키마 생성: ENUM, SEQUENCE, TABLE, INDEX, VIEW 전체 정상 생성 확인
- UPSERT (ON CONFLICT): users 테이블 정상 동작 확인
- STRUCT 타입 INSERT/SELECT: content_stats 정상 동작 확인
- LIST 타입 INSERT/SELECT: modified_fields 정상 동작 확인
- CHECKPOINT + close: WAL 플러시 정상 확인
- Pyright 타입 체크: 전체 수정 파일 0 error (google.cloud.exceptions 기존 이슈 제외)
- 린터 에러: 없음
