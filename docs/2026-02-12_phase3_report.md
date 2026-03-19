# Phase 3 ~ 3.6 개발 보고서 — CSRF, Audit, Draft, Master-Detail UI, 인라인 편집

**작성일**: 2026-02-12  
**최종 갱신**: 2026-02-13  
**작성자**: AI Assistant + 사용자 협업  
**Phase**: 3 → 3.5 → 3.6  
**상태**: COMPLETED

---

## 1. 개요

Phase 3 ~ 3.6에서는 보안 강화(CSRF), 추적 가능성(Audit), 사용자 경험 개선(Draft/Auto-save), UI 레이아웃 전면 개편(Master-Detail), 인라인 편집 모드를 순차적으로 구현했다.

### Phase별 주요 목표

| Phase | 주제 | 상태 |
|-------|------|------|
| **3** | CSRF Protection + AuditService + Draft/Auto-save | COMPLETED (2026-02-12) |
| **3.5** | Master-Detail UI + 데이터 호환성 수정 | COMPLETED (2026-02-13) |
| **3.6** | 모달 편집 → 인라인 편집 모드 전환 | COMPLETED (2026-02-13) |

---

## 2. CSRF Protection

### 2.1 설계

`fastapi-csrf-protect` 라이브러리의 **Double Submit Cookie** 패턴을 채택했다.

**동작 흐름:**
1. HTML 뷰 렌더링 시 CSRF 토큰 쌍 생성 (token + signed_token)
2. signed_token → 쿠키(`csrf_token`)에 저장
3. token → HTML `<meta>` 태그에 삽입
4. 프론트엔드 JS가 `X-CSRF-Token` 헤더로 토큰 전송
5. 서버에서 헤더 토큰과 쿠키 토큰을 대조 검증

### 2.2 구현 파일

| 파일 | 역할 |
|------|------|
| `app/core/csrf.py` | CsrfSettings 정의, `@CsrfProtect.load_config` |
| `app/core/exceptions.py` | `csrf_protect_exception_handler` (403 응답) |
| `app/main.py` | CSRF 예외 핸들러 등록, CSRF 설정 로드 |
| `app/templates/base.html` | `<meta name="csrf-token">` + `csrfFetch()` JS 래퍼 |

### 2.3 적용 범위

| 엔드포인트 | CSRF 검증 | 비고 |
|-----------|-----------|------|
| `POST /auth/login` | X | 세션 없는 상태이므로 제외 |
| `POST /auth/logout` | X | 로그아웃은 부작용 제한적 |
| `POST /editor/lock/{file_id}/{row_idx}` | O | Lock 획득 |
| `DELETE /editor/lock/{file_id}/{row_idx}` | O | Lock 해제 |
| `PUT /editor/data/{file_id}/{row_idx}` | O | 데이터 저장 |
| `POST /editor/draft/{file_id}/{row_idx}` | O | Draft 저장 |
| `DELETE /editor/draft/{file_id}/{row_idx}` | O | Draft 삭제 |
| `GET` 엔드포인트들 | X | 읽기 전용 |

### 2.4 프론트엔드 통합

```javascript
// base.html - 모든 페이지에서 사용 가능한 CSRF fetch 래퍼
function csrfFetch(url, options = {}) {
    const csrfToken = getCsrfToken();
    const headers = options.headers || {};
    if (csrfToken) {
        headers['X-CSRF-Token'] = csrfToken;
    }
    return fetch(url, { ...options, headers, credentials: 'same-origin' });
}
```

---

## 3. AuditService (감사 로그)

### 3.1 설계

사용자의 주요 행위를 **일자별 JSONL 파일**로 기록한다.

**설계 결정:**
- Redis 대신 JSONL 파일 선택 → 영속성 보장, Redis 장애 시에도 로그 유지
- 일자별 파일 분리 → 검색/정리 용이
- 비동기 I/O → 성능 영향 최소화
- 실패 시 서비스 중단 방지 (try/except)

### 3.2 로그 스키마

