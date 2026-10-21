"""一轮对话的编排：加载历史 → 自动压缩 → 跑 agent → 分发事件 → 落库。

设计要点：
- 全程持有会话的独占锁（由 SessionRegistry.acquire 拿到），同会话串行、跨会话并行。
- 内存缓冲与数据库在每轮结束时整份对齐（历史压缩会改写整个列表，增量追加会不一致）。
- 失败时的取舍：已经流给用户看过的内容必须落库（否则刷新后历史与所见不符）；
  完全没产出时才回滚到本轮之前的状态。
"""

from __future__ import annotations

import threading
import time

from app.core.agent import stream_agent
from app.core.context import get_context_window, maybe_auto_compact
from app.core.events import COMPACT, TEXT, TEXT_DELTA, TOOL_CALL, TOOL_RESULT, USAGE
from app.core.prompt import build_system_prompt
from app.db.base import session_scope
from app.db.repositories import messages as messages_repo
from app.db.repositories import sessions as sessions_repo
from app.db.repositories import usages as usages_repo
from app.infra.config import Settings
from app.infra.mcp import MCPManager
from app.services.session_service import SessionRuntime
from app.services.stream_broker import EventStream
from app.tools import execute_tool

DEFAULT_TITLE = "新会话"  # 与 db/models.py 的默认值一致
GENERIC_TITLES = {DEFAULT_TITLE, "新问诊"}  # 尚未被首问改写的占位标题
MAX_TITLE_LEN = 6

BASH_DISABLED_REPLY = (
    "[web 模式已禁用命令执行。若需要执行命令，"
    "请在输入栏打开『允许执行命令』开关后重试。]"
)


def make_tool_executor(allow_bash: bool, mcp: MCPManager, mcp_lock: threading.Lock):
    """工具执行器：MCP 工具串行化调用；run_bash 受「允许执行命令」开关控制。"""

    def executor(name: str, args: dict) -> str:
        parsed = MCPManager.parse_tool_name(name)
        if parsed and mcp.is_connected(parsed[0]):
            with mcp_lock:
                return mcp.call_tool(parsed[0], parsed[1], args)
        if name == "run_bash" and not allow_bash:
            return BASH_DISABLED_REPLY
        return execute_tool(name, args)

    return executor


def load_messages(session_id: str, settings: Settings) -> list[dict]:
    """从库里读回历史，并补上最新的 system prompt。

    system 消息不落库：skill 列表会变，每次加载时重建才能即时生效。
    """
    with session_scope() as db:
        history = messages_repo.list_payloads(db, session_id)
    ctx_window = get_context_window(settings.model)
    return [{"role": "system", "content": build_system_prompt(ctx_window, settings)}] + history


def visible_messages(payloads: list[dict]) -> list[dict]:
    """对外只暴露 user / assistant 的纯文本消息（工具过程不展示）。

    带 tool_calls 的 assistant 消息是「调用工具前的中间发言」，不是最终回答；
    若把它也算作回复，历史里会出现同一轮的两段重复文本。
    """
    out = []
    for m in payloads:
        if m.get("role") not in ("user", "assistant"):
            continue
        if m.get("tool_calls"):
            continue
        content = m.get("content")
        if not content:
            continue
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        out.append({"role": m["role"], "content": str(content)})
    return out


def _title_from_history(payloads: list[dict]) -> str | None:
    """用第一条 user 消息的前 6 个字当标题（不足 6 个字则全取）。"""
    for m in payloads:
        if m.get("role") != "user":
            continue
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        text = str(content).strip().replace("\n", " ")
        return text[:MAX_TITLE_LEN] if text else None
    return None


