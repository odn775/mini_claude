"""门诊科室白名单与症状关键词。

两个用途：
1. 分诊工具校验模型给出的科室名——只能落在白名单内，避免模型编造不存在的科室。
2. 知识库为空（或检索无命中）时，作为兜底的分诊依据。

关键词只做「常见门诊分诊经验」层面的归集，不构成临床指南。
"""

from __future__ import annotations

# 科室 → 典型症状关键词
DEPARTMENTS: dict[str, list[str]] = {
    "呼吸内科": ["咳嗽", "咳痰", "气喘", "呼吸困难", "咯血", "发热伴咳嗽", "胸闷伴咳嗽", "肺结核"],
    "消化内科": ["腹痛", "反酸", "烧心", "恶心", "呕吐", "腹泻", "便秘", "腹胀", "消化不良", "黑便", "黄疸"],
    "心血管内科": ["心悸", "心慌", "胸痛", "胸闷", "高血压", "心律不齐", "下肢水肿", "晕厥"],
    "神经内科": ["头痛", "头晕", "眩晕", "肢体麻木", "偏瘫", "抽搐", "失眠", "记忆力下降", "面瘫"],
    "内分泌科": ["多饮", "多尿", "体重下降", "血糖高", "甲状腺", "怕冷", "怕热", "多汗", "月经紊乱"],
    "肾内科": ["尿频", "尿急", "尿痛", "血尿", "蛋白尿", "水肿", "腰痛伴尿异常", "肾功能"],
    "血液科": ["贫血", "乏力伴面色苍白", "出血点", "瘀斑", "淋巴结肿大", "白细胞异常"],
    "普通外科": ["腹部包块", "疝气", "阑尾", "胆囊", "乳腺肿块", "痔疮", "伤口感染"],
    "骨科": ["关节痛", "腰腿痛", "颈肩痛", "骨折", "扭伤", "运动损伤", "脊柱", "膝关节"],
    "泌尿外科": ["结石", "排尿困难", "尿潴留", "睾丸疼痛", "前列腺", "包皮"],
    "神经外科": ["头部外伤", "脑外伤", "颅内", "脊柱外伤伴神经症状"],
    "心胸外科": ["胸部外伤", "肺部结节需手术", "心脏手术", "食管"],
    "妇产科": ["月经不调", "痛经", "白带异常", "阴道出血", "孕期", "产检", "不孕", "更年期"],
    "儿科": ["儿童发热", "小儿咳嗽", "小儿腹泻", "儿童皮疹", "儿童呕吐", "生长发育"],
    "眼科": ["眼红", "眼痛", "视力下降", "视物模糊", "流泪", "眼外伤", "眼异物"],
    "耳鼻喉科": ["耳痛", "耳鸣", "听力下降", "鼻塞", "流鼻血", "咽痛", "声音嘶哑", "扁桃体"],
    "皮肤科": ["皮疹", "瘙痒", "红斑", "风团", "脱发", "痤疮", "湿疹", "疱疹", "皮肤过敏"],
    "口腔科": ["牙痛", "牙龈出血", "口腔溃疡", "智齿", "口腔异味", "牙齿松动"],
    "精神心理科": ["焦虑", "抑郁", "情绪低落", "失眠伴情绪", "强迫", "惊恐", "心理压力", "自杀念头"],
    "感染科": ["持续发热", "传染病接触", "肝炎", "结核", "不明原因发热"],
    "风湿免疫科": ["关节肿痛", "晨僵", "狼疮", "口干眼干", "痛风", "免疫"],
    "肿瘤科": ["肿瘤", "化疗", "放疗", "癌症", "结节随访"],
    "急诊科": ["急危重症", "外伤出血", "中毒", "意识障碍", "剧烈腹痛", "高热惊厥"],
    "全科医学科": ["症状不明确", "多系统不适", "初诊", "体检异常", "不知道挂什么科"],
}

# 常见口语/别名 → 标准科室名
ALIASES: dict[str, str] = {
    "内科": "全科医学科",
    "外科": "普通外科",
    "心内科": "心血管内科",
    "心内": "心血管内科",
    "神内": "神经内科",
    "神外": "神经外科",
    "消化科": "消化内科",
    "呼吸科": "呼吸内科",
    "耳鼻咽喉科": "耳鼻喉科",
    "耳鼻咽喉头颈外科": "耳鼻喉科",
    "心理科": "精神心理科",
    "精神科": "精神心理科",
    "皮肤性病科": "皮肤科",
    "口腔颌面外科": "口腔科",
    "妇科": "妇产科",
    "产科": "妇产科",
    "泌尿科": "泌尿外科",
    "风湿科": "风湿免疫科",
    "风湿科免疫科": "风湿免疫科",
    "内分泌代谢科": "内分泌科",
    "急诊": "急诊科",
    "急救": "急诊科",
    "全科": "全科医学科",
}


def names() -> list[str]:
    """白名单科室名（顺序固定，便于提示词与前端展示）。"""
    return list(DEPARTMENTS)


def describe_list() -> str:
    """给系统提示词用的「科室：典型症状」清单。"""
    return "\n".join(
        f"- {name}：{'、'.join(keywords[:8])}" for name, keywords in DEPARTMENTS.items()
    )


def resolve(raw: str | None) -> str | None:
    """把模型给出的科室名归一到白名单；无法归属时返回 None。

    依次尝试：精确匹配 → 别名 → 去掉「科/门诊/部」等后缀 → 包含关系 → 关键词反查。
    """
    if not raw:
        return None

    text = str(raw).strip().replace(" ", "")
    if not text:
        return None

    if text in DEPARTMENTS:
        return text
    if text in ALIASES:
        return ALIASES[text]

    # 去掉常见后缀再试一次
    trimmed = text
    for suffix in ("门诊", "科室", "科", "部"):
        if trimmed.endswith(suffix) and len(trimmed) > len(suffix):
            trimmed = trimmed[: -len(suffix)]
            break
    for name in DEPARTMENTS:
        if trimmed == name or trimmed == name.removesuffix("科"):
            return name

    # 包含关系（如「消化内科门诊」「附属医院儿科」）
    for name in DEPARTMENTS:
        if name in text:
            return name

    # 关键词反查（如「胸外科」→ 心胸外科）
    for name, keywords in DEPARTMENTS.items():
        if any(k in text for k in keywords):
            return name

    return None


def match_by_symptoms(text: str) -> str | None:
    """按症状关键词从文本里推断科室（知识库为空时的兜底）。"""
    if not text:
        return None
    best: tuple[int, str] | None = None
    for name, keywords in DEPARTMENTS.items():
        hits = sum(1 for k in keywords if k in text)
        if hits and (best is None or hits > best[0]):
            best = (hits, name)
    return best[1] if best else None


def normalize_urgency(raw: str | None) -> str:
    """紧急程度归一：非法的按普通处理。"""
    text = (raw or "").strip().lower()
    if text in ("emergency", "urgent", "急诊", "紧急", "立即"):
        return "emergency"
    if text in ("soon", "尽快", "短期", "24小时", "48小时"):
        return "soon"
    return "routine"