```json
{
    "id": "uuid4",
    "timestamp": "2026-02-12T10:30:00+00:00",
    "user_id": "hong.gildong",
    "display_name": "홍길동",
    "action": "edit_save",
    "file_id": "sample_data",
    "row_idx": 42,
    "ip_address": "192.168.1.100",
    "user_agent": "Mozilla/5.0 ...",
    "changes": {"content": {...}, "version": 3},
    "metadata": null
}
```

### 3.3 지원 ActionType

| Action | 시점 | 기록 위치 |
|--------|------|-----------|
| `login` | 로그인 성공 | `auth.py` |
| `logout` | 로그아웃 | `auth.py` |
| `view` | 파일 뷰어 접근 | `files.py` |
| `edit_start` | Lock 획득 | `editor.py` |
| `edit_save` | 데이터 저장 | `editor.py` |
| `edit_cancel` | Lock 해제/편집 취소 | `editor.py` |
| `download` | 파일 다운로드 | `files.py` |
| `draft_save` | Draft 저장 | (향후 확장) |
| `draft_restore` | Draft 복원 | (향후 확장) |
| `draft_delete` | Draft 삭제 | (향후 확장) |

### 3.4 로그 관리

- **보존 기간**: 90일 (설정: `AUDIT_RETENTION_DAYS`)
- **자동 정리**: 앱 시작 시 `cleanup_old_logs()` 실행
- **파일 위치**: `data/audit/audit_YYYYMMDD.jsonl`
- **조회 API**: 날짜/사용자/행위별 필터링 지원

### 3.5 구현 파일

| 파일 | 역할 |
|------|------|
| `app/services/audit_service.py` | AuditService 클래스 (싱글톤) |
| `app/schemas/audit.py` | AuditLog Pydantic 스키마 |

---

## 4. Draft / Auto-save

### 4.1 설계

편집 중 데이터를 **Redis에 임시 저장**하여, 브라우저 종료·네트워크 장애·실수 등으로 인한 데이터 손실을 방지한다.

**Redis Key 구조:**
```
draft:{file_id}:{row_idx}:{user_id}
```

**TTL**: 30분 (설정: `DRAFT_TTL_SECONDS`)

### 4.2 동작 흐름

```
[편집 시작]
    ↓
[Lock 획득 성공]
    ↓
[Draft 존재 확인] → [있으면] → Draft 복원 배너 표시
    ↓                              ↓ (복원 클릭)
[원본 데이터 로드]              [Draft 데이터로 textarea 채움]
    ↓                              ↓ (무시 클릭)
[Auto-save 시작 (30초)]        [Draft 삭제, 원본 유지]
    ↓
[사용자 편집 중...]
    ↓ (30초마다)
[Draft 저장 → Redis]
    ↓
[저장 클릭] → [Atomic Write] → [Draft 삭제] → [Lock 해제]
    ↓
[취소 클릭] → [Draft 삭제] → [Lock 해제]
```

### 4.3 API 엔드포인트

| Method | Path | 설명 | CSRF |
|--------|------|------|------|
| `POST` | `/editor/draft/{file_id}/{row_idx}` | Draft 저장 | O |
| `GET` | `/editor/draft/{file_id}/{row_idx}` | Draft 조회 | X |
| `DELETE` | `/editor/draft/{file_id}/{row_idx}` | Draft 삭제 | O |
| `GET` | `/editor/draft/list` | 내 Draft 목록 | X |

### 4.4 프론트엔드 기능

1. **자동 저장**: `AUTO_SAVE_INTERVAL`(30초) 주기로 편집 내용 Redis 저장
2. **복원 배너**: 편집 시작 시 Draft 존재하면 "복원/무시" 선택 UI
3. **저장 상태 표시**: "저장 중..." → "자동 저장됨 (HH:MM:SS)"
4. **Ctrl+S 단축키**: 즉시 저장
5. **페이지 이탈 경고**: 편집 중 페이지 이탈 시 `beforeunload` 경고

### 4.5 구현 파일

| 파일 | 역할 |
|------|------|
| `app/services/draft_service.py` | DraftService 클래스 (DI 패턴) |
| `app/api/v1/endpoints/draft.py` | Draft API 엔드포인트 |
| `app/api/deps.py` | `get_draft_service` 의존성 |
| `app/api/v1/api.py` | Draft 라우터 등록 |
| `app/templates/editor.html` | Auto-save JS + Draft 복원 UI |

