# EditerJsonlData

JSONL 파일을 웹 브라우저에서 Row 단위로 조회/편집/저장할 수 있는 내부 임직원용 서비스.

## 주요 기능

- MathJax 수식 렌더링 지원 JSONL 뷰어
- Row 단위 분산 Lock (Redis) + Optimistic Locking
- 대용량 파일 지원 (Line Index 기반 Random Access)
- 세션 기반 인증 + CSRF 보호
- 감사 로그 (일자별 JSONL 기록)
- 편집 중 Auto-save (Redis Draft, 30초 주기)
- Rate Limiting (IP/사용자 기반)

---

## Quick Start (로컬 개발)

### 1. 사전 요구사항

- **Python** 3.10+
- **Redis** 6+ (로컬 실행 필요)
- **uv** (Python 패키지 매니저)

```bash
# uv 설치 (미설치 시)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. 프로젝트 설정

```bash
# 프로젝트 디렉터리 이동
cd EditerJsonlData

# 가상환경 생성 + 의존성 설치
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# 환경 변수 설정
cp .env.example .env
# 필요 시 .env 수정
```

### 3. Redis 실행

**방법 A) Docker (권장)**

```bash
docker run -d --name redis-dev -p 6379:6379 redis:7-alpine
```

**방법 B) Homebrew (macOS)**

```bash
brew install redis
brew services start redis
```

**방법 C) 직접 설치**

```bash
redis-server --daemonize yes
```

Redis 연결 확인:

```bash
redis-cli ping
# 응답: PONG
```

### 4. 앱 실행

```bash
# 가상환경 활성화 확인
source .venv/bin/activate

# 개발 서버 실행
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 5. 브라우저 접속

```
http://localhost:8000
```

자동으로 로그인 페이지로 이동합니다.

1. **사용자 ID**: 아무 값 입력 (예: `hong.gildong`)
2. **이름**: 표시 이름 입력 (예: `홍길동`)
3. 로그인 후 파일 목록 페이지에서 `sample_ebs.jsonl` 클릭
4. 항목의 **편집** 버튼으로 수정 가능

---

## Docker Compose (전체 스택)

Redis Sentinel HA 구성 포함:

```bash
docker compose up -d

# 로그 확인
docker compose logs -f web
```

접속: `http://localhost:8000`

중지:

```bash
docker compose down
```

---

## 프로젝트 구조

```
EditerJsonlData/
├── app/
│   ├── main.py                     # FastAPI 앱 진입점
│   ├── core/
│   │   ├── config.py               # 설정 (환경 변수)
│   │   ├── csrf.py                 # CSRF 보호 설정
│   │   ├── exceptions.py           # 전역 예외 핸들러
│   │   ├── logger.py               # 로깅 설정
│   │   └── rate_limit.py           # Rate Limiting (slowapi)
│   ├── db/
│   │   └── redis_client.py         # Redis 연결 관리
│   ├── services/
│   │   ├── file_service.py         # JSONL 파일 I/O (LineIndex)
│   │   ├── lock_service.py         # 분산 Lock (Redis)
│   │   ├── auth_service.py         # 세션 인증 (Redis)
│   │   ├── audit_service.py        # 감사 로그
│   │   ├── draft_service.py        # Draft / Auto-save
│   │   └── render_service.py       # HTML 렌더링 (MathJax 등)
│   ├── api/
│   │   ├── deps.py                 # 공통 DI
│   │   └── v1/
│   │       ├── api.py              # 라우터 통합
│   │       └── endpoints/
│   │           ├── auth.py         # 로그인/로그아웃 API
│   │           ├── editor.py       # Lock + Data CRUD API
│   │           ├── draft.py        # Draft API
│   │           └── files.py        # HTML 뷰 (파일 목록/뷰어)
│   ├── schemas/
│   │   ├── item.py                 # JSONL Row 스키마
│   │   └── audit.py                # 감사 로그 스키마
│   └── templates/
│       ├── base.html               # 레이아웃 (MathJax, CSRF, Toast)
│       ├── login.html              # 로그인 페이지
│       ├── index.html              # 파일 목록 페이지
│       └── editor.html             # 편집기 (Auto-save, Draft 복원)
├── data/                           # JSONL 데이터 디렉터리
│   ├── sample_ebs.jsonl            # 샘플 데이터 (5개 항목)
│   ├── backups/                    # 자동 백업
│   └── audit/                      # 감사 로그
├── tests/
│   ├── conftest.py                 # 테스트 Fixture (fakeredis)
│   ├── unit/                       # 단위 테스트
│   └── integration/                # 통합 테스트
├── docs/                           # 개발 문서
├── static/                         # 정적 파일 (CSS, JS)
├── pyproject.toml                  # 의존성 + 도구 설정
├── Dockerfile                      # 컨테이너 빌드
├── docker-compose.yml              # 전체 스택 (Redis Sentinel)
├── .env                            # 환경 변수 (로컬)
└── .env.example                    # 환경 변수 템플릿
```

---

