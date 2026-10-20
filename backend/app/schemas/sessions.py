"""会话相关出入参。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MessageOut(BaseModel):
    """对外暴露的对话消息（只含 user/assistant 的纯文本，不含工具过程）。"""

    role: str
    content: str


class SessionSummary(BaseModel):
    id: str
    title: str
    source: str
    model: str
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionSummary):
    messages: list[MessageOut]


class SessionCreated(BaseModel):
    id: str
    title: str
    source: str
    model: str


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=64)
