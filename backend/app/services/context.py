"""应用上下文：进程内共享的配置、MCP 连接、工具表与会话运行时表。

由 create_app() 在启动时构造，挂在 app.state 上，路由通过依赖注入取用。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from app.infra.config import Settings
from app.infra.mcp import MCPManager
from app.services.session_service import SessionRegistry
from app.tools import TOOLS


@dataclass
class AppContext:
    settings: Settings
    mcp: MCPManager = field(default_factory=MCPManager)
    # MCP 子进程跨线程调用需要串行化
    mcp_lock: threading.Lock = field(default_factory=threading.Lock)
    tools: list[dict] = field(default_factory=lambda: list(TOOLS))
    registry: SessionRegistry = field(default_factory=SessionRegistry)
