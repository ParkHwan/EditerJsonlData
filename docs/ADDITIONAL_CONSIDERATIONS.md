# 추가 고려사항 및 예방 조치

> **작성일**: 2026-01-19  
> **관련 문서**: Gemini.md, DEVELOPMENT_SPECIALIST.md  
> **목적**: JSONL 에디터 서비스 개발 시 예방 차원에서 고려해야 할 추가 사항 정리

---

## 1. 데이터 무결성 강화

### 1.1 Lock TTL 만료 시 데이터 충돌 문제

**문제점**  
현재 Lock TTL이 10분으로 설정되어 있어, 사용자가 편집 중에 Lock이 만료되면:
- 다른 사용자가 같은 Row를 수정 가능
- 기존 작업자가 저장 시 **데이터 덮어쓰기** 발생

**해결 방안: Lock Heartbeat + 만료 임박 경고**

```python
class LockService:
    LOCK_TTL = 600  # 10분
    WARN_THRESHOLD = 120  # 만료 2분 전 경고
    
    async def extend_lock(self, file_id: str, row_idx: int, user_id: str) -> bool:
        """하트비트로 Lock 연장"""
        key = f"lock:{file_id}:{row_idx}"
        current = await self.redis.get(key)
        if current == user_id:
            await self.redis.expire(key, self.LOCK_TTL)
            return True
        return False
    
    async def get_lock_remaining_time(self, file_id: str, row_idx: int) -> int:
        """Lock 남은 시간 조회 (초 단위)"""
        key = f"lock:{file_id}:{row_idx}"
        return await self.redis.ttl(key)
```

**프론트엔드 구현 예시**

```javascript
// 30초마다 Lock Heartbeat 전송
let heartbeatInterval = setInterval(async () => {
    const response = await fetch(`/api/v1/lock/${fileId}/${rowIdx}/heartbeat`, {
        method: 'POST'
    });
    
    const data = await response.json();
    if (data.remaining_seconds < 120) {
        showWarning("Lock이 곧 만료됩니다. 저장하거나 연장하세요.");
    }
}, 30000);
```

---

### 1.2 Optimistic Locking 추가

**목적**  
Row별 버전 번호를 추가하여 충돌 감지 및 데이터 손실 방지

**스키마 확장**

```python
# schemas/item.py
class ItemBase(BaseModel):
    content: dict
    version: int = 1
    modified_at: datetime
    modified_by: str | None = None
```

**저장 시 충돌 감지**

```python
async def update_row(
    file_id: str, 
    row_idx: int, 
    update_data: ItemUpdate,
    expected_version: int
):
    current_row = await file_service.get_row(file_id, row_idx)
    
    if current_row.version != expected_version:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "conflict",
                "message": "다른 사용자가 먼저 수정했습니다.",
                "current_version": current_row.version,
                "your_version": expected_version
            }
        )
    
    # 버전 증가 후 저장
    update_data.version = current_row.version + 1
    await file_service.save_row(file_id, row_idx, update_data)
```

---

### 1.3 백업 전략 명시

**백업 정책**

| 구분 | 주기 | 보관 기간 | 저장 위치 |
|------|------|----------|----------|
| 수정 전 자동 백업 | 매 저장 시 | 24시간 | `data/backups/{file_id}/` |
| 정기 스냅샷 | 1시간마다 | 7일 | `data/snapshots/` |
| 일일 백업 | 매일 00:00 | 30일 | GCS 또는 외부 스토리지 |

**구현 예시**

```python
import shutil
from datetime import datetime
from pathlib import Path

class BackupService:
    BACKUP_DIR = Path("data/backups")
    
    async def create_backup(self, file_path: Path) -> Path:
        """수정 전 자동 백업 생성"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = self.BACKUP_DIR / file_path.stem
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        backup_path = backup_dir / f"{file_path.stem}_{timestamp}.jsonl.bak"
        shutil.copy2(file_path, backup_path)
        
        # 오래된 백업 정리 (24시간 이상)
        await self._cleanup_old_backups(backup_dir, hours=24)
        
        return backup_path
    
    async def restore_from_backup(self, file_path: Path, backup_path: Path) -> bool:
        """백업에서 복구"""
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup not found: {backup_path}")
        
        shutil.copy2(backup_path, file_path)
        return True
```

**롤백 API**

