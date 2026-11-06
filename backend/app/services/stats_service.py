"""导诊统计：按疾病系统/科室聚合分诊结论。

数据来源是 messages 表里 `recommend_department` 工具返回的 JSON（payload.content）。
时间窗按 sessions.updated_at 过滤——messages.created_at 每轮都会被整表重写（历史压缩需要），
不能用来区分轮次时间。
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from app.core import departments, disease_systems
from app.db.base import session_scope
from app.db.models import Message, Session, utcnow


def _parse_triage(content) -> dict | None:
    """把工具结果解析成分诊结论；不是分诊 JSON 的返回 None。"""
    if not isinstance(content, str) or not content.startswith("{"):
        return None
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("primary_department") is None:
        return None
    return data


def triage_stats(days: int = 7) -> dict:
    """统计时间窗内的问诊量、急诊占比与各系统访问数。days=0 表示全部。"""
    cutoff = utcnow() - timedelta(days=days) if days > 0 else None

    stmt = (
        select(Message.role, Message.payload)
        .join(Session, Session.id == Message.session_id)
        .where(Message.role.in_(("user", "tool")))
    )
    if cutoff is not None:
        stmt = stmt.where(Session.updated_at >= cutoff)

    dept_counts: Counter[str] = Counter()
    total_consultations = 0
    emergency_count = 0

    with session_scope() as db:
        for role, payload in db.execute(stmt):
            if role == "user":
                total_consultations += 1
                continue
            data = _parse_triage((payload or {}).get("content"))
            if data is None:
                continue
            dept = departments.resolve(data.get("primary_department"))
            if dept is None:
                continue
            dept_counts[dept] += 1
            if str(data.get("urgency")) == "emergency":
                emergency_count += 1

    # 系统级聚合：每个科室只归一个系统，各类之和等于有结论的总次数
    system_rows = []
    for system in disease_systems.names():
        depts = [
            {"name": dept, "count": dept_counts[dept]}
            for dept in disease_systems.SYSTEMS[system]
            if dept_counts.get(dept)
        ]
        total = sum(d["count"] for d in depts)
        if total:
            system_rows.append({"name": system, "count": total, "departments": depts})
    system_rows.sort(key=lambda x: x["count"], reverse=True)

    return {
        "days": days,
        "total_consultations": total_consultations,
        "triage_count": sum(dept_counts.values()),
        "emergency_count": emergency_count,
        "department_count": len(dept_counts),
        "systems": system_rows,
    }
