# Phase 4 개발 보고서 — Health Check, Graceful Shutdown, WebSocket Lock Status

**작성일**: 2026-02-13  
**작성자**: AI Assistant + 사용자 협업  
**Phase**: 4  
**상태**: COMPLETED

---

## 1. 개요

Phase 4에서는 운영 안정성과 다중 사용자 실시간 협업 지원을 위해 다음 3가지 기능을 구현했다.

| 기능 | 목적 | 상태 |
|------|------|------|
| **Health Check** | 서비스 상태 모니터링 (Redis, 스토리지, 디스크) | COMPLETED |
| **Graceful Shutdown** | 종료 시 진행 중인 저장 작업 완료 보장 | COMPLETED |
| **WebSocket Lock Status** | 편집 잠금 상태 실시간 동기화 (사이드바 배지) | COMPLETED |

---

## 2. Health Check

### 2.1 설계

단일 엔드포인트로 Redis, 파일 스토리지, 디스크 사용률을 종합 진단한다.

**핵심 설계 원칙:**
- Redis 미연결 시에도 **항상 HTTP 200 OK** 반환 → 모니터링 시스템이 응답 코드만으로 앱 프로세스 생존 여부 확인 가능
- 상태 판정은 응답 body의 `status` 필드로 전달
- `Depends(get_redis_client)` 미사용 → DI 실패로 인한 500 에러 방지

### 2.2 엔드포인트

| Method | Path | 인증 | Rate Limit |
|--------|------|------|------------|
| `GET` | `/api/v1/health` | 불필요 | 없음 |

### 2.3 응답 스키마

```json
{
    "status": "healthy | degraded | unhealthy",
    "redis_ok": true,
    "storage_ok": true,
    "disk_usage_pct": 45.3,
    "details": {
        "redis": "ok",
        "storage": "ok",
        "disk": "45.3%"
    }
}
```

### 2.4 상태 판정 로직

| 조건 | status |
|------|--------|
| Redis 연결 실패 | `unhealthy` |
| 스토리지 접근 불가 OR 디스크 사용률 ≥ 90% | `degraded` |
| 모든 항목 정상 | `healthy` |

### 2.5 스토리지 검증 방식

```python
# DATA_DIR에 임시 파일 생성/삭제로 쓰기 권한 확인
(data_path / ".health_check").touch()
(data_path / ".health_check").unlink(missing_ok=True)
```

- 디렉터리 미존재 시 자동 생성 시도 → 성공하면 `storage_ok = True`
- 권한 오류 등은 `details["storage"]`에 예외 메시지 기록

### 2.6 설정

| 설정 키 | 기본값 | 설명 |
|---------|--------|------|
| `DISK_USAGE_WARNING_PCT` | `90.0` | 디스크 사용률 경고 임계치 (%) |

### 2.7 구현 세부

- `redis_manager.redis` 직접 접근 → `None` 체크 후 `ping()` 시도
- `shutil.disk_usage()` 동기 호출 (μs 수준, 이벤트 루프 블로킹 무시 가능)
- `HealthStatus` Pydantic 모델로 응답 직렬화

---

## 3. Graceful Shutdown

### 3.1 설계

서버 종료(SIGTERM/SIGINT) 시 진행 중인 저장 작업이 도중에 중단되면 데이터 정합성이 깨질 수 있다. `PendingTaskTracker`로 활성 작업 수를 추적하고, Lifespan shutdown 단계에서 모두 완료될 때까지 대기한다.

### 3.2 PendingTaskTracker 클래스

```python
class PendingTaskTracker:
    """비동기 작업 카운트 추적 + 완료 대기"""

    _count: int          # 현재 진행 중인 작업 수
    _lock: asyncio.Lock  # 카운트 변경 동기화
    _zero_event: Event   # count == 0이면 set 상태

    @asynccontextmanager
    async def track() -> AsyncIterator[None]:
        # 진입 시 count++, 탈출 시 count-- (finally 보장)

    async def wait_all_done(timeout: float = 30.0) -> None:
        # _zero_event가 set될 때까지 대기 (최대 timeout초)
```

### 3.3 적용 지점

| 위치 | 사용 방식 |
|------|-----------|
| `editor.py → save_data` | `async with pending_tracker.track():` 로 저장 작업 전체 래핑 |
| `main.py → lifespan shutdown` | `await pending_tracker.wait_all_done(timeout=30.0)` |

### 3.4 Shutdown 시퀀스

