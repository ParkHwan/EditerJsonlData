# 개발 보고서 — 2026-02-12

> **프로젝트**: Editer ResultData Jsonl  
> **작업자**: parkhwan  
> **날짜**: 2026-02-12  
> **범위**: PM HTML 포맷 접목 + Phase 2 (File & Auth) 전체 구현

---

## 1. 작업 개요

오늘 수행한 작업은 크게 두 가지입니다.

1. **PM 스크립트(`JJIn_last.py`)의 HTML/렌더링 로직을 서비스에 접목**
2. **Phase 2 (File & Auth) 전체 구현 완료**

---

## 2. PM HTML 포맷 접목

### 2.1 반영 항목

PM이 AI 도움을 받아 만든 `JJIn_last.py` 스크립트에서 아래 로직을 추출하여 웹 서비스 구조에 맞게 포팅했습니다.

| PM 스크립트 로직 | 적용 위치 | 설명 |
|---|---|---|
| CSS 전체 (카드 레이아웃, 색상코드, 테이블) | `app/templates/base.html` | Jinja2 베이스 템플릿, 네비게이션/Toast 알림 추가 |
| MathJax CDN + 수식 보존 | `base.html` + `render_service.py` | `protect/restore_math_expressions` 서버 사이드 포팅 |
| `process_content_with_tags` | `render_service.py` | content 내 `<태그>` → `content_meta` 연결 렌더링 |
| `render_meta_inline` | `render_service.py` | 이미지/테이블/차트 타입별 인라인 렌더링 |
| `render_match_table` | `render_service.py` | `[[left], [right]]` 매칭 테이블 렌더링 |
| `render_bogi` | `render_service.py` | 보기: `list` → ㄱㄴㄷ순번, `dict` → 키-값 쌍 |
| 이미지 base64 변환 | `render_service.py` | `get_image_base64`, JSONL-이미지 폴더 매칭 |
| 키별 색상 맵 (`BG_COLORS`) | `render_service.py` | 공통질문/선택지/정답/해설 등 색상 유지 |
| `generate_html` 전체 렌더링 | `render_service.py` → `render_item_card()` | 단일 아이템 → HTML 카드 변환 |

### 2.2 제외 항목

| PM 스크립트 로직 | 제외 사유 |
|---|---|
| `validate_data_structure` | 사용자 요청에 따라 제외 |
| 엑셀 리포트 (`openpyxl`) | 모니터링 전용, 웹 서비스에 불필요 |
| tkinter 파일 선택 GUI | 웹 서비스이므로 불필요 |

### 2.3 생성/수정 파일

| 파일 | 상태 | 역할 |
|---|---|---|
| `app/services/render_service.py` | 신규 | PM 렌더링 로직 서비스화 |
| `app/templates/base.html` | 작성 | CSS + MathJax + 네비게이션 |
| `app/templates/index.html` | 작성 | 파일 목록 페이지 |
| `app/templates/editor.html` | 작성 | 아이템 뷰어 + 편집 모달 + Lock 연동 JS |
| `app/api/v1/endpoints/files.py` | 작성 | 뷰 엔드포인트 (목록/뷰어/다운로드) |
| `app/api/v1/api.py` | 작성 | 라우터 통합 (editor + view) |
| `app/core/logger.py` | 작성 | 로거 구현 |
| `app/main.py` | 수정 | `/` → 파일 목록 리다이렉트 |

---

## 3. Phase 2: File & Auth 구현

### 3.1 FileService 고도화 — Line Indexer

**문제**: 기존 `get_row()`는 전체 파일을 `readlines()`로 메모리에 올려 O(N) 시간이 소요.

**해결**: `LineIndex` 클래스 도입으로 바이트 오프셋 기반 Random Access 구현.

```
┌─────────────────────────────────────────────────────┐
│  LineIndex                                          │
│                                                     │
│  offsets[0] = 0      → Row 0 시작 위치 (byte)       │
│  offsets[1] = 245    → Row 1 시작 위치              │
│  offsets[2] = 512    → Row 2 시작 위치              │
│  ...                                                │
│                                                     │
│  get_row(file_id, 1)                                │
│    → seek(245) → readline() → JSON parse            │
│    → 전체 파일 로드 없이 특정 Row만 읽기            │
└─────────────────────────────────────────────────────┘
```

