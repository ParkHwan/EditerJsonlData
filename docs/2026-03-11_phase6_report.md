# Phase 6 — GCS 연동 개발 보고서

**작성일**: 2026-03-11  
**작성자**: AI Assistant + 사용자 협업  
**Phase**: 6  
**상태**: COMPLETED

---

## 1. 개요

Phase 6에서는 Google Cloud Storage(GCS) 연동을 구현하여, GCS 버킷에 업로드된 JSONL 파일을 웹 에디터에서 탐색·다운로드·편집·재업로드할 수 있는 워크플로우를 완성했다.

### GCS 구성 정보

| 항목 | 값 |
|------|-----|
| 프로젝트 | `crowdworks-platform` |
| 리전 | `asia-northeast1` |
| 버킷 | `de-download-service-storage` |
| 경로 패턴 | `manual/YYYYMMDD/filename.jsonl` |

### 전체 워크플로우

```mermaid
sequenceDiagram
    participant U as 사용자 (브라우저)
    participant W as 웹 서버 (FastAPI)
    participant L as 로컬 스토리지 (data/)
    participant G as GCS Bucket

    Note over U,G: ① GCS 탐색 & 다운로드
    U->>W: GET /gcs/browse → 날짜 폴더 목록
    W->>G: list_blobs(prefix=manual/, delimiter=/)
    G-->>W: [20260310/, 20260311/, ...]
    W-->>U: 폴더 카드 렌더링

    U->>W: GET /gcs/browse/20260311 → 파일 목록
    W->>G: list_blobs(prefix=manual/20260311/)
    G-->>W: [data_A.jsonl, data_B.jsonl]
    W-->>U: 파일 테이블 렌더링

    U->>W: POST /gcs/download {gcs_path}
    W->>G: blob.download_to_filename()
    G-->>W: 파일 바이너리
    W->>L: data/data_A.jsonl 저장
    W-->>U: "다운로드 완료"

    Note over U,G: ② 로컬 편집
    U->>W: GET /view/files/data_A → 에디터 접속
    W->>L: LineIndex + get_row()
    L-->>W: Row 데이터
    W-->>U: Master-Detail 카드 렌더링

    U->>W: PUT /editor/data/{file_id}/{row_idx}
    W->>L: Backup → Atomic Write → 인덱스 무효화
    L-->>W: 저장 완료 (version+1)
    W-->>U: "저장 성공"

    Note over U,G: ③ GCS 업로드
    U->>W: POST /gcs/upload {file_id, date_str}
    W->>L: data/data_A.jsonl 읽기
    W->>G: blob.upload_from_filename()
    G-->>W: 업로드 완료
    W-->>U: "GCS 업로드 완료"
```

---

## 2. 설계

### 2.1 GCSService 아키텍처

GCS SDK(`google-cloud-storage`)의 동기 API를 `asyncio.to_thread()`로 래핑하여 비동기 서비스로 구현했다. 기존 FileService(LineIndex, Atomic Write, Backup)는 그대로 유지하고, GCS는 파일의 원본 저장소/최종 배포처로만 활용한다.

```mermaid
graph LR
    subgraph GCS ["☁️ Google Cloud Storage"]
        B["de-download-service-storage<br/>manual/YYYYMMDD/*.jsonl"]
    end

    subgraph Server ["🖥️ FastAPI Server"]
        GS["GCSService<br/>(google-cloud-storage SDK)<br/>asyncio.to_thread()"]
        FS["FileService<br/>(LineIndex + Atomic Write + Backup)"]
        LOCAL["📁 data/*.jsonl"]

        GS -- "download_to_local()" --> LOCAL
        LOCAL -- "upload_from_local()" --> GS
        FS -- "get_row() / update_row_atomic()" --> LOCAL
    end

    subgraph Client ["🌐 브라우저"]
        UI["GCS 관리 UI<br/>+ JSONL 에디터"]
    end

    B <-- "download / upload" --> GS
    UI <-- "HTTP / WebSocket" --> Server
```

### 2.2 인증 전략

```mermaid
graph TD
    A{"실행 환경"}
    A -->|"GCP VM / Cloud Run"| ADC["Application Default Credentials<br/>(자동 인증)"]
    A -->|"로컬 개발"| LOCAL_AUTH{"인증 방식 선택"}
    A -->|"Docker"| DOCKER["서비스 계정 키 마운트<br/>또는 Workload Identity"]

    LOCAL_AUTH -->|"방법 1 (권장)"| GCLOUD["gcloud auth<br/>application-default login"]
    LOCAL_AUTH -->|"방법 2"| KEY["GCS_CREDENTIALS_PATH<br/>= /path/to/key.json"]

    ADC --> CLIENT["GCS Client 초기화"]
    GCLOUD --> CLIENT
    KEY --> CLIENT
    DOCKER --> CLIENT
```

### 2.3 GCS 경로 구조

```mermaid
graph TD
    BUCKET["🪣 gs://de-download-service-storage"]
    PREFIX["📁 manual/ (GCS_PREFIX)"]
    DATE1["📂 20260310/"]
    DATE2["📂 20260311/"]
    DATE3["📂 20260312/"]
    FILE1["📄 EPT_1029_data.jsonl"]
    FILE2["📄 EPT_2001_data.jsonl"]
    FILE3["📄 EPT_1050_data.jsonl"]

    BUCKET --> PREFIX
    PREFIX --> DATE1
    PREFIX --> DATE2
    PREFIX --> DATE3
    DATE2 --> FILE1
    DATE2 --> FILE2
    DATE3 --> FILE3
```

---

## 3. 구현

### 3.1 GCSService (`app/services/gcs_service.py`)

| 메서드 | 설명 |
|--------|------|
| `list_date_folders()` | `manual/` 하위 날짜 폴더(YYYYMMDD) 목록 조회 |
| `list_files(date_str)` | 특정 날짜 폴더 내 JSONL 파일 목록 |
| `download_to_local(gcs_path, overwrite)` | GCS → 로컬 `data/` 다운로드 |
| `upload_from_local(file_id, date_str)` | 로컬 → GCS 업로드 (날짜 폴더 지정) |
| `blob_exists(gcs_path)` | GCS blob 존재 여부 확인 |
| `get_blob_metadata(gcs_path)` | GCS blob 메타데이터 조회 |
| `check_connection()` | GCS 연결 상태 확인 |

**설계 결정:**
- `asyncio.to_thread()` 사용: GCS SDK의 동기 I/O를 이벤트 루프 블로킹 없이 실행
- Lazy initialization: `client`/`bucket` 프로퍼티로 첫 접근 시에만 초기화
- 경로 안전성: `Path(file_id).name`으로 디렉터리 순회 방지

### 3.2 GCS 다운로드/업로드 플로우

```mermaid
flowchart TD
    subgraph DOWNLOAD ["📥 GCS → 로컬 다운로드"]
        D1["POST /gcs/download<br/>{gcs_path, overwrite}"]
        D2{"로컬 파일<br/>이미 존재?"}
        D3{"overwrite<br/>= true?"}
        D4["GCS blob.download_to_filename()"]
        D5["FileService._invalidate_index()"]
        D6["AuditService.log(gcs_download)"]
        D7["✅ 다운로드 완료"]
        D8["ℹ️ 이미 존재 (스킵)"]

        D1 --> D2
        D2 -->|"예"| D3
        D2 -->|"아니오"| D4
        D3 -->|"true"| D4
        D3 -->|"false"| D8
        D4 --> D5 --> D6 --> D7
    end

    subgraph UPLOAD ["📤 로컬 → GCS 업로드"]
        U1["POST /gcs/upload<br/>{file_id, date_str}"]
        U2{"로컬 파일<br/>존재?"}
        U3{"date_str<br/>지정?"}
        U4["오늘 날짜<br/>YYYYMMDD 사용"]
        U5["GCS blob.upload_from_filename()<br/>→ manual/{date_str}/{file_id}.jsonl"]
        U6["AuditService.log(gcs_upload)"]
        U7["✅ 업로드 완료"]
        U8["❌ 404 File Not Found"]

        U1 --> U2
        U2 -->|"예"| U3
        U2 -->|"아니오"| U8
        U3 -->|"빈 문자열"| U4 --> U5
        U3 -->|"지정됨"| U5
        U5 --> U6 --> U7
    end
```

### 3.3 GCS API 엔드포인트 (`app/api/v1/endpoints/gcs.py`)

