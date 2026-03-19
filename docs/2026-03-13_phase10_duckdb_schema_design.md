# Phase 10: DuckDB 영구 저장소 스키마 설계

> 작성일: 2026-03-13  
> 상태: 설계 문서 v2 (검토 반영)  
> 설계 패턴: Embedded Lakehouse + Log-centric Audit

---

## 0. 설계 검토 결과 (v1 → v2 변경 사항)

v1 스키마를 아래 6개 기준으로 검토하여 개선했습니다.

### 검토 기준 및 판정

| # | 검토 항목 | v1 판정 | v2 개선 |
|---|----------|---------|---------|
| 1 | **DuckDB 네이티브 타입 활용** | ❌ VARCHAR로 status/event_type 저장 | ✅ ENUM 타입 적용 (컬럼 압축률 향상, 오타 방지) |
| 2 | **Denormalized Counter 안티패턴** | ❌ users.total_edits/total_updates 수동 관리 | ✅ 카운터 제거 → VIEW로 실시간 집계 (OLAP 원칙) |
| 3 | **GCS 직접 쿼리 (httpfs)** | ❌ 로컬 파일만 read_json_auto 예시 | ✅ httpfs + GCS Secret 설정 추가 |
| 4 | **데이터 카탈로그 차원 부재** | ❌ 파일 생명주기만 추적 | ✅ content_stats 컬럼 추가 (STRUCT 타입) |
| 5 | **TIMESTAMP 타임존** | ❌ TIMESTAMP (타임존 없음) | ✅ TIMESTAMPTZ (UTC 기준 명시적) |
| 6 | **벌크 등록 전략 부재** | ❌ 개별 INSERT만 고려 | ✅ GCS 스캔 → 일괄 등록 패턴 추가 |
| 7 | **프로세스 간 Lock** | ⚠️ threading.Lock만 (단일 프로세스) | ✅ fcntl.flock 2단계 Lock (프로세스 간 안전) |
| 8 | **종료 시 WAL 플러시** | ❌ close()만 호출 | ✅ CHECKPOINT → close 순서 (WAL 데이터 보호) |

### v1에서 잘 설계된 부분 (유지)

- **저장소 역할 분리** (Redis / DuckDB / GCS / JSONL Audit): Separation of Concerns 원칙에 부합
- **edit_events의 gcs_path 비정규화**: 분석 쿼리 성능 향상 (JOIN 회피)
- **FILTER (WHERE ...) 구문 활용**: DuckDB Vectorized Execution에 최적화된 집계 패턴
- **분석 VIEW 4종**: task_progress, user_activity, daily_activity, file_edit_history

---

## 1. 배경 및 목적

### 1.1 현재 아키텍처의 한계

| 저장소 | 역할 | 문제점 |
|--------|------|--------|
| **Redis** | 편집 세션, 잠금, 드래프트, GCS 캐시 | 휘발성 — 재시작/TTL 만료 시 메타데이터 소실 |
| **JSONL 감사 로그** | 사용자 행위 기록 | 파일 기반 → 분석 쿼리 어려움, 집계 불가 |
| **GCS Versioning** | 파일 버전 관리 | 버전 메타만 존재, 누가/왜 수정했는지 추적 불가 |

### 1.2 DuckDB 도입 근거

| 근거 | 설명 |
|------|------|
| **Embedded Lakehouse** | 데이터 레이크(GCS)의 메타데이터를 DuckDB로 관리하는 DuckLake 패턴 (2025 트렌드) |
| **read_json_auto + httpfs** | GCS JSONL을 다운로드 없이 직접 쿼리 (Pandas 대비 10~20x 성능) |
| **Small File Problem 해결** | 수만 개 JSONL 메타데이터를 Columnar Storage 단일 테이블로 통합 → 100ms 이내 검색 |
| **OLAP for Metadata** | 생성자/수정자/승인자 패턴은 Analytical Metadata → DuckDB FILTER/Window Function에 최적 |
| **ACID Compliance** | 단일 파일 기반이지만 트랜잭션 안전성 보장 → 상태 전이 시 데이터 무결성 |
| **Vectorized Execution** | Python 루프 없이 CPU 레벨 벡터 처리 → 실시간 대시보드/통계 |

### 1.3 저장소 역할 분리 (최종)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           데이터 생명주기                                  │
├──────────────┬──────────────┬──────────────┬────────────────────────────┤
│    Redis     │   DuckDB     │     GCS      │    JSONL 감사 로그           │
│   (휘발성)    │  (영구/분석)   │   (원본)      │   (상세 이력)               │
├──────────────┼──────────────┼──────────────┼────────────────────────────┤
│ 편집 세션     │ 파일 카탈로그  │ JSONL 원본    │ 모든 사용자 행위             │
│ 행 잠금       │ 이벤트 요약   │ 버전 히스토리  │ (기존 audit 유지)            │
│ 드래프트      │ 사용자 프로필  │ 이미지 원본   │                             │
│ GCS 캐시     │ 분석 뷰/통계  │              │                             │
│              │ GCS 직접 쿼리 │              │ ← DuckDB read_json_auto로  │
│              │ (httpfs)     │              │   감사 로그도 SQL 분석 가능   │
└──────────────┴──────────────┴──────────────┴────────────────────────────┘
```

---

## 2. DuckDB 인프라 설계

### 2.1 파일 위치 및 설정

```
EditerJsonlData/
├── data/
│   ├── editor.duckdb          ← DuckDB 데이터 파일
│   ├── audit/                 ← 기존 감사 로그 (유지)
│   └── backups/               ← 기존 백업 (유지)
```

### 2.2 설정 추가 (config.py)

```python
# DuckDB (Phase 10)
DUCKDB_PATH: str = "data/editor.duckdb"
DUCKDB_READ_ONLY: bool = False
```

### 2.3 연결 관리 전략

```python
import asyncio
import fcntl
import threading
from pathlib import Path

