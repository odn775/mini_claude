"""会话路由：列表 / 新建 / 详情 / 改名 / 删除。

会话与消息都存在数据库里，CLI 与 Web 共用同一份数据（source 区分来源）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_ctx
from app.db.base import session_scope
from app.db.repositories import messages as messages_repo
from app.db.repositories import sessions as sessions_repo
from app.schemas.sessions import (
    SessionCreated,
    SessionDetail,
    SessionRename,
    SessionSummary,
)
from app.services import chat_service
from app.services.context import AppContext

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionSummary])
def list_sessions(
    limit: int = Query(100, ge=1, le=500),
    source: str | None = Query(None, pattern="^(web|cli)$"),
) -> list[SessionSummary]:
    with session_scope() as db:
        rows = sessions_repo.list_all(db, limit=limit, source=source)
        return [
            SessionSummary(
                id=s.id, title=s.title, source=s.source, model=s.model,
                created_at=s.created_at, updated_at=s.updated_at,
            )
            for s in rows
        ]


@router.post("", response_model=SessionCreated, status_code=201)
def create_session(ctx: AppContext = Depends(get_ctx)) -> SessionCreated:
    with session_scope() as db:
        s = sessions_repo.create(db, source="web", model=ctx.settings.model)
        return SessionCreated(id=s.id, title=s.title, source=s.source, model=s.model)


@router.get("/{sid}", response_model=SessionDetail)
def get_session(sid: str) -> SessionDetail:
    with session_scope() as db:
        s = sessions_repo.get(db, sid)
        if s is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        messages = chat_service.visible_messages(messages_repo.list_payloads(db, sid))
    return SessionDetail(
        id=s.id, title=s.title, source=s.source, model=s.model,
        created_at=s.created_at, updated_at=s.updated_at, messages=messages,
    )


@router.patch("/{sid}", response_model=SessionSummary)
def rename_session(sid: str, body: SessionRename) -> SessionSummary:
    with session_scope() as db:
        if not sessions_repo.rename(db, sid, body.title):
            raise HTTPException(status_code=404, detail="会话不存在")
        s = sessions_repo.get(db, sid)
    return SessionSummary(
        id=s.id, title=s.title, source=s.source, model=s.model,
        created_at=s.created_at, updated_at=s.updated_at,
    )


@router.delete("/{sid}")
def delete_session(sid: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    with session_scope() as db:
        deleted = sessions_repo.delete_session(db, sid)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 同时丢掉内存里的运行时，避免被删会话的缓冲继续占用
    ctx.registry.invalidate(sid)
    return {"ok": True}