---

## 5. Config 변경 사항

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `AUDIT_DIR` | `data/audit` | 감사 로그 저장 디렉터리 |
| `AUDIT_RETENTION_DAYS` | `90` | 감사 로그 보존 기간 (일) |
| `DRAFT_TTL_SECONDS` | `1800` | Draft TTL (30분) |
| `DRAFT_AUTO_SAVE_INTERVAL` | `30` | 프론트엔드 자동 저장 주기 (초) |

---

## 6. 전체 파일 변경 목록 (Phase 3 ~ 3.6 누적)

### 신규 파일
- `app/core/csrf.py` — CSRF 설정
- `app/services/audit_service.py` — 감사 로그 서비스
- `app/services/draft_service.py` — Draft/Auto-save 서비스
- `app/api/v1/endpoints/draft.py` — Draft API

### 수정 파일 (Phase 3)
- `app/core/config.py` — Audit, Draft 설정 추가
- `app/core/exceptions.py` — CSRF 예외 핸들러 추가
- `app/main.py` — CSRF 핸들러 등록, Audit 정리 로직
- `app/api/deps.py` — `get_draft_service` 추가
- `app/api/v1/api.py` — Draft 라우터 등록
- `app/api/v1/endpoints/editor.py` — CSRF 검증, Audit 로깅, Draft 삭제 통합
- `app/api/v1/endpoints/auth.py` — Audit 로깅 (login, logout)
- `app/api/v1/endpoints/files.py` — CSRF 토큰 생성, Audit 로깅 (view, download)
- `app/templates/base.html` — CSRF 메타 태그, `csrfFetch()`, Draft 스타일
- `app/templates/editor.html` — Auto-save JS, Draft 복원 UI, Ctrl+S
- `app/templates/login.html` — CSRF 토큰 메타 태그 지원

### 추가 수정 파일 (Phase 3.5)
- `app/schemas/item.py` — content 유니온 타입, model_config extra=ignore
- `app/services/file_service.py` — `get_data_id_list()` 추가, `update_row_atomic()` partial update
- `app/api/v1/endpoints/editor.py` — `/ids/`, `/card/` API 추가, `SaveRequest.changes`
- `app/api/v1/endpoints/files.py` — data_id 목록 전달 (카드 렌더링 제거)
- `app/services/render_service.py` — 원본 스크립트 기준 이모지/렌더링 복원
- `app/templates/editor.html` — Master-Detail 레이아웃 + 모달 편집 UI
- `app/templates/base.html` — `content_full` 블록 추가, CSS 원본 복원

### 추가 수정 파일 (Phase 3.6)
- `app/services/render_service.py` — `render_item_card`: `data-field` 속성, `inline-edit-status` span 추가
- `app/templates/editor.html` — 모달 완전 제거 → 인라인 편집 모드 JS/CSS 전면 재설계

---

## 7. Phase 3.5 — Master-Detail UI + 데이터 호환성 (2026-02-13)

### 7.1 배경

Phase 3까지는 파일 뷰어에서 전체 Row를 페이지네이션으로 렌더링했다. 대량 데이터(수백 Row) 시 초기 로딩이 느리고, 특정 `data_id`를 찾기 어려운 UX 문제가 있었다. 또한 편집 시 Pydantic `content` 타입 불일치(dict vs string)로 HTTP 500 오류가 발생했고, 저장 시 `data_id`, `data_file` 등 원본 필드가 유실되는 치명적 버그가 있었다.

### 7.2 Master-Detail 레이아웃

| 영역 | 구성 | 설명 |
|------|------|------|
| **좌측 사이드바** (320px) | `data_id` 목록 | 검색 필터, 클릭 시 우측 패널에 해당 카드 로드 |
| **우측 상세 패널** | 렌더링된 HTML 카드 | AJAX로 단일 Row 카드 렌더링, MathJax 동적 적용 |

**새로운 API 엔드포인트:**

| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/api/v1/editor/ids/{file_id}` | 전체 data_id 목록 (row_idx 포함) |
| `GET` | `/api/v1/editor/card/{file_id}/{row_idx}` | 단일 Row HTML 카드 렌더링 |

### 7.3 데이터 호환성 수정

| 문제 | 원인 | 해결 |
|------|------|------|
| 편집 모드 진입 시 HTTP 500 | `ItemBase.content`가 `dict`만 허용, 실제 데이터엔 `str`도 존재 | `dict[str, Any] \| str` 유니온 타입 |
| JSONL 추가 필드로 ValidationError | `data_file` 등 스키마 미정의 필드 존재 | `model_config = {"extra": "ignore"}` |
| 저장 시 원본 필드 유실 | `update_data.model_dump()`로 새 dict 생성 → 원본 필드 소실 | 원본 raw dict에 changes만 병합 |
| content만 편집 가능 | 편집 UI가 content 단일 필드만 지원 | content + content_meta + add_info 3섹션 지원 |

**데이터 보존 전략 — Partial Update:**

```python
# file_service.py — update_row_atomic
editable_fields = {"content", "content_meta", "add_info"}
original_data = await self.get_row_raw(file_id, row_idx)
for key, value in changes.items():
    if key in editable_fields:
        original_data[key] = value  # 원본에 변경분만 병합
```

### 7.4 변경 파일 (Phase 3.5)

| 파일 | 변경 내용 |
|------|-----------|
| `app/schemas/item.py` | content 유니온 타입, extra=ignore |
| `app/services/file_service.py` | `get_data_id_list()` 추가, `update_row_atomic()` 원본 보존 |
| `app/api/v1/endpoints/editor.py` | `/ids/`, `/card/` API 추가, `get_data` raw 반환, `SaveRequest.changes` |
| `app/api/v1/endpoints/files.py` | data_id 목록 전달 방식 전환 |
| `app/services/render_service.py` | 원본 스크립트(JJIn_last.py) 기준 이모지/렌더링 복원 |
| `app/templates/editor.html` | Master-Detail 레이아웃 + 모달 편집 UI |
| `app/templates/base.html` | `content_full` 블록 추가, CSS 원본 복원 |

---

## 8. Phase 3.6 — 인라인 편집 모드 (2026-02-13)

### 8.1 배경

Phase 3.5에서는 "편집" 버튼 클릭 시 **모달 오버레이**가 열리고, JSON textarea에서 raw 텍스트를 수정하는 방식이었다. 이는 다음 문제를 가졌다:

- 렌더링된 카드와 편집 화면이 분리되어 시각적 맥락 상실
- JSON 구조 이해 필요 (비개발자에게 불편)
- 모달이 전체 화면을 가려 참조 불가

### 8.2 인라인 편집 UX 설계

**핵심 원칙**: 렌더링된 카드 위에서 직접 값을 편집한다.

```
[일반 모드]
┌─────────────────────────────────┐
│ Header: No.1 | ID: xxx  [편집] │
│ Content: 렌더링된 HTML          │
│ ┌─────────┬───────────────────┐ │
│ │ 학교급   │ 초등학교           │ │
│ │ 정답     │ 3                  │ │
│ └─────────┴───────────────────┘ │
└─────────────────────────────────┘

     ↓ "편집" 클릭

[인라인 편집 모드]
┌═════════════════════════════════┐ ← 녹색 테두리
│ Header (녹색)     [취소] [저장] │ ← 버튼 교체
│ Content: ┌─────────────────┐   │
│          │ 편집 가능한 텍스트│   │ ← contenteditable
│          └─────────────────┘   │
│ ┌─────────┬───────────────────┐ │
│ │ 학교급   │ 초등학교 ←편집가능│ │ ← td contenteditable
│ │ 정답     │ 3 ←편집가능       │ │
│ └─────────┴───────────────────┘ │
│ ┌ content_meta (동적 추가) ────┐ │
│ │ type │ text ←편집가능        │ │
│ └──────────────────────────────┘ │
└═════════════════════════════════┘
```

### 8.3 render_item_card 변경 — `data-field` 속성 추가

렌더링된 HTML 요소에 `data-field` 속성을 부여하여 JS가 JSON 키 경로를 식별할 수 있도록 했다.

| 영역 | data-field 예시 | 요소 |
|------|-----------------|------|
| Content (dict) | `content.공통지문` | `<span class="editable-value">` |
| Content (string) | `content` | `<span class="editable-value">` |
| Source meta | `add_info.source_file` | `<span class="editable-value">` |
| Page meta | `add_info.page_num` | `<span class="editable-value">` |
| Info table 셀 | `add_info.문제.정답` | `<td class="editable-value">` |
| Header | — | `<span class="inline-edit-status">` 추가 |

### 8.4 편집 모드 JS 흐름

```
"편집" 클릭
    ↓