def _finalize(
    session_id: str,
    payloads: list[dict],
    usage: dict | None,
    latency_ms: int,
) -> None:
    """一个事务里完成：整份替换消息 + 刷新活跃时间 + 首次设标题 + 记用量。"""
    with session_scope() as db:
        messages_repo.replace_all(
            db, session_id, [m for m in payloads if m.get("role") != "system"]
        )
        session = sessions_repo.get(db, session_id)
        if session is None:
            return
        if session.title in GENERIC_TITLES:
            title = _title_from_history(payloads)
            if title:
                session.title = title
        if usage:
            usages_repo.record(
                db,
                session_id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cached_tokens=usage.get("cached_tokens", 0),
                latency_ms=latency_ms,
            )
        # 刷新最近活跃时间（列表排序与内存缓存 TTL 都依赖它）
        sessions_repo.touch(db, session_id)


def run_turn(
    runtime: SessionRuntime,
    *,
    user_message: str,
    allow_bash: bool,
    stream: EventStream,
    settings: Settings,
    tools: list[dict],
    mcp: MCPManager,
    mcp_lock: threading.Lock,
) -> None:
    """在 worker 线程里跑一轮。

    契约：调用方必须已经持有 runtime.lock（Web 走 SessionRegistry.acquire，
    CLI 自己 acquire），本函数负责在结束时释放它。
    """
    started = time.monotonic()
    pre_turn = list(runtime.messages)
    text_buffer = ""
    streamed = False
    usage: dict | None = None

    try:
        if not runtime.messages:
            runtime.messages[:] = load_messages(runtime.session_id, settings)
        pre_turn = list(runtime.messages)
        runtime.messages.append({"role": "user", "content": user_message})

        # 接近上下文窗口上限时自动压缩（保留刚问的这条 user 消息）
        comp = maybe_auto_compact(
            runtime.messages, settings, get_context_window(settings.model)
        )
        if comp.compacted:
            stream.put({
                "type": COMPACT,
                "before_tokens": comp.before_tokens,
                "after_tokens": comp.after_tokens,
                "degraded": comp.degraded,
                "error": comp.error,
            })

        executor = make_tool_executor(allow_bash, mcp, mcp_lock)
        final_text: str | None = None

        for event in stream_agent(
            runtime.messages, tools, tool_executor=executor, settings=settings
        ):
            etype = event.get("type")
            if etype == TEXT_DELTA:
                text_buffer += event.get("content", "")
                streamed = True
            elif etype == TEXT:
                final_text = event.get("content", "")
                streamed = True
            elif etype in (TOOL_CALL, TOOL_RESULT):
                streamed = True
            elif etype == USAGE:
                usage = event
            stream.put(event)

        answer = final_text if final_text is not None else text_buffer
        if answer:
            runtime.messages.append({"role": "assistant", "content": answer})

        _finalize(
            runtime.session_id, runtime.messages, usage,
            int((time.monotonic() - started) * 1000),
        )
    except Exception as e:
        if streamed:
            # 用户已经在流里看到的内容必须留痕，保证历史与所见一致
            if text_buffer and runtime.messages[-1].get("content") != text_buffer:
                runtime.messages.append({"role": "assistant", "content": text_buffer})
            _safe_finalize(runtime.session_id, runtime.messages, usage, started)
        else:
            # 什么也没产出 → 回滚到本轮之前
            runtime.messages[:] = pre_turn
            _safe_finalize(runtime.session_id, runtime.messages, None, started)
        stream.put({"type": "error", "kind": type(e).__name__, "message": str(e)})
    finally:
        stream.close()
        runtime.lock.release()


def _safe_finalize(
    session_id: str, payloads: list[dict], usage: dict | None, started: float
) -> None:
    """收尾落库，失败不影响事件流（错误已经通过 error 事件报给用户）。"""
    try:
        _finalize(session_id, payloads, usage, int((time.monotonic() - started) * 1000))
    except Exception:
        pass


def persist(runtime: SessionRuntime) -> None:
    """把当前缓冲整体落库。

    供入口层直接改写历史的操作使用（如 CLI 的 /compact），
    保证「内存缓冲 == 数据库」这条不变式始终成立。
    """
    _finalize(runtime.session_id, runtime.messages, None, 0)