#### HTML 뷰

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/api/v1/gcs/browse` | GCS 날짜 폴더 브라우저 |
| `GET` | `/api/v1/gcs/browse/{date_str}` | 특정 날짜 파일 목록 |

#### JSON API

| Method | Path | CSRF | Rate Limit | 설명 |
|--------|------|------|------------|------|
| `GET` | `/api/v1/gcs/folders` | X | 60/분 | 날짜 폴더 목록 |
| `GET` | `/api/v1/gcs/files/{date_str}` | X | 60/분 | 파일 목록 |
| `POST` | `/api/v1/gcs/download` | O | 30/분 | GCS → 로컬 다운로드 |
| `POST` | `/api/v1/gcs/upload` | O | 10/분 | 로컬 → GCS 업로드 |
| `GET` | `/api/v1/gcs/status` | X | — | GCS 연결 상태 |

### 3.4 GCS 관리 UI

#### 폴더 브라우저 (`gcs_browse.html`)
- 날짜별 폴더 카드 그리드 (최신순 정렬)
- 로컬 파일 → GCS 업로드 테이블 (날짜 입력 모달)
- GCS 연결 오류 시 알림 배너

#### 파일 목록 (`gcs_files.html`)
- 파일 테이블: 이름, 크기, 수정일, 로컬 상태 배지
- 로컬 미존재: "📥 다운로드" 버튼
- 로컬 존재: "🔄 덮어쓰기" + "📝 편집" 버튼
- 다운로드 완료 시 자동 새로고침

### 3.5 UI 화면 구성

```mermaid
graph TD
    subgraph NAV ["🧭 네비게이션 바"]
        N1["파일 목록"]
        N2["GCS 관리 ← NEW"]
        N3["사용자명 / 로그아웃"]
    end

    subgraph BROWSE ["📁 gcs_browse.html"]
        B1["버킷 배지: 📦 de-download-service-storage"]
        B2["날짜 폴더 카드 그리드<br/>📂 2026-03-10<br/>📂 2026-03-11<br/>📂 2026-03-12"]
        B3["로컬 → GCS 업로드 테이블<br/>파일명 | 크기 | 수정일 | [GCS 업로드]"]
        B4["업로드 모달<br/>날짜 입력 (YYYYMMDD) + 확인/취소"]
    end

    subgraph FILES ["📄 gcs_files.html"]
        F1["← 폴더 목록 | 📂 2026-03-11 | 📦 버킷명"]
        F2["파일 테이블<br/>파일명 | 크기 | 수정일 | 로컬 상태 | 액션"]
        F3["✅ 로컬 존재 → 🔄 덮어쓰기 | 📝 편집"]
        F4["☁️ GCS만 → 📥 다운로드"]
    end

    N2 --> BROWSE
    B2 -->|"폴더 클릭"| FILES
    F3 -->|"📝 편집 클릭"| EDITOR["기존 에디터 페이지<br/>/view/files/{file_id}"]
```

### 3.6 설정 (`app/core/config.py`)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `GCS_PROJECT_ID` | `crowdworks-platform` | GCP 프로젝트 ID |
| `GCS_BUCKET_NAME` | `de-download-service-storage` | GCS 버킷명 |
| `GCS_PREFIX` | `manual` | 버킷 내 기본 prefix |
| `GCS_LOCATION` | `asia-northeast1` | 리전 |
| `GCS_CREDENTIALS_PATH` | `""` (빈 문자열=ADC) | 서비스 계정 키 파일 경로 |

---

## 4. 전체 시스템 아키텍처 (Phase 6 반영)

```mermaid
graph TB
    subgraph Browser ["🌐 브라우저"]
        UI_FILES["파일 목록<br/>(index.html)"]
        UI_EDITOR["JSONL 에디터<br/>(editor.html)"]
        UI_GCS["GCS 관리<br/>(gcs_browse / gcs_files)"]
        UI_LOGIN["로그인<br/>(login.html)"]
        WS_CLIENT["WebSocket<br/>LockStatusManager"]
    end

    subgraph FastAPI ["🖥️ FastAPI Server"]
        AUTH["auth.py<br/>로그인/로그아웃"]
        EDITOR_API["editor.py<br/>Lock/Data/Card API"]
        FILES_API["files.py<br/>HTML 뷰/다운로드"]
        GCS_API["gcs.py ← NEW<br/>GCS 탐색/다운로드/업로드"]
        DRAFT_API["draft.py<br/>Auto-save API"]
        HEALTH["health.py<br/>Health Check"]
        WS_SERVER["websocket.py<br/>Lock 상태 브로드캐스트"]
    end

    subgraph Services ["⚙️ Service Layer"]
        FILE_SVC["FileService<br/>LineIndex + Atomic Write + Backup"]
        LOCK_SVC["LockService<br/>Redis 분산 락"]
        AUTH_SVC["AuthService<br/>Redis 세션"]
        AUDIT_SVC["AuditService<br/>JSONL 감사 로그"]
        DRAFT_SVC["DraftService<br/>Redis Draft"]
        GCS_SVC["GCSService ← NEW<br/>GCS SDK Wrapper"]
        WS_MGR["ConnectionManager<br/>WebSocket 풀"]
        PENDING["PendingTaskTracker<br/>Graceful Shutdown"]
    end

    subgraph Infra ["🏗️ Infrastructure"]
        REDIS["Redis Sentinel<br/>(세션/락/Draft)"]
        LOCAL_FS["📁 Local Filesystem<br/>data/*.jsonl<br/>data/backups/<br/>data/audit/"]
        GCS_BUCKET["☁️ GCS Bucket<br/>de-download-service-storage<br/>manual/YYYYMMDD/"]
    end

    UI_FILES --> FILES_API
    UI_EDITOR --> EDITOR_API
    UI_GCS --> GCS_API
    UI_LOGIN --> AUTH
    WS_CLIENT <--> WS_SERVER

    AUTH --> AUTH_SVC
    EDITOR_API --> FILE_SVC & LOCK_SVC & AUDIT_SVC
    FILES_API --> FILE_SVC & AUDIT_SVC
    GCS_API --> GCS_SVC & FILE_SVC & AUDIT_SVC
    DRAFT_API --> DRAFT_SVC
    WS_SERVER --> WS_MGR & LOCK_SVC

    AUTH_SVC --> REDIS
    LOCK_SVC --> REDIS
    DRAFT_SVC --> REDIS
    FILE_SVC --> LOCAL_FS
    AUDIT_SVC --> LOCAL_FS
    GCS_SVC --> GCS_BUCKET
    GCS_SVC --> LOCAL_FS