import duckdb

class DuckDBClient:
    """Thread-safe + Process-safe Singleton DuckDB 연결 관리자
    
    DuckDB single-writer 아키텍처에 맞춰 2단계 Lock 적용:
    - Level 1 (threading.Lock): 단일 프로세스 내 스레드 간 직렬화
    - Level 2 (fcntl.flock):    Gunicorn 멀티 워커(프로세스) 간 직렬화
    - 읽기: cursor()로 동시 읽기 허용 (Lock 불필요)
    """
    _conn: duckdb.DuckDBPyConnection | None = None
    _thread_lock: threading.Lock = threading.Lock()
    _lock_file_path: Path = Path(settings.DUCKDB_PATH + ".lock")
    
    @classmethod
    def get_connection(cls) -> duckdb.DuckDBPyConnection:
        if cls._conn is None:
            cls._conn = duckdb.connect(settings.DUCKDB_PATH)
            cls._init_schema()
            cls._init_gcs_access()
        return cls._conn
    
    @classmethod
    def get_read_cursor(cls) -> duckdb.DuckDBPyConnection:
        """읽기 전용 커서 (동시 읽기 가능, Lock 불필요)"""
        return cls.get_connection().cursor()
    
    @classmethod
    def _acquire_file_lock(cls, fd: int) -> None:
        """프로세스 간 배타적 파일 락 획득 (fcntl)"""
        fcntl.flock(fd, fcntl.LOCK_EX)
    
    @classmethod
    def _release_file_lock(cls, fd: int) -> None:
        """프로세스 간 파일 락 해제"""
        fcntl.flock(fd, fcntl.LOCK_UN)
    
    @classmethod
    def execute_write(cls, sql: str, params: list | None = None) -> None:
        """쓰기 작업을 Thread Lock + File Lock으로 이중 직렬화"""
        with cls._thread_lock:
            lock_fd = open(cls._lock_file_path, "w")
            try:
                cls._acquire_file_lock(lock_fd.fileno())
                conn = cls.get_connection()
                conn.execute(sql, params or [])
            finally:
                cls._release_file_lock(lock_fd.fileno())
                lock_fd.close()
    
    @classmethod
    def execute_write_many(cls, statements: list[tuple[str, list]]) -> None:
        """다중 쓰기를 단일 트랜잭션으로 실행 (ACID + 이중 Lock)"""
        with cls._thread_lock:
            lock_fd = open(cls._lock_file_path, "w")
            try:
                cls._acquire_file_lock(lock_fd.fileno())
                conn = cls.get_connection()
                conn.execute("BEGIN TRANSACTION")
                try:
                    for sql, params in statements:
                        conn.execute(sql, params)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                cls._release_file_lock(lock_fd.fileno())
                lock_fd.close()
    
    @classmethod
    def _init_gcs_access(cls) -> None:
        """httpfs 확장 + GCS 인증 설정"""
        conn = cls.get_connection()
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        # GCS는 S3 호환 API 사용
        # 인증은 gcloud CLI 기반 자동 감지 또는 HMAC 키 설정
        # conn.execute("""
        #     CREATE SECRET (
        #         TYPE GCS,
        #         KEY_ID 'HMAC_ACCESS_KEY',
        #         SECRET 'HMAC_SECRET_KEY'
        #     );
        # """)
    
    @classmethod
    def close(cls) -> None:
        """WAL 플러시 + 연결 종료
        
        CHECKPOINT를 명시적으로 수행하여 WAL에 남아있는 데이터를
        메인 DB 파일로 완전히 기록한 후 연결을 닫는다.
        """
        if cls._conn:
            try:
                cls._conn.execute("CHECKPOINT")
            except Exception:
                pass  # 종료 시점이므로 에러 무시
            cls._conn.close()
            cls._conn = None