```python
@router.post("/api/v1/files/{file_id}/rollback")
async def rollback_file(
    file_id: str,
    backup_timestamp: str,
    current_user: User = Depends(get_current_user)
):
    """특정 시점의 백업으로 롤백"""
    backup_path = await backup_service.find_backup(file_id, backup_timestamp)
    await backup_service.restore_from_backup(file_id, backup_path)
    
    # 감사 로그 기록
    await audit_service.log(
        user_id=current_user.id,
        action="rollback",
        file_id=file_id,
        details={"restored_from": str(backup_path)}
    )
    
    return {"status": "success", "message": f"Rolled back to {backup_timestamp}"}
```

---

## 2. 고가용성 및 장애 대응

### 2.1 Redis SPOF(Single Point of Failure) 대응

**문제점**  
Redis가 단일 인스턴스라면 장애 시 전체 서비스 중단

**해결 방안: Redis Sentinel 구성**

```yaml
# docker-compose.yml
version: '3.8'

services:
  redis-master:
    image: redis:7-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis-master-data:/data
    ports:
      - "6379:6379"

  redis-slave:
    image: redis:7-alpine
    command: redis-server --replicaof redis-master 6379 --appendonly yes
    depends_on:
      - redis-master
    volumes:
      - redis-slave-data:/data

  redis-sentinel:
    image: redis:7-alpine
    command: redis-sentinel /etc/redis/sentinel.conf
    volumes:
      - ./sentinel.conf:/etc/redis/sentinel.conf
    depends_on:
      - redis-master
      - redis-slave
    ports:
      - "26379:26379"

volumes:
  redis-master-data:
  redis-slave-data:
```

**sentinel.conf**

```conf
sentinel monitor mymaster redis-master 6379 2
sentinel down-after-milliseconds mymaster 5000
sentinel failover-timeout mymaster 60000
sentinel parallel-syncs mymaster 1
```

---

### 2.2 Redis 장애 시 Graceful Degradation

**구현 예시**

```python
from redis.exceptions import ConnectionError as RedisConnectionError
from contextlib import asynccontextmanager

class LockServiceWithFallback:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.degraded_mode = False
    
    @asynccontextmanager
    async def safe_operation(self):
        """Redis 연결 실패 시 안전하게 처리"""
        try:
            yield
        except RedisConnectionError as e:
            self.degraded_mode = True
            logger.error(f"Redis connection failed: {e}")
            raise ServiceDegradedError(
                "현재 수정 기능을 사용할 수 없습니다. 잠시 후 다시 시도해 주세요."
            )
    
    async def acquire_lock_safe(
        self, 
        file_id: str, 
        row_idx: int, 
        user_id: str
    ) -> bool:
        async with self.safe_operation():
            return await self.acquire_lock(file_id, row_idx, user_id)
    
    async def check_redis_health(self) -> bool:
        """Redis 연결 상태 확인"""
        try:
            await self.redis.ping()
            self.degraded_mode = False
            return True
        except RedisConnectionError:
            self.degraded_mode = True
            return False
```

**프론트엔드 알림**

```python
@router.get("/api/v1/status")
async def service_status(lock_service: LockService = Depends(get_lock_service)):
    return {
        "status": "degraded" if lock_service.degraded_mode else "healthy",
        "features": {
            "read": True,
            "edit": not lock_service.degraded_mode,
            "download": True
        }
    }
```

---

## 3. 보안 강화

### 3.1 CSRF 보호

**설치**

```bash
pip install fastapi-csrf-protect
```

**구현**

```python
# core/security.py
from fastapi_csrf_protect import CsrfProtect
from pydantic import BaseModel

class CsrfSettings(BaseModel):
    secret_key: str
    cookie_samesite: str = "lax"
    cookie_secure: bool = True

@CsrfProtect.load_config
def get_csrf_config():
    return CsrfSettings(secret_key=settings.SECRET_KEY)

# main.py
from fastapi_csrf_protect import CsrfProtect

app = FastAPI()

@app.exception_handler(CsrfProtectError)
async def csrf_exception_handler(request: Request, exc: CsrfProtectError):
    return JSONResponse(
        status_code=403,
        content={"detail": "CSRF token validation failed"}
    )

# api/v1/endpoints/editor.py
@router.post("/api/v1/editor/{file_id}/{row_idx}")
async def update_row(
    file_id: str,
    row_idx: int,
    update_data: ItemUpdate,
    csrf_protect: CsrfProtect = Depends()
):
    await csrf_protect.validate_csrf()
    # ... 처리 로직
```

