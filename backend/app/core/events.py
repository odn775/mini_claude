"""Agent 事件流契约：`stream_agent` 产出的所有事件类型在此集中定义。

CLI 与 Web 两个入口都只消费这里定义的事件，不各自解析 agent 内部细节：

    text_delta  最终回答的增量文本（token 流）
    tool_call   模型要调用工具（name / arguments / call_id）
    tool_result 工具执行结果（name / call_id / content）
    text        最终回答全文或终止说明
    usage       本次 run 累计用量（input_tokens / output_tokens / cached_tokens）
    compact     本轮触发了历史自动压缩（before_tokens / after_tokens / degraded）
"""

from typing import Any, Literal

TEXT_DELTA: str = "text_delta"
TOOL_CALL: str = "tool_call"
TOOL_RESULT: str = "tool_result"
TEXT: str = "text"
USAGE: str = "usage"
COMPACT: str = "compact"

EventType = Literal[
    "text_delta", "tool_call", "tool_result", "text", "usage", "compact"
]

# 事件是普通 dict（便于直接 JSON 序列化进 SSE），此处仅作类型标注
AgentEvent = dict[str, Any]
