"""内置工具实现：8 个工具的具体逻辑。

约定：任何异常都不向外抛，统一返回给模型看的字符串，让模型自行决定下一步。
"""

import glob as glob_mod
import os
import re
import subprocess

from app.infra import health
from app.infra import knowledge as kb
from app.infra.skills import list_skills, load_skill, skill_dir

# ── 文件读缓存（避免重复读浪费 token） ──
_read_cache: set[str] = set()


def clear_read_cache() -> None:
    """清空读缓存，给 /clear 调用。"""
    _read_cache.clear()


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
        results = kb.search(query)
        if not results:
            return "知识库尚未构建索引或未找到相关内容。请先重建知识库索引。"
        lines = [f"[{r['source']}] {r['content']}" for r in results]
        return "\n\n---\n\n".join(lines)
    except Exception as e:
        return f"知识库搜索失败: {e}"


def run_skill_tool(skill_name: str) -> str:
    """读取完整 skill 指令，并列出该 skill 下的所有参考文件。"""
    meta, body = load_skill(skill_name)
    if meta is None:
        all_skills = list_skills()
        names = ", ".join(s["name"] for s in all_skills) if all_skills else "无"
        return f"skill 不存在: {skill_name}。可用: {names}"

    # 构建返回内容
    result = f"=== {skill_name} skill 指令 ===\n\n{body}" if body else ""

    # 列出 skill 目录下附属文件
    extras = _list_skill_extra_files(skill_dir(skill_name))

    if extras:
        result += (
            f"\n\n--- 参考文件 (可调 read_file 读取) ---\n"
            + "\n".join(f"  {f}" for f in extras)
        )

    return result if result else f"skill {skill_name} 无内容"


def check_health() -> str:
    """检查各模块状态，返回纯文本报告供模型读取（零成本、纯本地）。"""
    results = health.check_all()
    lines = []
    for r in results:
        mark = {"ok": "OK", "warn": "WARN", "error": "ERR", "skip": "SKIP"}[r["status"]]
        lines.append(f"[{mark}] {r['name']}: {r['detail']}")
    return "\n".join(lines) if lines else "体检无结果"


def _list_skill_extra_files(skill_path: str) -> list[str]:
    """列出 skill 目录下除 SKILL.md 外的所有文件（相对路径）。"""
    extras = []
    if not os.path.isdir(skill_path):
        return extras

    for root, dirs, files in os.walk(skill_path):
        dirs.sort()
        files.sort()
        for fname in files:
            if fname == "SKILL.md":
                continue
            rel_path = os.path.relpath(os.path.join(root, fname), skill_path)
            extras.append(rel_path)

    return extras