## 사용법

### JSONL 데이터 추가

`data/` 폴더에 `.jsonl` 파일을 넣으면 자동으로 목록에 표시됩니다.

```bash
# 기존 JSONL 파일 복사
cp /path/to/your-data.jsonl data/
```

각 Row의 JSON 구조:

```json
{
  "content": {
    "id": "Q001",
    "question": "문제 내용...",
    "answer": "정답"
  },
  "version": 1
}
```

- `content`: 실제 데이터 (자유 구조)
- `version`: Optimistic Locking 용 (자동 관리, 없으면 1로 초기화)

### 편집 흐름

1. 파일 목록에서 파일 선택
2. 항목의 **편집** 버튼 클릭 → Lock 획득
3. textarea에서 내용 수정 (30초마다 자동 저장)
4. **저장** 클릭 → Atomic Write + Lock 해제
5. **취소** 또는 ESC → Lock 해제 (Draft 삭제)
6. **Ctrl+S** → 즉시 저장

### 주요 API 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/api/v1/auth/login` | 로그인 |
| `POST` | `/api/v1/auth/logout` | 로그아웃 |
| `GET` | `/api/v1/auth/me` | 현재 사용자 정보 |
| `GET` | `/api/v1/view/files` | 파일 목록 (HTML) |
| `GET` | `/api/v1/view/files/{file_id}` | 파일 뷰어 (HTML) |
| `GET` | `/api/v1/editor/data/{file_id}/{row_idx}` | Row 데이터 조회 |
| `PUT` | `/api/v1/editor/data/{file_id}/{row_idx}` | Row 저장 |
| `POST` | `/api/v1/editor/lock/{file_id}/{row_idx}` | Lock 획득 |
| `DELETE` | `/api/v1/editor/lock/{file_id}/{row_idx}` | Lock 해제 |
| `POST` | `/api/v1/editor/draft/{file_id}/{row_idx}` | Draft 저장 |
| `GET` | `/api/v1/editor/draft/{file_id}/{row_idx}` | Draft 조회 |

Swagger UI: `http://localhost:8000/api/v1/openapi.json`

---

## 테스트

```bash
# 전체 테스트 실행
pytest tests/ -v

# 커버리지 리포트 (HTML)
pytest tests/ --cov=app --cov-report=html:htmlcov
open htmlcov/index.html

# 단위 테스트만
pytest tests/unit/ -v

# 통합 테스트만
pytest tests/integration/ -v
```

---

## 환경 변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `SECRET_KEY` | `changethis-...` | CSRF/세션 서명용 비밀키 |
| `REDIS_HOST` | `localhost` | Redis 호스트 |
| `REDIS_PORT` | `6379` | Redis 포트 |
| `DATA_DIR` | `data` | JSONL 데이터 디렉터리 |
| `BACKUP_DIR` | `data/backups` | 백업 디렉터리 |
| `AUDIT_DIR` | `data/audit` | 감사 로그 디렉터리 |
| `SESSION_TTL_HOURS` | `48` | 세션 유효 시간 |
| `SESSION_COOKIE_SECURE` | `false` | HTTPS 전용 쿠키 (프로덕션: `true`) |
| `RATE_LIMIT_DEFAULT` | `100/minute` | 기본 Rate Limit |
| `RATE_LIMIT_WRITE` | `30/minute` | 쓰기 Rate Limit |
| `DRAFT_TTL_SECONDS` | `1800` | Draft 유효 시간 (30분) |
| `DRAFT_AUTO_SAVE_INTERVAL` | `30` | 자동 저장 주기 (초) |

---

## 트러블슈팅

### Redis 연결 실패

```
Redis connection failed (service will start in degraded mode)
```

- Redis가 실행 중인지 확인: `redis-cli ping`
- `.env`의 `REDIS_HOST`, `REDIS_PORT` 확인
- Redis 없이도 앱은 시작되지만, Lock/세션/Draft 기능 사용 불가

### 파일 목록이 비어 있음

- `data/` 폴더에 `.jsonl` 파일이 있는지 확인
- 샘플 데이터: `data/sample_ebs.jsonl` (프로젝트에 포함)

### MathJax 수식이 렌더링되지 않음

- 인터넷 연결 필요 (CDN에서 MathJax 로드)
- `\\(`, `\\)` 또는 `\\[`, `\\]`로 수식 래핑 필요

---

## 개발 Phase

| Phase | 상태 | 내용 |
|-------|------|------|
| 1 | DONE | 프로젝트 스캐폴드 (FastAPI, Redis, Docker) |
| 1.5 | DONE | Lock Heartbeat, Optimistic Locking, Atomic Write |
| 2 | DONE | LineIndex, 세션 인증, Rate Limiting, PM HTML 포맷 |
| 3 | DONE | CSRF 보호, 감사 로그, Draft/Auto-save |
| 4 | TODO | HTMX + WebSocket 실시간 Lock 동기화 |
| 5 | TODO | Health Check, Graceful Shutdown, 동시성 테스트 |
