# 버그 수정 리포트 (2026-03-20)

> GCP VM 배포 과정에서 발견된 버그 및 운영 환경 이슈 수정 기록

---

## BUG-01: Docker 이미지 아키텍처 불일치

| 항목 | 내용 |
|---|---|
| **증상** | `no matching manifest for linux/amd64 in the manifest list entries` |
| **원인** | 로컬(Mac ARM/apple silicon)에서 빌드한 이미지가 `linux/arm64`로 생성되어 GCP VM(x86_64)에서 실행 불가 |
| **수정** | `docker buildx build --platform linux/amd64 --push` 로 크로스 플랫폼 빌드 |

### 재현 명령

```bash
# 기존 (문제)
docker build -t IMAGE_TAG .

# 수정 (amd64 지정 빌드)
docker buildx build --platform linux/amd64 -t IMAGE_TAG --push .
```

---

## BUG-02: Docker 컨테이너 사용자 오류

| 항목 | 내용 |
|---|---|
| **증상** | `unable to find user de: no matching entries in passwd file` |
| **원인** | Dockerfile에서 비루트 사용자 생성(`useradd`)이 QEMU 에뮬레이션(크로스 플랫폼 빌드) 환경에서 정상 동작하지 않음 |
| **수정** | `USER` 지시어 및 사용자 생성 로직 제거, 컨테이너는 root로 실행 |

### 변경 파일

- `Dockerfile` — `groupadd`, `useradd`, `USER` 지시어 제거

---

## BUG-03: Redis Sentinel 설정 파일 누락

| 항목 | 내용 |
|---|---|
| **증상** | `dependency failed to start: container editer-jsonl-redis-sentinel is unhealthy`, `cp: omitting directory '/etc/sentinel/sentinel.conf'` |
| **원인** | VM에 `redis/sentinel.conf` 파일이 없는 상태에서 Docker Compose가 볼륨 마운트 시 **디렉터리**로 자동 생성 |
| **수정** | 잘못 생성된 디렉터리 삭제 → 올바른 `sentinel.conf` 파일 생성 → 컨테이너 재시작 |

### sentinel.conf 내용

```conf
port 26379
sentinel resolve-hostnames yes
sentinel announce-hostnames yes
sentinel monitor mymaster redis-master 6379 1
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 60000
sentinel parallel-syncs mymaster 1
```

---

## BUG-04: Nginx SSL 인증서 부재

| 항목 | 내용 |
|---|---|
| **증상** | Nginx `Restarting (1)`, `cannot load certificate "/etc/nginx/ssl/server.crt": BIO_new_file() failed` |
| **원인** | `nginx.conf`에 SSL 설정이 포함되어 있으나 인증서 파일(`server.crt`, `server.key`)이 VM에 없음 |
| **수정** | HTTP-only 설정 파일(`nginx-http-only.conf`) 생성 및 `docker-compose.prod.yml` 수정 |

### 변경 파일

- `nginx/nginx-http-only.conf` — 신규 생성 (HTTP-only 프록시 설정)
- `docker-compose.prod.yml` — `443:443` 포트 제거, SSL 볼륨 제거, HTTP-only 설정 마운트

---

## BUG-05: DuckDB 파일 잠금 충돌

| 항목 | 내용 |
|---|---|
| **증상** | `IO Error: Could not set lock on file "/app/data/editor.duckdb"`, `504 Gateway Time-out` |
| **원인** | Gunicorn `WORKERS=4` 설정으로 다중 프로세스가 DuckDB 파일에 동시 접근 (DuckDB는 단일 프로세스 쓰기만 지원) |
| **수정** | `.env.production`에서 `WORKERS=1`로 변경 |

### 변경 파일

- `.env.production` — `WORKERS=4` → `WORKERS=1`

### 후속 조치

- DuckDB의 단일 Writer 제한은 구조적 한계. 향후 동시 접속 증가 시 PostgreSQL 등 별도 DB 전환 검토 필요

---

## BUG-06: 로그인 리다이렉트 무한 루프

| 항목 | 내용 |
|---|---|
| **증상** | 로그인 성공 후 다시 로그인 페이지로 돌아옴 (무한 리다이렉트) |
| **원인** | `SESSION_COOKIE_SECURE=true` + HTTP 접속 → 브라우저가 `Secure` 플래그 쿠키를 HTTP에서 전송하지 않음 |
| **수정** | `SESSION_COOKIE_SECURE=false`, `SESSION_COOKIE_SAMESITE=lax` |

### 변경 파일

- `.env.production` — `SESSION_COOKIE_SECURE=true` → `false`, `SESSION_COOKIE_SAMESITE=strict` → `lax`

### 주의사항

- HTTPS 적용 시 `SESSION_COOKIE_SECURE=true`로 반드시 복원해야 함

---

## BUG-07: GCS 신규 업로드 파일 미반영

| 항목 | 내용 |
|---|---|
| **증상** | GCS에 `20260319` 날짜로 파일 업로드 후 웹에서 보이지 않음 |
| **원인** | DuckDB-first 동기화 전략이 하루 1회만 GCS 동기화 수행 (`registry_sync` 테이블 기반). 이미 동기화 기록이 있으면 같은 날 재동기화 안 함. 또한 `GCS_CREDENTIALS_PATH`가 비어있어 인증 실패 |
| **수정** | (1) `GCS_CREDENTIALS_PATH` 명시적 설정, (2) 수동 동기화 API 추가 (별도 개선사항 참조) |

### 변경 파일

- `.env.production` — `GCS_CREDENTIALS_PATH=/app/.security/gcs-credentials.json`

### 동기화 로직 흐름

```
사용자 → GET /gcs/browse?task=task1
  ↓
DuckDB에 해당 task 폴더 데이터 있음?
  ├── Yes → DuckDB에서 바로 응답 (GCS 미조회)
  │         └── 오늘 동기화 기록 있음? → 없으면 백그라운드 동기화 시작
  └── No  → GCS에서 블로킹 동기화 후 응답
```

---

## BUG-08: `NameError: name 'Path' is not defined`

| 항목 | 내용 |
|---|---|
| **증상** | `app/main.py` 실행 시 `NameError` |
| **원인** | `pathlib.Path` import 누락 |
| **수정** | `from pathlib import Path` 추가 |

### 변경 파일

- `app/main.py` — `from pathlib import Path` 추가
