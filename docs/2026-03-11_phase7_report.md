# Phase 7: 환경 분리 구성 (로컬 개발 / GCP 운영)

**작성일**: 2026-03-11  
**목적**: 로컬 개발 환경과 GCP Compute Engine 운영 환경을 분리하여 환경별로 적절한 설정, 보안, 서버 구성으로 서비스를 실행할 수 있도록 한다.

---

## 1. 아키텍처 개요

### 로컬 개발 환경

```
브라우저 → uvicorn --reload (단일 워커) → Redis Standalone → GCS (gcloud auth)
```

- **특징**: hot-reload, DEBUG 로깅, HTTP, 단일 Redis 컨테이너
- **실행**: `make dev` (bare-metal) 또는 `make dev-docker` (Docker)

### GCP Compute Engine 운영 환경

```
브라우저 → Nginx (SSL 종단) → Gunicorn + Uvicorn Workers → Redis Sentinel HA → GCS (ADC)
```

- **특징**: 멀티 워커, INFO 로깅, HTTPS 강제, JSON 로그 포맷, OpenAPI docs 비활성화
- **실행**: `make prod`

---

## 2. 변경/신규 파일 상세

### 2.1 `app/core/config.py` (수정)

| 새 필드 | 기본값 | 설명 |
|---------|--------|------|
| `ENVIRONMENT` | `"local"` | `"local"` \| `"production"` |
| `DEBUG` | `True` | 운영 시 `False` |
| `LOG_LEVEL` | `"DEBUG"` | 운영 시 `"INFO"` |
| `WORKERS` | `1` | Gunicorn 워커 수 (0 = CPU 자동) |
| `ALLOWED_HOSTS` | `"*"` | 운영 시 도메인 제한 |
| `GUNICORN_TIMEOUT` | `120` | Gunicorn 워커 타임아웃(초) |

- `is_production` 프로퍼티: 환경 판별 편의 메서드
- `effective_workers` 프로퍼티: WORKERS=0이면 CPU 코어 기반 자동 계산

### 2.2 환경 변수 파일 (신규)

| 파일 | 용도 |
|------|------|
| `.env` | 기본 로컬 환경 (비-Docker) |
| `.env.local` | Docker Compose 로컬 개발용 |
| `.env.production` | Docker Compose 운영용 (비밀키 교체 필수) |
| `.env.example` | 템플릿 (Git 추적용) |

### 2.3 `docker-compose.yml` (수정)

- Redis Standalone 단일 컨테이너로 간소화 (Sentinel 제거)
- `env_file: .env.local` 참조
- 볼륨 마운트로 코드 변경 즉시 반영

### 2.4 `docker-compose.prod.yml` (신규)

- **nginx**: 포트 80/443 노출, SSL 종단, 정적 파일 직접 서빙
- **web**: Gunicorn + Uvicorn Workers, 포트 비노출 (Nginx 경유만)
- **redis-master / redis-slave / redis-sentinel**: HA 구성
- `restart: always` 전 서비스 적용

### 2.5 `gunicorn.conf.py` (신규)

- 환경 변수 기반 워커 수 결정 (`WORKERS` 또는 CPU*2+1)
- `worker_class: uvicorn.workers.UvicornWorker`
- `preload_app: True` (메모리 공유)
- `max_requests: 1000` + jitter (메모리 누수 방지)

### 2.6 `nginx/nginx.conf` (신규)

- HTTP→HTTPS 301 리다이렉트
- upstream gunicorn 프록시
- `/static/` 직접 서빙 (Gunicorn 부하 감소)
- WebSocket 프록시 (`/api/v1/ws/`) upgrade 설정
- 보안 헤더 (HSTS, X-Frame-Options 등)
- `nginx/nginx-http-only.conf`: SSL 없이 HTTP-only 운영용 대체 설정

### 2.7 `app/core/logger.py` (수정)

- `settings.LOG_LEVEL` 기반 동적 로그 레벨
- 운영 환경: `JSONFormatter`로 구조화 JSON 로그 출력
- 로컬 환경: 기존 읽기 쉬운 텍스트 포맷 유지

### 2.8 `Makefile` (신규)

| 명령어 | 설명 |
|--------|------|
| `make dev` | uvicorn --reload 로컬 실행 |
| `make dev-docker` | Docker Compose 로컬 실행 |
| `make prod` | 운영 환경 Docker 실행 |
| `make prod-logs` | 운영 로그 확인 |
| `make prod-down` | 운영 중지 |
| `make prod-restart` | 운영 재시작 |
| `make ssl-self-signed` | 자체 서명 SSL 인증서 생성 |

### 2.9 `app/main.py` (수정)

- 운영 환경 시 OpenAPI docs (`/docs`, `/redoc`, `openapi.json`) 비활성화
- lifespan 로깅에 환경 정보(env, debug, workers) 포함

### 2.10 `Dockerfile` (수정)

- CMD를 `gunicorn.conf.py` 참조로 변경
- 로컬 Docker에서는 `docker-compose.yml`의 command로 uvicorn --reload 덮어씀
- `logs/` 디렉터리 자동 생성 추가

---

## 3. 운영 배포 가이드

### 3.1 최초 배포

```bash
# 1. 저장소 클론
git clone <repo-url> && cd EditerJsonlData

# 2. 운영 환경 변수 설정
cp .env.production .env.production.bak
# SECRET_KEY, ALLOWED_HOSTS 등 수정
vi .env.production

# 3. SSL 인증서 (테스트용 자체 서명)
make ssl-self-signed
# 또는 Let's Encrypt: certbot certonly --standalone -d your-domain.com

# 4. 서비스 시작
make prod
```

### 3.2 HTTP-only 모드 (SSL 없이)

SSL 인증서 없이 운영할 경우 `docker-compose.prod.yml`의 nginx 볼륨을 수정:

```yaml
volumes:
  - ./nginx/nginx-http-only.conf:/etc/nginx/nginx.conf:ro
```

### 3.3 롤링 업데이트

```bash
# 코드 업데이트 후
git pull
make prod-build
make prod-restart
```

---

## 4. 환경별 설정 비교

| 항목 | 로컬 (local) | 운영 (production) |
|------|-------------|------------------|
| 서버 | uvicorn --reload | Gunicorn + Uvicorn Workers |
| 워커 수 | 1 | CPU*2+1 (또는 WORKERS 지정) |
| 리버스 프록시 | 없음 | Nginx |
| Redis | Standalone | Sentinel HA (Master/Slave) |
| 로그 레벨 | DEBUG | INFO |
| 로그 포맷 | 텍스트 | JSON |
| OpenAPI docs | 활성화 | 비활성화 |
| HTTPS | 비활성 | 활성 (Nginx SSL 종단) |
| Cookie Secure | false | true |
| Rate Limit | 100/min | 60/min |
