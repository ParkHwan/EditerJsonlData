# 에러 대응 및 트러블슈팅 리포트

**작성일**: 2026-02-12 (최종 갱신: 2026-02-13)  
**작성자**: AI Assistant + 사용자 협업  
**대상 프로젝트**: EditerJsonlData  
**환경**: Docker Compose (macOS Darwin 25.2.0)

---

## 목차

1. [에러 요약 타임라인](#1-에러-요약-타임라인)
2. [ERR-01: Docker 이미지 빌드 실패](#2-err-01-docker-이미지-빌드-실패)
3. [ERR-02: MasterNotFoundError](#3-err-02-masternotfounderror)
4. [ERR-03: Sentinel 컨테이너 비정상 종료 (exit 1)](#4-err-03-sentinel-컨테이너-비정상-종료-exit-1)
5. [ERR-04: Sentinel DNS 해석 실패 (FATAL CONFIG FILE ERROR)](#5-err-04-sentinel-dns-해석-실패-fatal-config-file-error)
6. [ERR-05: Sentinel 컨테이너 조기 종료 (exit 0)](#6-err-05-sentinel-컨테이너-조기-종료-exit-0)
7. [ERR-06: 편집 모드 데이터 로드 실패 (Pydantic ValidationError)](#7-err-06-편집-모드-데이터-로드-실패-pydantic-validationerror)
8. [ERR-07: 저장 시 원본 데이터 필드 유실](#8-err-07-저장-시-원본-데이터-필드-유실)
9. [ERR-08: HTML 렌더링 원본 스크립트 불일치](#9-err-08-html-렌더링-원본-스크립트-불일치)
10. [최종 안정 구성](#10-최종-안정-구성)
11. [교훈 및 베스트 프랙티스](#11-교훈-및-베스트-프랙티스)

---

## 1. 에러 요약 타임라인

| 순서 | 에러 코드 | 에러 메시지 (요약) | 영향 범위 | 발견일 | 상태 |
|:---:|:---:|---|---|:---:|:---:|
| 1 | ERR-01 | `uv pip install --system .` build failed | Dockerfile | 02-12 | **해결** |
| 2 | ERR-02 | `MasterNotFoundError: No master found for 'mymaster'` | redis_client.py, docker-compose.yml | 02-12 | **해결** |
| 3 | ERR-03 | `container redis-sentinel exited (1)` | docker-compose.yml | 02-12 | **해결** |
| 4 | ERR-04 | `FATAL CONFIG FILE ERROR — Can't resolve hostname` | docker-compose.yml | 02-12 | **해결** |
| 5 | ERR-05 | `container redis-sentinel exited (0)` | docker-compose.yml | 02-12 | **해결** |
| 6 | ERR-06 | 편집 모드 진입 시 `ValidationError` (content 타입) | item.py, editor.py | 02-13 | **해결** |
| 7 | ERR-07 | 저장 시 `data_id`, `add_info` 등 원본 필드 유실 | file_service.py | 02-13 | **해결** |
| 8 | ERR-08 | HTML 렌더링이 원본 스크립트(JJIn_last.py)와 불일치 | render_service.py, base.html | 02-13 | **해결** |

---

## 2. ERR-01: Docker 이미지 빌드 실패

### 에러 메시지

```
failed to solve: process "/bin/sh -c uv pip install --system ." did not complete successfully: exit code: 1
```

### 근본 원인

`hatchling` 빌드 백엔드가 패키지 메타데이터를 구성할 때 `README.md`와 `app/__init__.py`가 필요하지만,
Dockerfile의 의존성 캐시 레이어에서 `pyproject.toml`만 복사한 상태로 `uv pip install --system .`을 실행했기 때문에 빌드 컨텍스트가 불완전했다.

### 수정 전 (Dockerfile)

```dockerfile
COPY pyproject.toml ./
RUN uv pip install --system .
```

### 수정 후 (Dockerfile)

```dockerfile
COPY pyproject.toml README.md ./
COPY app/__init__.py app/__init__.py
RUN uv pip install --system .
```

### 수정 파일

| 파일 | 변경 내용 |
|---|---|
| `Dockerfile` | `README.md`, `app/__init__.py` 복사 단계 추가 |
| `pyproject.toml` | `[tool.hatch.build.targets.wheel] packages = ["app"]` 추가 |
| `README.md` | 파일 신규 생성 (빌드 의존) |

### 핵심 교훈

> `hatchling` 기반 프로젝트에서 Docker 레이어 캐싱을 위해 `pyproject.toml`만 먼저 복사할 경우,
> `readme` 필드와 `packages` 설정에 해당하는 최소 파일도 함께 복사해야 한다.

---

## 3. ERR-02: MasterNotFoundError

### 에러 메시지

```python
raise MasterNotFoundError(f"No master found for {service_name!r}{error_info}")
redis.sentinel.MasterNotFoundError: No master found for 'mymaster'
```

### 근본 원인

**3가지 원인이 복합적으로 작용:**

| # | 원인 | 설명 |
|:---:|---|---|
| 1 | **Sentinel quorum 불일치** | `sentinel monitor mymaster redis-master 6379 2` — quorum=2인데 Sentinel 인스턴스가 1개뿐이라 과반수 합의 불가 |
| 2 | **서비스 기동 순서 미보장** | `depends_on`에 healthcheck condition이 없어 Sentinel이 준비되기 전에 앱이 연결 시도 |
| 3 | **redis_client.py 설정 미흡** | `socket_timeout=0.5`(너무 짧음), `decode_responses=True` 누락, 재시도 로직 없음 |

### 수정 내용

#### docker-compose.yml

```yaml
# Before: quorum=2 (Sentinel 1개로는 합의 불가)
sentinel monitor mymaster redis-master 6379 2

# After: quorum=1 (Sentinel 수에 맞춤)
sentinel monitor mymaster redis-master 6379 1
```

```yaml
# Before: 단순 depends_on (기동 순서만 보장, 준비 상태 미확인)
depends_on:
  - redis-sentinel

# After: healthcheck 기반 의존성 (실제 서비스 준비 후 시작)
depends_on:
  redis-sentinel:
    condition: service_healthy
```

#### redis_client.py — 전면 리팩토링

| 항목 | Before | After |
|---|---|---|
| `socket_timeout` | 0.5s | 5.0s |
| `decode_responses` (Sentinel) | 미설정 | `True` |
| 재시도 로직 | 없음 | 최대 5회, 2초 간격 |
| 연결 모드 분리 | if-else 1개 함수 | `_connect_sentinel()` / `_connect_standalone()` |

### 수정 파일

| 파일 | 변경 내용 |
|---|---|
| `docker-compose.yml` | quorum 2→1, healthcheck 추가, depends_on condition 추가 |
| `app/db/redis_client.py` | Sentinel/Standalone 분리, 재시도 로직, 타임아웃 증가 |

---

## 4. ERR-03: Sentinel 컨테이너 비정상 종료 (exit 1)

### 에러 메시지

```
dependency failed to start: container redis-sentinel exited (1)
```

### 근본 원인

`redis:7-alpine` 이미지에 `/etc/redis/` 디렉토리가 존재하지 않는다.  
Sentinel 설정 파일을 `/etc/redis/sentinel.conf`에 기록하려고 했으나 디렉토리가 없어 `echo` 명령이 실패했다.

```yaml
# 실패한 명령 — /etc/redis/ 디렉토리 없음
command: >
  sh -c 'echo "..." > /etc/redis/sentinel.conf && ...'
```

### 수정 내용

설정 파일 경로를 쓰기 가능한 `/tmp/sentinel.conf`로 변경:

```yaml
command: >
  sh -c '
    cat > /tmp/sentinel.conf <<EOF
    ...
    EOF
    redis-sentinel /tmp/sentinel.conf --protected-mode no
  '
```

### 핵심 교훈

> `redis:7-alpine` (및 대부분의 minimal 이미지)에서는 커스텀 디렉토리가 존재하지 않는다.  
> Sentinel은 런타임에 conf 파일을 수정하므로 반드시 **쓰기 가능한 경로**를 사용해야 한다.

---

## 5. ERR-04: Sentinel DNS 해석 실패 (FATAL CONFIG FILE ERROR)

### 에러 메시지

```
*** FATAL CONFIG FILE ERROR (Redis 7.4.7) ***
Reading the configuration file, at line 2
>>> 'sentinel monitor mymaster redis-master 6379 1'
Can't resolve instance hostname.
Failed to resolve hostname 'redis-master'
```

### 근본 원인

Redis Sentinel은 설정 파일을 **파싱하는 시점**에 `redis-master` 호스트명을 즉시 DNS 해석하려 한다.  
그런데 Docker Compose 내부 DNS는 네트워크가 완전히 구성된 후에야 작동하므로, Sentinel 컨테이너가 시작되는 순간에는 아직 `redis-master`를 해석할 수 없다.

### 수정 내용

Redis 6.2+에서 도입된 호스트명 지연 해석 옵션 추가:

```conf
# sentinel.conf
sentinel resolve-hostnames yes    # 호스트명을 런타임에 해석 (파싱 시 즉시 해석 X)
sentinel announce-hostnames yes   # IP 대신 호스트명으로 마스터 주소 전파
```

### 핵심 교훈

> Docker 환경에서 Redis Sentinel을 사용할 때 `sentinel resolve-hostnames yes`는 **필수**다.  
> 이 옵션 없이는 conf 파싱 시 DNS 해석이 실패하여 Sentinel이 시작조차 되지 않는다.

---

## 6. ERR-05: Sentinel 컨테이너 조기 종료 (exit 0)

### 에러 메시지

```
dependency failed to start: container redis-sentinel exited (0)
```

### 근본 원인

YAML `>` (folded scalar)가 `command` 값의 **모든 줄바꿈을 공백으로 변환**한다.  
이로 인해 heredoc(`<<EOF ... EOF`)가 한 줄로 합쳐져서 `sentinel.conf` 파일이 올바르게 생성되지 않았다.

**YAML 처리 과정:**

```yaml
# 작성한 코드 (의도)
command: >
  sh -c '
    cat > /tmp/sentinel.conf <<EOF
    port 26379
    sentinel monitor mymaster redis-master 6379 1
    EOF
    redis-sentinel /tmp/sentinel.conf
  '

# YAML 파서가 실제로 해석한 결과 (한 줄)
sh -c ' cat > /tmp/sentinel.conf <<EOF port 26379 sentinel monitor ... EOF redis-sentinel ...'
```

heredoc의 `EOF` 마커가 별도 줄에 위치하지 않아 인식되지 않고, 결과적으로 빈 conf 파일이 생성되어 Sentinel이 정상 종료(exit 0)했다.

### 수정 내용

**인라인 shell command + heredoc** 방식을 포기하고, 설정 파일을 호스트에서 별도 생성 후 **볼륨 마운트** 방식으로 전환:

```
EditerJsonlData/
└── redis/
    └── sentinel.conf    ← 호스트에서 직접 관리
```

```yaml
# docker-compose.yml
redis-sentinel:
  image: redis:7-alpine
  # 읽기 전용 마운트 → /tmp/로 복사 후 실행 (Sentinel이 conf를 수정하므로)
  command: sh -c "cp /etc/sentinel/sentinel.conf /tmp/sentinel.conf && redis-sentinel /tmp/sentinel.conf --protected-mode no"
  volumes:
    - ./redis/sentinel.conf:/etc/sentinel/sentinel.conf:ro
```

```conf
# redis/sentinel.conf
port 26379
sentinel resolve-hostnames yes
sentinel announce-hostnames yes
sentinel monitor mymaster redis-master 6379 1
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 60000
sentinel parallel-syncs mymaster 1
```

### 핵심 교훈

> Docker Compose의 `command`에서 복잡한 shell 스크립트 (heredoc 포함)를 사용하면 안 된다.  
> YAML scalar 타입(`>`, `|`, 따옴표)에 따라 줄바꿈 처리가 달라지므로 예측하기 어렵다.  
> **설정 파일은 별도로 관리하고 볼륨 마운트하는 것이 가장 안전하다.**

---

## 7. ERR-06: 편집 모드 데이터 로드 실패 (Pydantic ValidationError)

> **발견일**: 2026-02-13  
> **증상**: 에디터에서 "편집" 버튼 클릭 시 "데이터 로드 실패" 토스트 출력

### 에러 메시지

```
HTTP 500 Internal Server Error
pydantic_core._pydantic_core.ValidationError: 1 validation error for ItemBase
content
  Input should be a valid dictionary [type=dict_type, input_value='문제: 우리는 매일 물을...', input_type=str]
```

### 근본 원인

`ItemBase` Pydantic 모델이 `content: dict[str, Any]`로 정의되어 있어 **dict 타입만 허용**했다.  
그러나 실제 운영 JSONL 데이터(예: `EPT_1029_*.jsonl`)의 `content` 필드는 **문자열(string)** 형태이다.

```jsonc
// 기존 샘플 데이터 (개발 중 사용) — content가 dict
{"content": {"공통지문": "...", "단일질문": "..."}, "content_meta": {...}}

// 실제 운영 데이터 — content가 string
{"content": "문제: 우리는 매일 물을 사용합니다...", "add_info": {...}}
```

`get_row()` 메서드에서 `ItemBase(**data)` 호출 시 Pydantic 검증에 실패하여 500 에러가 발생했다.

또한 JSONL 데이터에는 `data_id`, `data_file`, `category_main` 등 `ItemBase`에 정의되지 않은 추가 필드가 있으나, Pydantic v2 기본 설정이 `extra="ignore"`가 아니므로 이 역시 잠재적 에러 원인이었다.

### 수정 내용

#### app/schemas/item.py

```python
# Before
class ItemBase(BaseModel):
    content: dict[str, Any] = Field(...)
    version: int = Field(1)
    modified_at: datetime | None = None
    modified_by: str | None = None

# After
class ItemBase(BaseModel):
    content: dict[str, Any] | str = Field(...)  # dict + string 모두 허용
    version: int = Field(1)
    modified_at: datetime | None = None
    modified_by: str | None = None

    model_config = {"extra": "ignore"}  # JSONL 추가 필드 안전 무시
```

#### app/api/v1/endpoints/editor.py

```python
# SaveRequest도 동일하게 수정
class SaveRequest(BaseModel):
    content: dict[str, Any] | str  # string content 저장 지원
    version: int
```

#### app/templates/editor.html (프론트엔드)

```javascript
// loadEditData: string content 처리 추가
if (typeof data.content === 'object') {
    // dict → 키별 textarea 생성
} else if (typeof data.content === 'string') {
    // string → 단일 textarea 생성 (min-height: 300px)
}

// collectEditContent: string 분기 추가
function collectEditContent() {
    const singleTa = document.querySelector('textarea[data-key="content"]');
    if (singleTa) return singleTa.value;  // string 반환
    // ... dict 처리
}
```

### 수정 파일

| 파일 | 변경 내용 |
|---|---|
| `app/schemas/item.py` | `content` Union 타입 + `model_config` 추가 |
| `app/api/v1/endpoints/editor.py` | `SaveRequest.content` Union 타입 |
| `app/templates/editor.html` | string content 편집/수집 지원 |

### 핵심 교훈

> 스키마 설계 시 **실제 운영 데이터의 형태**를 반드시 검증해야 한다.  
> 개발 단계에서 샘플 데이터로만 테스트하면, 운영 데이터의 타입 차이(dict vs string)를 놓칠 수 있다.  
> Pydantic v2의 `model_config = {"extra": "ignore"}`는 유연한 JSON 처리에 유용하다.

---

## 8. ERR-07: 저장 시 원본 데이터 필드 유실

> **발견일**: 2026-02-13  
> **증상**: 편집 후 저장 시 `data_id`, `add_info`, `data_file` 등 원본 필드가 삭제됨

### 근본 원인

`update_row_atomic()` 메서드가 저장 시 `update_data.model_dump()`의 결과만 새 Row 데이터로 사용했다.  
`ItemUpdate`에는 `content`, `version`, `modified_at`, `modified_by` 4개 필드만 있으므로, 원본 JSONL Row의 다른 모든 필드가 유실되었다.

```python
# Before (문제 코드)
new_data = update_data.model_dump()      # content, version, modified_at, modified_by만 포함
new_data["version"] += 1
new_line = json.dumps(new_data)          # data_id, add_info 등 모두 유실!
```

실제 JSONL Row는 10개 이상의 필드를 포함:

```
data_id, data_file, data_title, data_source, category_main, category_sub,
data_type, collected_date, content, add_info
```

### 수정 내용

#### app/services/file_service.py — `update_row_atomic()`

```python
# After (원본 보존)
original_data = await self.get_row_raw(file_id, row_idx)  # 원본 전체 dict
original_data["content"] = update_data.content              # content만 교체
original_data["version"] = update_data.version + 1
original_data["modified_at"] = datetime.now(tz=timezone.utc).isoformat()
original_data["modified_by"] = user_id

new_line = json.dumps(original_data, ensure_ascii=False)   # 모든 필드 보존!
```

**핵심 변경**: `model_dump()` 대신 `get_row_raw()`로 원본 raw dict를 읽고, `content` 필드만 교체.

### 수정 파일

| 파일 | 변경 내용 |
|---|---|
| `app/services/file_service.py` | `update_row_atomic` — 원본 raw dict 기반 content 교체 방식으로 변경 |

### 핵심 교훈

> **부분 업데이트(Partial Update)**를 구현할 때, 모델의 `model_dump()` 결과로 전체 Row를 덮어쓰면  
> 스키마에 정의되지 않은 원본 필드가 유실된다.  
> 반드시 **원본 데이터를 읽고 → 변경 필드만 교체 → 전체를 다시 저장**하는 패턴을 사용해야 한다.

---

## 9. ERR-08: HTML 렌더링 원본 스크립트 불일치

> **발견일**: 2026-02-13  
> **증상**: 에디터의 HTML 카드 렌더링이 원본 스크립트(JJIn_last.py)의 출력과 시각적으로 다름

### 근본 원인

`render_service.py`로 포팅하는 과정에서 원본 `JJIn_last.py`의 렌더링 로직이 일부 변경/누락되었다.

### 차이점 목록

| # | 위치 | 원본 (JJIn_last.py) | 포팅 후 (render_service.py) |
|:---:|---|---|---|
| 1 | `render_meta_inline` meta_key | `🏷️ {key}` | `{key}` (이모지 누락) |
| 2 | `render_meta_inline` 타입 배지 | `📊 Table`, `🖼️ Image`, `📈 Chart` | CSS 서브클래스, 이모지 없음 |
| 3 | `render_meta_inline` image/chart 텍스트 | `<strong>Text:</strong> ...` 접두사 | 모든 타입 동일 처리 (접두사 없음) |
| 4 | `render_meta_inline` 타입별 분기 | table/image/chart/기타 4분기 | 단일 통합 로직 |
| 5 | `render_item_card` 개념 배지 | `📚 개념` | `개념` |
| 6 | `render_item_card` 소스 메타 | `📄 Source` / `📖 Page` | `Source` / `Page` |
| 7 | `render_item_card` 에러 메시지 | `⚠️`, `❌` 접두사 | 접두사 없음 |
| 8 | `render_item_card` 섹션 테이블 | `<table>` 바로 시작 | `section-label` div 추가 (원본에 없음) |
| 9 | `_render_image_tag` 에러 | `⚠️ 이미지 파일 찾을 수 없음` | `이미지 파일 찾을 수 없음` |
| 10 | CSS `info-table th` | `width: 150px` | `width: 120px` |
| 11 | CSS `meta-type-badge` | 단일 `#FF6F00` 색상 | 3개 서브클래스 (table/image/chart) |

### 수정 내용

모든 항목을 원본과 동일하게 복원:

1. `render_meta_inline`: 🏷️ 이모지 복원, 타입별 4분기 로직 복원 (image/chart는 `<strong>Text:</strong>` 접두사)
2. `render_item_card`: 이모지 복원 (📚📄📖⚠️❌), `section-label` div 제거
3. `_render_image_tag`: ⚠️ 이모지 복원
4. CSS: `info-table th` width 150px 복원, `meta-type-badge` 서브클래스 제거, `section-label` 클래스 제거

### 수정 파일

| 파일 | 변경 내용 |
|---|---|
| `app/services/render_service.py` | `render_meta_inline`, `render_item_card`, `_render_image_tag` 원본 복원 |
| `app/templates/base.html` | CSS 원본 복원 (width, meta-type-badge, section-label 제거) |

### 핵심 교훈

> 기존 스크립트를 서비스 계층으로 포팅할 때, 이모지·CSS 클래스·타입별 분기 로직 등  
> **시각적 렌더링에 직접 영향을 미치는 세부 사항**이 누락되기 쉽다.  
> 포팅 후 반드시 원본 출력과 **시각적 비교(visual diff)**를 수행해야 한다.

---

## 10. 최종 안정 구성

### 아키텍처 다이어그램

```
┌──────────────┐     healthcheck     ┌──────────────┐
│  redis-master │ ◄─────────────────── │  redis-slave  │
│  (port 6379)  │     replicaof       │  (replica)    │
└──────┬───────┘                     └──────────────┘
       │
       │ sentinel monitor (quorum=1)
       │
┌──────┴───────┐     healthcheck     ┌──────────────┐
│redis-sentinel │ ◄─────────────────── │     web       │
│ (port 26379)  │   condition:healthy  │  (port 8000)  │
└──────────────┘                     └──────────────┘
       ▲
       │ volume mount (ro)
       │
  redis/sentinel.conf
```

### 서비스 기동 순서 (보장됨)

```
redis-master (healthcheck: ping) 
    ↓ condition: service_healthy
redis-slave (started)
    ↓ condition: service_started
redis-sentinel (healthcheck: ping on 26379, start_period: 10s)
    ↓ condition: service_healthy
web (FastAPI + redis_client.py 내 5회 재시도)
```

### 최종 파일 변경 목록

#### 2026-02-12 (ERR-01 ~ ERR-05: Docker / Redis 배포)

| 파일 | 변경 유형 | 설명 |
|---|:---:|---|
| `Dockerfile` | 수정 | `README.md`, `app/__init__.py` 복사 추가, `data/audit` 디렉토리 생성 |
| `pyproject.toml` | 수정 | `[tool.hatch.build.targets.wheel]` 섹션 추가 |
| `docker-compose.yml` | 수정 | quorum 수정, healthcheck 추가, 볼륨 마운트 방식 전환 |
| `redis/sentinel.conf` | **신규** | Sentinel 설정 파일 (호스트 관리) |
| `app/db/redis_client.py` | 수정 | Sentinel/Standalone 분리, 재시도, 타임아웃 개선 |
| `README.md` | **신규** | 빌드 의존 + 운영 문서 |

#### 2026-02-13 (ERR-06 ~ ERR-08: 데이터 호환성 + 렌더링 복원 + UI 개선)

| 파일 | 변경 유형 | 설명 |
|---|:---:|---|
| `app/schemas/item.py` | 수정 | `content: dict \| str` Union 타입, `model_config` 추가 |
| `app/services/file_service.py` | 수정 | `get_data_id_list()` 추가, `update_row_atomic()` 원본 보존 방식 변경 |
| `app/services/render_service.py` | 수정 | `render_meta_inline`, `render_item_card`, `_render_image_tag` 원본 복원 |
| `app/api/v1/endpoints/editor.py` | 수정 | `SaveRequest` 타입 수정, `get_data_id_list` / `get_rendered_card` API 추가 |
| `app/api/v1/endpoints/files.py` | 수정 | `view_file` → data_id 목록 전달 방식으로 변경 |
| `app/templates/editor.html` | **전면 재설계** | Master-Detail 레이아웃 (좌측 data_id 목록 + 우측 상세 뷰) |
| `app/templates/base.html` | 수정 | `content_full` 블록 추가, CSS 원본 복원 |

---

## 11. 교훈 및 베스트 프랙티스

### Docker Compose + Redis Sentinel 체크리스트

| # | 항목 | 설명 |
|:---:|---|---|
| 1 | **quorum = Sentinel 인스턴스 수의 과반** | 개발 환경에서 Sentinel 1개라면 quorum=1 |
| 2 | **`sentinel resolve-hostnames yes` 필수** | Docker DNS는 컨테이너 생성 후에야 작동 |
| 3 | **설정 파일은 볼륨 마운트** | YAML scalar에서 heredoc/복잡한 shell 사용 금지 |
| 4 | **Sentinel conf는 쓰기 가능 경로에** | Sentinel이 런타임에 conf 파일을 수정하므로 `:ro` 마운트 후 `/tmp/`로 복사 |
| 5 | **healthcheck + depends_on condition** | `service_healthy`로 실제 준비 상태 확인 후 다음 서비스 시작 |
| 6 | **애플리케이션 측 재시도 로직** | 인프라 준비 지연에 대비해 exponential backoff 적용 |

### hatchling + Docker 빌드 체크리스트

| # | 항목 | 설명 |
|:---:|---|---|
| 1 | **`readme` 참조 파일 복사** | `pyproject.toml`의 `readme` 필드가 가리키는 파일 필요 |
| 2 | **`packages` 명시** | `[tool.hatch.build.targets.wheel] packages = ["app"]` |
| 3 | **최소 패키지 구조 복사** | `app/__init__.py` 등 패키지 인식에 필요한 파일 선복사 |

### 데이터 모델 + 스키마 체크리스트 (2026-02-13 추가)

| # | 항목 | 설명 |
|:---:|---|---|
| 1 | **운영 데이터로 스키마 검증** | 개발 샘플이 아닌 실제 운영 JSONL로 Pydantic 모델 검증 |
| 2 | **Union 타입으로 유연성 확보** | `content: dict \| str` 처럼 여러 형태를 수용 |
| 3 | **`extra="ignore"` 설정** | 스키마에 정의되지 않은 필드가 있어도 안전하게 무시 |
| 4 | **부분 업데이트 시 원본 보존** | `model_dump()` 덮어쓰기 금지 → 원본 raw dict 읽고 필드만 교체 |
| 5 | **포팅 후 시각적 비교** | 원본 스크립트 출력과 포팅 결과를 나란히 비교하여 세부 차이 검출 |

---

> **결론 (종합)**:
>
> **2026-02-12** — Docker Compose + Redis Sentinel 구성에서 가장 빈번한 실패 원인은
> **(1) DNS 해석 타이밍**, **(2) 기동 순서 미보장**, **(3) YAML scalar 처리 차이**이다.
>
> **2026-02-13** — 서비스 레이어 포팅에서 가장 빈번한 실패 원인은
> **(4) 운영 데이터와 스키마 간 타입 불일치**, **(5) 부분 업데이트 시 원본 필드 유실**,
> **(6) 렌더링 세부 사항 (이모지, 분기 로직) 누락**이다.
>
> 위 체크리스트를 사전에 적용하면 대부분의 문제를 방지할 수 있다.
