# Phase 5 — Ops & Test 보고서

**작성일**: 2026-02-19  
**상태**: 완료

---

## 1. 개요

Phase 5는 운영 안정성 확보 및 테스트 전면 보강을 목표로 한다.  
기존 Phase 1~4에서 구현한 모든 기능에 대한 체계적 테스트를 작성하고,  
보안 헤더(CSP)를 추가하여 프로덕션 배포 준비를 완료했다.

---

## 2. 작업 내역

### 2.1 기존 테스트 수정

Phase 3.5에서 `update_row_atomic`의 시그니처가 `ItemUpdate` → `changes dict` 방식으로 변경되었으나, 기존 테스트가 미갱신 상태였다.

| 파일 | 변경 |
|---|---|
| `tests/unit/test_file_service.py` | `ItemUpdate` → `changes dict + version + user_id` 호출 방식 전환, 타입 검증 테스트 4건 추가, 필드 보존 테스트 추가 |
| `tests/integration/test_editor_api.py` | `SaveRequest` 포맷을 `{"changes": {...}, "version": N}` 구조로 수정, CSRF 헬퍼 분리, 버전 충돌/타입 불일치 테스트 추가 |

### 2.2 동시성 테스트 (신규)

**파일**: `tests/unit/test_concurrency.py`

| 테스트 | 검증 항목 |
|---|---|
| `test_two_users_lock_race` | 2명 동시 Lock → 1명만 성공 |
| `test_multiple_users_lock_race` | 5명 동시 Lock → 1명만 성공 |
| `test_lock_different_rows_concurrent` | 다른 Row 동시 Lock → 모두 성공 |
| `test_lock_release_then_reacquire` | Lock 해제 후 재획득 |
| `test_concurrent_heartbeat` | 동시 Heartbeat TTL 갱신 |
| `test_two_users_concurrent_save_same_version` | 같은 version 동시 저장 → 1명 성공, 1명 실패 (Atomic Write 안전성) |
| `test_sequential_saves_increment_version` | 순차 저장 시 버전 정확 증가 |
| `test_concurrent_saves_different_rows` | 다른 Row 동시 저장 → 모두 성공 |

### 2.3 복구 테스트 (신규)

**파일**: `tests/unit/test_recovery.py`

| 테스트 | 검증 항목 |
|---|---|
| `test_original_preserved_on_write_failure` | os.replace 실패 시 원본 무손상 |
| `test_temp_file_cleaned_on_failure` | 실패 후 .tmp 파일 제거 확인 |
| `test_version_not_incremented_on_failure` | 실패 시 버전 미증가 |
| `test_backup_matches_original` | 백업 내용 = 수정 전 원본 |
| `test_multiple_saves_create_multiple_backups` | 연속 저장 시 복수 백업 |
| `test_backup_can_restore_original` | 백업에서 원본 복원 시뮬레이션 |
| `test_corrupted_row_returns_error` | 손상된 JSON Row → error 필드 반환 |
| `test_empty_file_returns_zero_rows` | 빈 JSONL → 0 rows |

### 2.4 PendingTaskTracker 테스트 (신규)

**파일**: `tests/unit/test_pending_tasks.py`

| 테스트 | 검증 항목 |
|---|---|
| `test_initial_state` | 초기 count = 0 |
| `test_single_task` | 단일 작업 추적/해제 |
| `test_concurrent_tasks` | 동시 10개 작업 추적 |
| `test_wait_all_done_immediate` | 작업 없으면 즉시 반환 |
| `test_wait_all_done_waits_for_tasks` | 작업 완료 대기 |
| `test_wait_all_done_timeout` | 타임아웃 시 반환 |
| `test_exception_in_task_still_decrements` | 예외 시에도 카운터 감소 |
| `test_nested_tracking` | 중첩 추적 |

### 2.5 WebSocket ConnectionManager 테스트 (신규)

**파일**: `tests/unit/test_websocket_manager.py`

| 테스트 | 검증 항목 |
|---|---|
| `test_connect_and_disconnect` | 연결/해제 |
| `test_broadcast_to_all_clients` | 전체 클라이언트 메시지 전송 |
| `test_broadcast_isolates_file_ids` | file_id별 격리 |
| `test_broadcast_removes_dead_connections` | dead 연결 자동 제거 |
| `test_broadcast_no_connections` | 빈 풀 broadcast → 무시 |
| `test_disconnect_nonexistent` | 미등록 ws disconnect → 무시 |
| `test_disconnect_cleans_empty_pool` | 빈 풀 정리 |
| `test_all_dead_connections_cleans_pool` | 전체 dead 시 풀 정리 |

### 2.6 Health Check API 테스트 (신규)

**파일**: `tests/integration/test_health_api.py`

| 테스트 | 검증 항목 |
|---|---|
| `test_health_check_healthy` | 정상 환경 healthy 응답 |
| `test_health_check_no_auth_required` | 인증 불필요 |
| `test_health_check_includes_disk_usage` | 디스크 사용률 포함 |
| `test_health_check_redis_detail` | Redis 상세 정보 |

### 2.7 보안 헤더 (신규)

**파일**: `app/core/security_headers.py` (미들웨어), `tests/integration/test_security_headers.py`

적용된 보안 헤더:

| 헤더 | 값 | 목적 |
|---|---|---|
| `Content-Security-Policy` | default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; ... | XSS 완화, 리소스 출처 제한 |
| `X-Content-Type-Options` | nosniff | MIME 스니핑 방지 |
| `X-Frame-Options` | DENY | Clickjacking 방지 |
| `Referrer-Policy` | strict-origin-when-cross-origin | 리퍼러 정보 누출 최소화 |
| `Permissions-Policy` | camera=(), microphone=(), geolocation=() | 불필요 브라우저 기능 비활성화 |

CSP 세부 지시문:
- `default-src 'self'` — 기본 리소스는 같은 출처만
- `script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net` — MathJax CDN + 인라인 스크립트
- `style-src 'self' 'unsafe-inline'` — 인라인 스타일 허용
- `img-src 'self' data:` — base64 이미지 허용
- `connect-src 'self' ws: wss:` — WebSocket 연결 허용
- `frame-ancestors 'none'` — iframe 삽입 차단
- `form-action 'self'` — 폼 제출 동일 출처만

---

## 3. 테스트 결과

```
111 passed, 0 failed
```

### 커버리지 요약 (주요 모듈)

| 모듈 | Coverage |
|---|---|
| `core/pending_tasks.py` | 100% |
| `core/security_headers.py` | 100% |
| `services/websocket_manager.py` | 100% |
| `services/file_service.py` | 89% |
| `services/auth_service.py` | 91% |
| `services/draft_service.py` | 91% |
| `schemas/item.py` | 100% |
| `api/deps.py` | 100% |
| `api/v1/endpoints/auth.py` | 100% |
| `api/v1/endpoints/files.py` | 94% |
| **전체** | **66%** |

> `render_service.py` (0%, HTML 렌더링)와 `redis_client.py` (41%, Sentinel 분기)가  
> 전체 수치를 낮추고 있으며, 통합 테스트에서 간접 실행되거나 Docker 환경 전용 코드.

---

## 4. 테스트 파일 구조

```
tests/
├── conftest.py                          # 공통 fixture (fakeredis, tmp_data_dir, app_client)
├── unit/
│   ├── test_file_service.py             # FileService (16 tests)
│   ├── test_lock_service.py             # LockService (9 tests)
│   ├── test_auth_service.py             # AuthService (7 tests)
│   ├── test_audit_service.py            # AuditService (6 tests)
│   ├── test_draft_service.py            # DraftService (8 tests)
│   ├── test_concurrency.py       ★ NEW  # 동시성 (8 tests)
│   ├── test_recovery.py          ★ NEW  # 복구/안전성 (8 tests)
│   ├── test_pending_tasks.py     ★ NEW  # Graceful Shutdown (8 tests)
│   └── test_websocket_manager.py ★ NEW  # WebSocket 풀 (8 tests)
├── integration/
│   ├── test_auth_api.py                 # Auth API (5 tests)
│   ├── test_editor_api.py               # Editor API (7 tests, 수정)
│   ├── test_files_api.py                # Files View API (9 tests)
│   ├── test_health_api.py       ★ NEW   # Health Check (4 tests)
│   └── test_security_headers.py ★ NEW   # 보안 헤더 (8 tests)
```

---

## 5. 변경/추가 파일 요약

| 파일 | 상태 | 설명 |
|---|---|---|
| `app/core/security_headers.py` | 신규 | CSP + 보안 헤더 미들웨어 |
| `app/main.py` | 수정 | SecurityHeadersMiddleware 등록 |
| `pyproject.toml` | 수정 | fakeredis, pyright dev 의존성 추가 |
| `tests/unit/test_file_service.py` | 수정 | API 시그니처 갱신 + 타입 검증 + 필드 보존 테스트 |
| `tests/unit/test_concurrency.py` | 신규 | 동시성 테스트 8건 |
| `tests/unit/test_recovery.py` | 신규 | 복구/안전성 테스트 8건 |
| `tests/unit/test_pending_tasks.py` | 신규 | Graceful Shutdown 테스트 8건 |
| `tests/unit/test_websocket_manager.py` | 신규 | WebSocket 풀 관리 테스트 8건 |
| `tests/integration/test_editor_api.py` | 수정 | SaveRequest 포맷 수정 + 충돌/타입 테스트 |
| `tests/integration/test_health_api.py` | 신규 | Health Check API 테스트 4건 |
| `tests/integration/test_security_headers.py` | 신규 | 보안 헤더 검증 8건 |

---

## 6. 보안 체크리스트 최종 상태

- [x] CSRF Double Submit Cookie
- [x] 세션 쿠키 HttpOnly, SameSite=Lax
- [x] Rate Limiting (IP/사용자)
- [x] 경로 순회 방지
- [x] Audit Trail
- [x] 세션 고정 공격 방지
- [x] IP 변경 감지
- [x] 데이터 보존 (editable_fields 화이트리스트)
- [x] Optimistic Locking (409 Conflict)
- [x] XSS 방지 (textContent, JSON.parse)
- [x] **Content-Security-Policy** ★ Phase 5
- [x] **X-Content-Type-Options: nosniff** ★ Phase 5
- [x] **X-Frame-Options: DENY** ★ Phase 5
- [x] **Referrer-Policy** ★ Phase 5
- [x] **Permissions-Policy** ★ Phase 5
- [ ] HTTPS 강제 (프로덕션 배포 시 `SESSION_COOKIE_SECURE=True`)
