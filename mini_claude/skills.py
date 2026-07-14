import os
import re

SKILLS_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "skills")
MAX_DESC_TOTAL = 300  # 所有 skill description 总字符上限


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 SKILL.md 的 YAML frontmatter 和正文。"""
    text = text.strip()
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = {}
            for line in parts[1].strip().split("\n"):
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip().strip('"').strip("'")
            return meta, parts[2].strip()
    return {}, text


def list_skills(context_window: int | None = None) -> list[dict]:
    """扫描 skills 目录，返回所有可用 skill 的元信息。

    context_window: 模型上下文窗口大小（token 数）。如果提供，所有 description
                    总字符数不超过窗口的 1%（按 name 排序依次保留），超出丢弃。
    """
    if not os.path.isdir(SKILLS_DIR):
        return []

    skills = []
    for name in sorted(os.listdir(SKILLS_DIR)):
        skill_file = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if not os.path.isfile(skill_file):
            continue
        try:
            with open(skill_file, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        meta, _ = _parse_frontmatter(content)
        skills.append({
            "name": meta.get("name", name),
            "description": meta.get("description", ""),
            "argument_hint": meta.get("argument-hint", ""),
            "disable_model_invocation": meta.get("disable-model-invocation", "false").lower() == "true",
        })

    # 规则 A：单个 description 不超过 300 字符
    for s in skills:
        if len(s["description"]) > 300:
            s["description"] = s["description"][:297] + "..."

    # 规则 B：总量不超过上下文窗口的 1%
    if context_window is not None:
        budget = context_window // 100
        acc = 0
        for s in skills:
            acc += len(s["description"])
            if acc > budget:
                s["description"] = ""

    return skills


def load_skill(name: str) -> tuple[dict | None, str | None]:
    """加载指定 name 的 skill，返回 (meta, body)。找不到返回 (None, None)。"""
    skill_file = os.path.join(SKILLS_DIR, name, "SKILL.md")
    if not os.path.isfile(skill_file):
        return None, None

    try:
        with open(skill_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None, None

    meta, body = _parse_frontmatter(content)
    return meta, body


def run_skill(messages: list[dict], skill_name: str, skill_args: str, tools: list[dict]) -> str | None:
    """
    运行一个 skill。
    返回 agent 的输出文本，如果 skill 不存在则返回 None。
    """
    from .agent import run_agent

    meta, body = load_skill(skill_name)
    if meta is None:
        return None

    if not body:
        body = meta.get("description", "")

    # 用 skill body 替换 system 消息
    skill_messages = []

    # 有 system？
    if messages and messages[0].get("role") == "system":
        skill_messages.append({"role": "system", "content": body})
    else:
        skill_messages.append({"role": "system", "content": body})

    # 把用户参数作为 user 消息
    user_content = skill_args if skill_args else meta.get("description", "")
    skill_messages.append({"role": "user", "content": user_content})

    return run_agent(skill_messages, tools)
