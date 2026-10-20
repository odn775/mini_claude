"""系统级路由：配置、健康检查、工具清单。"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from app.api.deps import get_ctx
from app.infra import health
from app.infra.mcp import MCPManager
from app.schemas.system import ConfigOut, HealthOut, PingOut, ToolInfo
from app.services.context import AppContext

router = APIRouter(tags=["system"])


@router.get("/config", response_model=ConfigOut)
def get_config(ctx: AppContext = Depends(get_ctx)) -> ConfigOut:
    """当前模型配置（不回传 api_key）。"""
    return ConfigOut(
        model=ctx.settings.model,
        base_url=ctx.settings.base_url,
        default_allow_bash=True,
    )


@router.get("/health", response_model=HealthOut)
async def health_check() -> HealthOut:
    """静态体检：零成本，只读本地文件与配置。"""
    items = await asyncio.to_thread(health.check_all)
    return HealthOut(items=items)


@router.get("/health/ping", response_model=PingOut)
async def health_ping(ctx: AppContext = Depends(get_ctx)) -> PingOut:
    """活体探测（net / llm / embedding），会消耗少量 token。"""
    items = await asyncio.to_thread(health.ping, ctx.settings)
    return PingOut(items=items)


@router.get("/tools", response_model=list[ToolInfo])
def list_tools(ctx: AppContext = Depends(get_ctx)) -> list[ToolInfo]:
    """当前可用工具（内置 + 已连接的 MCP）。"""
    out = []
    for tool in ctx.tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        out.append(
            ToolInfo(
                name=name,
                description=fn.get("description", ""),
                source="mcp" if MCPManager.parse_tool_name(name) else "builtin",
            )
        )
    return out