```

---

## 5. 변경/추가 파일 목록

### 신규 파일

| 파일 | 역할 |
|------|------|
| `app/services/gcs_service.py` | GCS 파일 관리 서비스 |
| `app/api/v1/endpoints/gcs.py` | GCS API 엔드포인트 (HTML 뷰 + JSON API) |
| `app/templates/gcs_browse.html` | GCS 폴더 브라우저 + 업로드 UI |
| `app/templates/gcs_files.html` | GCS 파일 목록 + 다운로드/편집 UI |

### 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/core/config.py` | GCS 설정 5항목 추가 |
| `app/api/v1/api.py` | GCS 라우터 등록 (`/gcs` prefix) |
| `app/services/audit_service.py` | `gcs_download`, `gcs_upload` ActionType 추가 |
| `app/templates/base.html` | 네비게이션에 "GCS 관리" 메뉴 추가 |
| `pyproject.toml` | `google-cloud-storage>=2.14.0` 의존성 추가 |
| `.env` | GCS 환경변수 5항목 추가 |
| `.env.example` | GCS 환경변수 5항목 추가 |

---

## 6. 사용 가이드

### 6.1 GCS 인증 설정 (로컬 개발)

```bash
# 방법 1: gcloud CLI 인증 (권장)
gcloud auth application-default login

# 방법 2: 서비스 계정 키 파일
export GCS_CREDENTIALS_PATH=/path/to/service-account-key.json
```

### 6.2 사용자 워크플로우

```mermaid
flowchart LR
    A["🌐 GCS 관리 접속"] --> B["📂 날짜 폴더 선택"]
    B --> C["📥 JSONL 다운로드"]
    C --> D["📝 에디터에서 편집"]
    D --> E["💾 Row 저장<br/>(Atomic Write)"]
    E --> F{"추가 편집?"}
    F -->|"예"| D
    F -->|"아니오"| G["📤 GCS 업로드<br/>(날짜 폴더 지정)"]
    G --> H["✅ 완료"]
```

### 6.3 URL 구조

```
# GCS 관리 (HTML)
GET  /api/v1/gcs/browse                → 날짜 폴더 브라우저
GET  /api/v1/gcs/browse/{YYYYMMDD}     → 특정 날짜 파일 목록

# GCS JSON API
GET  /api/v1/gcs/folders               → 폴더 목록 (JSON)
GET  /api/v1/gcs/files/{YYYYMMDD}      → 파일 목록 (JSON)
POST /api/v1/gcs/download              → GCS → 로컬 다운로드
POST /api/v1/gcs/upload                → 로컬 → GCS 업로드
GET  /api/v1/gcs/status                → 연결 상태 확인
```

---

## 7. 전체 진행 현황

```mermaid
gantt
    title EditerJsonlData 전체 Phase 진행 현황
    dateFormat YYYY-MM-DD
    axisFormat %m/%d

    section Phase 1
    프로젝트 스캐폴드 (Docker, Redis Sentinel)     :done, p1, 2026-01-19, 1d

    section Phase 1.5
    Lock Heartbeat, Optimistic Locking, Backup     :done, p15, 2026-01-19, 1d

    section Phase 2
    Line Indexer, Auth, Rate Limiting, PM HTML 포팅 :done, p2, 2026-02-12, 1d

    section Phase 3
    CSRF, AuditService, Draft Auto-save            :done, p3, 2026-02-12, 1d

    section Phase 3.5~3.6
    Master-Detail UI, 인라인 편집                   :done, p35, 2026-02-13, 1d

    section Phase 4
    Health Check, Graceful Shutdown, WebSocket Lock :done, p4, 2026-02-13, 1d

    section Phase 5
    Ops & Test (111 tests, CSP 보안 헤더)          :done, p5, 2026-02-19, 1d

    section Phase 6
    GCS 연동 (다운로드/업로드/브라우저 UI)          :done, p6, 2026-03-11, 1d
```