```
SIGTERM 수신
    ↓
uvicorn: 새 요청 수신 중단
    ↓
lifespan shutdown 진입
    ↓
pending_tracker.wait_all_done(timeout=30s)
    ├─ 모든 작업 완료 → "All pending tasks completed" 로그
    └─ 30초 초과 → "Shutdown timeout: N task(s) still pending" 경고
    ↓
redis_manager.close()
    ↓
"Shutdown complete." 로그
```

### 3.5 DI 등록

```python
# app/api/deps.py
def get_pending_tracker() -> PendingTaskTracker:
    return pending_tracker  # 모듈 수준 싱글톤
```

---

## 4. WebSocket 실시간 Lock 상태

### 4.1 설계

다중 사용자가 동일 파일을 열었을 때, 다른 사용자의 편집 잠금 상태를 **실시간으로 사이드바에 표시**한다.

**프로토콜:**
- WebSocket 연결: `WS /api/v1/ws/lock-status/{file_id}`
- 인증: 세션 쿠키 기반 (HTTP와 동일한 인증 체계)
- 메시지 형식: JSON

### 4.2 메시지 타입

#### 서버 → 클라이언트

| type | 시점 | 데이터 |
|------|------|--------|
| `init` | 연결 직후 | `{"type": "init", "locks": [{"row_idx": 0, "user_id": "hong"}]}` |
| `lock_change` | Lock 획득/해제 시 | `{"type": "lock_change", "action": "acquired\|released", "row_idx": 0, "user_id": "hong", "display_name": "홍길동"}` |
| `pong` | keepalive 응답 | `{"type": "pong"}` |

#### 클라이언트 → 서버

| type | 주기 | 데이터 |
|------|------|--------|
| `ping` | 30초 | `{"type": "ping"}` |

### 4.3 Backend — ConnectionManager

```python
class ConnectionManager:
    _connections: dict[str, list[WebSocket]]  # file_id → [ws1, ws2, ...]

    async def connect(file_id, ws)     # accept + 풀 등록
    def disconnect(file_id, ws)        # 풀에서 제거
    async def broadcast(file_id, data) # 풀 전체에 JSON 전송, 실패 시 자동 제거
```

**방어적 설계:**
- `broadcast()` 시 연결 리스트의 **스냅샷(복사본)**을 순회 → `await send_json` 중 다른 코루틴이 리스트를 수정해도 안전
- 전송 실패 연결은 `dead` 리스트에 수집 후 일괄 `disconnect()`

### 4.4 Backend — WebSocket 엔드포인트

```python
@router.websocket("/ws/lock-status/{file_id}")
async def lock_status_ws(websocket, file_id, auth_service, lock_service):
    # 1. 경로 순회 방지 ("/" 또는 ".." 포함 시 거부)
    # 2. 세션 쿠키로 인증 (validate_session(session_id, request=None))
    # 3. ConnectionManager에 등록
    # 4. 초기 Lock 목록 전송 (lock_service.get_all_locks)
    # 5. Keepalive 루프:
    #    - 클라이언트 ping → 서버 pong
    #    - 45초 timeout → 서버 자체 pong (keepalive)
    #    - 전송 실패 시 안전하게 루프 탈출
    # 6. disconnect 시 풀에서 제거 (finally)
```

**예외 처리:**
- `WebSocketDisconnect` → 정상 종료
- `asyncio.TimeoutError` 후 `send_json` 실패 → `try/except`로 감싸서 `break`
- 미인증/잘못된 file_id → `websocket.close(code=4401|4000)`

### 4.5 Backend — Lock 변경 브로드캐스트

| 엔드포인트 | 브로드캐스트 시점 | 메시지 |
|-----------|-----------------|--------|
| `POST /editor/lock/{file_id}/{row_idx}` | Lock 획득 성공 후 | `lock_change / acquired` |
| `DELETE /editor/lock/{file_id}/{row_idx}` | Lock 해제 후 | `lock_change / released` |
| `PUT /editor/data/{file_id}/{row_idx}` | 저장+Lock 해제 완료 후 | `lock_change / released` |

### 4.6 Backend — LockService.get_all_locks

```python
async def get_all_locks(self, file_id: str) -> list[dict[str, Any]]:
    """Redis SCAN으로 lock:{file_id}:* 패턴 조회"""
    pattern = f"lock:{file_id}:*"
    # scan_iter 사용 (KEYS 커맨드보다 프로덕션에 안전)
    # 각 key에서 row_idx 추출 + owner GET
    return [{"row_idx": 0, "user_id": "hong"}, ...]
```

