"""会话仓储：sessions 表的读写，收敛成领域语义的方法。"""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as OrmSession

from app.db.models import Message, Session, utcnow

DEFAULT_TITLE = "新会话"


def create(db: OrmSession, *, source: str = "web", model: str = "") -> Session:
    """新建会话。"""
    s = Session(source=source, model=model)
    db.add(s)
    db.flush()
    return s


def get(db: OrmSession, session_id: str) -> Session | None:
    return db.get(Session, session_id)


def list_all(db: OrmSession, *, limit: int = 200, source: str | None = None) -> list[Session]:
    """按最近活跃倒序列出会话；source 可选过滤 web / cli。"""
    stmt = select(Session).order_by(Session.updated_at.desc()).limit(limit)
    if source:
        stmt = stmt.where(Session.source == source)
    return list(db.scalars(stmt))


def rename(db: OrmSession, session_id: str, title: str) -> bool:
    s = get(db, session_id)
    if not s:
        return False
    s.title = title[:64]
    db.flush()
    return True


def touch(db: OrmSession, session_id: str) -> None:
    """刷新最近活跃时间（同时供列表排序与内存缓存 TTL 判断）。"""
    s = get(db, session_id)
    if s:
        s.updated_at = utcnow()
        db.flush()


def delete_session(db: OrmSession, session_id: str) -> bool:
    """删除会话（消息随外键级联删除）。"""
    result = db.execute(delete(Session).where(Session.id == session_id))
    return bool(result.rowcount)


def message_count(db: OrmSession, session_id: str) -> int:
    return int(
        db.scalar(select(func.count()).select_from(Message).where(Message.session_id == session_id)) or 0
    )
