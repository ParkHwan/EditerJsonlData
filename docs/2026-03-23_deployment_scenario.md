# Phase 14 배포 시나리오

> **작성일**: 2026-03-23  
> **대상 버전**: `60d3280` (fix: 편집 종료/취소 흐름 개선 + 한글 키 입력 IME 수정)  
> **이전 버전**: `716a77a` (bug: KaTex 렌더링 오류 수정) — 서버 운영 중  
> **환경**: GCP Compute Engine (linux/amd64)

---

## 1. 배포 개요

### 1.1 이번 배포에 포함된 커밋 (4건)

| 커밋 | 유형 | 설명 |
|---|---|---|
| `eaf9b1c` | feat | 파일 단위 Lock + 편집 키 생성/삭제 + UI 복원 |
| `bb6abfc` | feat | 다운로드 간소화 — JSONL 개별/전체 다운로드만 유지 |
| `4f6c84c` | fix | Starlette 1.0.0 TemplateResponse API 호환성 + DuckDB 버전 고정 |
| `60d3280` | fix | 편집 종료/취소 흐름 개선 + 한글 키 입력 IME 수정 |

### 1.2 변경된 파일 목록 (11개)

| 카테고리 | 파일 |
|---|---|
| API 엔드포인트 | `app/api/v1/endpoints/editor.py`, `files.py`, `gcs.py`, `websocket.py` |
| 서비스 | `app/services/lock_service.py`, `draft_service.py`, `render_service.py`, `audit_service.py` |
| 템플릿 | `app/templates/editor.html`, `gcs_files.html` |
| 의존성 | `pyproject.toml` (duckdb 버전 핀) |

### 1.3 주요 변경사항 요약

- **Lock 범위 변경**: row 단위 → **파일 단위** (Redis key 구조 변경: `lock:{file_id}`)
- **편집 시작 버튼 제거**: 페이지 진입 시 자동 Lock 획득
- **편집 종료**: Lock 해제 + working copy 보존 + 파일목록 이동
- **편집 취소**: working copy 삭제 + 날짜폴더로 이동
- **다운로드 간소화**: PNG/PDF 다운로드 제거, JSONL만 유지
- **키 CRUD**: add_info.unit_meta, 문제, 풀이에서 키 생성/삭제
- **한글 IME 수정**: prompt() → 커스텀 HTML 모달
- **DuckDB 버전 핀**: `duckdb>=1.5.0,<1.6.0`
- **Starlette 호환**: `TemplateResponse` API 시그니처 변경 대응

---

## 2. 사전 준비 (로컬)

### 2.1 코드 상태 확인

```bash
cd /Users/parkhwan/cw_project/data_processing/cw-de-process/2025/PROJ-14768_KT_EBS_Data-based_AI_Training_Data_Refinement/EditerJsonlData

# 워킹 트리 클린 확인
git status
# → "nothing to commit, working tree clean" 확인

# 최신 커밋 확인
git log --oneline -5
# → 60d3280 이 HEAD인지 확인
```

### 2.2 Docker Buildx 준비

```bash
# buildx 빌더 확인 (linux/amd64 지원 필요)
docker buildx ls

# 빌더가 없거나 amd64 미지원이면 생성
docker buildx create --name multiarch --use
docker buildx inspect --bootstrap
```

---

## 3. 이미지 빌드 & 푸시 (로컬)

### 3.1 이미지 빌드 + Artifact Registry 푸시

```bash
docker buildx build --platform linux/amd64 \
  -t asia-northeast1-docker.pkg.dev/crowdworks-platform/editer-jsonl/web:latest \
  -t asia-northeast1-docker.pkg.dev/crowdworks-platform/editer-jsonl/web:phase14 \
  --push .
```

> **참고**: `phase14` 태그를 함께 부여하여 롤백 시 이전 버전 식별 가능하게 합니다.  
> **소요 시간**: 약 3~5분 (의존성 캐시 레이어 활용, pyproject.toml 변경 시 더 길어질 수 있음)

### 3.2 빌드 확인

```bash
# Artifact Registry에 이미지 존재 확인
gcloud artifacts docker images list \
  asia-northeast1-docker.pkg.dev/crowdworks-platform/editer-jsonl \
  --include-tags --limit=5
```

---

## 4. 서버 배포 (GCP VM)

### 4.1 VM 접속

```bash
# 방법 1: gcloud SSH
gcloud compute ssh <VM_INSTANCE_NAME> --zone=<ZONE>

# 방법 2: 직접 SSH
ssh de@<VM_IP>
```

### 4.2 현재 서비스 상태 확인

```bash
cd ~/EditerJsonlData

# 현재 실행 중인 컨테이너 확인
docker compose -f docker-compose.prod.yml ps

# 현재 이미지 버전 확인
docker inspect editer-jsonl-web --format='{{.Image}}' | head -c 20
echo ""
docker images asia-northeast1-docker.pkg.dev/crowdworks-platform/editer-jsonl/web --format='{{.Tag}}\t{{.CreatedAt}}'

# 헬스체크
curl -s http://localhost/api/v1/health | python3 -m json.tool
```

### 4.3 DuckDB 백업 (중요!)

> DuckDB 버전이 1.5.0으로 핀 되었으므로, 기존 DB 파일 호환성 문제 대비 백업 필수

```bash
# DuckDB 파일 백업
cp data/editor.duckdb data/editor.duckdb.bak.pre-phase14 2>/dev/null || echo "DB 파일 없음 — 스킵"
```

### 4.4 Redis 데이터 확인

> Lock 구조가 row 단위 → 파일 단위로 변경되므로, 기존 row Lock이 남아있으면 정리 필요

```bash
# 기존 row-level Lock 키 확인
docker exec editer-jsonl-redis-master redis-cli KEYS "lock:*"

# 기존 Lock 키가 있으면 모두 삭제 (형식이 변경되므로)
docker exec editer-jsonl-redis-master redis-cli KEYS "lock:*" | xargs -r docker exec -i editer-jsonl-redis-master redis-cli DEL
echo "기존 Lock 키 정리 완료"
```

### 4.5 새 이미지 풀

```bash
docker compose -f docker-compose.prod.yml pull web
```

### 4.6 서비스 재시작 (무중단은 아님, 순간 다운타임 발생)

```bash
# web 컨테이너만 재시작 (Redis, Nginx는 유지)
docker compose -f docker-compose.prod.yml up -d web

# Nginx가 web 의존성을 가지므로 함께 재시작될 수 있음
# 필요 시 명시적으로:
docker compose -f docker-compose.prod.yml up -d
```

> **다운타임**: 약 10~20초 (이미지 교체 + 컨테이너 기동 + Healthcheck 통과)

---

## 5. 배포 후 검증

### 5.1 컨테이너 상태 확인

```bash
# 모든 컨테이너가 Up 상태인지 확인
docker compose -f docker-compose.prod.yml ps

# web 컨테이너 로그 확인 (최근 50줄)
docker compose -f docker-compose.prod.yml logs --tail=50 web

# 에러 로그만 필터
docker compose -f docker-compose.prod.yml logs web 2>&1 | grep -i "error\|traceback" | tail -20
```

### 5.2 헬스체크

```bash
# API 헬스체크
curl -s http://localhost/api/v1/health | python3 -m json.tool
# → "status": "healthy" 확인
```

### 5.3 기능 검증 체크리스트

| # | 검증 항목 | 확인 방법 | 예상 결과 |
|---|---|---|---|
| 1 | 로그인 | 브라우저에서 로그인 페이지 접속 | 정상 로그인 |
| 2 | GCS 파일 목록 | TASK1 → 날짜폴더 → 파일목록 | 파일 목록 정상 표시 |
| 3 | 편집 진입 | "편집" 버튼 클릭 | **자동 Lock 획득**, "편집 시작" 버튼 없음 |
| 4 | 편집 종료 | "편집 종료" 버튼 클릭 | 파일목록(날짜폴더)으로 이동 |
| 5 | 편집 재진입 | 같은 파일 "편집" 재클릭 | 이전 수정사항 유지된 상태로 표시 |
| 6 | 편집 취소 | "편집 취소" 버튼 클릭 | 날짜폴더로 이동, 수정사항 폐기 |
| 7 | 키 생성 | 편집 모드 → 키 추가 버튼 → "단일질문" 입력 | **"단일질문" 그대로 생성** (마지막 글자 누락 없음) |
| 8 | 키 삭제 | 키 삭제 버튼 → 확인 | 삭제 예정 표시, 저장 시 반영 |
| 9 | 멀티 유저 Lock | 다른 계정으로 같은 파일 접근 | "OO님이 편집 중" 배너, 편집 불가 |
| 10 | JSONL 다운로드 | JSONL 전체 다운로드 | ZIP 파일 다운로드 |
| 11 | GCS 업데이트 | 편집 후 "GCS 파일 업데이트" | GCS 반영 성공 |

