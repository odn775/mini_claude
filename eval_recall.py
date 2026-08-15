"""评估脚本：黄金测试集上计算 新旧检索管线 的 Recall@1 / @3 / @5。

黄金集 = 14 条 query，每条标注标准答案短语（取自语料，脚本会先校验存在）。
命中规则：gold 短语出现在返回 top-K 任一 chunk 的 content 中即命中。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mini_claude.config import get_config
from mini_claude.knowledge import search as new_search
from compare_retrieval import _old_search  # 旧管线快照

INDEX_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "faiss_index")

GOLDEN = [
    # (query, [gold 短语]，任一出现即命中)
    ("Lyocell长丝的干断裂强度是多少", ["4.11"]),
    ("Lyocell长丝和粘胶强力丝哪个断裂伸长率更低", ["12.81"]),
    ("粘胶强力丝的平衡回潮率是多少", ["13.00"]),
    ("为什么Lyocell长丝的模量更高", ["结晶度"]),
    ("测定纤维回潮率依据哪个标准", ["GB/T 6503-2017"]),
    ("两种纤维在循环拉伸下谁更容易发生塑性形变", ["塑性形变"]),
    ("论文用了哪些手段研究两种长丝的结构差异", ["原纤化"]),
    ("Lyocell长丝的结晶度是百分之多少", ["69"]),
    ("原纤化实验中超声波的功率是多少", ["500 W"]),
    ("烘干机风量平衡的计算以什么为基准", ["质量流量"]),
    ("韩立多少岁结丹成功", ["126"]),
    ("韩立的掌天瓶是在哪里获得的", ["后山"]),
    ("这篇论文主要研究什么", ["结构性能差异"]),
    ("粘胶强力丝主要用在什么领域", ["帘子线"]),
]


def load_chunks():
    with open(os.path.join(INDEX_DIR, "chunks.json"), encoding="utf-8") as f:
        return json.load(f)


def first_hit_rank(gold_phrases, results):
    """返回 gold 短语第一次命中的 1-based rank，未命中返回 None。"""
    for rank, r in enumerate(results, 1):
        if any(p in r["content"] for p in gold_phrases):
            return rank
    return None


def main():
    config = get_config()
    chunks = load_chunks()
    corpus = "\n".join(c["content"] for c in chunks)

    # ── 校验黄金集可信：答案短语必须存在于语料 ──
    print("黄金集校验:")
    missing = []
    for q, phrases in GOLDEN:
        ok = [p for p in phrases if p in corpus]
        if not ok:
            missing.append((q, phrases))
            print(f"  [缺失] {q!r} 短语 {phrases}")
        else:
            print(f"  [OK]   {q!r} -> {ok}")
    if missing:
        print(f"\n警告: {len(missing)} 条 query 的答案短语不在语料中，黄金集不可信，中止。")
        sys.exit(1)
    print(f"黄金集校验通过: {len(GOLDEN)} 条全部可在语料中找到答案\n")

    # ── 逐条跑新旧管线 ──
    stats = {"new": [], "old": []}
    for q, phrases in GOLDEN:
        new_r = new_search(q, config, INDEX_DIR)
        old_r = _old_search(q, config, INDEX_DIR)
        nh = first_hit_rank(phrases, new_r)
        oh = first_hit_rank(phrases, old_r)
        stats["new"].append(nh)
        stats["old"].append(oh)
        nhs = str(nh) if nh else "未命中"
        ohs = str(oh) if oh else "未命中"
        print(f"{q!r}")
        print(f"    新命中rank={nhs}   旧命中rank={ohs}")
        if nh and (not oh or nh < oh):
            print(f"      新胜: top-{nh} = {[r['content'][:36].replace(chr(10),' ') for r in new_r[:nh]][-1]}")
        elif oh and (not nh or oh < nh):
            print(f"      旧胜: top-{oh} = {[r['content'][:36].replace(chr(10),' ') for r in old_r[:oh]][-1]}")
    print()

    # ── 汇总 Recall@K ──
    print("=" * 60)
    print("Recall@K 汇总（新 / 旧）:")
    for K in (1, 3, 5):
        n_new = sum(1 for r in stats["new"] if r and r <= K)
        n_old = sum(1 for r in stats["old"] if r and r <= K)
        N = len(GOLDEN)
        print(f"  Recall@{K:<2}: 新 {n_new}/{N} = {n_new/N*100:.1f}%   |   旧 {n_old}/{N} = {n_old/N*100:.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
