"""
MCP（Model Context Protocol）管理器。

管理 MCP 服务器的生命周期（启动→工具发现→调用→关闭）。
仅支持 stdio transport，仅处理 text 类型返回值。
"""

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".mini_claude", "mcp.json")


def _load_mcp_config() -> dict:
    """读取 ~/.mini_claude/mcp.json 中的 mcpServers 配置。"""
    if not os.path.isfile(MCP_CONFIG_FILE):
        return {}
    try:
        with open(MCP_CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("mcpServers", {})
    except (json.JSONDecodeError, IOError):
        return {}


class _ServerConnection:
    """单个 MCP 服务器的连接状态。"""

    def __init__(self, name: str, params: StdioServerParameters):
        self.name = name
        self.params = params
        self.session: ClientSession | None = None
        self._read = None
        self._write = None
        self._stdio_ctx = None
        self._session_ctx = None

    async def connect(self):
        """建立 stdio 连接 → 握手 → 发现工具列表。"""
        # 手动管理 async 上下文管理器，保持连接长期存活
        self._stdio_ctx = stdio_client(self.params)
        self._read, self._write = await self._stdio_ctx.__aenter__()

        self._session_ctx = ClientSession(self._read, self._write)
        self.session = await self._session_ctx.__aenter__()

        # 初始化握手
        await self.session.initialize()

        result = await self.session.list_tools()
        self.tools = result.tools

    async def disconnect(self):
        """关闭连接。"""
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_ctx = None
        if self._stdio_ctx:
            try:
                await self._stdio_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._stdio_ctx = None

    async def call(self, tool_name: str, arguments: dict) -> str:
        """调用一个工具，返回纯文本结果。"""
        result = await self.session.call_tool(tool_name, arguments)
        parts = []
        for item in result.content:
            if item.type == "text":
                parts.append(item.text)
            elif item.type == "image":
                parts.append(f"[MCP 返回了图片类型，无法处理: {item.mimeType}]")
            else:
                parts.append(f"[MCP 返回了不支持的类型: {item.type}]")
        return "\n".join(parts) if parts else f"[工具 {tool_name} 执行完成，无文本输出]"


class MCPManager:
    """管理所有 MCP 服务器的生命周期。"""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connections: dict[str, _ServerConnection] = {}

    # ── 生命周期 ──

    def start_all(self) -> list[dict]:
        """
        读取 mcp.json，启动所有服务器，返回工具定义列表。

        返回的每个工具定义是 OpenAI function-calling 格式，工具名已加
        ``{server_name}__{tool_name}`` 前缀。
        """
        config = _load_mcp_config()
        if not config:
            return []

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        all_tool_defs: list[dict] = []
        for name, cfg in config.items():
            env = None
            if cfg.get("env"):
                env = {**os.environ, **cfg["env"]}
            params = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
                env=env,
            )

            conn = _ServerConnection(name, params)
            try:
                self._loop.run_until_complete(conn.connect())
                self._connections[name] = conn
                for tool in conn.tools:
                    all_tool_defs.append(self._to_function_calling(name, tool))
                print(f"  [OK] MCP [{name}] — {len(conn.tools)} 个工具")
            except Exception as e:
                print(f"  [ERR] MCP [{name}] 连接失败: {e}")

        return all_tool_defs

    def shutdown_all(self):
        """关闭所有 MCP 服务器连接。"""
        if not self._loop:
            return
        for name, conn in self._connections.items():
            try:
                self._loop.run_until_complete(conn.disconnect())
            except Exception:
                pass
        self._connections.clear()
        try:
            self._loop.close()
        except Exception:
            pass

    # ── 工具调用 ──

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> str:
        """在指定服务器上调用工具，返回纯文本结果。"""
        conn = self._connections.get(server_name)
        if not conn:
            return f"[MCP 错误] 服务器 '{server_name}' 不可用（未连接或已断开）"
        try:
            return self._loop.run_until_complete(conn.call(tool_name, arguments))
        except Exception as e:
            return f"[MCP 错误] 调用 {server_name}.{tool_name} 失败: {e}"

    # ── 状态查询 ──

    def is_connected(self, server_name: str) -> bool:
        return server_name in self._connections

    def connected_servers(self) -> list[str]:
        return list(self._connections.keys())

    # ── 辅助 ──

    @staticmethod
    def _to_function_calling(server_name: str, tool) -> dict:
        """将 MCP Tool 对象转换为 OpenAI function-calling 格式。"""
        return {
            "type": "function",
            "function": {
                "name": f"{server_name}__{tool.name}",
                "description": tool.description or f"调用 {server_name} 的 {tool.name}",
                "parameters": tool.inputSchema,
            },
        }

    @staticmethod
    def parse_tool_name(prefixed_name: str) -> tuple[str, str] | None:
        """从带前缀的工具名解析 ``(server_name, tool_name)``。"""
        if "__" not in prefixed_name:
            return None
        server, tool = prefixed_name.split("__", 1)
        return server, tool
