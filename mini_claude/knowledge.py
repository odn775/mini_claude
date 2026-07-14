import json
import os
import re
import requests


EMBEDDING_MODEL = "multimodal-embedding-v1"
DEFAULT_CHUNK_SIZE = 2000
# ── 检索参数 ──
COARSE_TOP_K = 20   # embedding 粗筛条数（新策略：给 rerank 喂更大候选池）
FINAL_TOP_K = 5     # rerank 后最终返回条数
KEYWORD_TOP_K = 10  # 关键词检索补漏条数
MAX_BATCH_CHARS = 10000  # multimodal-embedding API 单批总字符上限 10240，留余量

RERANK_MODEL = "gte-rerank-v2"
_EMBEDDING_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
_RERANK_URL = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"


# ── 文本切块 ──

def split_chunks(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """按段落边界将文本切分成大小均匀的块。"""
    paragraphs = re.split(r"\n\n+", text)
    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) <= chunk_size:
            current += para + "\n\n"
        else:
            if current.strip():
                chunks.append(current.strip())
            current = ""
            # 长段落按句切
            if len(para) > chunk_size:
                sentences = re.split(r"(?<=[。！？.!?])", para)
                acc = ""
                for s in sentences:
                    s = s.strip()
                    if not s:
                        continue
                    if len(acc) + len(s) <= chunk_size:
                        acc += s
                    else:
                        if acc.strip():
                            chunks.append(acc.strip())
                        # 单句超大则强制截断
                        if len(s) > chunk_size:
                            for i in range(0, len(s), chunk_size):
                                chunks.append(s[i:i + chunk_size])
                            acc = ""
                        else:
                            acc = s
                if acc.strip():
                    chunks.append(acc.strip())
            else:
                chunks.append(para)

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]


# ── Embedding ──