### 4.7 Backend — AuthService 변경

```python
# WebSocket 호환을 위해 request 매개변수를 Optional로 변경
async def validate_session(
    self,
    session_id: str,
    request: Request | None = None,  # ← 기존: Request (필수)
) -> dict[str, Any] | None:
    # request가 None이면 IP 검사 생략
```

### 4.8 Frontend — LockStatusManager

```javascript
class LockStatusManager {
    constructor(fileId, currentUserId)

    // WebSocket URL 생성 (ws:// 또는 wss://)
    get wsUrl()

    // 연결 관리
    connect()       // WebSocket 연결 + 메시지 핸들러 등록
    disconnect()    // 연결 종료 + ping interval 정리

    // 내부: ping/keepalive
    _startPing()    // 30초 간격 ping 전송 시작
    _stopPing()     // ping interval 정리

    // 메시지 핸들러:
    //   init     → 전체 Lock 상태 사이드바에 반영
    //   lock_change → 개별 Lock 배지 업데이트/제거

    // 재연결: exponential backoff (1초 → 최대 30초)
}
```

### 4.9 Frontend — 사이드바 Lock 배지

```
┌──────────────────────────────────────┐
│ [1]  data_id_001                     │
│ [2]  data_id_002  🔒 (나)            │ ← 내가 편집 중 (녹색)
│ [3]  data_id_003  🔒 hong.gildong   │ ← 다른 사용자 편집 중 (빨간)
│ [4]  data_id_004                     │ ← 잠금 없음
└──────────────────────────────────────┘
```

**CSS:**

| 선택자 | 스타일 |
|--------|--------|
| `.lock-badge` | font-size: 11px, color: #F44336 (빨간), margin-left: auto |
| `.lock-badge.mine` | color: #4CAF50 (녹색) |

---

## 5. 코드 리뷰 및 수정 사항 (2026-02-13)

구현 완료 후 코드 리뷰를 통해 4건의 이슈를 식별하고 즉시 수정했다.

### 5.1 수정 목록

| # | 파일 | 이슈 | 심각도 | 수정 내용 |
|---|------|------|--------|-----------|
| 1 | `app/core/pending_tasks.py` | `track()` 반환 타입 `-> None` (pyright 에러) | 중 | `-> AsyncIterator[None]` + `collections.abc.AsyncIterator` import |
| 2 | `app/api/v1/endpoints/websocket.py` | timeout 후 `send_json` 실패 시 미처리 예외 | **높음** | `send_json`을 `try/except`로 감싸서 실패 시 `break` |
| 3 | `app/services/websocket_manager.py` | `broadcast` 중 리스트 변경 가능성 | 낮음 | `list()` 스냅샷 복사 후 순회 |
| 4 | `app/templates/editor.html` | `LockStatusManager`에 ping 전송 미구현 | 중 | `_startPing()` / `_stopPing()` 추가 (30초 간격) |

### 5.2 수정 전후 비교

#### 이슈 #1: PendingTaskTracker 타입 어노테이션

```python
# Before
@asynccontextmanager
async def track(self) -> None:

# After
from collections.abc import AsyncIterator

@asynccontextmanager
async def track(self) -> AsyncIterator[None]:
```

#### 이슈 #2: WebSocket timeout 예외 처리

```python
# Before
except asyncio.TimeoutError:
    await websocket.send_json({"type": "pong"})  # 연결 끊겼으면 미처리 예외!

# After
except asyncio.TimeoutError:
    try:
        await websocket.send_json({"type": "pong"})
    except Exception:
        break  # 안전하게 루프 탈출 → finally에서 disconnect
```

#### 이슈 #3: broadcast 방어적 복사

```python
# Before
for conn in self._connections[file_id]:  # await 중 리스트 변경 가능

# After
snapshot = list(self._connections[file_id])  # 복사본
for conn in snapshot:
```

#### 이슈 #4: 클라이언트 ping 전송

```javascript
// After — onopen 시 ping interval 시작
this.ws.onopen = () => {
    this.reconnectDelay = 1000;
    this._startPing();   // 추가
};

_startPing() {
    this._stopPing();
    this.pingInterval = setInterval(() => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            try { this.ws.send(JSON.stringify({ type: 'ping' })); } catch (e) {}
        }
    }, 30000);
}
```

