"""消息仓储：messages 表的追加、回放与回滚。

payload 存原始 OpenAI 格式消息，list_payloads 读回来可直接喂给模型。
"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as OrmSession

from app.db.models import Message


def next_seq(db: OrmSession, session_id: str) -> int:
    """会话内下一个可用序号（从 0 开始）。"""
    current = db.scalar(
        select(func.max(Message.seq)).where(Message.session_id == session_id)
    )
    return 0 if current is None else int(current) + 1


def append(db: OrmSession, session_id: str, payload: dict, *, seq: int | None = None) -> Message:
    """追加一条消息；不传 seq 则自动接在末尾。"""
    if seq is None:
        seq = next_seq(db, session_id)
    m = Message(session_id=session_id, seq=seq, role=payload.get("role", ""), payload=payload)
    db.add(m)
    db.flush()
    return m


def append_many(db: OrmSession, session_id: str, payloads: list[dict]) -> list[Message]:
    """批量追加（保持传入顺序）。"""
    seq = next_seq(db, session_id)
    out = []
    for i, payload in enumerate(payloads):
        out.append(append(db, session_id, payload, seq=seq + i))
    return out


def list_payloads(db: OrmSession, session_id: str) -> list[dict]:
    """按序号回放整个会话的消息。"""
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.seq)
    )
    return [m.payload for m in db.scalars(stmt)]


def delete_from_seq(db: OrmSession, session_id: str, seq: int) -> int:
    """删除 seq 及之后的消息（一轮对话失败时回滚用）。"""
    result = db.execute(
        delete(Message).where(Message.session_id == session_id, Message.seq >= seq)
    )
    return int(result.rowcount or 0)


def replace_all(db: OrmSession, session_id: str, payloads: list[dict]) -> None:
    """整份替换会话消息，让库与内存缓冲始终一致。

    一轮对话结束时调用（历史压缩会改写整个列表，只做增量追加会不一致）。
    delete 走 Core 语句立即执行，先删后插，不会踩 (session_id, seq) 唯一约束。
    """
    db.execute(delete(Message).where(Message.session_id == session_id))
    for i, payload in enumerate(payloads):
        db.add(
            Message(
                session_id=session_id,
                seq=i,
                role=payload.get("role", ""),
                payload=payload,
            )
        )
    db.flush()
