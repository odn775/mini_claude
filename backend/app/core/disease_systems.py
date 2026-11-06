"""科室 → 疾病系统分类。

统计页按「疾病系统」聚合分诊结论；24 个门诊科室每个只归属一个系统，
保证各类占比之和等于有结论的总次数（不会重复计数）。

系统划分只用于统计展示，不构成临床分类标准。
"""

from __future__ import annotations

from app.core.departments import DEPARTMENTS

SYSTEMS: dict[str, list[str]] = {
    "呼吸系统": ["呼吸内科"],
    "消化系统": ["消化内科", "普通外科"],
    "心血管系统": ["心血管内科", "心胸外科"],
    "神经与精神": ["神经内科", "神经外科", "精神心理科"],
    "泌尿系统": ["肾内科", "泌尿外科"],
    "内分泌与代谢": ["内分泌科"],
    "血液与免疫": ["血液科", "风湿免疫科"],
    "皮肤与五官": ["皮肤科", "眼科", "耳鼻喉科", "口腔科"],
    "骨与运动": ["骨科"],
    "妇儿": ["妇产科", "儿科"],
    "感染与急危重症": ["感染科", "急诊科", "全科医学科", "肿瘤科"],
}

# 科室 → 系统 的反向索引
_DEPT_TO_SYSTEM: dict[str, str] = {
    dept: system for system, depts in SYSTEMS.items() for dept in depts
}


def system_of(department: str) -> str:
    """取科室所属系统；不在映射表内的归入「其他」。"""
    return _DEPT_TO_SYSTEM.get(department, "其他")


def names() -> list[str]:
    return list(SYSTEMS)


def check_coverage() -> list[str]:
    """自检：白名单里没有归属系统的科室（应为空）。"""
    return [d for d in DEPARTMENTS if d not in _DEPT_TO_SYSTEM]
