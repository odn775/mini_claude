"""工具注册与分发：把工具名映射到实现，并统一处理未知工具与参数错误。"""

from app.tools import builtin
from app.tools.specs import TOOLS

clear_read_cache = builtin.clear_read_cache

TOOL_EXECUTORS = {
    "read_file": builtin.read_file,
    "write_file": builtin.write_file,
    "run_bash": builtin.run_bash,
    "glob_search": builtin.glob_search,
    "grep_search": builtin.grep_search,
    "search_knowledge": builtin.search_knowledge,
    "run_skill": builtin.run_skill_tool,
    "check_health": builtin.check_health,
}


def execute_tool(name: str, args: dict) -> str:
    """根据工具名和参数执行对应的工具函数。"""
    executor = TOOL_EXECUTORS.get(name)
    if not executor:
        return f"未知工具: {name}"
    try:
        return executor(**args)
    except TypeError as e:
        return f"工具参数错误: {e}"