```

**v2 개선 포인트:**
- `_write_lock`: 단일 프로세스 내 스레드 간 쓰기 직렬화
- `_file_lock` (fcntl): 멀티 프로세스(Gunicorn 워커) 간 쓰기 직렬화
- `get_read_cursor()`: 동시 읽기 허용 (DuckDB는 multiple readers 지원)
- `execute_write_many()`: 다중 테이블 업데이트를 단일 트랜잭션으로 묶어 ACID 보장
- `_init_gcs_access()`: httpfs 확장 로드 + GCS Secret 설정
- `close()`: 종료 시 `CHECKPOINT` 명시 실행 → WAL 데이터 디스크 플러시

---

## 3. 테이블 스키마 (v2)

### 3.1 ENUM 타입 정의

DuckDB 네이티브 ENUM을 사용하여 컬럼 압축률 향상 및 값 제한.

```sql
CREATE TYPE IF NOT EXISTS file_status AS ENUM (
    'registered',       -- 최초 등록됨 (GCS에 존재, 편집 이력 없음)
    'editing',          -- 현재 편집 중 (Redis 세션 활성)
    'updated',          -- GCS에 업데이트 완료 (검수 대기)
    'completed'         -- 최종 검수 완료
);

CREATE TYPE IF NOT EXISTS event_type AS ENUM (
    'session_start',    -- 편집 세션 시작 (GCS → Redis 로드)
    'row_save',         -- 개별 행 저장
    'gcs_update',       -- GCS 파일 업데이트 (발행)
    'session_discard',  -- 편집 취소
    'review_complete',  -- 최종 검수 완료
    'bulk_register'     -- GCS 스캔 → 일괄 등록
);
```

**근거:**
- VARCHAR 대비 컬럼 스토리지에서 압축률 2~5배 향상 (Dictionary Encoding 자동 적용)
- 잘못된 값 INSERT 방지 (컴파일 타임 검증)
- `FILTER (WHERE event_type = 'row_save')` 같은 분석 쿼리에서 비교 연산 최적화

### 3.2 file_registry (파일 카탈로그)

파일의 전체 생명주기 + 콘텐츠 메타데이터를 추적하는 핵심 테이블.

```sql
CREATE TABLE IF NOT EXISTS file_registry (
    -- PK
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_file_registry'),
    
    -- 파일 식별
    gcs_path        VARCHAR NOT NULL UNIQUE,    -- GCS 전체 경로 (UK)
    file_name       VARCHAR NOT NULL,            -- 파일명 (예: data_001.jsonl)
    task_id         VARCHAR NOT NULL,            -- task1, task2, task3
    date_folder     VARCHAR NOT NULL,            -- YYYYMMDD
    
    -- 생성 정보
    created_by      VARCHAR NOT NULL,            -- 최초 편집 세션 시작자 (→ users.user_id)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    
    -- 수정 정보
    last_modified_by    VARCHAR,                 -- 마지막 수정자 (→ users.user_id)
    last_modified_at    TIMESTAMPTZ,
    
    -- 완료 정보
    completed_by    VARCHAR,                     -- 최종 완료 확인자 (→ users.user_id)
    completed_at    TIMESTAMPTZ,
    
    -- 상태 관리 (ENUM)
    status          file_status NOT NULL DEFAULT 'registered',
    
    -- 콘텐츠 통계 (STRUCT: 데이터 카탈로그 차원)
    content_stats   STRUCT(
        total_rows      INTEGER,                 -- JSONL 전체 행 수
        has_images      BOOLEAN,                 -- 이미지 포함 여부
        data_id_range   VARCHAR                  -- data_id 범위 (예: "001~150")
    ),
    
    -- 파일 운영 통계
    update_count    INTEGER DEFAULT 0,           -- GCS 업데이트 횟수
    total_edit_sessions INTEGER DEFAULT 0,       -- 총 편집 세션 수
    
    -- 타임스탬프
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE SEQUENCE IF NOT EXISTS seq_file_registry START 1;
```

**v2 변경 포인트:**
- `TIMESTAMPTZ`: UTC 기준 명시적 타임존 처리 (기존 TIMESTAMP → TIMESTAMPTZ)
- `file_status ENUM`: VARCHAR → ENUM으로 변경 (압축률 + 값 제한)
- `content_stats STRUCT`: 데이터 카탈로그 차원 추가 (DuckDB 네이티브 복합 타입)
- `total_edit_sessions`: 편집 세션 수 추적 추가 (update_count와 구분)
- `total_rows` → `content_stats.total_rows`로 이동 (콘텐츠 메타와 그룹핑)

**인덱스:**
```sql
CREATE INDEX IF NOT EXISTS idx_file_task     ON file_registry (task_id);
CREATE INDEX IF NOT EXISTS idx_file_status   ON file_registry (status);
CREATE INDEX IF NOT EXISTS idx_file_date     ON file_registry (date_folder);
CREATE INDEX IF NOT EXISTS idx_file_modified ON file_registry (last_modified_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_task_date ON file_registry (task_id, date_folder);
```

### 3.3 edit_events (편집 이벤트)

경량 이벤트 로그. 상세 diff는 기존 JSONL 감사 로그에 위임.

```sql
CREATE TABLE IF NOT EXISTS edit_events (
    -- PK
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_edit_events'),
    
    -- 이벤트 식별
    file_id         INTEGER NOT NULL,            -- FK → file_registry.id
    gcs_path        VARCHAR NOT NULL,            -- 빠른 조회용 비정규화
    
    -- 사용자
    user_id         VARCHAR NOT NULL,            -- → users.user_id
    display_name    VARCHAR NOT NULL,
    
    -- 이벤트 (ENUM)
    event_type      event_type NOT NULL,
    
    -- 이벤트 상세
    summary         VARCHAR,                     -- 예: "3 items modified"
    rows_affected   INTEGER DEFAULT 0,
    modified_fields VARCHAR[],                   -- 변경된 필드 목록 (LIST 타입)
                                                 -- 예: ['content', 'content_meta']
    
    -- 타임스탬프
    created_at      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE SEQUENCE IF NOT EXISTS seq_edit_events START 1;
```

**v2 변경 포인트:**
- `event_type ENUM`: VARCHAR → ENUM
- `TIMESTAMPTZ`: 타임존 명시
- `modified_fields VARCHAR[]`: DuckDB LIST 타입으로 변경된 필드를 구조화 저장
  - 분석 시 `list_contains(modified_fields, 'content')` 같은 네이티브 함수 활용 가능
  - VARCHAR에 CSV로 넣는 것보다 쿼리 효율 및 타입 안전성 향상

**인덱스:**
```sql
CREATE INDEX IF NOT EXISTS idx_event_file    ON edit_events (file_id);
CREATE INDEX IF NOT EXISTS idx_event_user    ON edit_events (user_id);
CREATE INDEX IF NOT EXISTS idx_event_type    ON edit_events (event_type);
CREATE INDEX IF NOT EXISTS idx_event_time    ON edit_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_file_time ON edit_events (file_id, created_at DESC);
```

### 3.4 users (사용자 프로필)

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR PRIMARY KEY,
    display_name    VARCHAR NOT NULL,
    first_login_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_login_at   TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    login_count     INTEGER DEFAULT 1
);
```

**v2 변경 포인트:**
- `total_edits`, `total_updates` **제거**
- **근거**: OLAP 원칙상 집계 카운터를 수동 관리하는 것은 안티패턴
  - DuckDB Vectorized Execution으로 `edit_events`에서 실시간 집계해도 100ms 이내
  - 카운터 동기화 실패로 인한 불일치 위험 제거
  - 대신 `user_activity` VIEW에서 실시간 계산

---

## 4. 분석용 뷰 (VIEW) — v2

### 4.1 task_progress (TASK별 진행 현황)

```sql
CREATE OR REPLACE VIEW task_progress AS
SELECT
    task_id,
    COUNT(*)                                           AS total_files,
    COUNT(*) FILTER (WHERE status = 'completed')       AS completed_files,
    COUNT(*) FILTER (WHERE status = 'editing')         AS editing_files,
    COUNT(*) FILTER (WHERE status = 'updated')         AS updated_files,
    COUNT(*) FILTER (WHERE status = 'registered')      AS registered_files,
    ROUND(
        COUNT(*) FILTER (WHERE status = 'completed') * 100.0 
        / NULLIF(COUNT(*), 0), 1
    )                                                  AS completion_rate,
    -- v2: 콘텐츠 통계 집계 추가
    SUM(content_stats.total_rows)                      AS total_rows_all,
    SUM(update_count)                                  AS total_updates_all
FROM file_registry
GROUP BY task_id
ORDER BY task_id;
```

### 4.2 user_activity (사용자별 활동 요약) — 카운터 VIEW 대체

```sql
CREATE OR REPLACE VIEW user_activity AS
SELECT
    u.user_id,
    u.display_name,
    u.login_count,
    -- edit_events에서 실시간 집계 (카운터 대체)
    COUNT(e.id) FILTER (WHERE e.event_type = 'row_save')       AS total_saves,
    COUNT(e.id) FILTER (WHERE e.event_type = 'gcs_update')     AS total_updates,
    COUNT(e.id) FILTER (WHERE e.event_type = 'review_complete') AS total_reviews,
    COALESCE(SUM(e.rows_affected), 0)                          AS total_rows_affected,
    COUNT(DISTINCT e.gcs_path)                                  AS unique_files_edited,
    MIN(e.created_at)                                           AS first_activity,
    MAX(e.created_at)                                           AS last_activity
FROM users u
LEFT JOIN edit_events e ON u.user_id = e.user_id
GROUP BY u.user_id, u.display_name, u.login_count
ORDER BY total_saves DESC;
```

### 4.3 daily_activity (일별 작업 현황)

```sql
CREATE OR REPLACE VIEW daily_activity AS
SELECT
    CAST(e.created_at AS DATE)                               AS activity_date,
    COUNT(DISTINCT e.user_id)                                AS active_users,
    COUNT(*) FILTER (WHERE e.event_type = 'row_save')       AS saves,
    COUNT(*) FILTER (WHERE e.event_type = 'gcs_update')     AS updates,
    COUNT(*) FILTER (WHERE e.event_type = 'review_complete') AS reviews,
    COALESCE(SUM(e.rows_affected), 0)                       AS rows_affected
FROM edit_events e
GROUP BY CAST(e.created_at AS DATE)
ORDER BY activity_date DESC;
```

### 4.4 file_edit_history (파일별 편집 타임라인)

```sql
CREATE OR REPLACE VIEW file_edit_history AS
SELECT
    f.gcs_path,
    f.file_name,
    f.task_id,
    f.status,
    f.created_by,
    f.created_at,
    f.last_modified_by,
    f.last_modified_at,
    f.completed_by,
    f.completed_at,
    f.update_count,
    e.event_type,
    e.user_id            AS event_user,
    e.display_name       AS event_user_name,
    e.summary            AS event_summary,
    e.modified_fields    AS event_modified_fields,
    e.created_at         AS event_time
FROM file_registry f
LEFT JOIN edit_events e ON f.id = e.file_id
ORDER BY f.gcs_path, e.created_at DESC;
```

### 4.5 (신규) hourly_heatmap — 시간대별 작업 히트맵

```sql
CREATE OR REPLACE VIEW hourly_heatmap AS
SELECT
    EXTRACT(DOW FROM created_at)   AS day_of_week,   -- 0=일요일
    EXTRACT(HOUR FROM created_at)  AS hour_of_day,
    COUNT(*)                       AS event_count,
    COUNT(DISTINCT user_id)        AS unique_users
FROM edit_events
GROUP BY EXTRACT(DOW FROM created_at), EXTRACT(HOUR FROM created_at)
ORDER BY day_of_week, hour_of_day;
```

**근거:** Vectorized Execution으로 시간대별 작업 패턴을 실시간 분석. 대시보드에 활용 가능.

---

## 5. GCS 직접 쿼리 (httpfs 연동) — v2 신규

### 5.1 GCS Secret 설정

```sql
-- 방법 1: HMAC 키 기반 (권장 - 프로그래밍 방식)
CREATE SECRET gcs_secret (
    TYPE GCS,
    KEY_ID 'GOOG_HMAC_ACCESS_KEY_ID',
    SECRET 'GOOG_HMAC_SECRET_ACCESS_KEY'
);

-- 방법 2: gcloud CLI 자동 감지 (개발 환경)
-- gcloud auth application-default login 실행 후 자동 인증
```

### 5.2 GCS JSONL 직접 분석 예시

```sql
-- GCS에서 JSONL 파일을 다운로드 없이 직접 쿼리
SELECT 
    data_id,
    json_extract_string(content, '$.type') AS content_type,
    length(content) AS content_length
FROM read_json_auto(
    'gs://de-download-service-storage/manual/PROJ-14768/TASK1/20260313/*.jsonl'
)
LIMIT 100;
```

### 5.3 감사 로그 SQL 분석

```sql
-- 기존 JSONL 감사 로그를 SQL로 집계 (파일 순차 읽기 불필요)
SELECT 
    action,
    user_id,
    COUNT(*) AS cnt,
    MIN(timestamp) AS first_at,
    MAX(timestamp) AS last_at
FROM read_json_auto('data/audit/audit_*.jsonl')
GROUP BY action, user_id
ORDER BY cnt DESC;
```

### 5.4 GCS 파일 일괄 등록 (벌크 카탈로그)

```sql
-- GCS 스캔 결과를 file_registry에 일괄 등록
INSERT INTO file_registry (gcs_path, file_name, task_id, date_folder, created_by)
SELECT 
    gcs_path,
    regexp_extract(gcs_path, '[^/]+$') AS file_name,
    'task1' AS task_id,
    regexp_extract(gcs_path, '(\d{8})') AS date_folder,
    'system' AS created_by
FROM read_json_auto('gs://bucket/manual/PROJ-14768/TASK1/**/*.jsonl')
WHERE gcs_path NOT IN (SELECT gcs_path FROM file_registry);
```

**근거:** DuckLake 패턴의 핵심 — 데이터 레이크(GCS)와 메타데이터 카탈로그(DuckDB)를 연결하는 bridge.

---

## 6. 데이터 흐름 설계

### 6.1 편집 세션 시작 (GCS → Redis 로드)

```
[사용자가 GCS 파일 열기]
  │
  ├─→ Redis: gcs_wc:{file_id}:rows/meta (기존 유지)
  │
  └─→ DuckDB: BEGIN TRANSACTION
       ├─ file_registry UPSERT (status='editing', total_edit_sessions+=1)
       ├─ edit_events INSERT (event_type='session_start')
       └─ users UPSERT (last_login_at 갱신)
       COMMIT
```

### 6.2 행 저장

```
[사용자가 편집 후 저장]
  │
  ├─→ Redis: 행 데이터 업데이트 (기존 유지)
  │
  └─→ DuckDB: BEGIN TRANSACTION
       ├─ edit_events INSERT (event_type='row_save', modified_fields=['content',...])
       └─ file_registry UPDATE (last_modified_by, last_modified_at)
       COMMIT
```

### 6.3 GCS 파일 업데이트

```
[사용자가 파일 업데이트 클릭]
  │
  ├─→ GCS: JSONL 덮어쓰기 (기존 유지, GCS Versioning 자동 버전 생성)
  │
  └─→ DuckDB: BEGIN TRANSACTION
       ├─ file_registry UPDATE (status='updated', update_count+=1)
       └─ edit_events INSERT (event_type='gcs_update')
       COMMIT
```

### 6.4 최종 완료 확인

```
[검수자가 완료 확인]
  │
  └─→ DuckDB: BEGIN TRANSACTION
       ├─ file_registry UPDATE (status='completed', completed_by, completed_at)
       └─ edit_events INSERT (event_type='review_complete')
       COMMIT
```

### 6.5 편집 취소

```
[사용자가 편집 취소]
  │
  ├─→ Redis: working copy 삭제 (기존 유지)
  │
  └─→ DuckDB: BEGIN TRANSACTION
       ├─ file_registry UPDATE (status → 이전 상태 복원)
       └─ edit_events INSERT (event_type='session_discard')
       COMMIT
```

---

## 7. 구현 계획

### 7.1 파일 구조

```
app/
├── db/
│   ├── redis_client.py      ← 기존 유지
│   └── duckdb_client.py     ← 신규: DuckDB 연결 관리 + 스키마 초기화 + httpfs
├── services/
│   ├── metadata_service.py  ← 신규: file_registry/edit_events/users CRUD
│   └── gcs_edit_service.py  ← 수정: metadata_service 호출 추가
```

### 7.2 구현 순서

| 단계 | 작업 | 설명 |
|------|------|------|
| 1 | `config.py` 설정 추가 | DUCKDB_PATH 등 |
| 2 | `duckdb_client.py` | 연결 관리, ENUM/테이블/뷰 자동 생성, httpfs, lifespan 연동 |
| 3 | `metadata_service.py` | file_registry/edit_events/users CRUD + 트랜잭션 |
| 4 | `gcs_edit_service.py` 수정 | 이벤트 시점에 metadata_service 호출 |
| 5 | `editor.py` 수정 | 행 저장/업데이트 시 이벤트 기록 |
| 6 | 분석 API 엔드포인트 | TASK 진행률, 사용자 통계 |

### 7.3 FastAPI Lifespan 연동

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup
    await init_redis()
    DuckDBClient.get_connection()  # ENUM + 테이블 + 뷰 + httpfs 자동 초기화
    
    yield
    
    # shutdown
    DuckDBClient.close()
    await close_redis()
```

---

## 8. DuckDB 확장 활용 가능성

### 8.1 MotherDuck 연동 (향후)

로컬 DuckDB를 클라우드 MotherDuck으로 확장하여 팀 협업 분석 가능.

```python
# 로컬 → MotherDuck 동기화
conn = duckdb.connect("md:editor_analytics")
conn.execute("ATTACH 'data/editor.duckdb' AS local_db")
conn.execute("CREATE TABLE remote_events AS SELECT * FROM local_db.edit_events")
```

### 8.2 Parquet 내보내기 (리포트 자동화)

```sql
COPY (
    SELECT * FROM daily_activity
    WHERE activity_date >= '2026-03-01'
) TO 'reports/march_2026.parquet' (FORMAT PARQUET);
```

### 8.3 Polars/Pandas 직접 연동

```python
import polars as pl

df = pl.read_database(
    "SELECT * FROM task_progress",
    connection=duckdb_conn
)
```

### 8.4 Lance Format 확장 (AI 데이터 정제)

향후 JSONL → Lance 변환 시 DuckDB가 양쪽 모두 네이티브 쿼리 지원.

---

## 9. 제약사항 및 주의점

### 9.1 동시 쓰기 관리 (2단계 Lock)

DuckDB는 single-writer 아키텍처이므로 쓰기 직렬화가 필수.

**2단계 Lock 전략:**

| 레벨 | 메커니즘 | 보호 범위 | 용도 |
|------|---------|----------|------|
| Level 1 | `threading.Lock` | 단일 프로세스 내 스레드 간 | FastAPI async workers (uvicorn) |
| Level 2 | `fcntl.flock()` | Gunicorn 멀티 워커(프로세스) 간 | 프로덕션 멀티 프로세스 환경 |

**동작 원리:**
1. `execute_write()` 호출 → `threading.Lock` 획득 (스레드 안전)
2. `.lock` 파일에 `fcntl.LOCK_EX` 획득 (프로세스 안전)
3. SQL 실행
4. 파일 락 해제 → 스레드 락 해제

**왜 `fcntl.flock`인가:**
- `threading.Lock`은 단일 프로세스 내에서만 유효 → Gunicorn 워커(별도 프로세스)는 공유 불가
- `fcntl.flock`은 OS 커널 레벨 파일 락 → 프로세스 경계를 넘어 동작
- Lock 파일: `data/editor.duckdb.lock` (DuckDB 파일 옆에 자동 생성)

**쓰기 빈도 고려:**
- 이 프로젝트의 쓰기: 파일 저장/업데이트 시점만 (초당 1~2건 수준)
- Lock 경합 확률 매우 낮음 → 성능 영향 무시 가능

### 9.2 CHECKPOINT (WAL 플러시)

DuckDB는 기본적으로 WAL(Write-Ahead Log) 모드로 동작하며,
쓰기 데이터가 메모리/WAL에 일시 보관될 수 있다.

**적용 시점:**
- `DuckDBClient.close()` 호출 시 `CHECKPOINT` 명시적 실행
- 이를 통해 WAL에 남은 데이터를 메인 `.duckdb` 파일로 완전 플러시
- 비정상 종료 시에도 DuckDB WAL 자체의 recovery로 데이터 안전

**Lifespan 연동:**
```python
# shutdown
DuckDBClient.close()  # 내부에서 CHECKPOINT → close 순서로 실행
```

### 9.3 백업 전략

```bash
cp data/editor.duckdb data/backups/editor_$(date +%Y%m%d).duckdb
```

DuckDB EXPORT DATABASE 명령으로 SQL 덤프도 가능:
```sql
EXPORT DATABASE 'data/backups/dump_20260313' (FORMAT PARQUET);
```

### 9.4 기존 감사 로그와의 관계

| 항목 | JSONL 감사 로그 | DuckDB edit_events |
|------|----------------|-------------------|
| 목적 | 상세 행위 기록 (디버깅/감사) | 핵심 이벤트 요약 (분석/조회) |
| 데이터 | 변경 diff, IP, User-Agent 포함 | 이벤트 유형, 요약, 행 수, 변경 필드 |
| 보존 | 90일 후 자동 삭제 | 영구 보존 |
| 조회 | `read_json_auto()`로 SQL 분석 가능 | 네이티브 SQL 쿼리 |

**기존 JSONL 감사 로그는 삭제하지 않고 유지. DuckDB는 분석/조회 요약 계층으로 추가.**

---

## 10. ERD (Entity Relationship Diagram) — v2

```
┌───────────────────────┐
│       users            │
├───────────────────────┤
│ PK user_id  VARCHAR    │
│    display_name        │
│    first_login_at TZ   │
│    last_login_at  TZ   │
│    login_count         │
└──────────┬────────────┘
           │ 1:N (created_by, modified_by, completed_by)
           │
┌──────────▼────────────────────────────┐
│         file_registry                  │
├────────────────────────────────────────┤
│ PK id           INTEGER (SEQUENCE)     │
│ UK gcs_path     VARCHAR                │
│    file_name    VARCHAR                │
│    task_id      VARCHAR                │
│    date_folder  VARCHAR                │
│    created_by   VARCHAR → users        │
│    created_at   TIMESTAMPTZ            │
│    last_modified_by → users            │
│    last_modified_at TIMESTAMPTZ        │
│    completed_by     → users            │
│    completed_at     TIMESTAMPTZ        │
│    status       file_status (ENUM)     │
│    content_stats STRUCT(               │
│      total_rows, has_images,           │
│      data_id_range)                    │
│    update_count INTEGER                │
│    total_edit_sessions INTEGER         │
│    updated_at   TIMESTAMPTZ            │
└──────────┬────────────────────────────┘
           │ 1:N
           │
┌──────────▼────────────────────────────┐
│         edit_events                    │
├────────────────────────────────────────┤
│ PK id           INTEGER (SEQUENCE)     │
│ FK file_id      → file_registry.id     │
│    gcs_path     VARCHAR (비정규화)       │
│    user_id      → users.user_id        │
│    display_name VARCHAR                │
│    event_type   event_type (ENUM)      │
│    summary      VARCHAR                │
│    rows_affected INTEGER               │
│    modified_fields VARCHAR[] (LIST)    │
│    created_at   TIMESTAMPTZ            │
└────────────────────────────────────────┘
```

---

## 11. 의존성

```
# requirements.txt 추가
duckdb>=1.2.0
```

---

## 부록: 전체 DDL 스크립트 (v2)

```sql
-- ============================================
-- EditerJsonlData DuckDB Schema v2
-- Phase 10: Embedded Lakehouse + Log-centric Audit
-- ============================================

-- Extensions
INSTALL httpfs;
LOAD httpfs;

-- ENUM Types
CREATE TYPE IF NOT EXISTS file_status AS ENUM (
    'registered', 'editing', 'updated', 'completed'
);
CREATE TYPE IF NOT EXISTS event_type AS ENUM (
    'session_start', 'row_save', 'gcs_update',
    'session_discard', 'review_complete', 'bulk_register'
);

-- Sequences
CREATE SEQUENCE IF NOT EXISTS seq_file_registry START 1;
CREATE SEQUENCE IF NOT EXISTS seq_edit_events START 1;

-- Tables
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR PRIMARY KEY,
    display_name    VARCHAR NOT NULL,
    first_login_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_login_at   TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    login_count     INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS file_registry (
    id                  INTEGER PRIMARY KEY DEFAULT nextval('seq_file_registry'),
    gcs_path            VARCHAR NOT NULL UNIQUE,
    file_name           VARCHAR NOT NULL,
    task_id             VARCHAR NOT NULL,
    date_folder         VARCHAR NOT NULL,
    created_by          VARCHAR NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
    last_modified_by    VARCHAR,
    last_modified_at    TIMESTAMPTZ,
    completed_by        VARCHAR,
    completed_at        TIMESTAMPTZ,
    status              file_status NOT NULL DEFAULT 'registered',
    content_stats       STRUCT(
                            total_rows INTEGER,
                            has_images BOOLEAN,
                            data_id_range VARCHAR
                        ),
    update_count        INTEGER DEFAULT 0,
    total_edit_sessions INTEGER DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE TABLE IF NOT EXISTS edit_events (
    id              INTEGER PRIMARY KEY DEFAULT nextval('seq_edit_events'),
    file_id         INTEGER NOT NULL,
    gcs_path        VARCHAR NOT NULL,
    user_id         VARCHAR NOT NULL,
    display_name    VARCHAR NOT NULL,
    event_type      event_type NOT NULL,
    summary         VARCHAR,
    rows_affected   INTEGER DEFAULT 0,
    modified_fields VARCHAR[],
    created_at      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_file_task      ON file_registry (task_id);
CREATE INDEX IF NOT EXISTS idx_file_status    ON file_registry (status);
CREATE INDEX IF NOT EXISTS idx_file_date      ON file_registry (date_folder);
CREATE INDEX IF NOT EXISTS idx_file_modified  ON file_registry (last_modified_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_task_date ON file_registry (task_id, date_folder);
CREATE INDEX IF NOT EXISTS idx_event_file     ON edit_events (file_id);
CREATE INDEX IF NOT EXISTS idx_event_user     ON edit_events (user_id);
CREATE INDEX IF NOT EXISTS idx_event_type     ON edit_events (event_type);
CREATE INDEX IF NOT EXISTS idx_event_time     ON edit_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_file_time ON edit_events (file_id, created_at DESC);

-- Views
CREATE OR REPLACE VIEW task_progress AS
SELECT
    task_id,
    COUNT(*)                                           AS total_files,
    COUNT(*) FILTER (WHERE status = 'completed')       AS completed_files,
    COUNT(*) FILTER (WHERE status = 'editing')         AS editing_files,
    COUNT(*) FILTER (WHERE status = 'updated')         AS updated_files,
    COUNT(*) FILTER (WHERE status = 'registered')      AS registered_files,
    ROUND(
        COUNT(*) FILTER (WHERE status = 'completed') * 100.0
        / NULLIF(COUNT(*), 0), 1
    )                                                  AS completion_rate,
    SUM(content_stats.total_rows)                      AS total_rows_all,
    SUM(update_count)                                  AS total_updates_all
FROM file_registry
GROUP BY task_id
ORDER BY task_id;

CREATE OR REPLACE VIEW user_activity AS
SELECT
    u.user_id,
    u.display_name,
    u.login_count,
    COUNT(e.id) FILTER (WHERE e.event_type = 'row_save')       AS total_saves,
    COUNT(e.id) FILTER (WHERE e.event_type = 'gcs_update')     AS total_updates,
    COUNT(e.id) FILTER (WHERE e.event_type = 'review_complete') AS total_reviews,
    COALESCE(SUM(e.rows_affected), 0)                          AS total_rows_affected,
    COUNT(DISTINCT e.gcs_path)                                  AS unique_files_edited,
    MIN(e.created_at)                                           AS first_activity,
    MAX(e.created_at)                                           AS last_activity
FROM users u
LEFT JOIN edit_events e ON u.user_id = e.user_id
GROUP BY u.user_id, u.display_name, u.login_count
ORDER BY total_saves DESC;

CREATE OR REPLACE VIEW daily_activity AS
SELECT
    CAST(e.created_at AS DATE)                               AS activity_date,
    COUNT(DISTINCT e.user_id)                                AS active_users,
    COUNT(*) FILTER (WHERE e.event_type = 'row_save')       AS saves,
    COUNT(*) FILTER (WHERE e.event_type = 'gcs_update')     AS updates,
    COUNT(*) FILTER (WHERE e.event_type = 'review_complete') AS reviews,
    COALESCE(SUM(e.rows_affected), 0)                       AS rows_affected
FROM edit_events e
GROUP BY CAST(e.created_at AS DATE)
ORDER BY activity_date DESC;

CREATE OR REPLACE VIEW file_edit_history AS
SELECT
    f.gcs_path,
    f.file_name,
    f.task_id,
    f.status,
    f.created_by,
    f.created_at,
    f.last_modified_by,
    f.last_modified_at,
    f.completed_by,
    f.completed_at,
    f.update_count,
    e.event_type,
    e.user_id            AS event_user,
    e.display_name       AS event_user_name,
    e.summary            AS event_summary,
    e.modified_fields    AS event_modified_fields,
    e.created_at         AS event_time
FROM file_registry f
LEFT JOIN edit_events e ON f.id = e.file_id
ORDER BY f.gcs_path, e.created_at DESC;

CREATE OR REPLACE VIEW hourly_heatmap AS
SELECT
    EXTRACT(DOW FROM created_at)   AS day_of_week,
    EXTRACT(HOUR FROM created_at)  AS hour_of_day,
    COUNT(*)                       AS event_count,
    COUNT(DISTINCT user_id)        AS unique_users
FROM edit_events
GROUP BY EXTRACT(DOW FROM created_at), EXTRACT(HOUR FROM created_at)
ORDER BY day_of_week, hour_of_day;
```
