"""聊天路由：一轮对话以 SSE 流式返回事件。

事件类型见 core/events.py，另有 error（本轮失败）与 done（流结束）两个控制事件。
客户端断开时只停止推送，worker 线程照常跑完并落库——历史不会丢。
"""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.deps import get_ctx
from app.db.base import session_scope
from app.db.repositories import sessions as sessions_repo
from app.schemas.chat import ChatRequest
from app.services import chat_service
from app.services.context import AppContext
from app.services.stream_broker import CLOSED, EventStream

router = APIRouter(prefix="/sessions", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _sse_frame(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _session_exists(session_id: str) -> bool:
    with session_scope() as db:
        return sessions_repo.get(db, session_id) is not None


async def _event_stream(request: Request, stream: EventStream):
    while True:
        if await request.is_disconnected():
            break
        # 带超时地取事件：超时返回 None，用来周期性检查客户端是否还在
        event = await asyncio.to_thread(stream.get, 1.0)
        if event is None:
            continue
        if event is CLOSED:
            break
        notice = stream.dropped_notice()
        if notice:
            yield _sse_frame(notice)
        yield _sse_frame(event)
    yield _sse_frame({"type": "done"})


@router.post("/{sid}/messages")
async def send_message(
    sid: str,
    body: ChatRequest,
    request: Request,
    ctx: AppContext = Depends(get_ctx),
) -> StreamingResponse:
    if not await asyncio.to_thread(_session_exists, sid):
        raise HTTPException(status_code=404, detail="会话不存在")

    runtime = ctx.registry.acquire(sid)
    if runtime is None:
        raise HTTPException(status_code=409, detail="该会话正在处理中，请稍候")

    stream = EventStream()
    threading.Thread(
        target=chat_service.run_turn,
        kwargs={
            "runtime": runtime,
            "user_message": body.message,
            "allow_bash": body.allow_bash,
            "stream": stream,
            "settings": ctx.settings,
            "tools": ctx.tools,
            "mcp": ctx.mcp,
            "mcp_lock": ctx.mcp_lock,
        },
        daemon=True,
    ).start()

    return StreamingResponse(
        _event_stream(request, stream),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
