# Editer ResultData Jsonl Project

> **Project Identity**: Jsonl파일을 웹 브라우져 형식으로 내부 임직원이 직접 수정하고 저장해가면서 최종적으로 수정된 데이터를 Jsonl형식으로 다운받게 하는 서비스

## 0. SuperGemini Configuration (Advanced)
이 프로젝트는 **고가용성**과 **데이터 무결성**을 최우선으로 하며, 에이전트는 아래의 확장된 설정을 준수합니다.

### 0.1 Execution Modes & Flags
- **Primary Mode**: `--task-manage` (복잡한 상태 관리 및 멀티 세션 대응)
- **Safety Protocol**: `--safe-mode` (Optimistic Locking 및 자동 백업을 통한 데이터 보호 강제)
- **Documentation**: `--c7` (Context7: FastAPI, Redis, Pydantic 최신 모범 사례 준수)
- **Testing**: `--playwright` (브라우저 기반 E2E 테스트 및 동시성 시나리오 검증)

### 0.2 Memory & Context Strategy (`serena`)
- **Session Persistence**: 작업자의 마지막 커서 위치, 수정 중인 Draft 상태를 Redis와 연동하여 메모리화.
- **Schema Tracking**: JSONL 스키마 변경 이력을 추적하여 하위 호환성 유지.
- **Conflict Resolution**: 동시 수정 시도 발생 시 `LockService`의 상태 및 데이터 버전을 기록하여 충돌 분석 지원.

### 0.3 Agent Behavior Rules
1.  **Atomic Operations**: 파일 쓰기 작업은 반드시 원자성(Atomicity)을 보장해야 하며, 실패 시 자동 롤백되어야 함.
2.  **Lock Safety**: 단순 TTL 만료에 의존하지 않고, **Heartbeat 매커니즘**을 통해 활성 작업자의 Lock을 연장하며, 저장 시 **Optimistic Locking**(버전 확인)을 수행하여 덮어쓰기를 방지함.
3.  **Strict Integrity**: Lock 만료(60분) 시 미저장 작업 내용은 폐기되며, 모든 저장 작업 직전에 **자동 백업**을 수행해야 함.
4.  **Security First**: CSRF 보호, Rate Limiting 적용 및 모든 PII(개인식별정보) 마스킹 처리.

---

## 1. 개발 표준 및 품질 보증 (Development Standards & QA)
본 프로젝트는 **DEVELOPMENT_SPECIALIST.md**에 정의된 Global Top-Tier 수준을 준수합니다.

### 1.1 핵심 품질 목표 (Goal: 10/10)
- **신뢰성**: Redis Sentinel을 통한 고가용성 확보, 장애 시 Graceful Degradation(읽기 전용 모드 전환).
- **보안**: CSRF 보호, 세션 고정 공격 방지, Rate Limiting, Audit Log(감사 로그) 기록.
- **API 품질**: Restful API v1, 멱등성 보장, 표준 에러 모델, Health Check 엔드포인트 제공.
- **테스트**: Unit/Integration 커버리지 85%+, 동시성 및 복구 시나리오 테스트 필수.

---

## 2. 기술 스택 (Tech Stack)

### 2.1 Backend (Core)
- **Language**: Python 3.10+
- **Framework**: **FastAPI** (Async support, Pydantic v2)
- **Dependency Strategy**: 
    - **Local**: `uv venv` (Virtual Env)
    - **Docker**: `uv pip install --system` (System Install)
- **Security Libs**: `fastapi-csrf-protect` (CSRF), `slowapi` (Rate Limiting)
- **Server**: Uvicorn (ASGI) + Gunicorn (Process Manager), Graceful Shutdown 지원

### 2.2 Frontend (View & Interaction)
- **Rendering**: **Jinja2 Templates** (Server-Side Rendering)
- **Interactivity**: **HTMX** (AJAX), **WebSocket** (실시간 Lock 상태 동기화)
- **Styling**: TailwindCSS or Bootstrap 5

### 2.3 Data & Infrastructure
- **Source Data**: Local/Mounted Storage `.jsonl` Files
- **State/Cache Store**: **Redis Sentinel** (High Availability for Session/Lock)
- **Containerization**: Docker (Multi-stage build)

---

## 3. 디렉터리 구조 및 모듈화 (Directory Structure & Modularity)
Clean Architecture 원칙을 일부 차용하여 관심사를 분리하고 유지보수성을 극대화합니다.

```plaintext
editer-jsonl/
├── app/
│   ├── __init__.py
│   ├── main.py              # Application Entrypoint (Lifecycle, Middleware)
│   ├── core/                # 핵심 설정 및 유틸리티
│   │   ├── config.py        # Env, Settings
│   │   ├── security.py      # Auth, CSRF, Session Security
│   │   ├── rate_limit.py    # Rate Limiting Config
│   │   ├── exceptions.py    # Global Exception Handlers
│   │   └── logger.py        # Structured Logging
│   ├── api/                 # API Endpoints
│   │   ├── v1/
│   │   │   ├── endpoints/
│   │   │   │   ├── auth.py
│   │   │   │   ├── editor.py    # CRUD, Locking, Draft
│   │   │   │   ├── files.py
│   │   │   │   ├── health.py    # Health Check
│   │   │   │   └── websocket.py # Real-time Status
│   │   │   └── api.py
│   │   └── deps.py
│   ├── schemas/             # Pydantic Data Models
│   │   ├── item.py          # Row Schema (with Versioning)
│   │   ├── audit.py         # Audit Log Schema
│   │   └── ...
│   ├── services/            # Business Logic
│   │   ├── file_service.py  # I/O, Backup, Rollback
│   │   ├── lock_service.py  # Redis Locking, Heartbeat
│   │   ├── auth_service.py  # Session Management
│   │   └── audit_service.py # Audit Logging
│   ├── db/
│   │   └── redis_client.py  # Connection Pool (Sentinel support)
│   └── templates/
├── static/
├── tests/
├── data/                    # Storage
│   ├── backups/             # Auto-backups
│   └── snapshots/           # Regular snapshots
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 4. 모듈화 전략 (Modularity Strategy)

### 4.1 Service Layer Pattern
- **`FileService`**: JSONL 인덱싱, Random Access, **Atomic Write**, **Backup & Rollback** 담당.
- **`LockService`**: Redis Sentinel 연동, **Heartbeat**, **Graceful Degradation**(Redis 장애 시 Fallback) 처리.
- **`AuditService`**: 사용자 행위(조회, 수정, 다운로드, 롤백 등)를 불변 로그로 기록.

### 4.2 Security & Middleware
- **CSRF Middleware**: 모든 상태 변경 요청(POST/PUT/DELETE)에 대한 토큰 검증.
- **Rate Limiting**: IP 및 사용자 기반 요청 제한으로 어뷰징 방지.
- **Session Security**: 로그인 시 세션 ID 재발급(Session Fixation 방지), IP/User-Agent 검증.

---

## 5. 핵심 기능 상세 요구사항

1.  **사용자 인증 및 세션 관리**
    *   **Auto-Login**: Redis 세션 (TTL 48h). 세션 하이재킹 방지 로직 포함.
    *   **Context Awareness**: 재접속 시 마지막 작업 위치 복원.

2.  **Row-Level Locking & Integrity (고도화)**
    *   **Heartbeat**: 클라이언트는 주기적(예: 30초)으로 Heartbeat를 전송하여 Lock(TTL 60분)을 연장. 만료 임박 시 경고 표시.
    *   **Optimistic Locking**: 데이터 Row에 `version` 필드를 도입. 저장 시점의 버전이 읽기 시점과 다르면 충돌(Conflict, 409) 처리하여 덮어쓰기 방지.
    *   **Graceful Degradation**: Redis 장애 발생 시 편집 기능을 비활성화하고 '읽기 전용' 모드로 전환하여 데이터 오염 방지.

3.  **데이터 편집 및 파일 처리**
    *   **Auto-Save (Draft)**: 작업 내용을 Redis에 주기적(예: 30초)으로 임시 저장. 브라우저 비정상 종료 시에도 작업 내용 복구 가능.
    *   **Backup & Rollback**: 저장 수행 전 자동으로 백업본 생성(24시간 유지). 관리자가 특정 시점으로 롤백할 수 있는 API 제공.
    *   **Audit Logging**: 누가, 언제, 어떤 데이터를 수정했는지 상세 기록(변경 전후 Diff 포함).

4.  **UI/UX (Web Browser)**
    *   **Real-time Feedback (WebSocket)**: 다른 사용자가 점유 중인 데이터(Lock)를 실시간으로 시각화(🔒 표시).
    *   **CSRF Protection**: Form 전송 시 CSRF 토큰 자동 포함.

---

## 6. 실행 계획 (Action Plan)

| Phase | Task | 상세 내용 |
| :--- | :--- | :--- |
| **Phase 1** | **Project Scaffold** | FastAPI, uv 설정, Redis Sentinel, Docker 환경 및 디렉터리 구조(Backup/Audit 포함) 생성 |
| **Phase 1.5**| **Integrity Core** | Lock Heartbeat, Optimistic Locking, 자동 백업 전략 및 Rollback API 구현 |
| **Phase 2** | **File & Auth** | `FileService`(Atomic Write), Session Security, Rate Limiting 구현 |
| **Phase 3** | **API & Security** | CSRF 보호, 편집 API, `AuditService` 연동, Draft(Auto-save) API |
| **Phase 4** | **UI & Real-time** | Jinja2 + HTMX, WebSocket(Lock 상태 동기화), 만료 경고 UI |
| **Phase 5** | **Ops & Test** | Health Check, Graceful Shutdown, 동시성/복구 테스트, E2E 검증 |
