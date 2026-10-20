"""
工具调用正确率评估器（v2）。

支持两种用例模式：

1. normal（默认）
   - 最小 LLM 调用额度：捕获首个 tool_call 事件后立即 break（stream_agent 是惰性
     生成器，不再拉取就不会发下一次请求），因此每用例只触发 1 次 LLM 调用。
   - 评分：工具选择 + 参数完整性 + 可选参数值校验（expect_args）。

2. retry（错误重试，case 里 `retry: true`）
   - 必须让模型看到工具返回的错误后再行动，因此天然需要多轮调用。
   - 通过 MAX_CALLS 上限（默认 3 个 tool_call）控制成本：最多约 4 次 LLM 调用/用例。
   - 评分：模型在首次失败后是否"重试成功"（第二次及以后用正确工具 + 正确参数再次调用）。

参数值断言 expect_args 语法（只对 normal/retry 判定的那次调用生效）：
   字段名: 标量/字符串           -> 完全相等（忽略首尾空白）
   字段名: [a, b, ...]           -> 命中任一即通过
   字段名: {contains: "子串"}     -> 值包含子串
   字段名: {regex: "pattern"}     -> 值匹配正则
   断言中出现的字段若在模型参数里缺失/为空 -> 判为失败

用法：
    python evals/tools_runner.py [--repeat N] [--cases evals/cases.yaml] [--json out.json]
"""

import argparse
import json
import os
import re
import statistics
import sys

import yaml

# 让 runner 能 import 后端包（evals/../backend）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from app.core.agent import stream_agent  # noqa: E402
from app.tools import TOOLS  # noqa: E402

# retry 用例最多观察到的工具调用数（超出即判失败，控制成本）
MAX_CALLS = 3

SYSTEM_PROMPT = (
    "你是 mini_claude，请根据用户需求在必要时调用工具。"
    "先调用工具，再基于结果作答。"
    "若工具调用失败或参数解析错误，请修正参数后重试。"
)


# ── 记录型 executor：不产生副作用；可按工具模拟首调失败 ──

def make_executor(error_tool):
    """error_tool：对该工具首次调用返回错误，之后正常。None 则始终正常。"""
    calls = []
    failed = set()

    def executor(name, args):
        calls.append((name, args))
        if error_tool is not None and name == error_tool and name not in failed:
            failed.add(name)
            return (
                f"[eval mock] 调用 {name} 失败(模拟)：参数校验或执行发生错误，"
                f"请修正后重试。收到参数: {json.dumps(args, ensure_ascii=False)}"
            )
        return f"[eval mock] {name} 执行成功，参数: {json.dumps(args, ensure_ascii=False)}"

    return executor, calls


# ── 参数校验 ──

def _truthy_str(v):
    return isinstance(v, str) and v.strip() != ""


def check_expected_arg(actual_value, spec):
    """actual_value 为模型实际传的值；spec 为 yaml 中的断言描述。返回 bool。"""
    if not _truthy_str(actual_value):
        return False
    val = actual_value.strip()
    if isinstance(spec, str):
        return val == spec.strip()
    if isinstance(spec, list):
        return any(_truthy_str(a) and val == str(a).strip() for a in spec)
    if isinstance(spec, dict):
        if "contains" in spec:
            return str(spec["contains"]) in val
        if "regex" in spec:
            try:
                return re.search(str(spec["regex"]), val) is not None
            except re.error:
                return False
    return False


def evaluate_arg_values(args, expect_args):
    """对 expect_args 逐字段做值校验，返回 (ok, 失败字段列表)。expect_args 为空则视为通过。"""
    if not expect_args:
        return True, []
    bad = []
    for field, spec in expect_args.items():
        if not check_expected_arg(args.get(field), spec):
            bad.append(field)
    return not bad, bad


# ── 单用例评估 ──

def pick_judged_call(case, first_call, all_calls):
    """选定用哪个调用来判定 tool/args。
    normal: 首个调用；retry: 调用序列中『最后一次 name==期望工具』的调用。"""
    if not case.get("retry"):
        return first_call
    expected = case["tool"]
    judged = None
    for name, args in all_calls:
        if name == expected:
            judged = {"name": name, "arguments": args}
    return judged


def evaluate_case(case, tools):
    """
    返回 (pass, tool_ok, args_ok, args_value_ok, retry_ok, detail)。
    评分维度见模块 docstring。
    """
    retry_mode = bool(case.get("retry"))
    error_tool = case["tool"] if retry_mode else None
    executor, calls = make_executor(error_tool)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": case["prompt"]},
    ]

    first_call = None
    all_calls = []
    try:
        if retry_mode:
            for event in stream_agent(messages, tools, tool_executor=executor):
                if event["type"] == "tool_call":
                    c = {"name": event["name"], "arguments": event.get("arguments") or {}}
                    all_calls.append(c)
                    if first_call is None:
                        first_call = c
                    if len(all_calls) >= MAX_CALLS:
                        break
                elif event["type"] == "text":
                    # 模型已给出最终回答（放弃重试或结束），不再等待更多调用
                    break
        else:
            for event in stream_agent(messages, tools, tool_executor=executor):
                if event["type"] == "tool_call":
                    first_call = {
                        "name": event["name"],
                        "arguments": event.get("arguments") or {},
                    }
                    break
    except Exception as e:
        detail = {"error": f"{type(e).__name__}: {e}"}
        return False, False, False, False, False, detail

    judged = pick_judged_call(case, first_call, all_calls)
    if judged is None:
        reason = "模型未在判定位置使用期望工具" if retry_mode else "模型首轮未调用任何工具"
        detail = {"error": reason, "retry": retry_mode}
        return False, False, False, False, False, detail

    expected_tool = case["tool"]
    tool_ok = judged["name"] == expected_tool
    args = judged["arguments"] or {}

    required = case.get("required") or {}
    missing = [k for k in required if not _truthy_str(args.get(k))]
    args_ok = not missing

    expect_args = case.get("expect_args") or {}
    args_value_ok, bad_values = evaluate_arg_values(args, expect_args)

    # retry 成功：存在『第二次及以后』对期望工具的成功调用
    retry_ok = False
    if retry_mode:
        success_idx = [
            i for i, (name, a) in enumerate(all_calls)
            if name == expected_tool
            and not [k for k in required if not _truthy_str(a.get(k))]
        ]
        retry_ok = any(i >= 1 for i in success_idx)

    passed = bool(tool_ok and args_ok and args_value_ok)
    if retry_mode:
        passed = retry_ok

    detail = {
        "retry": retry_mode,
        "called_tool": judged["name"],
        "expected_tool": expected_tool,
        "called_args": {k: args.get(k) for k in (set(required) | set(expect_args))},
        "missing": missing,
        "bad_values": bad_values,
        "call_seq": [n for n, _ in all_calls],
        "retry_ok": retry_ok,
    }
    return passed, tool_ok, args_ok, args_value_ok, retry_ok, detail


