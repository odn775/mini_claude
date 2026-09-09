"""全局状态层：进程级会话表、MCP 子进程、知识库状态，以及会话模型 ChatSession。

只持有"状态"，不实现具体接口逻辑；业务功能在 service.py，接口在 routers.py。
"""

import os
import threading
import time
import uuid

from ..config import get_config
from ..main import _build_system_prompt, _get_context_window
from ..mcp_manager import MCPManager
from ..tools import TOOLS


# ── 会话状态 ──

_sessions: dict[str, "ChatSession"] = {}
_sessions_lock = threading.Lock()

# ── MCP 子进程（全局共享，call 用 _mcp_lock 串行化） ──

_mcp = MCPManager()
_mcp_lock = threading.Lock()
_tools: list[dict] = list(TOOLS)  # 启动后由 lifespan 追加 MCP 工具

# ── 知识库状态（与 main.py 保持一致的数据目录） ──

_KB_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "knowledge")
_KB_INDEX_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "faiss_index")
_kb_building = False
_kb_last_error: str | None = None
_kb_state_lock = threading.Lock()


class ChatSession:
    """一次浏览器会话的对话状态。"""

    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.title = "新会话"
        self.created_at = time.time()
        self.lock = threading.Lock()
        config = get_config()
        ctx_window = _get_context_window(config["model"])
        self.messages: list[dict] = [
            {"role": "system", "content": _build_system_prompt(ctx_window)},
        ]