"""聊天相关出入参。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """发一条消息：round 内的 SSE 事件流见 core/events.py。"""

    message: str = Field(min_length=1)
    # 维持既有行为：默认允许执行命令，前端可用开关临时关闭
    allow_bash: bool = True
