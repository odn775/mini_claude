"""上下文管理：token 估算、LLM 摘要压缩、超阈值自动压缩。

本模块只做纯逻辑与返回结果，不做任何终端输出——展示交给入口层。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.infra.config import Settings, get_settings
from app.infra.llm import chat_completion

# 常见模型的上下文窗口（token）
_CONTEXT_WINDOWS = {
    "qwen3.7-plus": 1_000_000,
    "qwen3.7-max": 1_000_000,
    "qwen3-plus": 131_072,
    "qwen3-max": 131_072,
}

DEFAULT_CONTEXT_WINDOW = 200_000


def get_context_window(model_name: str) -> int:
    """获取模型的最大上下文窗口。"""
    for prefix, size in _CONTEXT_WINDOWS.items():
        if model_name.startswith(prefix):
            return size
    return DEFAULT_CONTEXT_WINDOW


def estimate_tokens(messages: list[dict]) -> tuple[int, dict[str, int]]:
    """粗略估算当前上下文的 token 数（中文 1 token/字，英文 0.25 token/字符）。

    统计每条消息的 content 文本，以及 assistant 消息中 tool_calls 的
    函数名 + arguments JSON（这部分在 content 之外，容易被漏算）。
    """
    total = 0
    counts = {"system": 0, "user": 0, "assistant": 0, "tool": 0}
    for m in messages:
        content = m.get("content") or ""
        if isinstance(content, list):
            texts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    texts.append(c.get("text", ""))
            content = " ".join(texts)
        # assistant 的 tool_calls（函数名 + arguments）不在 content 里，单独计入
        extra = ""
        if m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                extra += fn.get("name", "") + " " + fn.get("arguments", "")
        text = content + " " + extra
        cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other = len(text) - cn
        tokens = int(cn * 1.0 + other * 0.25) + 5  # +5 消息头开销
        total += tokens
        role = m.get("role", "unknown")
        if role in counts:
            counts[role] += tokens
    return total, counts


def compact_messages(
    messages: list[dict],
    settings: Settings | None = None,
    preserve_last_user: bool = False,
) -> tuple[list[dict], str]:
    """用 LLM 压缩对话历史，返回新消息列表 + 摘要文本。

    preserve_last_user=True 时（自动压缩用），把最后一条 user 消息原样保留，
    只压缩 system 与该消息之间的历史——避免把"刚问的当前问题"压成摘要。
    """
    # 分离 system prompt
    system = None
    history = messages
    if messages and messages[0].get("role") == "system":
        system = messages[0]
        history = messages[1:]

    # 可选：保留最后一条 user 消息原文
    last_user = None
    if preserve_last_user and history and history[-1].get("role") == "user":
        last_user = history[-1]
        history = history[:-1]

    if not history:
        # 没有可压缩的历史，原样返回
        out = []
        if system:
            out.append(system)
        if last_user:
            out.append(last_user)
        return out, ""

    # 历史 → 纯文本
    history_lines = []
    for m in history:
        role = m["role"]
        content = m.get("content") or ""

        # 处理 tool_calls（assistant 消息 content 可能为空）
        if not content and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                calls.append(f"-> 调用工具: {fn.get('name', '?')}({fn.get('arguments', '')})")
            content = "\n".join(calls)

        # 处理 content 为 list 的情况
        if isinstance(content, list):
            texts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(part.get("text", ""))
            content = "\n".join(texts)

        if len(content) > 3000:
            content = content[:3000] + "\n...(截断)"
        history_lines.append(f"--- [{role}] ---\n{content}\n")

    prompt = (
        "你是一个对话摘要助手。请将以下 AI 助手与用户的对话历史压缩为一段连贯的摘要。\n\n"
        "要求：\n"
        "1. 保留所有用户需求、问题、已做出的决策和已发现的事实\n"
        "2. 保留关键信息（路径、配置、技术选型、错误信息等）\n"
        "3. 保留工具执行的关键结果\n"
        "4. 摘要应足够详细，让 AI 能根据摘要继续准确回答用户问题\n"
        "5. 用中文输出\n\n"
        "对话历史：\n" + "\n".join(history_lines)
    )

    resp = chat_completion(
        [{"role": "user", "content": prompt}],
        settings or get_settings(),
        max_tokens=2048,
        temperature=0.3,
    )
    summary = resp.choices[0].message.content or ""

    new_messages = []
    if system:
        new_messages.append(system)
    new_messages.append({
        "role": "user",
        "content": f"（对话历史摘要，请基于此继续回答）\n\n{summary}",
    })
    if last_user:
        new_messages.append(last_user)

    return new_messages, summary


@dataclass
class CompactResult:
    """自动压缩的结果，供入口层渲染提示。"""

    compacted: bool = False
    before_tokens: int = 0
    after_tokens: int = 0
    degraded: bool = False   # 摘要失败，退化为丢弃最早历史
    error: str | None = None


def maybe_auto_compact(
    messages: list[dict],
    settings: Settings | None = None,
    ctx_window: int | None = None,
) -> CompactResult:
    """run 之前调用：token 超过 ctx_window 90% 则自动压缩（原地改写 messages）。

    压缩失败时回退到丢弃最早的历史消息（保留 system + 最后一条 user），
    直到估算 token 降到 70% 以下。
    """
    s = settings or get_settings()
    window = ctx_window or get_context_window(s.model)
    threshold = int(window * 0.90)
    tokens, _ = estimate_tokens(messages)
    if tokens <= threshold:
        return CompactResult(compacted=False, before_tokens=tokens, after_tokens=tokens)

    try:
        new_messages, _ = compact_messages(messages, s, preserve_last_user=True)
        messages.clear()
        messages.extend(new_messages)
        new_tokens, _ = estimate_tokens(messages)
        # 摘要后仍超限 → 继续丢最早历史
        drop_threshold = int(window * 0.70)
        while new_tokens > drop_threshold and len(messages) > 2:
            # 丢掉 system 之后最早的一条
            messages.pop(1)
            new_tokens, _ = estimate_tokens(messages)
        return CompactResult(
            compacted=True, before_tokens=tokens, after_tokens=new_tokens,
        )
    except Exception as e:
        # 压缩失败：回退到丢弃最早历史
        drop_threshold = int(window * 0.70)
        while tokens > drop_threshold and len(messages) > 2:
            messages.pop(1)
            tokens, _ = estimate_tokens(messages)
        return CompactResult(
            compacted=True, before_tokens=tokens, after_tokens=tokens,
            degraded=True, error=str(e),
        )