Lock 획득 (POST /editor/lock)
    ↓
Raw 데이터 로드 (GET /editor/data)
    ↓
enterInlineEdit(card):
    1. card에 .inline-editing 클래스 추가 (녹색 테두리)
    2. 각 [data-field] 요소의 원본 innerHTML 저장 (originalHtmlMap)
    3. rawData에서 해당 필드의 원본 값 추출
       - 문자열 → el.textContent = rawValue; el.contentEditable = true
       - dict/array → <textarea class="inline-edit-ta"> 삽입
    4. content_meta → 카드 하단에 편집용 테이블 동적 생성
    5. "편집" 버튼 → "취소" + "저장" 버튼으로 교체
    ↓
Draft 확인 → 배너 표시 (선택적)
    ↓
Heartbeat (30초) + Auto-save (30초) 시작
```

### 8.5 값 수집 및 저장 — `collectInlineChanges()`

```javascript
// 수집 흐름
1. card 내 [data-field][data-editing="true"] 요소 순회
2. contenteditable → el.textContent / textarea → ta.value
3. JSON.parse 시도 (실패 시 문자열 유지)
4. field path 기반으로 changes 객체 구성
5. 수정된 top-level 키의 원본 rawData와 deepMerge
   → 편집하지 않은 하위 필드 보존
```

**Deep Merge 전략:**

- `add_info.문제.정답`만 수정 시:
  - rawData.add_info 전체를 deep clone
  - 수정된 `문제.정답`만 덮어쓰기
  - 결과: `add_info.book_meta`, `add_info.unit_meta` 등 보존
- 이 방식으로 backend의 `editable_fields` 화이트리스트와 호환

### 8.6 버튼 교체 UX

| 상태 | Header 표시 |
|------|-------------|
| 일반 모드 | `[편집]` 버튼 |
| 편집 모드 | `(자동저장 상태) [취소] [저장]` |

- 취소 시: 원본 HTML 복원 → contenteditable 해제 → MathJax 재렌더링 → Lock 해제
- 저장 시: changes 수집 → API PUT → 카드 새로고침 (재렌더링) → Lock 해제

### 8.7 CSS 스타일

| 선택자 | 용도 |
|--------|------|
| `.item.inline-editing` | 편집 중 카드 녹색 테두리 + 그림자 |
| `.editable-value[contenteditable="true"]` | 편집 가능 필드 노란 배경 + 주황 점선 |
| `.inline-edit-ta` | 복잡 값 편집용 textarea (monospace) |
| `.btn-inline-save` / `.btn-inline-cancel` | 저장/취소 버튼 |
| `.draft-banner-inline` | 인라인 Draft 복원 배너 |
| `.inline-meta-section` | content_meta 동적 편집 영역 |

### 8.8 변경 파일 (Phase 3.6)

| 파일 | 변경 내용 |
|------|-----------|
| `app/services/render_service.py` | `render_item_card`: content span, info-table td, source meta에 `data-field` 속성 추가 |
| `app/templates/editor.html` | **전면 재설계** — 모달 오버레이 제거, 인라인 편집 JS/CSS 구현 |

### 8.9 기존 대비 비교

| 항목 | Phase 3.5 (모달) | Phase 3.6 (인라인) |
|------|-------------------|---------------------|
| 편집 방식 | 별도 오버레이 + JSON textarea | 렌더링된 카드 위 contenteditable |
| 시각적 맥락 | 원본 카드 가려짐 | 카드 위에서 직접 수정 |
| JSON 이해 필요 | dict → 키별 textarea | 표시된 값 그대로 편집 |
| 복잡 값 (dict/array) | 모든 값 textarea | 문자열은 contenteditable, 복잡 값만 textarea |
| 버튼 위치 | 모달 하단 고정 | 카드 헤더 (편집 버튼 자리) |
| 다른 항목 접근 | 모달 닫아야 가능 | 확인 다이얼로그 후 전환 가능 |
| content_meta | 접이식 섹션 (모달 내) | 카드 하단 동적 테이블 |
| Draft/Auto-save | 모달 내 textarea 기반 | 인라인 편집 값 수집 기반 |

---

## 9. Phase 4 — Health Check, Graceful Shutdown, WebSocket Lock Status (완료)

### 9.1 Health Check

- **엔드포인트**: `GET /api/v1/health`
- **응답**: `status` (healthy | degraded | unhealthy), `redis_ok`, `storage_ok`, `disk_usage_pct`, `details`
- **판단**: Redis 실패 시 `unhealthy`, 스토리지 실패 또는 디스크 사용률 ≥ 90% 시 `degraded`
- **설정**: `DISK_USAGE_WARNING_PCT` (config)

### 9.2 Graceful Shutdown

- **PendingTaskTracker** (`app/core/pending_tasks.py`): 저장 작업 추적 (count, track(), wait_all_done)
- **save_data**: `async with pending_tracker.track()` 로 래핑
- **Lifespan shutdown**: `await pending_tracker.wait_all_done(timeout=30)` 후 Redis 종료

### 9.3 WebSocket 실시간 Lock 상태

- **엔드포인트**: `WS /api/v1/ws/lock-status/{file_id}` (세션 쿠키 인증)
- **ConnectionManager**: file_id별 WebSocket 연결 관리, `broadcast(file_id, data)`
- **LockService.get_all_locks(file_id)**: Redis SCAN으로 해당 파일의 모든 Lock 조회
- **editor.py**: acquire_lock / release_lock / save_data 시 `ws_manager.broadcast()` 호출
- **프론트엔드**: LockStatusManager — 연결 시 init 수신, lock_change 시 사이드바에 🔒 배지 (자신은 녹색 "나")

### 9.4 Phase 4 변경/추가 파일

| 파일 | 변경 |
|------|------|
| `app/api/v1/endpoints/health.py` | Health Check 구현 |
| `app/api/v1/endpoints/websocket.py` | WebSocket Lock 상태 엔드포인트 |
| `app/core/pending_tasks.py` | **신규** — PendingTaskTracker |
| `app/core/config.py` | DISK_USAGE_WARNING_PCT |
| `app/services/websocket_manager.py` | **신규** — ConnectionManager |
| `app/services/lock_service.py` | get_all_locks() 추가 |
| `app/services/auth_service.py` | validate_session(request=None) 지원 |
| `app/main.py` | Graceful Shutdown (wait_all_done) |
| `app/api/v1/endpoints/editor.py` | pending_tracker.track(), ws_manager.broadcast |
| `app/api/v1/api.py` | health, websocket 라우터 등록 |
| `app/api/deps.py` | get_pending_tracker |
| `app/templates/editor.html` | LockStatusManager JS, lock-badge CSS |

---

## 10. 남은 Phase

### Phase 5 — Ops & Test
- 동시성/복구 테스트

---

## 11. 보안 체크리스트

- [x] CSRF Double Submit Cookie 적용
- [x] 세션 쿠키 HttpOnly, SameSite=Lax
- [x] Rate Limiting (IP/사용자 기반)
- [x] 경로 순회 방지 (`Path(file_id).name`)
- [x] Audit Trail (행위 추적 가능)
- [x] 세션 고정 공격 방지 (로그인 시 새 세션 발급)
- [x] IP 변경 감지 (경고 로그)
- [x] 데이터 보존 — editable_fields 화이트리스트 (data_id 등 보호)
- [x] Optimistic Locking — 버전 충돌 감지 (409 Conflict)
- [x] 인라인 편집 — contenteditable XSS 방지 (textContent 수집, JSON.parse 검증)
- [ ] HTTPS 강제 (프로덕션 배포 시 `SESSION_COOKIE_SECURE=True`)
- [ ] Content Security Policy 헤더 (Phase 4+)
