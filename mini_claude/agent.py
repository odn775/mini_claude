import json
from typing import Iterator
from openai import OpenAI
from .config import get_config
from .tools import execute_tool
from .retry import with_retry

MAX_TOOL_ITERATIONS = 20


def stream_agent(
    messages: list[dict],
    tools: list[dict],
    tool_executor=None,
) -> Iterator[dict]:
    """
    Agent 循环（事件流版本）。

    与旧的 run_agent 行为等价，但把过程产出为事件 dict 序列，供 web SSE 消费：
      {"type": "text_delta", "content"}                       最终回答的增量文本（token 流）
      {"type": "tool_call",  "name", "arguments", "call_id"}  模型要调用工具
      {"type": "tool_result", "name", "call_id", "content"}   工具执行结果
      {"type": "text", "content"}                             最终回答全文 / 终止说明
      {"type": "usage", "input_tokens", "output_tokens", "cached_tokens"}  本次 run 累计用量（最后产出，含缓存命中）

    用 stream=True 逐 token 流式请求，text_delta 实时产出；工具调用分片累加。

    副作用与旧版一致：assistant 的 tool_calls 和 tool 结果都会追加进 messages。
    最终 assistant 文本不在这里追加（CLI 与 web 各自负责）。

    参数：
        tool_executor：可选的工具执行函数，签名 ``(name, args) -> str``。
                       不传则使用内置的 execute_tool。
    """
    config = get_config()
    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )

    if tool_executor is None:
        tool_executor = execute_tool

    # 累计本次 run 的 token 用量：工具循环里每次 LLM 调用各有一份 usage，逐轮相加
    usage_total = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = with_retry(lambda: client.chat.completions.create(
            model=config["model"],
            messages=messages,
            tools=tools,
            max_tokens=config["max_tokens"],
            stream=True,
            # usage（含缓存命中）只在最后一个 chunk 给出，且该 chunk 的 choices 为空
            stream_options={"include_usage": True},
        ))

        # 流式累加：content 片段 + 各工具调用的分片（id/name 首包给全，arguments 分段拼）
        content_parts: list[str] = []
        tool_acc: dict[int, dict] = {}
        seen_choices = False
        usage_last = None  # 本轮流式响应的 usage（覆盖式保存，兼容逐 chunk 都带 usage 的非标准实现）

        for chunk in response:
            if chunk.usage is not None:
                usage_last = chunk.usage
            if not chunk.choices:
                continue
            seen_choices = True
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield {"type": "text_delta", "content": delta.content}
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    acc = tool_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            acc["name"] += tc.function.name
                        if tc.function.arguments:
                            acc["arguments"] += tc.function.arguments

        # 累加本轮 usage（prompt_tokens_details.cached_tokens 为缓存命中 token，可能缺失）
        if usage_last is not None:
            usage_total["input_tokens"] += usage_last.prompt_tokens or 0
            usage_total["output_tokens"] += usage_last.completion_tokens or 0
            ptd = usage_last.prompt_tokens_details
            if ptd and ptd.cached_tokens:
                usage_total["cached_tokens"] += ptd.cached_tokens

        # 模型要调用工具
        if tool_acc:
            calls = [tool_acc[i] for i in sorted(tool_acc)]
            # 将 assistant 消息（含 tool_calls）追加到历史
            messages.append({
                "role": "assistant",
                "content": "".join(content_parts) or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in calls
                ],
            })

            # 执行每个工具调用并追加结果
            for c in calls:
                name = c["name"]
                try:
                    tool_args = json.loads(c["arguments"]) if c["arguments"] else {}
                except json.JSONDecodeError:
                    tool_args = {}
                yield {"type": "tool_call", "name": name, "arguments": tool_args, "call_id": c["id"]}
                result = tool_executor(name, tool_args)
                # 截断过长的工具返回，防止撑爆上下文
                if len(result) > 15000:
                    result = result[:15000] + "\n...(工具返回过长，已截断至 15000 字符)"
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                yield {"type": "tool_result", "name": name, "call_id": c["id"], "content": result}

            # 继续循环，让模型看到工具结果
            continue

        # 模型返回最终文本（完全没有返回时给提示，行为与旧版一致）
        if not seen_choices:
            final_text = "[错误] 模型未返回任何结果"
        else:
            final_text = "".join(content_parts)
        break
    else:
        final_text = f"[警告] 达到最大工具调用次数 ({MAX_TOOL_ITERATIONS})，已停止"

    yield {"type": "text", "content": final_text}
    # usage 事件最后产出：本次 run 的累计 token 用量与缓存命中
    yield {"type": "usage", **usage_total}


def run_agent(
    messages: list[dict],
    tools: list[dict],
    tool_executor=None,
) -> str:
    """
    Agent 循环（兼容旧接口，返回最终文本字符串）。

    消费 stream_agent 的全部事件，返回最后一条 text 事件的内容。
    CLI / 其他同步调用方继续用这个函数，行为与旧版一致。
    """
    last = ""
    for event in stream_agent(messages, tools, tool_executor=tool_executor):
        if event["type"] == "text":
            last = event["content"]
    return last