**주요 특징:**
- 인덱스 캐시: `_index_cache[file_id]` → 반복 조회 시 재구축 방지
- Stale 감지: `file.st_mtime` 비교로 파일 변경 시 자동 재구축
- 무효화: `update_row_atomic()` 후 `_invalidate_index()` 자동 호출

**신규 메서드:**

| 메서드 | 설명 |
|---|---|
| `list_files()` | `data/` 디렉터리의 JSONL 파일 목록 반환 |
| `get_total_rows(file_id)` | 파일의 전체 Row 수 |
| `get_row(file_id, row_idx)` | 인덱스 기반 단일 Row 읽기 (Random Access) |
| `get_row_raw(file_id, row_idx)` | 스키마 검증 없이 raw dict 반환 |
| `get_rows_paginated(file_id, page, per_page)` | 페이지네이션 조회 |
| `cleanup_old_backups(file_id, keep_hours)` | 오래된 백업 자동 정리 |

### 3.2 AuthService — Redis 세션 기반 인증

**설계 원칙:**
- 내부 임직원 전용 서비스 → 간소화된 세션 기반 인증
- 세션 고정 공격 방지 (로그인 시 새 세션 ID 발급)
- IP / User-Agent 변경 감지 (경고 로그)

**세션 흐름:**

```
[로그인]
  POST /api/v1/auth/login
  → body: { user_id, display_name }
  → Redis에 세션 생성 (TTL 48h)
  → Set-Cookie: session_id=<token>

[인증된 요청]
  GET /api/v1/editor/data/...
  → Cookie: session_id=<token>
  → deps.get_current_user() → Redis 세션 조회
  → 유효 → user_id 자동 추출

[로그아웃]
  POST /api/v1/auth/logout
  → Redis 세션 삭제
  → 쿠키 삭제
```

**구현 파일:**

| 파일 | 역할 |
|---|---|
| `app/services/auth_service.py` | 세션 CRUD, 검증, 쿠키 관리 |
| `app/api/v1/endpoints/auth.py` | 로그인/로그아웃/현재사용자 엔드포인트 |
| `app/api/deps.py` | `get_current_user`, `get_optional_user` 의존성 |
| `app/templates/login.html` | 로그인 UI 페이지 |

### 3.3 Rate Limiting (slowapi)

**설정:**

| 엔드포인트 | 제한 | 사유 |
|---|---|---|
| 파일 목록/뷰어 (GET) | 100/분 | 일반 조회 |
| Lock 획득 (POST) | 30/분 | 쓰기 작업 |
| 데이터 저장 (PUT) | 30/분 | 쓰기 작업 |
| 파일 다운로드 | 10/분 | 대역폭 보호 |

**키 전략:**
- 인증된 사용자: `user:<user_id>` 기준
- 비인증: `ip:<client_ip>` 기준

**구현 파일:**

| 파일 | 역할 |
|---|---|
| `app/core/rate_limit.py` | slowapi Limiter 설정 |
| `app/core/exceptions.py` | 429 Rate Limit 핸들러 |

### 3.4 인프라 변경

| 파일 | 변경 내용 |
|---|---|
| `app/core/config.py` | 세션 TTL/쿠키 설정, Rate Limit 설정, Redis 포트 6379로 수정 |
| `app/main.py` | Redis lifespan 연결/해제, Rate Limiter 등록, 예외 핸들러 |
| `app/schemas/item.py` | `LockRequest` 제거 (세션 기반 대체) |
| `app/schemas/audit.py` | 감사 로그 스키마 정의 |
| `app/api/v1/api.py` | auth 라우터 추가 |
| `app/api/v1/endpoints/editor.py` | 세션 인증 기반으로 전체 전환, Rate Limiting 적용 |
| `app/api/v1/endpoints/files.py` | 인증 체크 → 비인증 시 로그인 페이지 리다이렉트 |
| `app/templates/base.html` | 사용자 표시명 / 로그아웃 버튼 추가 |
| `app/templates/editor.html` | fetch에 `credentials: 'same-origin'` 적용, `user_id` 수동 전송 제거 |

---

## 4. 전체 URL 구조

```
GET  /                                    → /api/v1/view/files (리다이렉트)

# 인증
POST /api/v1/auth/login                   → 세션 생성 + 쿠키 설정
POST /api/v1/auth/logout                  → 세션 삭제 + 쿠키 삭제
GET  /api/v1/auth/me                      → 현재 사용자 정보

# HTML 뷰 (인증 필요, 미인증 시 로그인 리다이렉트)
GET  /api/v1/view/login                   → 로그인 페이지
GET  /api/v1/view/files                   → 파일 목록 페이지
GET  /api/v1/view/files/{file_id}         → 아이템 뷰어/에디터
GET  /api/v1/view/files/{file_id}/download → JSONL 다운로드

# Editor API (세션 인증 기반)
POST   /api/v1/editor/lock/{file_id}/{row_idx}           → Lock 획득
POST   /api/v1/editor/lock/{file_id}/{row_idx}/heartbeat → Lock 연장
DELETE /api/v1/editor/lock/{file_id}/{row_idx}           → Lock 해제
GET    /api/v1/editor/data/{file_id}/{row_idx}           → Row 읽기
PUT    /api/v1/editor/data/{file_id}/{row_idx}           → Row 저장
```

---

## 5. 전체 진행 현황

```
Phase 1     ██████████ 100%  프로젝트 스캐폴드, Docker, Redis Sentinel
Phase 1.5   ██████████ 100%  Lock Heartbeat, Optimistic Locking, Atomic Write, Backup
Phase 2     ██████████ 100%  Line Indexer, Auth, Rate Limiting  ← 오늘 완료
Phase 3     ░░░░░░░░░░   0%  CSRF, AuditService, Draft (Auto-save)
Phase 4     ░░░░░░░░░░   0%  UI 고도화 (HTMX + WebSocket 실시간 Lock 상태)
Phase 5     ░░░░░░░░░░   0%  Health Check, Graceful Shutdown, 테스트
```

---

## 6. 다음 단계 (Phase 3)

| 항목 | 상세 |
|---|---|
| CSRF 보호 | `fastapi-csrf-protect` 적용, 상태 변경 요청 토큰 검증 |
| AuditService | 사용자 행위 로깅 (view/edit/save/download), JSONL 파일 기반 저장 |
| Draft (Auto-save) | Redis에 임시 저장 (30초 주기), 브라우저 비정상 종료 시 복구 |

---

## 7. 디렉터리 구조 (현재)

```
EditerJsonlData/
├── app/
│   ├── __init__.py
│   ├── main.py                    # Lifespan, Rate Limiter, Router 통합
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py              # Settings (세션/Rate Limit 설정 추가)
│   │   ├── logger.py              # 구조화 로깅
│   │   ├── rate_limit.py          # slowapi Limiter
│   │   ├── exceptions.py          # 글로벌 예외 핸들러
│   │   └── security.py            # (Phase 3: CSRF)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py                # 공통 의존성 (get_current_user 등)
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── api.py             # 라우터 통합
│   │       └── endpoints/
│   │           ├── __init__.py
│   │           ├── auth.py        # ★ 로그인/로그아웃/me
│   │           ├── editor.py      # ★ 세션 인증 기반 Lock/Data API
│   │           ├── files.py       # ★ HTML 뷰 (인증 연동)
│   │           ├── health.py      # (Phase 5)
│   │           └── websocket.py   # (Phase 4)
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── item.py                # ItemBase/Update/Response, LockResponse
│   │   └── audit.py               # ★ AuditLog 스키마
│   ├── services/
│   │   ├── __init__.py
│   │   ├── file_service.py        # ★ LineIndex + Atomic Write + Backup
│   │   ├── lock_service.py        # Redis 분산 락
│   │   ├── auth_service.py        # ★ Redis 세션 인증
│   │   ├── render_service.py      # ★ PM HTML 렌더링 로직
│   │   └── audit_service.py       # (Phase 3)
│   ├── db/
│   │   ├── __init__.py
│   │   └── redis_client.py        # Redis/Sentinel 연결
│   └── templates/
│       ├── base.html              # ★ CSS + MathJax + 네비게이션
│       ├── login.html             # ★ 로그인 페이지
│       ├── index.html             # ★ 파일 목록
│       └── editor.html            # ★ 아이템 뷰어/에디터
├── static/
│   ├── css/
│   └── js/
├── tests/
├── data/
│   ├── backups/
│   └── snapshots/
├── docs/                           # ★ 개발 문서
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

> ★ 표시: 오늘 생성 또는 주요 수정된 파일