**Jinja2 템플릿에 CSRF 토큰 포함**

```html
<!-- templates/editor.html -->
<form method="POST" action="/api/v1/editor/{{ file_id }}/{{ row_idx }}">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <!-- 폼 내용 -->
</form>
```

---

### 3.2 Rate Limiting

**설치**

```bash
pip install slowapi
```

**구현**

```python
# core/rate_limit.py
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)

# main.py
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# api/v1/endpoints/editor.py
@router.post("/api/v1/editor/{file_id}/{row_idx}")
@limiter.limit("30/minute")  # 분당 30회 제한
async def update_row(request: Request, ...):
    ...

@router.get("/api/v1/files")
@limiter.limit("100/minute")  # 분당 100회 제한
async def list_files(request: Request, ...):
    ...
```

**사용자별 제한 (인증 기반)**

```python
def get_user_identifier(request: Request) -> str:
    """인증된 사용자 ID 또는 IP로 Rate Limit 키 생성"""
    user = getattr(request.state, "user", None)
    if user:
        return f"user:{user.id}"
    return f"ip:{get_remote_address(request)}"

limiter = Limiter(key_func=get_user_identifier)
```

---

### 3.3 세션 보안 강화

**세션 고정 공격 방지**

```python
# services/auth_service.py
import secrets
from datetime import timedelta

class AuthService:
    SESSION_TTL = timedelta(hours=48)
    
    async def login(self, user: User, old_session_id: str | None = None) -> str:
        """로그인 시 새 세션 ID 생성 (세션 고정 공격 방지)"""
        # 기존 세션 무효화
        if old_session_id:
            await self.redis.delete(f"session:{old_session_id}")
        
        # 새 세션 생성
        new_session_id = secrets.token_urlsafe(32)
        session_data = {
            "user_id": user.id,
            "created_at": datetime.utcnow().isoformat(),
            "ip": request.client.host,
            "user_agent": request.headers.get("user-agent")
        }
        
        await self.redis.setex(
            f"session:{new_session_id}",
            int(self.SESSION_TTL.total_seconds()),
            json.dumps(session_data)
        )
        
        return new_session_id
    
    async def validate_session(
        self, 
        session_id: str, 
        request: Request
    ) -> User | None:
        """세션 유효성 검증 (IP, User-Agent 확인)"""
        session_data = await self.redis.get(f"session:{session_id}")
        if not session_data:
            return None
        
        data = json.loads(session_data)
        
        # IP 변경 감지 (선택적)
        if data.get("ip") != request.client.host:
            logger.warning(f"Session IP mismatch: {session_id}")
            # 엄격 모드에서는 세션 무효화
            # await self.redis.delete(f"session:{session_id}")
            # return None
        
        return await self.get_user(data["user_id"])
```

---

## 4. 운영 및 모니터링

### 4.1 감사 로그(Audit Log)

**스키마**

```python
# schemas/audit.py
from pydantic import BaseModel
from datetime import datetime
from typing import Literal

class AuditLog(BaseModel):
    id: str  # UUID
    timestamp: datetime
    user_id: str
    user_name: str
    action: Literal["view", "edit_start", "edit_save", "edit_cancel", "download", "rollback"]
    file_id: str
    row_idx: int | None = None
    ip_address: str
    user_agent: str
    changes: dict | None = None  # 수정 전/후 diff
    metadata: dict | None = None
```

**서비스 구현**

```python
# services/audit_service.py
import uuid
from datetime import datetime

class AuditService:
    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.storage_path.mkdir(parents=True, exist_ok=True)
    
    async def log(
        self,
        user_id: str,
        user_name: str,
        action: str,
        file_id: str,
        request: Request,
        row_idx: int | None = None,
        changes: dict | None = None,
        metadata: dict | None = None
    ):
        audit_entry = AuditLog(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            user_id=user_id,
            user_name=user_name,
            action=action,
            file_id=file_id,
            row_idx=row_idx,
            ip_address=request.client.host,
            user_agent=request.headers.get("user-agent", ""),
            changes=changes,
            metadata=metadata
        )
        
        # 일별 파일로 저장
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        log_file = self.storage_path / f"audit_{date_str}.jsonl"
        
        async with aiofiles.open(log_file, "a") as f:
            await f.write(audit_entry.model_dump_json() + "\n")
    
    async def query_logs(
        self,
        start_date: datetime,
        end_date: datetime,
        user_id: str | None = None,
        file_id: str | None = None,
        action: str | None = None
    ) -> list[AuditLog]:
        """감사 로그 조회"""
        logs = []
        # ... 구현
        return logs
```

---

### 4.2 Health Check 엔드포인트

```python
# api/v1/endpoints/health.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
import psutil

router = APIRouter()

class HealthStatus(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    redis: bool
    storage: bool
    disk_usage_percent: float
    memory_usage_percent: float
    details: dict | None = None

@router.get("/health", response_model=HealthStatus)
async def health_check(
    redis_client = Depends(get_redis_client),
    file_service = Depends(get_file_service)
):
    redis_ok = await check_redis(redis_client)
    storage_ok = await check_storage(file_service)
    disk_usage = psutil.disk_usage("/").percent
    memory_usage = psutil.virtual_memory().percent
    
    # 상태 판정
    if redis_ok and storage_ok and disk_usage < 90:
        status = "healthy"
    elif redis_ok and storage_ok:
        status = "degraded"
    else:
        status = "unhealthy"
    
    return HealthStatus(
        status=status,
        redis=redis_ok,
        storage=storage_ok,
        disk_usage_percent=disk_usage,
        memory_usage_percent=memory_usage,
        details={
            "disk_warning": disk_usage > 80,
            "memory_warning": memory_usage > 85
        }
    )

async def check_redis(redis_client) -> bool:
    try:
        await redis_client.ping()
        return True
    except Exception:
        return False

async def check_storage(file_service) -> bool:
    try:
        # 테스트 파일 읽기/쓰기
        test_path = file_service.data_dir / ".health_check"
        test_path.write_text("ok")
        test_path.unlink()
        return True
    except Exception:
        return False
```

---

### 4.3 Graceful Shutdown

```python
# main.py
import asyncio
from contextlib import asynccontextmanager

# 진행 중인 작업 추적
pending_tasks: set[asyncio.Task] = set()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Application starting...")
    yield
    
    # Shutdown
    logger.info("Application shutting down...")
    
    # 진행 중인 저장 작업 완료 대기 (최대 30초)
    if pending_tasks:
        logger.info(f"Waiting for {len(pending_tasks)} pending tasks...")
        done, pending = await asyncio.wait(
            pending_tasks, 
            timeout=30,
            return_when=asyncio.ALL_COMPLETED
        )
        if pending:
            logger.warning(f"{len(pending)} tasks did not complete in time")
            for task in pending:
                task.cancel()
    
    # Redis 연결 종료
    await redis_client.close()
    logger.info("Shutdown complete")

app = FastAPI(lifespan=lifespan)

# 저장 작업을 추적 가능하게 래핑
async def save_with_tracking(coro):
    task = asyncio.current_task()
    pending_tasks.add(task)
    try:
        return await coro
    finally:
        pending_tasks.discard(task)
```

---

## 5. UX/기능 개선

### 5.1 Auto-save / Draft 저장

**백엔드 API**

```python
# api/v1/endpoints/draft.py
@router.post("/api/v1/draft/{file_id}/{row_idx}")
async def save_draft(
    file_id: str,
    row_idx: int,
    content: dict,
    current_user: User = Depends(get_current_user),
    redis_client = Depends(get_redis_client)
):
    """임시 저장 (Draft)"""
    draft_key = f"draft:{file_id}:{row_idx}:{current_user.id}"
    draft_data = {
        "content": content,
        "saved_at": datetime.utcnow().isoformat()
    }
    
    # 30분 TTL로 저장
    await redis_client.setex(draft_key, 1800, json.dumps(draft_data))
    return {"status": "saved", "key": draft_key}

@router.get("/api/v1/draft/{file_id}/{row_idx}")
async def get_draft(
    file_id: str,
    row_idx: int,
    current_user: User = Depends(get_current_user),
    redis_client = Depends(get_redis_client)
):
    """저장된 Draft 조회"""
    draft_key = f"draft:{file_id}:{row_idx}:{current_user.id}"
    draft_data = await redis_client.get(draft_key)
    
    if draft_data:
        return json.loads(draft_data)
    return None
```

**프론트엔드 구현**

```javascript
// static/js/autosave.js
class AutoSave {
    constructor(fileId, rowIdx, interval = 30000) {
        this.fileId = fileId;
        this.rowIdx = rowIdx;
        this.interval = interval;
        this.hasChanges = false;
        this.lastSavedContent = null;
        this.timer = null;
    }
    
    start() {
        this.timer = setInterval(() => this.save(), this.interval);
        
        // 페이지 이탈 시 저장
        window.addEventListener('beforeunload', (e) => {
            if (this.hasChanges) {
                this.saveSync();
                e.returnValue = '저장되지 않은 변경사항이 있습니다.';
            }
        });
    }
    
    markChanged(content) {
        if (JSON.stringify(content) !== JSON.stringify(this.lastSavedContent)) {
            this.hasChanges = true;
        }
    }
    
    async save() {
        if (!this.hasChanges) return;
        
        const content = this.getCurrentContent();
        try {
            await fetch(`/api/v1/draft/${this.fileId}/${this.rowIdx}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });
            
            this.lastSavedContent = content;
            this.hasChanges = false;
            this.showNotification('임시 저장됨', 'success');
        } catch (error) {
            console.error('Auto-save failed:', error);
        }
    }
    
    stop() {
        if (this.timer) {
            clearInterval(this.timer);
        }
    }
}
```

---

### 5.2 실시간 Lock 상태 표시 (WebSocket)

**백엔드 구현**

```python
# api/v1/endpoints/websocket.py
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Set

class ConnectionManager:
    def __init__(self):
        # file_id -> set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket, file_id: str):
        await websocket.accept()
        if file_id not in self.active_connections:
            self.active_connections[file_id] = set()
        self.active_connections[file_id].add(websocket)
    
    def disconnect(self, websocket: WebSocket, file_id: str):
        if file_id in self.active_connections:
            self.active_connections[file_id].discard(websocket)
    
    async def broadcast(self, file_id: str, message: dict):
        if file_id in self.active_connections:
            for connection in self.active_connections[file_id]:
                try:
                    await connection.send_json(message)
                except Exception:
                    pass

manager = ConnectionManager()

@router.websocket("/ws/lock-status/{file_id}")
async def lock_status_websocket(
    websocket: WebSocket,
    file_id: str,
    lock_service: LockService = Depends(get_lock_service)
):
    await manager.connect(websocket, file_id)
    
    try:
        # 현재 Lock 상태 전송
        locks = await lock_service.get_all_locks(file_id)
        await websocket.send_json({"type": "init", "locks": locks})
        
        while True:
            # 클라이언트 메시지 대기 (ping/pong)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket, file_id)

# Lock 변경 시 브로드캐스트
async def notify_lock_change(file_id: str, row_idx: int, action: str, user_name: str):
    await manager.broadcast(file_id, {
        "type": "lock_change",
        "row_idx": row_idx,
        "action": action,  # "acquired" | "released"
        "user_name": user_name,
        "timestamp": datetime.utcnow().isoformat()
    })
```

**프론트엔드 구현**

```javascript
// static/js/realtime-lock.js
class LockStatusManager {
    constructor(fileId) {
        this.fileId = fileId;
        this.socket = null;
        this.lockStatus = new Map();  // row_idx -> { user_name, locked_at }
    }
    
    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        this.socket = new WebSocket(`${protocol}//${window.location.host}/ws/lock-status/${this.fileId}`);
        
        this.socket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleMessage(data);
        };
        
        this.socket.onclose = () => {
            // 3초 후 재연결
            setTimeout(() => this.connect(), 3000);
        };
        
        // 30초마다 ping
        setInterval(() => {
            if (this.socket.readyState === WebSocket.OPEN) {
                this.socket.send('ping');
            }
        }, 30000);
    }
    
    handleMessage(data) {
        switch (data.type) {
            case 'init':
                data.locks.forEach(lock => {
                    this.lockStatus.set(lock.row_idx, lock);
                    this.updateRowUI(lock.row_idx, true, lock.user_name);
                });
                break;
            
            case 'lock_change':
                if (data.action === 'acquired') {
                    this.lockStatus.set(data.row_idx, {
                        user_name: data.user_name,
                        locked_at: data.timestamp
                    });
                    this.updateRowUI(data.row_idx, true, data.user_name);
                } else {
                    this.lockStatus.delete(data.row_idx);
                    this.updateRowUI(data.row_idx, false, null);
                }
                break;
        }
    }
    
    updateRowUI(rowIdx, isLocked, userName) {
        const row = document.querySelector(`[data-row-idx="${rowIdx}"]`);
        if (!row) return;
        
        const editBtn = row.querySelector('.edit-btn');
        const lockIndicator = row.querySelector('.lock-indicator');
        
        if (isLocked) {
            editBtn.disabled = true;
            lockIndicator.textContent = `🔒 ${userName}`;
            lockIndicator.classList.add('locked');
        } else {
            editBtn.disabled = false;
            lockIndicator.textContent = '';
            lockIndicator.classList.remove('locked');
        }
    }
}
```

---

## 6. 테스트 시나리오 보강

### 6.1 동시성 테스트 케이스

| 시나리오 | 테스트 방법 | 기대 결과 |
|---------|------------|----------|
| 두 사용자가 동시에 같은 Row Lock 요청 | pytest-asyncio로 동시 요청 | 한 명만 획득, 나머지는 409 Conflict |
| Lock 보유 중 10분 경과 | Mock time으로 TTL 만료 시뮬레이션 | Lock 자동 해제 + 다른 사용자 획득 가능 |
| 저장 중 서버 재시작 | 저장 도중 프로세스 종료 | Atomic Write로 데이터 무결성 유지 |
| Redis 장애 발생 | Redis 연결 끊기 | Read-only 모드 전환 + 적절한 에러 메시지 |
| 동시에 같은 Row 저장 시도 (Optimistic Lock) | 두 요청 동시 전송 | 하나만 성공, 나머지는 버전 충돌 에러 |

**테스트 코드 예시**

```python
# tests/integration/test_concurrent_lock.py
import pytest
import asyncio
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_concurrent_lock_acquisition():
    """두 사용자가 동시에 같은 Row Lock 요청 시 한 명만 성공"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        # 두 개의 동시 요청 생성
        task1 = client.post(
            "/api/v1/lock/test_file/0",
            headers={"Authorization": "Bearer user1_token"}
        )
        task2 = client.post(
            "/api/v1/lock/test_file/0",
            headers={"Authorization": "Bearer user2_token"}
        )
        
        results = await asyncio.gather(task1, task2)
        
        # 하나는 성공(200), 하나는 실패(409)
        status_codes = sorted([r.status_code for r in results])
        assert status_codes == [200, 409]

@pytest.mark.asyncio
async def test_optimistic_lock_conflict():
    """같은 버전으로 동시 저장 시 충돌 감지"""
    async with AsyncClient(app=app, base_url="http://test") as client:
        # 두 사용자가 같은 버전 기준으로 수정
        update_data = {"content": {"key": "value"}, "expected_version": 1}
        
        task1 = client.put(
            "/api/v1/editor/test_file/0",
            json={**update_data, "content": {"key": "value1"}},
            headers={"Authorization": "Bearer user1_token"}
        )
        task2 = client.put(
            "/api/v1/editor/test_file/0",
            json={**update_data, "content": {"key": "value2"}},
            headers={"Authorization": "Bearer user2_token"}
        )
        
        results = await asyncio.gather(task1, task2)
        
        # 하나는 성공(200), 하나는 충돌(409)
        status_codes = sorted([r.status_code for r in results])
        assert status_codes == [200, 409]
```

---

### 6.2 데이터 복구 테스트

```python
# tests/integration/test_backup_recovery.py
import pytest
from pathlib import Path

@pytest.mark.asyncio
async def test_backup_created_before_save():
    """저장 전 자동 백업 생성 확인"""
    # Arrange
    original_content = {"key": "original"}
    await file_service.create_test_file("test.jsonl", [original_content])
    
    # Act
    await file_service.update_row("test.jsonl", 0, {"key": "modified"})
    
    # Assert
    backups = list(Path("data/backups/test").glob("*.bak"))
    assert len(backups) >= 1
    
    # 백업 내용 확인
    backup_content = await file_service.read_jsonl(backups[0])
    assert backup_content[0] == original_content

@pytest.mark.asyncio
async def test_rollback_restores_data():
    """롤백 시 데이터 복구 확인"""
    # Arrange
    original_content = {"key": "original"}
    await file_service.create_test_file("test.jsonl", [original_content])
    await file_service.update_row("test.jsonl", 0, {"key": "modified"})
    
    # Act
    backups = await backup_service.list_backups("test.jsonl")
    await backup_service.restore_from_backup("test.jsonl", backups[0])
    
    # Assert
    current_content = await file_service.get_row("test.jsonl", 0)
    assert current_content == original_content

@pytest.mark.asyncio
async def test_atomic_write_on_crash():
    """저장 중 실패 시 원본 데이터 보존 확인"""
    # Arrange
    original_content = {"key": "original"}
    await file_service.create_test_file("test.jsonl", [original_content])
    
    # Act - 저장 중 예외 발생 시뮬레이션
    with pytest.raises(Exception):
        async with file_service.atomic_write("test.jsonl") as writer:
            await writer.write_row(0, {"key": "modified"})
            raise Exception("Simulated crash")
    
    # Assert - 원본 유지
    current_content = await file_service.get_row("test.jsonl", 0)
    assert current_content == original_content
```

---

## 7. 우선순위 및 실행 계획

### 7.1 우선순위 매트릭스

| 우선순위 | 항목 | 영향도 | 구현 난이도 | 비고 |
|:-------:|------|:------:|:----------:|------|
| **P0** | Lock Heartbeat + 만료 경고 | ★★★★★ | ★★☆☆☆ | 데이터 손실 직접 방지 |
| **P0** | Optimistic Locking | ★★★★★ | ★★★☆☆ | 동시 수정 충돌 방지 |
| **P0** | 자동 백업 전략 | ★★★★★ | ★★☆☆☆ | 복구 불가 시 치명적 |
| **P1** | CSRF 보호 | ★★★★☆ | ★☆☆☆☆ | 보안 기본 요소 |
| **P1** | Rate Limiting | ★★★★☆ | ★☆☆☆☆ | DoS 방지 |
| **P1** | 감사 로그 | ★★★★☆ | ★★☆☆☆ | 내부 서비스 필수 |
| **P2** | Redis HA | ★★★☆☆ | ★★★★☆ | 운영 안정성 |
| **P2** | Auto-save / Draft | ★★★☆☆ | ★★☆☆☆ | UX 개선 |
| **P2** | WebSocket 실시간 상태 | ★★☆☆☆ | ★★★☆☆ | UX 개선 (선택) |
| **P3** | Health Check | ★★☆☆☆ | ★☆☆☆☆ | 운영 편의 |
| **P3** | Graceful Shutdown | ★★☆☆☆ | ★★☆☆☆ | 안정성 |

### 7.2 추가 실행 계획

| Phase | Task | 상세 내용 | 예상 소요 |
|:-----:|------|----------|:--------:|
| Phase 1.5 | **데이터 무결성 강화** | Lock Heartbeat, Optimistic Locking, 백업 전략 구현 | 2일 |
| Phase 2.5 | **보안 강화** | CSRF, Rate Limiting, 세션 보안 | 1일 |
| Phase 3.5 | **운영 도구** | 감사 로그, Health Check, Graceful Shutdown | 1.5일 |
| Phase 4.5 | **UX 개선** | Auto-save, WebSocket 실시간 상태 (선택) | 1.5일 |
| Phase 5.5 | **테스트 보강** | 동시성/복구 테스트 시나리오 추가 | 1일 |

---

## 8. 체크리스트 요약

```plaintext
┌─────────────────────────────────────────────────────────────────┐
│                    예방 차원 체크리스트                            │
├─────────────────────────────────────────────────────────────────┤
│ ☐ Lock Heartbeat + 만료 경고 (프론트엔드)                        │
│ ☐ Optimistic Locking (version 필드)                             │
│ ☐ 자동 백업 전략 + 롤백 API                                      │
│ ☐ Redis HA 구성 (Sentinel/Cluster)                              │
│ ☐ Redis 장애 시 Graceful Degradation                            │
│ ☐ CSRF 토큰 + Rate Limiting                                     │
│ ☐ 감사 로그(Audit Log) 저장                                      │
│ ☐ Health Check 엔드포인트                                        │
│ ☐ Auto-save / Draft 저장                                        │
│ ☐ WebSocket 실시간 상태 동기화 (선택)                            │
│ ☐ Graceful Shutdown 구현                                        │
│ ☐ 동시성/장애 복구 테스트 시나리오                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 참고 자료

- [FastAPI Security](https://fastapi.tiangolo.com/tutorial/security/)
- [Redis Sentinel Documentation](https://redis.io/docs/management/sentinel/)
- [OWASP CSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [Optimistic Locking Pattern](https://martinfowler.com/eaaCatalog/optimisticOfflineLock.html)
