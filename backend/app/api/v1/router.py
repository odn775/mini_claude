"""聚合 /api/v1 下的全部子路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import chat, kb, sessions, system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(sessions.router)
api_router.include_router(chat.router)
api_router.include_router(kb.router)
