import subprocess
import glob as glob_mod
import re
import os
from .config import get_config
from .knowledge import search as kb_search

# ── 知识库路径 ──
_KB_INDEX_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "faiss_index")


# ── 文件读缓存（避免重复读浪费 token） ──
_read_cache: set[str] = set()


def clear_read_cache():
    """清空读缓存，给 /clear 调用。"""
    _read_cache.clear()


# ── 工具定义 (OpenAI function-calling 格式) ──

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径的文件内容并返回",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件绝对路径",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入指定路径的文件，如果文件已存在则覆盖",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件绝对路径",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文本内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "执行一条 bash 命令并返回标准输出和标准错误",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob_search",
            "description": "使用 glob 模式搜索匹配的文件路径",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "glob 模式，如 **/*.py、src/**/*.ts",
                    }
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "在文件中搜索匹配正则表达式的内容行",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "正则表达式模式",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索的起始目录，默认为当前工作目录",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "在本地知识库中搜索相关文档内容。"
                "当用户的问题需要查阅外部文档、笔记、参考资料、项目规范或任何可能保存在知识库中的信息时使用此工具。"
                "query 会经过语义匹配，不需要精确关键词。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询，描述你需要查找的内容",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_skill",
            "description": "读取完整 skill 指令。当用户的问题需要调用某个 skill 时，用此工具读取完整内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {
                        "type": "string",
                        "description": "skill 名称，如 translate、summarize、code-review",
                    }
                },
                "required": ["skill_name"],
            },
        },
    },
]


# ── 工具实现 ──

def read_file(path: str) -> str:
    """读取文件内容（同一文件只读一次，重复引用上下文已有内容）。"""
    if path in _read_cache:
        return f"[已读过] 文件 {path} 的内容已在对话上下文中，直接引用之前的读取结果即可。"
    _read_cache.add(path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if len(content) > 50000:
            content = content[:50000] + "\n... (内容过长，已截断至前 50000 字符)"
        return content
    except FileNotFoundError:
        return f"文件不存在: {path}"
    except PermissionError:
        return f"权限不足，无法读取: {path}"
    except Exception as e:
        return f"读取文件失败: {e}"


def write_file(path: str, content: str) -> str:
    """写入文件。"""
    try:
        dirname = os.path.dirname(path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"文件已写入: {path}"
    except Exception as e:
        return f"写入文件失败: {e}"


def run_bash(command: str) -> str:
    """执行 shell 命令。"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.getcwd(),
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout.rstrip())
        if result.stderr:
            parts.append("[stderr]\n" + result.stderr.rstrip())
        if not parts:
            parts.append(f"命令执行完成 (退出码: {result.returncode})")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return "命令执行超时 (60s)"
    except Exception as e:
        return f"执行命令失败: {e}"


def glob_search(pattern: str) -> str:
    """Glob 文件搜索。"""
    try:
        matches = glob_mod.glob(pattern, recursive=True)
        if not matches:
            return "未找到匹配的文件"
        if len(matches) > 200:
            return "\n".join(matches[:200]) + f"\n... (共 {len(matches)} 个结果，仅显示前 200 个)"
        return "\n".join(matches)
    except Exception as e:
        return f"文件搜索失败: {e}"


def grep_search(pattern: str, path: str = ".") -> str:
    """在文件中搜索匹配正则的行。"""
    SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".claude"}
    MAX_RESULTS = 100

    results = []
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in files:
                filepath = os.path.join(root, fname)
                try:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            if re.search(pattern, line):
                                results.append(f"{filepath}:{lineno}: {line.rstrip()}")
                                if len(results) >= MAX_RESULTS:
                                    break
                except Exception:
                    continue
                if len(results) >= MAX_RESULTS:
                    break
            if len(results) >= MAX_RESULTS:
                break

        if not results:
            return "未找到匹配的内容"
        return "\n".join(results)
    except Exception as e:
        return f"内容搜索失败: {e}"


def search_knowledge(query: str) -> str:
    """在本地知识库中检索相关内容。"""
    try:
        config = get_config()
        results = kb_search(query, config, _KB_INDEX_DIR)
        if not results:
            return "知识库尚未构建索引或未找到相关内容。请使用 /kb rebuild 先构建索引。"
        lines = [f"[{r['source']}] {r['content']}" for r in results]
        return "\n\n---\n\n".join(lines)
    except Exception as e:
        return f"知识库搜索失败: {e}"


def run_skill_tool(skill_name: str) -> str:
    """读取完整 skill 指令，并列出该 skill 下的所有参考文件。"""
    from .skills import load_skill, list_skills

    meta, body = load_skill(skill_name)
    if meta is None:
        all_skills = list_skills()
        names = ", ".join(s["name"] for s in all_skills) if all_skills else "无"
        return f"skill 不存在: {skill_name}。可用: {names}"

    # 构建返回内容
    result = f"=== {skill_name} skill 指令 ===\n\n{body}" if body else ""

    # 列出 skill 目录下附属文件
    skill_dir = os.path.join(os.path.expanduser("~"), ".mini_claude", "skills", skill_name)
    extras = _list_skill_extra_files(skill_dir)

    if extras:
        result += (
            f"\n\n--- 参考文件 (可调 read_file 读取) ---\n"
            + "\n".join(f"  {f}" for f in extras)
        )

    return result if result else f"skill {skill_name} 无内容"


def _list_skill_extra_files(skill_dir: str) -> list[str]:
    """列出 skill 目录下除 SKILL.md 外的所有文件（相对路径）。"""
    extras = []
    if not os.path.isdir(skill_dir):
        return extras

    for root, dirs, files in os.walk(skill_dir):
        dirs.sort()
        files.sort()
        for fname in files:
            if fname == "SKILL.md":
                continue
            rel_path = os.path.relpath(os.path.join(root, fname), skill_dir)
            extras.append(rel_path)

    return extras


# ── 工具分发 ──

TOOL_EXECUTORS = {
    "read_file": read_file,
    "write_file": write_file,
    "run_bash": run_bash,
    "glob_search": glob_search,
    "grep_search": grep_search,
    "search_knowledge": search_knowledge,
    "run_skill": run_skill_tool,
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