---

## 6. 전체 변경/추가 파일 목록

### 신규 파일

| 파일 | 역할 |
|------|------|
| `app/core/pending_tasks.py` | PendingTaskTracker — 진행 중 작업 추적 |
| `app/services/websocket_manager.py` | ConnectionManager — file_id별 WebSocket 풀 관리 |
| `app/api/v1/endpoints/health.py` | Health Check 엔드포인트 |
| `app/api/v1/endpoints/websocket.py` | WebSocket Lock 상태 엔드포인트 |

### 수정 파일

| 파일 | 변경 내용 |
|------|-----------|
| `app/core/config.py` | `DISK_USAGE_WARNING_PCT: float = 90.0` 추가 |
| `app/main.py` | Lifespan shutdown에 `pending_tracker.wait_all_done()` 추가 |
| `app/api/v1/api.py` | `health`, `websocket` 라우터 등록 |
| `app/api/deps.py` | `get_pending_tracker()` 의존성 추가 |
| `app/api/v1/endpoints/editor.py` | `pending_tracker.track()` 래핑, `ws_manager.broadcast()` 호출 |
| `app/services/lock_service.py` | `get_all_locks(file_id)` 메서드 추가 |
| `app/services/auth_service.py` | `validate_session(request=None)` 선택적 매개변수 |
| `app/templates/editor.html` | `LockStatusManager` JS 클래스, `.lock-badge` CSS |

---

## 7. 아키텍처 다이어그램

```
┌─────────────┐     HTTP      ┌─────────────────────────┐
│  Browser A   │─────────────→│  FastAPI (editor.py)     │
│  Browser B   │─────────────→│                          │
│  Browser C   │─────────────→│  Lock/Save API           │
└──────┬───────┘              │    │                      │
       │                      │    ├─ LockService (Redis) │
       │ WebSocket            │    ├─ FileService (JSONL) │
       │                      │    └─ AuditService (Log)  │
       │                      └──────────┬────────────────┘
       │                                 │ broadcast
       ▼                                 ▼
┌──────────────┐              ┌──────────────────────┐
│ LockStatus   │←─────────────│  ConnectionManager   │
│ Manager (JS) │  init /      │  (websocket_manager) │
│              │  lock_change  │                      │
│ updateBadge()│              │  _connections:        │
│ _startPing() │  ←───pong──→ │    file_id → [ws...] │
└──────────────┘              └──────────────────────┘

┌──────────────────────────────────────────────┐
│ Graceful Shutdown                             │
│                                               │
│  save_data() ──→ pending_tracker.track()      │
│                       │                       │
│  lifespan shutdown ──→ wait_all_done(30s)     │
│                       │                       │
│                       └──→ redis_manager.close │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│ Health Check (GET /api/v1/health)             │
│                                               │
│  redis_manager.redis.ping() ──→ redis_ok      │
│  DATA_DIR touch/unlink ────────→ storage_ok   │
│  shutil.disk_usage() ─────────→ disk_pct      │
│                                               │
│  → status: healthy | degraded | unhealthy     │
└──────────────────────────────────────────────┘
```

---

## 8. 테스트 가이드

### 8.1 Health Check

```bash
# 서비스 정상 시
curl http://localhost:8000/api/v1/health
# → {"status":"healthy","redis_ok":true,"storage_ok":true,"disk_usage_pct":45.3,...}

# Redis 중지 후
docker-compose stop redis-master
curl http://localhost:8000/api/v1/health
# → {"status":"unhealthy","redis_ok":false,...}
```

### 8.2 Graceful Shutdown

```bash
# 저장 중 종료 시뮬레이션
# 1. 브라우저에서 편집 → 저장 클릭 직후
# 2. docker-compose stop web
# 3. 로그 확인: "All pending tasks completed" 또는 timeout 경고
```

### 8.3 WebSocket Lock Status

```
# 브라우저 2개로 동일 파일 열기
# 1. 브라우저 A: 편집 시작 (Lock 획득)
# 2. 브라우저 B: 사이드바에서 해당 Row에 🔒 배지 확인
# 3. 브라우저 A: 저장 또는 취소
# 4. 브라우저 B: 🔒 배지 자동 제거 확인
```

---

## 9. 남은 Phase

### Phase 5 — Ops & Test
- 동시성/복구 테스트
- Content Security Policy 헤더
- HTTPS 강제 (프로덕션)
