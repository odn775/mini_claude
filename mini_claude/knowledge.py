import json
import os
import re
import requests


EMBEDDING_MODEL = "multimodal-embedding-v1"
DEFAULT_CHUNK_SIZE = 500
DEFAULT_OVERLAP = 50
# ── 检索参数 ──
COARSE_TOP_K = 20   # 每个 query 变体 embedding 粗筛条数
BM25_TOP_K = 20     # 每个 query 变体 BM25 召回条数
FINAL_TOP_K = 5     # 最终返回条数
QUERY_VARIANTS = 2  # 改写出的 query 变体数量（不含原始 query）
RRF_K = 60          # RRF 融合常数
MAX_BATCH_CHARS = 10000  # multimodal-embedding API 单批总字符上限 10240，留余量

_EMBEDDING_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"


# ── 文本切块 ──

def split_chunks(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """按段落边界将文本切分成大小均匀的块，相邻块之间有 overlap 字符重叠。"""
    paragraphs = re.split(r"\n\n+", text)
    raw_chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current) + len(para) <= chunk_size:
            current += para + "\n\n"
        else:
            if current.strip():
                raw_chunks.append(current.strip())
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
                            raw_chunks.append(acc.strip())
                        # 单句超大则强制截断
                        if len(s) > chunk_size:
                            for i in range(0, len(s), chunk_size):
                                raw_chunks.append(s[i:i + chunk_size])
                            acc = ""
                        else:
                            acc = s
                if acc.strip():
                    raw_chunks.append(acc.strip())
            else:
                raw_chunks.append(para)

    if current.strip():
        raw_chunks.append(current.strip())

    # ── 添加 overlap ──
    if overlap <= 0:
        return [c for c in raw_chunks if c]

    overlapped = []
    for i, chunk in enumerate(raw_chunks):
        if not chunk:
            continue
        if i > 0:
            prev = raw_chunks[i - 1]
            tail = prev[-overlap:] if len(prev) >= overlap else prev
            chunk = tail + "\n\n" + chunk
        overlapped.append(chunk)

    return overlapped


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

        chunks = split_chunks(content, chunk_size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_OVERLAP)
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


# ── 检索（多 query 改写 → 稠密 + 稀疏双通道 → RRF 融合 → Top-K）──

def _multi_query_rewrite(query: str, config: dict) -> list[str]:
    """多 query 改写：用 LLM 生成 2 个检索变体。

    - 变体1：同义改写，换说法但保持语义，利于稠密检索。
    - 变体2：关键词密集的检索式短语，提取核心实体，利于 BM25 稀疏检索。
    返回不含原始 query 的变体列表（0~2 个）。改写失败返回空，由调用方退化。
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
                "你是检索查询改写助手。把用户的问题改写成 2 个不同的检索查询变体，"
                "每行一个，只输出两行，不要编号、不要解释。\n"
                "第 1 行：同义改写，换一种说法但保持语义一致，写成自然的问句或陈述句。\n"
                "第 2 行：关键词密集的检索式短语，提取人名、地名、事件、物品、章节等核心实体，"
                "写成紧凑短语（如「韩立 南宫婉 结丹」），不要修饰词。"
            ),
        }, {
            "role": "user",
            "content": query,
        }],
        max_tokens=200,
        temperature=0.3,
    )
    text = (resp.choices[0].message.content or "").strip()
    variants = []
    for line in text.splitlines():
        line = _clean_variant(line)
        if line:
            variants.append(line)
        if len(variants) >= QUERY_VARIANTS:
            break
    return variants


def _clean_variant(line: str) -> str:
    """去掉模型输出行首的编号/列表符号（如 "1." "-" "•"）。"""
    line = line.strip()
    line = re.sub(r"^[\d\-\*••▪]+\s*[\.、．\))]?\s*", "", line)
    return line.strip()


def _build_bm25(chunks: list[dict]):
    """现场构建 BM25 索引：jieba 分词后喂给 rank_bm25。"""
    import jieba
    from rank_bm25 import BM25Okapi

    tokenized_docs = [list(jieba.cut(c["content"])) for c in chunks]
    return BM25Okapi(tokenized_docs)


def _bm25_search(bm25, query: str, chunks: list[dict], top_k: int = BM25_TOP_K) -> list[dict]:
    """用 BM25 对 query 打分，返回 top_k 个 {index} 命中（按分数降序）。"""
    import jieba

    tokens = [t for t in jieba.cut(query) if t.strip()]
    if not tokens:
        return []

    scores = bm25.get_scores(tokens)
    order = [i for i in range(len(scores)) if scores[i] > 0]
    order.sort(key=lambda i: scores[i], reverse=True)
    return [{"index": i} for i in order[:top_k]]


def _rrf_fuse(ranked_lists: list[list[dict]], k: int = RRF_K) -> dict[int, float]:
    """单层 RRF：把多个排序列表（每项含 index）融合成 {index: 分数}。

    score = Σ 1/(k + rank)，对稠密/稀疏两种打分尺度鲁棒，无需归一化。
    """
    scores: dict[int, float] = {}
    for lst in ranked_lists:
        for rank, item in enumerate(lst):
            idx = item["index"]
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank)
    return scores


def search(query: str, config: dict, index_dir: str, top_k: int = FINAL_TOP_K) -> list[dict]:
    """【检索管线】
    ① 多 query 改写 → ② 每个变体跑稠密(FAISS) + 稀疏(BM25) → ③ 单层 RRF 融合 → Top-K。
    任一环节失败都会降级：改写失败只用原 query，单变体失败丢弃该变体。
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

    # ── ① 多 query 改写（失败则退化只用原始 query）──
    queries = [query]
    try:
        for v in _multi_query_rewrite(query, config):
            v = v.strip()
            if v and v != query and v not in queries:
                queries.append(v)
    except Exception:
        pass  # 改写失败：仅用原始 query

    # ── ② BM25 现场构建 ──
    bm25 = _build_bm25(chunks)

    # ── ③ 每个变体跑双通道 ──
    ranked_lists: list[list[dict]] = []
    for q in queries:
        # 稠密通道
        try:
            q_emb = _get_embedding([q], config)[0]
            q_vec = np.array([q_emb], dtype=np.float32)
            distances, indices = index.search(q_vec, min(COARSE_TOP_K, len(chunks)))
            dense_list = [
                {"index": int(idx)}
                for dist, idx in zip(distances[0], indices[0])
                if 0 <= idx < len(chunks)
            ]
            ranked_lists.append(dense_list)
        except Exception:
            pass  # 该变体 embedding 失败，丢弃

        # 稀疏通道
        sparse_list = _bm25_search(bm25, q, chunks)
        if sparse_list:
            ranked_lists.append(sparse_list)

    # ── ④ RRF 融合 → Top-K ──
    fused = _rrf_fuse(ranked_lists)
    if not fused:
        return []

    final_idx = sorted(fused, key=fused.get, reverse=True)[:top_k]
    return [
        {
            "content": chunks[i]["content"],
            "source": chunks[i]["source"],
            "relevance": fused[i],
        }
        for i in final_idx
    ]


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