def _get_embedding(texts: list[str], config: dict) -> list[list[float]]:
    """调用阿里百炼 multimodal-embedding API，返回向量列表。"""
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": EMBEDDING_MODEL,
        "input": {
            "contents": [{"text": t} for t in texts],
        },
    }
    resp = requests.post(_EMBEDDING_URL, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    output = data.get("output", {})
    if "embeddings" in output:
        return [e["embedding"] for e in output["embeddings"]]

    # 处理错误响应
    code = data.get("code", "unknown")
    message = data.get("message", str(data))
    raise RuntimeError(f"Embedding API 异常 [{code}]: {message}")


# ── 索引构建 ──

def build_index(config: dict, knowledge_dir: str, index_dir: str) -> str:
    """扫描知识库目录下的 .txt/.md 文件，切块 → embedding → FAISS。

    支持断点续传：每完成一批 embedding 就保存进度，即使中途
    额度用完或网络中断，下次 rebuild 时也会从断点继续。
    """
    import numpy as np
    import faiss

    if not os.path.isdir(knowledge_dir):
        return f"知识库目录不存在: {knowledge_dir}"

    files = []
    for fname in sorted(os.listdir(knowledge_dir)):
        if fname.endswith((".txt", ".md")):
            files.append(os.path.join(knowledge_dir, fname))

    if not files:
        return "知识库目录中没有 .txt 或 .md 文件"

    os.makedirs(index_dir, exist_ok=True)
    checkpoint_path = os.path.join(index_dir, "_embeddings_partial.npy")

    # ── 读文件 + 切块 ──
    all_chunks = []
    texts_to_embed = []

    for filepath in files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        chunks = split_chunks(content)
        for i, chunk in enumerate(chunks):
            all_chunks.append({
                "content": chunk,
                "source": os.path.basename(filepath),
                "chunk_index": i,
            })
            texts_to_embed.append(chunk)

    if not texts_to_embed:
        return "文档切块后无内容"

    # 先保存 chunks.json（即使 embedding 中断也不丢块）
    with open(os.path.join(index_dir, "chunks.json"), "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    # ── 尝试恢复断点 ──
    embeddings: list[list[float]] = []
    start_batch = 0

    if os.path.exists(checkpoint_path):
        try:
            saved = np.load(checkpoint_path)
            embeddings = [list(v) for v in saved]
            start_batch = len(embeddings)
        except Exception:
            pass

    # ── 批量 embedding ──
    total = len(texts_to_embed)
    i = start_batch
    while i < total:
        # 动态分组：累计字符不超 MAX_BATCH_CHARS，且每批最多 10 条
        batch = []
        batch_chars = 0
        while i < total and len(batch) < 10:
            chunk_len = len(texts_to_embed[i])
            if batch_chars + chunk_len > MAX_BATCH_CHARS and batch:
                break  # 再加会超限，本批次到此为止
            batch.append(texts_to_embed[i])
            batch_chars += chunk_len
            i += 1

        try:
            embeddings.extend(_get_embedding(batch, config))
        except Exception as e:
            # 保存当前进度
            if embeddings:
                np.save(checkpoint_path, np.array(embeddings, dtype=np.float32))
            return (
                f"Embedding 中断于第 {i}/{total} 条 (进度 {100*i/total:.1f}%)\n"
                f"原因: {e}\n"
                f"进度已保存，修正问题后执行 /kb rebuild 继续。"
            )

        # 每批保存进度
        np.save(checkpoint_path, np.array(embeddings, dtype=np.float32))

        if (i - start_batch) % 50 == 0 or i >= total:
            print(f"  embedding 进度: {len(embeddings)}/{total} ({100*len(embeddings)/total:.1f}%)")

    # ── 构建 FAISS 索引 ──
    dim = len(embeddings[0])
    index = faiss.IndexFlatL2(dim)
    vectors = np.array(embeddings, dtype=np.float32)
    index.add(vectors)

    faiss.write_index(index, os.path.join(index_dir, "index.faiss"))
    with open(os.path.join(index_dir, "chunks.json"), "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    # 清除断点文件
    if os.path.exists(checkpoint_path):
        os.unlink(checkpoint_path)

    return f"索引构建完成: {len(files)} 个文件 → {len(all_chunks)} 个文本块 → {dim} 维向量"


# ── 检索（新策略：改写 → 粗筛+关键词 → 合并 → Rerank → Top-K）──

def _rewrite_query(query: str, config: dict) -> str:
    """【新策略1：查询改写】
    用 LLM 将用户自然语言转成密集检索关键词。
    提取人名、地名、事件、物品等核心实体，空格分隔。
    """
    from openai import OpenAI
    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )
    resp = client.chat.completions.create(
        model=config["model"],
        messages=[{
            "role": "system",
            "content": (
                "你是检索查询改写助手。将用户的问题转成密集的搜索关键词，"
                "提取出人名、地名、事件、物品、章节等核心实体，用空格分隔。"
                "只输出关键词，不要解释。"
            ),
        }, {
            "role": "user",
            "content": query,
        }],
        max_tokens=100,
        temperature=0.1,
    )
    return resp.choices[0].message.content.strip()


def _keyword_search(query_keywords: str, chunks: list[dict], top_k: int = KEYWORD_TOP_K) -> list[dict]:
    """【新策略2：混合检索-关键词】
    在内存 chunks 中做子串匹配，补 embedding 可能遗漏的精确人名/地名匹配。
    用改写后的关键词逐项命中计数，不需要分词库。
    """
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


def _rerank(query: str, documents: list[str], config: dict, top_n: int) -> list[dict]:
    """【新策略3：Rerank 精排】
    用 gte-rerank-v2 对候选文档重新打分排序。
    """
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    body = {
        "model": RERANK_MODEL,
        "input": {
            "query": query,
            "documents": documents,
        },
        "parameters": {"top_n": min(top_n, len(documents))},
    }
    resp = requests.post(_RERANK_URL, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["output"]["results"]


def search(query: str, config: dict, index_dir: str, top_k: int = FINAL_TOP_K) -> list[dict]:
    """【检索管线】
    ① 查询改写 → ② embedding 粗筛 + ③ 关键词补漏 → ④ 合并去重 → ⑤ Rerank → Top-K 返回。
    """
    import numpy as np
    import faiss

    index_path = os.path.join(index_dir, "index.faiss")
    chunks_path = os.path.join(index_dir, "chunks.json")

    if not os.path.exists(index_path) or not os.path.exists(chunks_path):
        return []

    index = faiss.read_index(index_path)
    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    # ── ① 查询改写 ──
    try:
        keywords = _rewrite_query(query, config)
    except Exception:
        keywords = query  # 改写失败则退化为原 query

    # ── ② Embedding 粗筛（Top-20） ──
    query_embedding = _get_embedding([query], config)[0]
    query_vec = np.array([query_embedding], dtype=np.float32)
    distances, indices = index.search(query_vec, min(COARSE_TOP_K, len(chunks)))

    # ── ③ 关键词补漏 ──
    kw_hits = _keyword_search(keywords, chunks)

    # ── ④ 合并去重 ──
    seen: set[int] = set()
    merged_items: list[dict] = []       # 候选文档（content/source）
    merged_indices: list[int] = []      # 对应的原 chunks 索引

    for dist, idx in zip(distances[0], indices[0]):
        if 0 <= idx < len(chunks) and idx not in seen:
            seen.add(idx)
            merged_indices.append(idx)
            merged_items.append({"content": chunks[idx]["content"], "source": chunks[idx]["source"]})

    for kw in kw_hits:
        if kw["index"] not in seen:
            seen.add(kw["index"])
            merged_indices.append(kw["index"])
            merged_items.append({"content": chunks[kw["index"]]["content"], "source": chunks[kw["index"]]["source"]})

    if not merged_items:
        return []

    # ── ⑤ Rerank 精排 ──
    documents = [item["content"] for item in merged_items]
    try:
        rerank_results = _rerank(query, documents, config, top_n=min(top_k, len(documents)))
    except Exception:
        rerank_results = None

    # 构建最终结果
    final = []
    if rerank_results:
        for r in rerank_results[:top_k]:
            ri = r["index"]
            final.append({
                "content": merged_items[ri]["content"],
                "source": merged_items[ri]["source"],
                "relevance": r.get("relevance_score", 0.0),
            })
    else:
        # Rerank 不可用则直接返回合并后的前 top_k
        for item in merged_items[:top_k]:
            final.append({
                "content": item["content"],
                "source": item["source"],
                "relevance": 0.0,
            })

    return final


# ── 状态查询 ──

def get_index_info(index_dir: str) -> dict:
    """返回当前索引状态。"""
    chunks_path = os.path.join(index_dir, "chunks.json")
    index_path = os.path.join(index_dir, "index.faiss")

    if not os.path.exists(index_path) or not os.path.exists(chunks_path):
        return {"exists": False}

    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    sources = sorted(set(c["source"] for c in chunks))
    return {
        "exists": True,
        "total_chunks": len(chunks),
        "files": sources,
    }
