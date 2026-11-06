"""导诊批量回归运行器。

直接在后端进程内调用 `stream_agent`，走与 Web/CLI 相同的 system prompt、工具
schema 和工具实现；不写数据库、不经过前端，适合批量回归。

与既有评测脚本的区别：
- tools_runner.py 只校验「首个工具名 + 参数」，不关心最终分诊结论；
- 本脚本校验完整导诊链路：是否先检索后推荐、主推科室、紧急程度、
  红旗症状本地识别、最终回复、知识库来源命中。

用法：
    python evals/triage_regression.py
    python evals/triage_regression.py --only normal_cough red_chest
    python evals/triage_regression.py --cases evals/triage_regression_cases.yaml

产物：
    evals/triage_regression_<日期>.json   机器可读明细
    evals/triage_regression_<日期>.md     人读报告
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO / "backend"))

# 评测默认使用仓库内索引与文档，避免依赖 ~/.mini_claude（容器外通常没有索引）
os.environ.setdefault("MINI_CLAUDE_DATA_DIR", str(_REPO / "data"))
os.environ.setdefault("MINI_CLAUDE_KNOWLEDGE_DIR", str(_REPO / "docs" / "departments"))

import yaml  # noqa: E402

from app.core.agent import stream_agent  # noqa: E402
from app.core.prompt import build_system_prompt  # noqa: E402
from app.core.triage_rules import detect as detect_red_flags  # noqa: E402
from app.infra.config import get_settings  # noqa: E402
from app.infra.knowledge import get_index_info  # noqa: E402
from app.tools import execute_tool  # noqa: E402
from app.tools.specs import TOOLS  # noqa: E402

TRIAGE_TOOL_NAMES = {"search_knowledge", "recommend_department"}
TRIAGE_TOOLS = [t for t in TOOLS if t["function"]["name"] in TRIAGE_TOOL_NAMES]
REJECT_REPLY = "请提问医学或导诊相关问题"
FOLLOWUP_WORDS = ["部位", "多久", "持续", "伴随", "具体", "什么感觉", "什么时候", "年龄", "有没有"]


def triage_executor(name: str, args: dict) -> str:
    """只允许导诊工具，防止回归过程产生文件/命令副作用。"""
    if name not in TRIAGE_TOOL_NAMES:
        return f"[eval] 非导诊工具已在回归中禁用: {name}"
    return execute_tool(name, args)


def norm_reply(text: str) -> str:
    return re.sub(r"[\s。.!！]+$", "", (text or "").strip())


def parse_sources(content: str) -> list[str]:
    """从 search_knowledge 返回文本中提取来源文件名（按返回顺序）。"""
    return re.findall(r"^\[([^\]]+)\]\s", content or "", flags=re.MULTILINE)


def parse_recommend(content: str) -> dict | None:
    try:
        data = json.loads(content or "")
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def run_turn(messages: list[dict], user_input: str, settings) -> dict:
    """跑一轮用户输入，返回工具调用、最终文本、用量等原始证据。"""
    started = time.monotonic()
    messages.append({"role": "user", "content": user_input})

    calls: list[dict] = []
    by_call_id: dict[str, dict] = {}
    final_text = ""
    usage: dict = {}

    for event in stream_agent(
        messages, TRIAGE_TOOLS, tool_executor=triage_executor, settings=settings
    ):
        etype = event.get("type")
        if etype == "tool_call":
            call = {
                "name": event.get("name"),
                "arguments": event.get("arguments") or {},
                "call_id": event.get("call_id"),
            }
            calls.append(call)
            if call["call_id"]:
                by_call_id[call["call_id"]] = call
        elif etype == "tool_result":
            call = by_call_id.get(event.get("call_id") or "")
            if call is not None:
                call["result"] = event.get("content", "")
        elif etype == "text":
            final_text = event.get("content", "")
        elif etype == "usage":
            usage = {
                "input_tokens": event.get("input_tokens", 0),
                "output_tokens": event.get("output_tokens", 0),
                "cached_tokens": event.get("cached_tokens", 0),
            }

    if final_text:
        messages.append({"role": "assistant", "content": final_text})

    search_calls = [c for c in calls if c["name"] == "search_knowledge"]
    recommend_calls = [c for c in calls if c["name"] == "recommend_department"]
    retrieved_sources: list[str] = []
    for c in search_calls:
        for src in parse_sources(c.get("result", "")):
            if src not in retrieved_sources:
                retrieved_sources.append(src)

    return {
        "input": user_input,
        "tool_seq": [c["name"] for c in calls],
        "tool_calls": [
            {"name": c["name"], "arguments": c.get("arguments") or {}}
            for c in calls
        ],
        "search_queries": [c.get("arguments", {}).get("query", "") for c in search_calls],
        "retrieved_sources": retrieved_sources,
        "recommend_payload": (
            parse_recommend(recommend_calls[-1].get("result", "")) if recommend_calls else None
        ),
        "final_text": final_text,
        "red_labels": detect_red_flags(user_input),
        "usage": usage,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }


def grade_turn(expect: dict, run: dict) -> dict:
    """按 mode 判定单轮结果，返回 checks 与 pass。"""
    mode = expect.get("mode", "conclusion")
    checks: dict[str, bool] = {}

    if mode == "reject":
        checks["no_tool_calls"] = len(run["tool_calls"]) == 0
        checks["exact_reply"] = norm_reply(run["final_text"]) == REJECT_REPLY
        passed = all(checks.values())
        return {"mode": mode, "checks": checks, "pass": passed}

    if mode == "followup":
        checks["no_recommend"] = not any(
            c["name"] == "recommend_department" for c in run["tool_calls"]
        )
        text = run["final_text"] or ""
        checks["asks_followup"] = ("？" in text or "?" in text or
                                   any(w in text for w in FOLLOWUP_WORDS))
        checks["has_text"] = bool(text.strip())
        passed = all(checks.values())
        return {"mode": mode, "checks": checks, "pass": passed}

    # conclusion
    payload = run["recommend_payload"] or {}
    primary = payload.get("primary_department")
    urgency = payload.get("urgency")
    expected_primary = expect.get("primary") or []
    expected_urgency = expect.get("urgency") or ["routine", "soon", "emergency"]

    checks["called_recommend"] = bool(run["recommend_payload"])
    checks["primary_ok"] = primary in expected_primary
    checks["urgency_ok"] = urgency in expected_urgency
    if expect.get("require_search", True):
        idx_search = next(
            (i for i, c in enumerate(run["tool_calls"]) if c["name"] == "search_knowledge"),
            None,
        )
        idx_rec = next(
            (i for i, c in enumerate(run["tool_calls"]) if c["name"] == "recommend_department"),
            None,
        )
        checks["search_before_recommend"] = (
            idx_search is not None and idx_rec is not None and idx_search < idx_rec
        )
    checks["reasoning_nonempty"] = bool((payload.get("reasoning") or "").strip())
    checks["advice_nonempty"] = bool((payload.get("advice") or "").strip())
    checks["has_text"] = bool((run["final_text"] or "").strip())
    if expect.get("text_any"):
        checks["text_any"] = any(
            str(k) in (run["final_text"] or "") for k in expect["text_any"]
        )
    if expect.get("red_flag"):
        checks["red_flag_detected"] = bool(run["red_labels"])

    passed = all(checks.values())
    return {
        "mode": mode,
        "checks": checks,
        "pass": passed,
        "expected": {"primary": expected_primary, "urgency": expected_urgency},
        "actual": {"primary": primary, "urgency": urgency, "payload": payload},
    }


def source_rank(run: dict, expected: str | None) -> int | None:
    if not expected:
        return None
    try:
        return run["retrieved_sources"].index(expected) + 1
    except ValueError:
        return None


def run_case(case: dict, settings) -> dict:
    system_prompt = build_system_prompt(settings=settings)
    messages = [{"role": "system", "content": system_prompt}]

    turns = []
    for turn in case.get("turns") or []:
        input_text = turn["input"]
        expect = turn.get("expect") or {}
        run = run_turn(messages, input_text, settings)
        grade = grade_turn(expect, run)
        expected_source = expect.get("expect_source")
        rank = source_rank(run, expected_source)
        turns.append({
            "input": input_text,
            "expect": expect,
            "run": run,
            "grade": grade,
            "expected_source": expected_source,
            "source_rank": rank,
        })

    passed = all(t["grade"]["pass"] for t in turns)
    return {
        "id": case["id"],
        "category": case.get("category", "未分类"),
        "description": case.get("description", ""),
        "pass": passed,
        "turns": turns,
    }


def md_cell(value) -> str:
    text = "-" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ").strip()


def summarize(results: list[dict]) -> dict:
    total_cases = len(results)
    passed_cases = sum(1 for r in results if r["pass"])

    by_category: dict[str, dict] = {}
    for r in results:
        stat = by_category.setdefault(r["category"], {"total": 0, "passed": 0})
        stat["total"] += 1
        stat["passed"] += int(r["pass"])

    conclusion_turns = []
    retrieval_turns = []
    red_turns = []
    total_turns = 0
    passed_turns = 0
    text_lengths: list[int] = []
    for r in results:
        for t in r["turns"]:
            total_turns += 1
            passed_turns += int(t["grade"]["pass"])
            if t["grade"]["mode"] == "conclusion":
                conclusion_turns.append(t)
                text_lengths.append(len((t["run"]["final_text"] or "").strip()))
            if t["expected_source"]:
                retrieval_turns.append(t)
            if t["expect"].get("red_flag"):
                red_turns.append(t)

    def rate(items, predicate):
        return (sum(1 for x in items if predicate(x)) / len(items)) if items else None

    return {
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "case_pass_rate": (passed_cases / total_cases) if total_cases else 0.0,
        "total_turns": total_turns,
        "passed_turns": passed_turns,
        "turn_pass_rate": (passed_turns / total_turns) if total_turns else 0.0,
        "by_category": by_category,
        "tool_chain_rate": rate(
            conclusion_turns, lambda t: t["grade"]["checks"].get("search_before_recommend", True)
        ),
        "department_rate": rate(
            conclusion_turns, lambda t: t["grade"]["checks"].get("primary_ok", False)
        ),
        "urgency_rate": rate(
            conclusion_turns, lambda t: t["grade"]["checks"].get("urgency_ok", False)
        ),
        "retrieval_hit_rate": rate(retrieval_turns, lambda t: t["source_rank"] is not None),
        "retrieval_top1_rate": rate(retrieval_turns, lambda t: t["source_rank"] == 1),
        "retrieval_total": len(retrieval_turns),
        "red_flag_detect_rate": rate(
            red_turns, lambda t: t["grade"]["checks"].get("red_flag_detected", False)
        ),
        "red_total": len(red_turns),
        "conclusion_total": len(conclusion_turns),
        "text_length_median": (
            statistics.median(text_lengths) if text_lengths else None
        ),
        "text_over_120_count": sum(1 for n in text_lengths if n > 120),
        "text_over_120_rate": rate(text_lengths, lambda n: n > 120),
    }


def build_markdown(results: list[dict], summary: dict, meta: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []
    lines.append(f"# 导诊批量回归测试报告（{meta['date']}）")
    lines.append("")
    lines.append("> 由 `evals/triage_regression.py` 自动生成，可重复执行。")
    lines.append("> 进程内直调后端 agent（与 Web/CLI 共用 system prompt、工具 schema 与实现），"
                 "不写数据库、不经过前端。")
    lines.append(f"> 运行时间：{now}")
    lines.append(f"> 模型：`{meta['model']}` · 索引：{meta['index_info'].get('total_chunks')} 个 chunk / "
                 f"{len(meta['index_info'].get('files') or [])} 份文档")
    lines.append(f"> 问题集：`{meta['cases_file']}`（{summary['total_cases']} 个用例 / "
                 f"{summary['total_turns']} 轮）")
    lines.append("")
    lines.append("## 一、总体结果")
    lines.append("")
    lines.append(f"- 用例通过率：**{summary['passed_cases']}/{summary['total_cases']}** "
                 f"（{summary['case_pass_rate']:.1%}）")
    lines.append(f"- 轮次通过率：{summary['passed_turns']}/{summary['total_turns']} "
                 f"（{summary['turn_pass_rate']:.1%}）")
    if summary["tool_chain_rate"] is not None:
        lines.append(f"- 检索→推荐工具链合规率：{summary['tool_chain_rate']:.1%}")
    if summary["department_rate"] is not None:
        lines.append(f"- 主推科室符合率：{summary['department_rate']:.1%}")
    if summary["urgency_rate"] is not None:
        lines.append(f"- 紧急程度符合率：{summary['urgency_rate']:.1%}")
    if summary["conclusion_total"]:
        lines.append(f"- 结论回复长度：中位 {summary['text_length_median']:g} 字，"
                     f"超过 120 字 {summary['text_over_120_count']}/{summary['conclusion_total']}"
                     f"（{summary['text_over_120_rate']:.1%}）")
    if summary["retrieval_total"]:
        lines.append(f"- 期望知识库文档命中率：{summary['retrieval_hit_rate']:.1%}"
                     f"（Top-1 {summary['retrieval_top1_rate']:.1%}，共 {summary['retrieval_total']} 轮）")
    if summary["red_total"]:
        lines.append(f"- 红旗症状本地识别命中率：{summary['red_flag_detect_rate']:.1%}"
                     f"（{summary['red_total']} 轮）")
    lines.append("")
    lines.append("## 二、分类结果")
    lines.append("")
    lines.append("| 类别 | 用例数 | 通过 | 通过率 |")
    lines.append("| --- | ---: | ---: | ---: |")
    for cat, stat in summary["by_category"].items():
        rate = stat["passed"] / stat["total"] if stat["total"] else 0.0
        lines.append(
            f"| {md_cell(cat)} | {stat['total']} | {stat['passed']} | {rate:.1%} |"
        )
    lines.append("")
    lines.append("## 三、逐条结果")
    lines.append("")
    lines.append("| # | 用例 | 类别 | 输入 | 期望 | 实际 | 结果 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    row = 0
    for r in results:
        for t in r["turns"]:
            row += 1
            mode = t["grade"]["mode"]
            if mode == "conclusion":
                expected = f"科室 {'/'.join(t['expect'].get('primary') or [])}<br>紧急 {'/'.join(t['expect'].get('urgency') or [])}"
                payload = t["run"]["recommend_payload"] or {}
                actual = f"{payload.get('primary_department') or '未给出'} / {payload.get('urgency') or '-'}"
            elif mode == "followup":
                expected = "不给科室，继续追问"
                actual = "已追问" if t["grade"]["checks"].get("asks_followup") else "未追问"
            else:
                expected = "固定拒答，不调工具"
                actual = "固定拒答" if t["grade"]["checks"].get("exact_reply") else "未按固定拒答"
            status = "PASS" if t["grade"]["pass"] else "FAIL"
            lines.append(
                f"| {row} | {md_cell(r['id'])} | {md_cell(r['category'])} | "
                f"{md_cell(t['input'])} | {expected} | {md_cell(actual)} | {status} |"
            )
    lines.append("")

    failures = [
        (r, t) for r in results for t in r["turns"] if not t["grade"]["pass"]
    ]
    lines.append("## 四、失败详情")
    lines.append("")
    if not failures:
        lines.append("本轮无失败用例。")
        lines.append("")
    else:
        for r, t in failures:
            lines.append(f"### {r['id']} · {t['input']}")
            lines.append("")
            lines.append(f"- 类别：{r['category']}（{r['description']}）")
            lines.append(f"- 工具序列：`{' → '.join(t['run']['tool_seq']) or '无'}`")
            lines.append(f"- 检索词：{'; '.join(t['run']['search_queries']) or '无'}")
            lines.append(f"- 检索来源：{', '.join(t['run']['retrieved_sources']) or '无'}")
            checks = t["grade"]["checks"]
            failed_checks = [k for k, v in checks.items() if not v]
            lines.append(f"- 未通过检查：{', '.join(failed_checks) or '无'}")
            if t["grade"]["mode"] == "conclusion":
                payload = t["run"]["recommend_payload"]
                lines.append(f"- 分诊 payload：`{json.dumps(payload, ensure_ascii=False)}`")
            lines.append(f"- 最终回复：{md_cell(t['run']['final_text'])}")
            lines.append("")

    # 检索命中明细
    retrieval = [(r, t) for r in results for t in r["turns"] if t["expected_source"]]
    lines.append("## 五、知识库检索命中")
    lines.append("")
    if not retrieval:
        lines.append("本轮用例未设置 `expect_source`。")
    else:
        lines.append("| 用例 | 输入 | 期望来源 | 实际 Top-5 来源 | 命中排名 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r, t in retrieval:
            actual = ", ".join(t["run"]["retrieved_sources"]) or "无"
            rank = t["source_rank"]
            lines.append(
                f"| {md_cell(r['id'])} | {md_cell(t['input'])} | {md_cell(t['expected_source'])} | "
                f"{md_cell(actual)} | {rank if rank else '未命中'} |"
            )
    lines.append("")

    # 自动归因
    lines.append("## 六、结论与发现")
    lines.append("")
    findings: list[str] = []

    if failures:
        finding = (f"失败用例共 {len({r['id'] for r, _ in failures})} 个，集中在："
                   + "、".join(sorted({r['category'] for r, _ in failures})) + "。")
        for r, t in failures[:8]:
            failed_checks = [k for k, v in t["grade"]["checks"].items() if not v]
            finding += f"\n   - `{r['id']}`（{t['input']}）未通过：{', '.join(failed_checks)}"
        findings.append(finding)
    else:
        findings.append("本轮全部用例通过，未发现导诊链路回归。")

    misses = [(r, t) for r, t in retrieval if t["source_rank"] is None]
    if misses:
        findings.append(f"知识库检索有 {len(misses)} 轮未命中期望文档："
                        + "、".join(f"`{r['id']}`" for r, _ in misses) + "。"
                        "科室结论可能仍正确，但依据链偏弱。")
    elif retrieval:
        findings.append("设置了期望来源的用例全部在 Top-5 内命中对应科室文档。")

    tool_fail = [t for r in results for t in r["turns"]
                 if t["grade"]["mode"] == "conclusion"
                 and not t["grade"]["checks"].get("search_before_recommend", True)]
    if tool_fail:
        findings.append(f"有 {len(tool_fail)} 轮未遵守「先 search_knowledge 再 recommend_department」："
                        + "、".join(f"`{t['input']}`" for t in tool_fail) + "。")

    red_miss = [t for r in results for t in r["turns"]
                if t["expect"].get("red_flag")
                and not t["grade"]["checks"].get("red_flag_detected", False)]
    if red_miss:
        findings.append(f"有 {len(red_miss)} 轮红旗症状未被本地规则识别："
                        + "、".join(f"`{t['input']}`" for t in red_miss) + "。")

    long_turns = [
        (r, t) for r in results for t in r["turns"]
        if t["grade"]["mode"] == "conclusion"
        and len((t["run"]["final_text"] or "").strip()) > 120
    ]
    if long_turns:
        longest_r, longest_t = max(
            long_turns, key=lambda pair: len((pair[1]["run"]["final_text"] or "").strip())
        )
        findings.append(
            f"有 {len(long_turns)} 轮结论回复超过系统提示词约定的 120 字"
            f"（中位 {summary['text_length_median']:g} 字），最长 "
            f"{len((longest_t['run']['final_text'] or '').strip())} 字出现在 "
            f"`{longest_r['id']}`，建议收紧回答篇幅。"
        )

    findings.append("单次运行结果；模型输出存在随机性，重要版本变更建议重复执行取均值。")

    for idx, finding in enumerate(findings, 1):
        lines.append(f"{idx}. {finding}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> 说明：本报告只评估「是否按导诊规则给出科室与紧急程度」，不构成医学诊断；"
                 "体温/血压等未在用例中给出具体数值的场景，紧急程度按知识库的常规门诊口径判定。")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="导诊批量回归")
    ap.add_argument("--cases", default=str(_HERE / "triage_regression_cases.yaml"))
    ap.add_argument("--only", nargs="*", default=None, help="只跑指定用例 id")
    ap.add_argument("--from-json", default=None,
                    help="不调用模型，直接用已有 JSON 明细重建 Markdown 报告")
    ap.add_argument("--out-md", default=None)
    ap.add_argument("--out-json", default=None)
    args = ap.parse_args()

    if args.from_json:
        payload = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        out_md = Path(args.out_md) if args.out_md else _HERE / (
            "triage_regression_" + payload["meta"]["date"].replace("-", "") + ".md"
        )
        out_md.write_text(
            build_markdown(payload["results"], payload["summary"], payload["meta"]),
            encoding="utf-8",
        )
        print(f"报告已重建：{out_md}")
        return

    cases_doc = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8"))
    cases = cases_doc.get("cases") or []
    if args.only:
        cases = [c for c in cases if c["id"] in set(args.only)]
        if not cases:
            print(f"没有匹配的用例: {args.only}")
            return

    settings = get_settings()
    index_info = get_index_info(settings)
    stamp = datetime.now().strftime("%Y%m%d")
    out_md = Path(args.out_md) if args.out_md else _HERE / f"triage_regression_{stamp}.md"
    out_json = Path(args.out_json) if args.out_json else _HERE / f"triage_regression_{stamp}.json"

    print(f"用例 {len(cases)} 个，模型 {settings.model}，索引 "
          f"{index_info.get('total_chunks')} chunks / {len(index_info.get('files') or [])} 文档\n")

    results = []
    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} ... ", end="", flush=True)
        try:
            result = run_case(case, settings)
        except Exception as e:  # 单用例异常不阻断批量
            result = {
                "id": case["id"],
                "category": case.get("category", "未分类"),
                "description": case.get("description", ""),
                "pass": False,
                "turns": [{
                    "input": (case.get("turns") or [{}])[0].get("input", ""),
                    "expect": {},
                    "run": {"final_text": "", "tool_seq": [], "tool_calls": [],
                            "search_queries": [], "retrieved_sources": [],
                            "recommend_payload": None, "red_labels": [], "usage": {},
                            "latency_ms": 0},
                    "grade": {"mode": "error", "checks": {"exception": False}, "pass": False},
                    "expected_source": None,
                    "source_rank": None,
                    "error": f"{type(e).__name__}: {e}",
                }],
            }
        results.append(result)
        status = "PASS" if result["pass"] else "FAIL"
        detail = ""
        first = result["turns"][0]
        actual = first["run"].get("recommend_payload") or {}
        if first["grade"]["mode"] == "conclusion":
            detail = f" → {actual.get('primary_department')}/{actual.get('urgency')}"
        elif first["grade"]["mode"] == "followup":
            detail = " → 追问" if first["grade"]["checks"].get("asks_followup") else " → 未追问"
        elif first["grade"]["mode"] == "reject":
            detail = " → 拒答" if first["grade"]["checks"].get("exact_reply") else " → 未拒答"
        print(f"{status}{detail} ({first['run']['latency_ms']}ms)")

    summary = summarize(results)
    raw_date = cases_doc.get("meta", {}).get("date")
    meta = {
        "date": str(raw_date) if raw_date else datetime.now().strftime("%Y-%m-%d"),
        "model": settings.model,
        "cases_file": str(Path(args.cases)),
        "index_info": index_info,
    }
    payload = {
        "meta": meta,
        "summary": summary,
        "results": results,
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text(build_markdown(results, summary, meta), encoding="utf-8")

    print("\n=== 汇总 ===")
    print(f"用例通过 {summary['passed_cases']}/{summary['total_cases']} "
          f"({summary['case_pass_rate']:.1%})；轮次通过 "
          f"{summary['passed_turns']}/{summary['total_turns']} ({summary['turn_pass_rate']:.1%})")
    if summary["retrieval_total"]:
        print(f"知识库命中 {summary['retrieval_hit_rate']:.1%}（Top-1 "
              f"{summary['retrieval_top1_rate']:.1%}）")
    print(f"报告：{out_md}")
    print(f"明细：{out_json}")


if __name__ == "__main__":
    main()
