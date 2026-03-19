"""API v1 라우터 통합 (Phase 6: GCS 추가)"""

from fastapi import APIRouter

from app.api.v1.endpoints import auth, draft, editor, files, gcs, health, websocket

api_router = APIRouter()

# Health Check (Phase 4)
api_router.include_router(health.router, tags=["health"])

# WebSocket (Phase 4)
api_router.include_router(websocket.router, tags=["websocket"])

# 인증 엔드포인트
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# JSON API 엔드포인트 (editor: lock, data CRUD)
api_router.include_router(editor.router, prefix="/editor", tags=["editor"])

# Draft / Auto-save 엔드포인트
api_router.include_router(draft.router, prefix="/editor", tags=["draft"])

# HTML 뷰 엔드포인트 (파일 목록, 뷰어, 다운로드)
api_router.include_router(files.router, prefix="/view", tags=["view"])

# GCS 파일 관리 (Phase 6)
api_router.include_router(gcs.router, prefix="/gcs", tags=["gcs"])
