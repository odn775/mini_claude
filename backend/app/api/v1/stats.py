"""统计路由：导诊数据的聚合查询。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

from app.schemas.system import TriageStatsOut
from app.services import stats_service

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/triage", response_model=TriageStatsOut)
async def triage_stats(
    days: int = Query(7, ge=0, le=3650, description="时间窗天数，0 表示全部"),
) -> TriageStatsOut:
    data = await asyncio.to_thread(stats_service.triage_stats, days)
    return TriageStatsOut(**data)