# ── 一轮运行 + 汇总 ──

def run_once(cases):
    results = []
    for case in cases:
        cid = case["id"]
        passed, tool_ok, args_ok, args_value_ok, retry_ok, detail = evaluate_case(case, TOOLS)
        flag = "PASS" if passed else "FAIL"
        results.append({
            "id": cid,
            "tool": case["tool"],
            "retry": bool(case.get("retry")),
            "pass": passed,
            "tool_ok": bool(tool_ok),
            "args_ok": bool(args_ok),
            "args_value_ok": bool(args_value_ok),
            "retry_ok": bool(retry_ok),
            "detail": detail,
        })
        note = []
        if isinstance(detail.get("missing"), list) and detail.get("missing"):
            note.append("缺参数:" + ",".join(detail["missing"]))
        if isinstance(detail.get("bad_values"), list) and detail.get("bad_values"):
            note.append("参数值不符:" + ",".join(detail["bad_values"]))
        if detail.get("retry"):
            note.append("重试成功" if retry_ok else "重试未成功")
            note.append("调用序列:" + ">".join(detail.get("call_seq") or ["无"]))
        print(f"[{flag}] {cid}  tool={detail.get('called_tool')} "
              f"(expect {case['tool']})  args_ok={args_ok} "
              f"args_value_ok={args_value_ok}  "
              f"{(' | '.join(note)) if note else ''}")
    return results


def summarize(results):
    total = len(results)
    if total == 0:
        return {"total": 0, "acc": 0.0}
    return {
        "total": total,
        "pass": sum(1 for r in results if r["pass"]),
        "acc": sum(1 for r in results if r["pass"]) / total,
        "tool_acc": sum(1 for r in results if r["tool_ok"]) / total,
        "args_acc": sum(1 for r in results if r["args_ok"]) / total,
        "args_value_acc": sum(1 for r in results if r["args_value_ok"]) / total,
        "retry_acc": sum(1 for r in results if r["retry"]) and
                     sum(1 for r in results if r["retry"] and r["retry_ok"]) /
                     max(1, sum(1 for r in results if r["retry"])),
    }


def load_cases(path):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = data["cases"]
    for c in cases:
        assert c.get("id") and c.get("prompt") and c.get("tool"), f"用例字段缺失: {c}"
    return cases


def main():
    ap = argparse.ArgumentParser(description="mini_claude 工具调用正确率评估 (v2)")
    ap.add_argument("--cases", default=os.path.join(os.path.dirname(__file__), "cases.yaml"))
    ap.add_argument("--repeat", type=int, default=1, help="运行轮数，用于抵消随机性（默认 1）")
    ap.add_argument("--only", action="append", default=None, metavar="ID",
                    help="只运行指定 id 的用例（可用多次），便于单测诊断")
    ap.add_argument("--json", default=None, help="输出 JSON 报告的路径（可选）")
    args = ap.parse_args()

    cases = load_cases(args.cases)
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
        if not cases:
            print(f"没有匹配的用例: {args.only}")
            return
    n_retry = sum(1 for c in cases if c.get("retry"))
    print(f"共 {len(cases)} 个用例 (normal {len(cases) - n_retry} + retry {n_retry}), "
          f"repeat={args.repeat}")
    print(f"  normal: 每用例 1 次 LLM 调用；retry: 每用例最多 {MAX_CALLS + 1} 次\n")

    per_case_scores = {c["id"]: [] for c in cases}
    for run in range(1, args.repeat + 1):
        if args.repeat > 1:
            print(f"--- 第 {run}/{args.repeat} 轮 ---")
        results = run_once(cases)
        for r in results:
            per_case_scores[r["id"]].append(1.0 if r["pass"] else 0.0)

    case_stats = {}
    overall_acc = []
    for cid, scores in per_case_scores.items():
        acc_avg = statistics.fmean(scores)
        case_stats[cid] = acc_avg
        overall_acc.append(acc_avg)
    overall = statistics.fmean(overall_acc) if overall_acc else 0.0

    if args.json:
        payload = {
            "repeat": args.repeat,
            "overall_acc": round(overall, 4),
            "per_case": {k: round(v, 4) for k, v in case_stats.items()},
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 报告已写入: {args.json}")

    print("\n=== 汇总 ===")
    print(f"repeat={args.repeat}，整体正确率(按用例平均): {overall:.1%}")
    for cid, v in case_stats.items():
        print(f"  {cid:26s} {v:.1%}")


if __name__ == "__main__":
    main()