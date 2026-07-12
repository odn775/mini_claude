import json
from openai import OpenAI
from .config import get_config
from .tools import execute_tool

MAX_TOOL_ITERATIONS = 20


def run_agent(
    messages: list[dict],
    tools: list[dict],
    tool_executor=None,
) -> str:
    """
    Agent 循环。

    1. 将 messages + tools 发给模型
    2. 如果模型返回 tool_calls → 执行工具 → 追加结果 → 回到步骤 1
    3. 如果模型返回纯文本 → 返回文本

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

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = client.chat.completions.create(
            model=config["model"],
            messages=messages,
            tools=tools,
            max_tokens=config["max_tokens"],
        )

        if not response.choices:
            return "[错误] 模型未返回任何结果"

        choice = response.choices[0]
        message = choice.message

        # 模型要调用工具
        if message.tool_calls:
            # 将 assistant 消息（含 tool_calls）追加到历史
            messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in message.tool_calls
                ],
            })

            # 执行每个工具调用并追加结果
            for tc in message.tool_calls:
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}
                result = tool_executor(tc.function.name, tool_args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            # 继续循环，让模型看到工具结果
            continue

        # 模型返回最终文本
        return message.content or ""

    return f"[警告] 达到最大工具调用次数 ({MAX_TOOL_ITERATIONS})，已停止"
