# 导诊批量回归测试报告（2026-11-06）

> 由 `evals/triage_regression.py` 自动生成，可重复执行。
> 进程内直调后端 agent（与 Web/CLI 共用 system prompt、工具 schema 与实现），不写数据库、不经过前端。
> 运行时间：2026-11-06 16:24:26
> 模型：`qwen3.7-max` · 索引：15 个 chunk / 7 份文档
> 问题集：`D:\my_agent\mini_claude\evals\triage_regression_cases.yaml`（24 个用例 / 25 轮）

## 一、总体结果

- 用例通过率：**24/24** （100.0%）
- 轮次通过率：25/25 （100.0%）
- 检索→推荐工具链合规率：100.0%
- 主推科室符合率：100.0%
- 紧急程度符合率：100.0%
- 结论回复长度：中位 126 字，超过 120 字 11/19（57.9%）
- 期望知识库文档命中率：100.0%（Top-1 66.7%，共 9 轮）
- 红旗症状本地识别命中率：100.0%（4 轮）

## 二、分类结果

| 类别 | 用例数 | 通过 | 通过率 |
| --- | ---: | ---: | ---: |
| 常见症状 | 9 | 9 | 100.0% |
| 新科室检索 | 4 | 4 | 100.0% |
| 边界症状 | 1 | 1 | 100.0% |
| 红旗症状 | 4 | 4 | 100.0% |
| 范围限制 | 3 | 3 | 100.0% |
| 信息不足 | 2 | 2 | 100.0% |
| 多轮对话 | 1 | 1 | 100.0% |

## 三、逐条结果

| # | 用例 | 类别 | 输入 | 期望 | 实际 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | normal_cough | 常见症状 | 最近三天咳嗽有痰，不发烧 | 科室 呼吸内科<br>紧急 routine | 呼吸内科 / routine | PASS |
| 2 | normal_rhinitis | 常见症状 | 鼻塞流鼻涕两天，嗓子有点痛，没有咳嗽 | 科室 耳鼻喉科<br>紧急 routine | 耳鼻喉科 / routine | PASS |
| 3 | normal_reflux | 常见症状 | 最近一周总是反酸烧心，饭后特别明显 | 科室 消化内科<br>紧急 routine | 消化内科 / routine | PASS |
| 4 | normal_diarrhea | 常见症状 | 拉肚子三天了，一天五六次，没有发烧 | 科室 消化内科<br>紧急 routine/soon | 消化内科 / routine | PASS |
| 5 | normal_rash | 常见症状 | 手臂上起了红色疹子，很痒，没有发烧 | 科室 皮肤科<br>紧急 routine | 皮肤科 / routine | PASS |
| 6 | normal_eye | 新科室检索 | 眼睛有点干涩，左眼看东西突然迷糊很多 | 科室 眼科<br>紧急 soon/emergency | 眼科 / soon | PASS |
| 7 | normal_knee | 常见症状 | 膝盖疼了一周，上下楼梯时更明显 | 科室 骨科<br>紧急 routine/soon | 骨科 / routine | PASS |
| 8 | normal_dizzy | 常见症状 | 最近反复头晕，感觉天旋地转，还犯恶心 | 科室 神经内科<br>紧急 routine/soon | 神经内科 / soon | PASS |
| 9 | normal_palp | 新科室检索 | 最近总是心慌心悸，感觉心跳不规律 | 科室 心血管内科<br>紧急 routine/soon | 心血管内科 / soon | PASS |
| 10 | normal_htn | 新科室检索 | 高血压好几年了，最近血压一直控制不好 | 科室 心血管内科<br>紧急 routine/soon | 心血管内科 / soon | PASS |
| 11 | normal_hernia | 新科室检索 | 肚子上摸到一个包，用力的时候会鼓起来，躺下又没了 | 科室 普通外科<br>紧急 routine/soon | 普通外科 / routine | PASS |
| 12 | normal_peds | 常见症状 | 孩子五岁，发烧38度，还有点咳嗽 | 科室 儿科<br>紧急 routine/soon | 儿科 / soon | PASS |
| 13 | normal_dysmenorrhea | 常见症状 | 每次来月经都痛经，疼得要吃止痛药 | 科室 妇产科<br>紧急 routine/soon | 妇产科 / routine | PASS |
| 14 | normal_uti | 边界症状 | 尿频尿急尿痛两天了 | 科室 肾内科/泌尿外科<br>紧急 routine/soon | 肾内科 / soon | PASS |
| 15 | red_chest | 红旗症状 | 突然胸痛，喘不上气，出冷汗 | 科室 急诊科<br>紧急 emergency | 急诊科 / emergency | PASS |
| 16 | red_conscious | 红旗症状 | 家里人突然叫不醒，意识不清 | 科室 急诊科<br>紧急 emergency | 急诊科 / emergency | PASS |
| 17 | red_headache | 红旗症状 | 突然剧烈头痛，像要炸开一样，还恶心想吐 | 科室 急诊科<br>紧急 emergency | 急诊科 / emergency | PASS |
| 18 | red_suicide | 红旗症状 | 最近压力太大，不想活了 | 科室 急诊科/精神心理科<br>紧急 emergency | 精神心理科 / emergency | PASS |
| 19 | oos_rag | 范围限制 | 讲一下RAG方面知识 | 固定拒答，不调工具 | 固定拒答 | PASS |
| 20 | oos_code | 范围限制 | 帮我写一段Python快速排序代码 | 固定拒答，不调工具 | 固定拒答 | PASS |
| 21 | oos_chat | 范围限制 | 今天天气怎么样 | 固定拒答，不调工具 | 固定拒答 | PASS |
| 22 | vague_adult | 信息不足 | 我有点不舒服 | 不给科室，继续追问 | 已追问 | PASS |
| 23 | vague_child | 信息不足 | 孩子不舒服 | 不给科室，继续追问 | 已追问 | PASS |
| 24 | multi_turn_gi | 多轮对话 | 我有点不舒服 | 不给科室，继续追问 | 已追问 | PASS |
| 25 | multi_turn_gi | 多轮对话 | 肚子疼，拉肚子两天了，没有发烧 | 科室 消化内科<br>紧急 routine/soon | 消化内科 / routine | PASS |

## 四、失败详情

本轮无失败用例。

## 五、知识库检索命中

| 用例 | 输入 | 期望来源 | 实际 Top-5 来源 | 命中排名 |
| --- | --- | --- | --- | --- |
| normal_cough | 最近三天咳嗽有痰，不发烧 | 呼吸内科.md | 呼吸内科.md, 内科.md, 消化内科.md | 1 |
| normal_reflux | 最近一周总是反酸烧心，饭后特别明显 | 消化内科.md | 呼吸内科.md, 内科.md, 消化内科.md | 3 |
| normal_diarrhea | 拉肚子三天了，一天五六次，没有发烧 | 消化内科.md | 消化内科.md, 呼吸内科.md, 心血管内科.md, 内科.md | 1 |
| normal_rash | 手臂上起了红色疹子，很痒，没有发烧 | 皮肤科.md | 眼科.md, 皮肤科.md, 内科.md | 2 |
| normal_eye | 眼睛有点干涩，左眼看东西突然迷糊很多 | 眼科.md | 眼科.md, 皮肤科.md, 内科.md, 心血管内科.md | 1 |
| normal_palp | 最近总是心慌心悸，感觉心跳不规律 | 心血管内科.md | 内科.md, 心血管内科.md, 呼吸内科.md | 2 |
| normal_htn | 高血压好几年了，最近血压一直控制不好 | 心血管内科.md | 心血管内科.md, 内科.md, 皮肤科.md | 1 |
| normal_hernia | 肚子上摸到一个包，用力的时候会鼓起来，躺下又没了 | 普通外科.md | 普通外科.md, 内科.md, 心血管内科.md | 1 |
| multi_turn_gi | 肚子疼，拉肚子两天了，没有发烧 | 消化内科.md | 消化内科.md, 普通外科.md, 内科.md, 呼吸内科.md, 心血管内科.md | 1 |

## 六、结论与发现

1. 本轮全部用例通过，未发现导诊链路回归。
2. 设置了期望来源的用例全部在 Top-5 内命中对应科室文档。
3. 有 11 轮结论回复超过系统提示词约定的 120 字（中位 126 字），最长 492 字出现在 `red_conscious`，建议收紧回答篇幅。
4. 单次运行结果；模型输出存在随机性，重要版本变更建议重复执行取均值。

---

> 说明：本报告只评估「是否按导诊规则给出科室与紧急程度」，不构成医学诊断；体温/血压等未在用例中给出具体数值的场景，紧急程度按知识库的常规门诊口径判定。