"""知识库路由：索引状态与后台重建。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_ctx
from app.schemas.system import KbStatus
from app.services import kb_service
from app.services.context import AppContext

router = APIRouter(prefix="/kb", tags=["knowledge"])


@router.get("/status", response_model=KbStatus)
def kb_status(ctx: AppContext = Depends(get_ctx)) -> KbStatus:
    return KbStatus(**kb_service.status(ctx.settings))


@router.post("/rebuild")
async def kb_rebuild(ctx: AppContext = Depends(get_ctx)) -> dict:
    started = await asyncio.to_thread(kb_service.rebuild, ctx.settings)
    if not started:
        raise HTTPException(status_code=409, detail="索引正在重建中")
    return {"started": True}