### 5.4 DuckDB 호환성 확인

> **주의**: DuckDB는 단일 프로세스만 파일에 접근 가능. 실행 중인 Gunicorn 워커가 Lock을 잡고 있으므로 별도 프로세스로 직접 연결 불가.

```bash
# 방법 1: 헬스체크 API로 DuckDB 상태 확인 (권장)
curl -s http://localhost/api/v1/health | python3 -m json.tool
# → "status": "healthy" 확인 (DuckDB 연결 포함)

# 방법 2: DuckDB 모듈 버전만 확인
docker exec editer-jsonl-web python3 -c "
import duckdb
print(f'DuckDB version: {duckdb.__version__}')
print('DuckDB 모듈 로드 OK')
"

# 방법 3: 로그인 테스트 (DuckDB users 테이블 조회 성공 = 호환성 정상)
# 브라우저에서 로그인 성공 여부로 확인
```

> **DuckDB 직렬화 에러 발생 시**:
> ```bash
> # 기존 DB 파일 제거 → 서버 재시작 시 자동 재생성
> docker compose -f docker-compose.prod.yml stop web
> mv data/editor.duckdb data/editor.duckdb.bak.broken
> rm -f data/editor.duckdb.lock data/editor.duckdb.wal
> docker compose -f docker-compose.prod.yml up -d web
> # 초기 admin 계정 자동 생성됨 (kanjanggun@crowdworks.kr / admin1234)
> ```

---

## 6. 롤백 절차 (문제 발생 시)

### 6.1 이전 이미지로 롤백

```bash
# 이전 버전 이미지가 로컬에 남아있는 경우
docker images asia-northeast1-docker.pkg.dev/crowdworks-platform/editer-jsonl/web --format='{{.ID}}\t{{.Tag}}\t{{.CreatedAt}}'

# 이전 이미지 태그로 실행 (.env.production 또는 직접 지정)
DOCKER_IMAGE=asia-northeast1-docker.pkg.dev/crowdworks-platform/editer-jsonl/web:<이전태그> \
  docker compose -f docker-compose.prod.yml up -d web
```

### 6.2 DuckDB 롤백

```bash
# Phase 14 배포 전 백업으로 복원
docker compose -f docker-compose.prod.yml stop web
cp data/editor.duckdb.bak.pre-phase14 data/editor.duckdb
docker compose -f docker-compose.prod.yml up -d web
```

### 6.3 Redis Lock 정리

```bash
# 비정상 Lock이 남아있는 경우
docker exec editer-jsonl-redis-master redis-cli KEYS "lock:*" | \
  xargs -r docker exec -i editer-jsonl-redis-master redis-cli DEL
```

---

## 7. 배포 체크리스트 (요약)

```
사전 준비
  [ ] 로컬 git status clean 확인
  [ ] 최신 커밋(60d3280) HEAD 확인

이미지 빌드
  [ ] docker buildx build --platform linux/amd64 --push 완료
  [ ] Artifact Registry에 이미지 업로드 확인

서버 배포
  [ ] VM 접속
  [ ] DuckDB 백업 (data/editor.duckdb.bak.pre-phase14)
  [ ] Redis 기존 Lock 키 정리
  [ ] docker compose pull web
  [ ] docker compose up -d web
  [ ] 컨테이너 상태 확인 (ps)
  [ ] 웹 로그 에러 확인

배포 후 검증
  [ ] /api/v1/health → healthy
  [ ] 로그인 정상
  [ ] 편집 진입 시 자동 Lock 획득 (편집 시작 버튼 없음)
  [ ] 편집 종료 → 파일목록 이동
  [ ] 편집 취소 → 날짜폴더 이동
  [ ] 한글 키 생성 시 마지막 글자 정상
  [ ] 멀티 유저 Lock 정상
  [ ] DuckDB 호환성 확인
```
