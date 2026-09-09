"""接口层：把所有 HTTP 路由打包成 APIRouter，只做薄封装，逻辑在 service/state。

对应原 web.py 的全部 API：
- /api/config /api/health …… 配置与健康检查
- /api/kb/...                    知识库
- /api/sessions ...              会话管理
- /api/chat                      聊天（SSE 流式）
"""

import asyncio
import json
import queue
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import health
from . import metrics, service, state


# ── 工具路由 ──


def _sse_frame(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _event_stream(session, q: "queue.Queue[dict | None]", is_new: bool):
    if is_new:
        yield _sse_frame({"type": "session", "id": session.id, "title": session.title})
    while True:
        ev = await asyncio.to_thread(q.get)
        if ev is None:
            break
        yield _sse_frame(ev)
    yield _sse_frame({"type": "done"})


# ── 请求模型 ──


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    allow_bash: bool = True


# ── 路由：配置 / 健康 ──


config_health_router = APIRouter(tags=["config/health"])


@config_health_router.get("/api/config")
def api_config():
    from ..config import get_config

    cfg = get_config()
    return {"model": cfg["model"], "base_url": cfg["base_url"]}


@config_health_router.get("/api/health")
def api_health():
    return {"items": health.check_all()}


# ── 路由：知识库 ──


kb_router = APIRouter(prefix="/api/kb", tags=["knowledge"])


@kb_router.get("/status")
def api_kb_status():
    return service.kb_status()


@kb_router.post("/rebuild")
def api_kb_rebuild():
    if not service.kb_rebuild():
        raise HTTPException(status_code=409, detail="索引正在重建中")
    return {"started": True}


# ── 路由：会话管理 ──


sessions_router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@sessions_router.get("")
def list_sessions():
    with state._sessions_lock:
        items = [{
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "message_count": len(service.public_messages(s.messages)),
        } for s in state._sessions.values()]
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


@sessions_router.post("")
def create_session():
    s = service.find_or_create_session(None)
    return {"id": s.id, "title": s.title}


@sessions_router.get("/{sid}")
def get_session(sid: str):
    s = service.get_session(sid)
    return {"id": s.id, "title": s.title, "messages": service.public_messages(s.messages)}


@sessions_router.delete("/{sid}")
def delete_session(sid: str):
    with state._sessions_lock:
        state._sessions.pop(sid, None)
    return {"ok": True}


# ── 路由：用量统计 ──


usage_router = APIRouter(prefix="/api/usage", tags=["usage"])


@usage_router.get("/sessions")
def usage_sessions():
    """按会话聚合的 token 用量与缓存命中率（只含当前内存中的会话，即本次启动以来）。"""
    with state._sessions_lock:
        sessions = sorted(state._sessions.values(), key=lambda s: s.created_at)
    agg = metrics.session_usage([s.id for s in sessions])
    out = []
    for s in sessions:
        u = agg.get(
            s.id, {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        )
        hit_rate = (
            u["cached_tokens"] / u["input_tokens"] if u["input_tokens"] else None
        )
        out.append({
            "id": s.id,
            "title": s.title,
            "input_tokens": u["input_tokens"],
            "output_tokens": u["output_tokens"],
            "cached_tokens": u["cached_tokens"],
            "hit_rate": hit_rate,
        })
    return out


# ── 路由：聊天（SSE） ──


chat_router = APIRouter(tags=["chat"])


@chat_router.post("/api/chat")
async def chat(body: ChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    session = service.find_or_create_session(body.session_id)
    if not session.lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该会话正在处理中，请稍候")

    q: "queue.Queue[dict | None]" = queue.Queue()
    threading.Thread(
        target=service.run_turn,
        args=(session, message, body.allow_bash, q),
        daemon=True,
    ).start()

    return StreamingResponse(
        _event_stream(session, q, body.session_id is None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )