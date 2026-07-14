"""
一个简单的 MCP 服务器示例 —— 提供文件搜索和文本处理工具。
"""

import asyncio
import json
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult


# ── 创建 MCP 服务器实例 ──────────────────────────────────
app = Server("my-tools")


# ── 声明工具（做了什么、参数是什么） ──────────────────────
@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="search_files",
            description="在指定目录下搜索文件名包含关键字的文件",
            inputSchema={
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "要搜索的目录路径",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "文件名关键字",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回结果数",
                        "default": 10,
                    },
                },
                "required": ["directory", "keyword"],
            },
        ),
        Tool(
            name="count_words",
            description="统计一段文本的字数和行数",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要统计的文本"},
                },
                "required": ["text"],
            },
        ),
    ]


# ── 实现工具逻辑 ──────────────────────────────────────────
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "search_files":
        return [TextContent(type="text", text=await search_files(**arguments))]
    elif name == "count_words":
        return [TextContent(type="text", text=count_words(**arguments))]
    else:
        raise ValueError(f"未知工具: {name}")


async def search_files(directory: str, keyword: str, max_results: int = 10) -> str:
    """搜索文件的具体实现。"""
    if not os.path.isdir(directory):
        return f"错误：目录 '{directory}' 不存在"

    results = []
    try:
        for root, dirs, files in os.walk(directory):
            for f in files:
                if keyword.lower() in f.lower():
                    results.append(os.path.join(root, f))
                    if len(results) >= max_results:
                        break
            if len(results) >= max_results:
                break
    except Exception as e:
        return f"搜索出错: {e}"

    if not results:
        return f"在 '{directory}' 中未找到包含 '{keyword}' 的文件"
    return f"找到 {len(results)} 个文件:\n" + "\n".join(results)


def count_words(text: str) -> str:
    """统计字数的具体实现。"""
    lines = text.split("\n")
    chars = len(text)
    words = len(text.split())
    return f"行数: {len(lines)}\n词数: {words}\n字符数: {chars}"


# ── 入口（通过 stdio 与客户端通信） ──────────────────────
async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
