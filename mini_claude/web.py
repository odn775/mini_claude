"""
Mini Claude Web —— FastAPI 入口。

与 CLI（main.py）共存：CLI 走终端交互，这里提供 HTTP + SSE 的浏览器聊天界面。
共享 agent / tools / knowledge / skills / health / mcp_manager 等纯逻辑模块。

会话状态：内存态 dict，每会话一把锁，同一会话同时只跑一个 turn；
不同会话并发运行（各自在独立 worker 线程里跑 agent）。

设计要点：
- run_agent 的事件流版本 stream_agent 在 worker 线程跑，事件经 queue 转发给 SSE。
  客户端断开连接不会中止 agent，turn 照常跑完并写回会话历史。
- run_bash 受 allow_bash 开关控制；关闭时拦截并告知模型，模型自动改用非命令手段。
- MCP 子进程全局共享，call 用 _MCP_LOCK 串行化（其内部 loop.run_until_complete 非线程安全）。
"""

import asyncio
import json
import os
import queue
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import health
from .agent import stream_agent
from .config import get_config
from .knowledge import build_index, get_index_info
from .main import (
    _build_system_prompt,
    _compact_messages,
    _estimate_tokens,
    _get_context_window,
)
from .mcp_manager import MCPManager
from .tools import TOOLS, execute_tool

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# ── 全局状态 ──

_sessions: dict[str, "ChatSession"] = {}
_sessions_lock = threading.Lock()

_mcp = MCPManager()
_mcp_lock = threading.Lock()      # MCP call_tool 串行化
_tools: list[dict] = list(TOOLS)  # 启动后追加 MCP 工具

# 知识库（与 main.py 保持一致的数据目录）
_KB_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "knowledge")
_KB_INDEX_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "faiss_index")
_kb_building = False
_kb_last_error: str | None = None
_kb_state_lock = threading.Lock()


class ChatSession:
    """一次浏览器会话的对话状态。"""

    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.title = "新会话"
        self.created_at = time.time()
        self.lock = threading.Lock()
        config = get_config()
        ctx_window = _get_context_window(config["model"])
        self.messages: list[dict] = [
            {"role": "system", "content": _build_system_prompt(ctx_window)},
        ]


# ── 生命周期 ──


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _tools
    health.set_mcp_manager(_mcp)
    # MCPManager.start_all 内部会自建事件循环（asyncio.set_event_loop），
    # 只能在独立线程里调，否则会与 FastAPI 主循环冲突。
    try:
        mcp_tools = await asyncio.to_thread(_mcp.start_all)
        _tools = TOOLS + mcp_tools
    except Exception:
        _tools = list(TOOLS)
    yield
    await asyncio.to_thread(_mcp.shutdown_all)


app = FastAPI(title="Mini Claude Web", lifespan=lifespan)


# ── 工具执行器（web 版：MCP 加锁 + run_bash 开关） ──


def _make_tool_executor(allow_bash: bool):
    def executor(name: str, args: dict) -> str:
        parsed = MCPManager.parse_tool_name(name)
        if parsed and _mcp.is_connected(parsed[0]):
            server_name, tool_name = parsed
            with _mcp_lock:
                return _mcp.call_tool(server_name, tool_name, args)
        if name == "run_bash" and not allow_bash:
            return (
                "[web 模式已禁用命令执行。若需要执行命令，"
                "请在输入栏打开『允许执行命令』开关后重试。]"
            )
        return execute_tool(name, args)

    return executor


# ── 会话辅助 ──


def _find_or_create_session(sid: str | None) -> ChatSession:
    if sid:
        with _sessions_lock:
            s = _sessions.get(sid)
        if s:
            return s
    s = ChatSession()
    with _sessions_lock:
        _sessions[s.id] = s
    return s


def _get_session(sid: str) -> ChatSession:
    with _sessions_lock:
        s = _sessions.get(sid)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


def _update_title(session: ChatSession) -> None:
    """用第一条 user 消息（截断到 20 字）当会话标题，只设一次。"""
    if session.title != "新会话":
        return
    for m in session.messages:
        if m.get("role") == "user":
            content = m.get("content") or ""
            if isinstance(content, list):
                content = " ".join(
                    c.get("text", "") for c in content if isinstance(c, dict)
                )
            text = str(content).strip().replace("\n", " ")
            if text:
                session.title = text[:20]
            return


