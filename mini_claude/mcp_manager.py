"""
MCP（Model Context Protocol）管理器。

管理 MCP 服务器的生命周期（启动→工具发现→调用→关闭）。
支持 stdio 和 streamable_http 两种 transport，仅处理 text 类型返回值。
"""

import asyncio
import json
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

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

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg  # 完整配置项
        self.server_type = cfg.get("type", "stdio")  # "stdio" 或 "streamable_http"/"sse"
        self.session: ClientSession | None = None
        self.tools = []
        self._read = None
        self._write = None
        self._transport_ctx = None
        self._session_ctx = None

    async def connect(self):
        """建立连接（stdio 或 SSE）→ 握手 → 发现工具列表。"""
        if self.server_type in ("streamable_http", "sse"):
            await self._connect_sse()
        else:
            await self._connect_stdio()

        self._session_ctx = ClientSession(self._read, self._write)
        self.session = await self._session_ctx.__aenter__()

        # 初始化握手
        await self.session.initialize()

        result = await self.session.list_tools()
        self.tools = result.tools

    async def _connect_stdio(self):
        """通过 stdio 连接本地 MCP 服务器。"""
        env = None
        if self.cfg.get("env"):
            env = {**os.environ, **self.cfg["env"]}
        params = StdioServerParameters(
            command=self.cfg["command"],
            args=self.cfg.get("args", []),
            env=env,
        )
        self._transport_ctx = stdio_client(params)
        self._read, self._write = await self._transport_ctx.__aenter__()

    async def _connect_sse(self):
        """通过 Streamable HTTP 连接远程 MCP 服务器。"""
        url = self.cfg["url"]
        self._transport_ctx = streamable_http_client(url)
        streams = await self._transport_ctx.__aenter__()
        self._read, self._write = streams[0], streams[1]

    async def disconnect(self):
        """关闭连接。"""
        if self._session_ctx:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_ctx = None
        if self._transport_ctx:
            try:
                await self._transport_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._transport_ctx = None

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
            conn = _ServerConnection(name, cfg)
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
