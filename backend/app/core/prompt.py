"""System prompt 构建：把可用 skill 列表附给模型，供其自主调用 run_skill。"""

from __future__ import annotations

from app.infra.config import Settings
from app.infra.skills import list_skills

BASE_PROMPT = "你是 Mini Claude，一个智能助手。"


def build_system_prompt(
    context_window: int | None = None, settings: Settings | None = None
) -> str:
    """构建 system prompt，附上 skill 列表供模型自主调用。"""
    skills = list_skills(context_window, settings)
    if not skills:
        return BASE_PROMPT

    skill_lines = "\n".join(
        f"  /{s['name']}: {s['description']}" for s in skills
    )
    return (
        f"{BASE_PROMPT}\n\n"
        "你有以下 skill 可用。当用户的问题匹配某个 skill 时，"
        "调 run_skill(skill_name) 读取完整指令并执行：\n"
        f"{skill_lines}"
    )
