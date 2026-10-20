"""用量仓储：每轮回答的 token 用量与耗时，供论文实验统计与后续可视化。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app.db.models import Usage


def record(
    db: OrmSession,
    session_id: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_tokens: int = 0,
    latency_ms: int = 0,
) -> Usage:
    """记录一轮回答的累计用量。"""
    u = Usage(
        session_id=session_id,
        input_tokens=input_tokens or 0,
        output_tokens=output_tokens or 0,
        cached_tokens=cached_tokens or 0,
        latency_ms=latency_ms or 0,
    )
    db.add(u)
    db.flush()
    return u


def aggregate_by_session(db: OrmSession, session_ids: list[str]) -> dict[str, dict]:
    """按会话聚合用量，返回 {session_id: {input_tokens, output_tokens, cached_tokens}}。"""
    if not session_ids:
        return {}
    rows = db.execute(
        select(
            Usage.session_id,
            func.sum(Usage.input_tokens),
            func.sum(Usage.output_tokens),
            func.sum(Usage.cached_tokens),
        )
        .where(Usage.session_id.in_(session_ids))
        .group_by(Usage.session_id)
    ).all()
    return {
        sid: {
            "input_tokens": int(i or 0),
            "output_tokens": int(o or 0),
            "cached_tokens": int(c or 0),
        }
        for sid, i, o, c in rows
    }


def totals(db: OrmSession) -> dict:
    """全局累计用量（论文实验汇总用）。"""
    row = db.execute(
        select(
            func.count(Usage.id),
            func.sum(Usage.input_tokens),
            func.sum(Usage.output_tokens),
            func.sum(Usage.cached_tokens),
            func.avg(Usage.latency_ms),
        )
    ).one()
    count, i, o, c, latency = row
    return {
        "turns": int(count or 0),
        "input_tokens": int(i or 0),
        "output_tokens": int(o or 0),
        "cached_tokens": int(c or 0),
        "avg_latency_ms": int(latency or 0),
    }
