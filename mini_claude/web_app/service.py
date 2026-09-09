"""业务功能层：会话生命周期辅助、工具执行器、知识库重建/状态。

只实现职责内的逻辑，不直接持有全局状态（统一从 state 模块读）、不拼 HTTP 响应。
"""

import queue
import threading

from fastapi import HTTPException

from ..config import get_config
from ..knowledge import build_index, get_index_info
from ..main import _compact_messages, _estimate_tokens, _get_context_window
from ..tools import execute_tool
from . import metrics, state


# ── 工具执行器（web 版：MCP 加锁 + run_bash 开关） ──


def make_tool_executor(allow_bash: bool):
    def executor(name: str, args: dict) -> str:
        parsed = state._mcp.parse_tool_name(name)
        if parsed and state._mcp.is_connected(parsed[0]):
            server_name, tool_name = parsed
            with state._mcp_lock:
                return state._mcp.call_tool(server_name, tool_name, args)
        if name == "run_bash" and not allow_bash:
            return (
                "[web 模式已禁用命令执行。若需要执行命令，"
                "请在输入栏打开『允许执行命令』开关后重试。]"
            )
        return execute_tool(name, args)

    return executor


# ── 会话辅助 ──


def find_or_create_session(sid: str | None) -> state.ChatSession:
    if sid:
        with state._sessions_lock:
            s = state._sessions.get(sid)
        if s:
            return s
    s = state.ChatSession()
    with state._sessions_lock:
        state._sessions[s.id] = s
    return s


def get_session(sid: str) -> state.ChatSession:
    with state._sessions_lock:
        s = state._sessions.get(sid)
    if not s:
        raise HTTPException(status_code=404, detail="会话不存在")
    return s


def update_title(session: state.ChatSession) -> None:
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


def auto_compact(messages: list[dict], config: dict, ctx_window: int) -> None:
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


def public_messages(messages: list[dict]) -> list[dict]:
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


def run_turn(session: state.ChatSession, message: str, allow_bash: bool,
             q: "queue.Queue[dict | None]") -> None:
    try:
        _run_turn_inner(session, message, allow_bash, q)
    finally:
        session.lock.release()


def _run_turn_inner(session: state.ChatSession, message: str, allow_bash: bool,
                    q: "queue.Queue[dict | None]") -> None:
    from ..agent import stream_agent

    config = get_config()
    ctx_window = _get_context_window(config["model"])
    snapshot_len = len(session.messages)
    try:
        session.messages.append({"role": "user", "content": message})
        auto_compact(session.messages, config, ctx_window)
        snapshot_len = len(session.messages)

        executor = make_tool_executor(allow_bash)
        final_text = ""
        for ev in stream_agent(session.messages, state._tools, tool_executor=executor):
            if ev["type"] == "text":
                final_text = ev["content"]
            elif ev["type"] == "usage":
                # 每轮回答的累计用量落库（写库失败不影响对话流程）
                try:
                    metrics.record_turn(session.id, ev)
                except Exception:
                    pass
            q.put(ev)

        if final_text:
            session.messages.append({"role": "assistant", "content": final_text})
            update_title(session)
        else:
            # 没有最终回答（如流被中断）→ 回滚本次 turn 的改动
            del session.messages[snapshot_len:]
        q.put(None)
    except Exception as e:
        del session.messages[snapshot_len:]
        q.put({"type": "error", "message": str(e)})
        q.put(None)


# ── 知识库（索引状态 + 重建） ──


def kb_status() -> dict:
    with state._kb_state_lock:
        building = state._kb_building
        last_error = state._kb_last_error
    info = get_index_info(state._KB_INDEX_DIR)
    return {
        "building": building,
        "last_error": last_error,
        "exists": info.get("exists", False),
        "total_chunks": info.get("total_chunks", 0),
        "files": info.get("files", []),
    }


def kb_rebuild():
    """触发知识库索引重建；相同任务进行中则返回 False。"""
    with state._kb_state_lock:
        if state._kb_building:
            return False
        state._kb_building = True
        state._kb_last_error = None

    def _build():
        try:
            build_index(get_config(), state._KB_DIR, state._KB_INDEX_DIR)
            state._kb_last_error = None
        except Exception as e:
            state._kb_last_error = str(e)
        finally:
            state._kb_building = False

    threading.Thread(target=_build, daemon=True).start()
    return True