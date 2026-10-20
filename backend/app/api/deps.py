"""FastAPI 依赖：从 app.state 取应用上下文，避免路由层自己拼装全局状态。"""

from __future__ import annotations

from fastapi import Request

from app.services.context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx
