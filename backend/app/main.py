"""FastAPI 应用装配：create_app() 工厂 + 生命周期（MCP 起停）。

本模块只负责把各层装配起来：路由来自 api/v1，上下文来自 services，
业务逻辑一律不写在这里。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.infra import health
from app.infra.config import get_settings
from app.services.context import AppContext
from app.tools import TOOLS


@asynccontextmanager
async def lifespan(app: FastAPI):
    ctx: AppContext = app.state.ctx
    health.set_mcp_manager(ctx.mcp)
    try:
        # MCPManager.start_all 内部会自建事件循环，必须放到线程里执行，
        # 否则会与 FastAPI 的主循环冲突。
        mcp_tools = await asyncio.to_thread(ctx.mcp.start_all, ctx.settings)
        ctx.tools = list(TOOLS) + mcp_tools
    except Exception:
        ctx.tools = list(TOOLS)
    yield
    await asyncio.to_thread(ctx.mcp.shutdown_all)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Mini Claude API",
        version="1.0.0",
        description="AI 编码助手后端：ReAct agent + RAG 知识库 + Skills + MCP。",
        lifespan=lifespan,
    )
    app.state.ctx = AppContext(settings=get_settings())
    app.include_router(api_router)
    return app


app = create_app()
