"""ORM 模型：会话、消息、用量。

约定：
- 时间统一存「UTC 的朴素 datetime」（MySQL DATETIME 不带时区），展示层负责转本地时区。
- messages.payload 存原始 OpenAI 格式消息（含 tool_calls / tool_call_id），
  保证从库里读回来能原样喂给模型，不会因结构化拆列而丢信息。
- messages.seq 是会话内单调递增序号，负责保证回放顺序。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    """当前 UTC 时间（朴素 datetime，避免时区混用）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_session_id() -> str:
    """会话 ID：12 位十六进制，与旧版格式保持一致。"""
    return uuid.uuid4().hex[:12]


class Session(Base):
    """一次对话（CLI 与 Web 共用同一张表，用 source 区分来源）。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_session_id)
    title: Mapped[str] = mapped_column(String(64), default="新会话")
    source: Mapped[str] = mapped_column(String(8), default="web")   # web / cli
    model: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.seq",
    )


class Message(Base):
    """一条对话消息（user / assistant / tool 都是消息）。"""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_messages_session_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    session: Mapped[Session] = relationship(back_populates="messages")


class Usage(Base):
    """一轮回答的累计 token 用量，论文实验数据来源。"""

    __tablename__ = "usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
