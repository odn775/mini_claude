"""一次性脚本：对比 旧检索管线（单次改写+子串计数） vs 新检索管线（多query+BM25+RRF）。

用法:
    python compare_retrieval.py

内嵌旧管线快照（git HEAD 时的 _rewrite_query/_keyword_search/search），
新管线直接从 mini_claude.knowledge.search 导入。会调用真实 API（LLM 改写 + embedding）。
"""
import json
import os
import sys

# Windows 控制台默认 GBK，强制 UTF-8 输出避免中文乱码 / emoji 报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mini_claude.config import get_config
from mini_claude.knowledge import search as new_search

INDEX_DIR = os.path.join(os.path.expanduser("~"), ".mini_claude", "faiss_index")
COARSE_TOP_K = 20
FINAL_TOP_K = 5
KEYWORD_TOP_K = 10

# ═══════════════════ 旧管线快照 ═══════════════════

def _old_rewrite_query(query, config):
    """旧：单次改写 → 空格分隔关键词。"""
    from openai import OpenAI
    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
    resp = client.chat.completions.create(
        model=config["model"],
        messages=[{
            "role": "system",
            "content": (
                "你是检索查询改写助手。将用户的问题转成密集的搜索关键词，"
                "提取出人名、地名、事件、物品、章节等核心实体，用空格分隔。"
                "只输出关键词，不要解释。"
            ),
        }, {"role": "user", "content": query}],
        max_tokens=100,
        temperature=0.1,
    )
    return resp.choices[0].message.content.strip()


def _old_keyword_search(query_keywords, chunks, top_k=KEYWORD_TOP_K):
    """旧：子串计数。"""
    terms = [t.strip() for t in query_keywords.replace("，", " ").replace(",", " ").split() if t.strip()]
    if not terms:
        return []
    scored = []
    for i, chunk in enumerate(chunks):
        score = sum(chunk["content"].count(t) for t in terms)
        if score > 0:
            scored.append((i, score))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [{"index": idx, "score": score} for idx, score in scored[:top_k]]


def _old_search(query, config, index_dir, top_k=FINAL_TOP_K):
    """旧：改写 → embedding 粗筛 + 关键词补漏 → 合并去重。"""
    import numpy as np
    import faiss

    index_path = os.path.join(index_dir, "index.faiss")
    chunks_path = os.path.join(index_dir, "chunks.json")
    index = faiss.read_index(index_path)
    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    try:
        keywords = _old_rewrite_query(query, config)
    except Exception:
        keywords = query

    q_emb = _get_emb([query], config)[0]
    distances, indices = index.search(np.array([q_emb], dtype=np.float32), min(COARSE_TOP_K, len(chunks)))

    kw_hits = _old_keyword_search(keywords, chunks)
    seen, final = set(), []
    for dist, idx in zip(distances[0], indices[0]):
        if 0 <= idx < len(chunks) and idx not in seen:
            seen.add(idx)
            final.append({"content": chunks[idx]["content"], "source": chunks[idx]["source"],
                          "relevance": float(1.0 / (1.0 + dist))})
    for kw in kw_hits:
        if kw["index"] not in seen:
            seen.add(kw["index"])
            final.append({"content": chunks[kw["index"]]["content"], "source": chunks[kw["index"]]["source"],
                          "relevance": float(kw["score"])})
    return final[:top_k]


def _get_emb(texts, config):
    """旧管线内嵌 embedding 调用（避免依赖 knowledge 内部函数）。"""
    import requests
    resp = requests.post(
        "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
        json={"model": "multimodal-embedding-v1", "input": {"contents": [{"text": t} for t in texts]}},
        timeout=60,
    )
    resp.raise_for_status()
    return [e["embedding"] for e in resp.json()["output"]["embeddings"]]


# ═══════════════════ 对比 ═══════════════════

QUERIES = [
    "他最后修炼到了什么境界",                       # 模糊语义（"他"指韩立）
    "南宫婉是谁",                                    # 人名专名
    "韩立是怎么得到青竹蜂云剑炼制之法的",            # 长问句 + 专名
    "谁收留了韩立做徒弟",                            # 同义改写（收留→收为弟子）
    "掌天瓶是什么来历",                              # 专名 + 模糊
    "老魔结丹花了多长时间",                          # 口语别名（老魔=韩立）
    "这份简历的主人会哪些技术",                      # 跨文档 + 口语
    "应聘者在杭州哪家公司实习",                      # 简历细节（地名+公司名）
]


def _fmt(results):
    lines = []
    for i, r in enumerate(results, 1):
        src = r["source"]
        content = r["content"].replace("\n", " ")[:60]
        lines.append(f"  {i}. [{src}] rel={r['relevance']:.4f}  {content}")
    return "\n".join(lines) if lines else "  (空)"


def main():
    config = get_config()
    print(f"索引: {INDEX_DIR}\n")
    for q in QUERIES:
        print("=" * 78)
        print(f"QUERY: {q}")
        try:
            old = _old_search(q, config, INDEX_DIR)
        except Exception as e:
            old = f"旧管线异常: {e}"
        new = new_search(q, config, INDEX_DIR)
        print("─ 旧（单次改写+子串计数）─")
        print(_fmt(old) if isinstance(old, list) else old)
        print("─ 新（多query+BM25+RRF）─")
        print(_fmt(new))
        print()


if __name__ == "__main__":
    main()