def _auto_compact(messages: list[dict], config: dict, ctx_window: int) -> None:
    """token 超过上下文 90% 则压缩历史；失败时回退为丢弃最早消息。"""
    tokens, _ = _estimate_tokens(messages)
    if tokens <= int(ctx_window * 0.90):
        return
    try:
        new_messages, _ = _compact_messages(messages, config, preserve_last_user=True)
        messages.clear()
        messages.extend(new_messages)
    except Exception:
        drop_threshold = int(ctx_window * 0.70)
        while tokens > drop_threshold and len(messages) > 2:
            messages.pop(1)
            tokens, _ = _estimate_tokens(messages)


def _public_messages(messages: list[dict]) -> list[dict]:
    """会话历史只暴露 user / assistant 的纯文本消息（不含 tool 过程）。"""
    out = []
    for m in messages:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not content:
            continue
        out.append({"role": m["role"], "content": content})
    return out


# ── 一轮对话（worker 线程内执行，负责会话状态的全部变更） ──


def _run_turn(session: ChatSession, message: str, allow_bash: bool,
              q: "queue.Queue[dict | None]") -> None:
    try:
        _run_turn_inner(session, message, allow_bash, q)
    finally:
        session.lock.release()


def _run_turn_inner(session: ChatSession, message: str, allow_bash: bool,
                    q: "queue.Queue[dict | None]") -> None:
    config = get_config()
    ctx_window = _get_context_window(config["model"])
    snapshot_len = len(session.messages)
    try:
        session.messages.append({"role": "user", "content": message})
        _auto_compact(session.messages, config, ctx_window)
        snapshot_len = len(session.messages)

        executor = _make_tool_executor(allow_bash)
        final_text = ""
        for ev in stream_agent(session.messages, _tools, tool_executor=executor):
            if ev["type"] == "text":
                final_text = ev["content"]
            q.put(ev)

        if final_text:
            session.messages.append({"role": "assistant", "content": final_text})
            _update_title(session)
        else:
            # 没有最终回答（如流被中断）→ 回滚本次 turn 的改动
            del session.messages[snapshot_len:]
        q.put(None)
    except Exception as e:
        del session.messages[snapshot_len:]
        q.put({"type": "error", "message": str(e)})
        q.put(None)


def _sse_frame(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _event_stream(session: ChatSession, q: "queue.Queue[dict | None]",
                        is_new: bool):
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


# ── 页面 ──


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


# ── API ──


@app.get("/api/config")
def api_config():
    cfg = get_config()
    return {"model": cfg["model"], "base_url": cfg["base_url"]}


@app.get("/api/health")
def api_health():
    return {"items": health.check_all()}


@app.get("/api/kb/status")
def api_kb_status():
    with _kb_state_lock:
        building = _kb_building
        last_error = _kb_last_error
    info = get_index_info(_KB_INDEX_DIR)
    return {
        "building": building,
        "last_error": last_error,
        "exists": info.get("exists", False),
        "total_chunks": info.get("total_chunks", 0),
        "files": info.get("files", []),
    }


@app.post("/api/kb/rebuild")
def api_kb_rebuild():
    global _kb_building, _kb_last_error
    with _kb_state_lock:
        if _kb_building:
            raise HTTPException(status_code=409, detail="索引正在重建中")
        _kb_building = True
        _kb_last_error = None

    def _build():
        global _kb_building, _kb_last_error
        try:
            build_index(get_config(), _KB_DIR, _KB_INDEX_DIR)
            _kb_last_error = None
        except Exception as e:
            _kb_last_error = str(e)
        finally:
            _kb_building = False

    threading.Thread(target=_build, daemon=True).start()
    return {"started": True}


@app.get("/api/sessions")
def list_sessions():
    with _sessions_lock:
        items = [{
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at,
            "message_count": _public_messages(s.messages).__len__(),
        } for s in _sessions.values()]
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


@app.post("/api/sessions")
def create_session():
    s = ChatSession()
    with _sessions_lock:
        _sessions[s.id] = s
    return {"id": s.id, "title": s.title}


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    s = _get_session(sid)
    return {"id": s.id, "title": s.title, "messages": _public_messages(s.messages)}


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    with _sessions_lock:
        _sessions.pop(sid, None)
    return {"ok": True}


@app.post("/api/chat")
async def chat(body: ChatRequest):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")

    session = _find_or_create_session(body.session_id)
    if not session.lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="该会话正在处理中，请稍候")

    q: "queue.Queue[dict | None]" = queue.Queue()
    threading.Thread(
        target=_run_turn,
        args=(session, message, body.allow_bash, q),
        daemon=True,
    ).start()

    return StreamingResponse(
        _event_stream(session, q, body.session_id is None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    print("Mini Claude Web: http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
