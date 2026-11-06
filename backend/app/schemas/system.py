"""系统级出入参：配置、健康检查、工具清单、知识库状态。"""

from __future__ import annotations

from pydantic import BaseModel


class ConfigOut(BaseModel):
    model: str
    base_url: str
    default_allow_bash: bool


class HealthItem(BaseModel):
    name: str
    status: str
    detail: str


class HealthOut(BaseModel):
    items: list[HealthItem]


class PingItem(BaseModel):
    name: str
    ok: bool
    detail: str
    ms: int


class PingOut(BaseModel):
    items: list[PingItem]


class ToolInfo(BaseModel):
    name: str
    description: str
    source: str   # builtin / mcp


class KbStatus(BaseModel):
    building: bool
    last_error: str | None
    exists: bool
    total_chunks: int
    files: list[str]


class DeptStat(BaseModel):
    name: str
    count: int


class SystemStat(BaseModel):
    name: str
    count: int
    departments: list[DeptStat]


class TriageStatsOut(BaseModel):
    """导诊统计：时间窗内的问诊量 + 按疾病系统的访问分布。"""

    days: int
    total_consultations: int
    triage_count: int
    emergency_count: int
    department_count: int
    systems: list[SystemStat]
