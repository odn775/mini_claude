"""Mini Claude Web —— FastAPI 入口（薄壳）。

与 CLI（main.py）共存：CLI 走终端交互，这里提供 HTTP + SSE 的浏览器聊天界面。
共享 agent / tools / knowledge / skills / health / mcp_manager 等纯逻辑模块。

分层结构（拆分在 web_app 子包）：
- web_app/state.py    全局状态 + ChatSession 模型
- web_app/service.py  业务功能（会话辅助、工具执行、知识库）
- web_app/routers.py  APIRouter 接口层
本文件只负责：创建 app、挂载 routers、静态页面与生命周期（MCP 起停）。
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import health
from .tools import TOOLS
from .web_app import routers
from .web_app import state as web_state

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# ── 生命周期 ──


@asynccontextmanager
async def lifespan(_app: FastAPI):
    health.set_mcp_manager(web_state._mcp)
    # MCPManager.start_all 内部会自建事件循环（asyncio.set_event_loop），
    # 只能在独立线程里调，否则会与 FastAPI 主循环冲突。
    try:
        mcp_tools = await asyncio.to_thread(web_state._mcp.start_all)
        web_state._tools = TOOLS + mcp_tools
    except Exception:
        web_state._tools = list(TOOLS)
    yield
    await asyncio.to_thread(web_state._mcp.shutdown_all)


app = FastAPI(title="Mini Claude Web", lifespan=lifespan)

# 挂载各 APIRouter
app.include_router(routers.config_health_router)
app.include_router(routers.kb_router)
app.include_router(routers.sessions_router)
app.include_router(routers.usage_router)
app.include_router(routers.chat_router)

# 静态资源（Vue 前端的 css / js）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── 页面 ──


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn

    print("Mini Claude Web: http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")