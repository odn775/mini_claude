import json
import os
import re
from openai import OpenAI


EMBEDDING_MODEL = "text-embedding-v3"
DEFAULT_CHUNK_SIZE = 500
SEARCH_TOP_K = 3


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
    """调用阿里百炼 text-embedding API，返回向量列表。"""
    client = OpenAI(
        api_key=config["api_key"],
        base_url=config["base_url"],
    )
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [d.embedding for d in response.data]


# ── 索引构建 ──

def build_index(config: dict, knowledge_dir: str, index_dir: str) -> str:
    """扫描知识库目录下的 .txt/.md 文件，切块 → embedding → FAISS。"""
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

    # 读文件 + 切块
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

    # 批量 embedding（阿里百炼限制每批最多 10 条）
    embeddings = []
    batch_size = 10
    for i in range(0, len(texts_to_embed), batch_size):
        batch = texts_to_embed[i:i + batch_size]
        embeddings.extend(_get_embedding(batch, config))

    # 构建 FAISS 索引
    dim = len(embeddings[0])
    index = faiss.IndexFlatL2(dim)
    vectors = np.array(embeddings, dtype=np.float32)
    index.add(vectors)

    # 写入磁盘
    os.makedirs(index_dir, exist_ok=True)
    faiss.write_index(index, os.path.join(index_dir, "index.faiss"))
    with open(os.path.join(index_dir, "chunks.json"), "w", encoding="utf-8") as f:
        json.dump(all_chunks, f, ensure_ascii=False, indent=2)

    return f"索引构建完成: {len(files)} 个文件 → {len(all_chunks)} 个文本块 → {dim} 维向量"


# ── 检索 ──

def search(query: str, config: dict, index_dir: str, top_k: int = SEARCH_TOP_K) -> list[dict]:
    """在知识库中检索与 query 最相关的 Top-K 文本块。"""
    import numpy as np
    import faiss

    index_path = os.path.join(index_dir, "index.faiss")
    chunks_path = os.path.join(index_dir, "chunks.json")

    if not os.path.exists(index_path) or not os.path.exists(chunks_path):
        return []

    index = faiss.read_index(index_path)
    with open(chunks_path, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    query_embedding = _get_embedding([query], config)[0]
    query_vec = np.array([query_embedding], dtype=np.float32)

    distances, indices = index.search(query_vec, min(top_k, len(chunks)))

    results = []
    for dist, idx in zip(distances[0], indices[0]):
        if 0 <= idx < len(chunks):
            results.append({
                "content": chunks[idx]["content"],
                "source": chunks[idx]["source"],
                "relevance": float(dist),
            })

    return results


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
