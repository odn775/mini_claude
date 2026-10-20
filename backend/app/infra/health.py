"""
检测 / 健康检查模块。

三个入口：
- ``/health`` 命令：全静态体检（config / api / mcp / knowledge / tools / skills），零成本秒回
- ``/ping`` 命令：活体探测（net / llm / embedding 三个探针），会消耗少量 token
- ``check_health`` 工具：模型可调用的文本版体检，出错时做差分诊断

异常自动诊断：入口捕获异常时调 ``classify_exception`` + ``check_all``，
定位疑似模块并给出下一步建议。

本模块只产生结构化数据（dict / list），不含 ANSI 颜色——展示层由入口负责。
MCP 状态共享：入口创建 MCPManager 后调 ``set_mcp_manager``，
这样命令路径和工具路径都能看到真实连接状态。
"""

import json
import os
import socket
import ssl
import time
import urllib.parse

from app.infra.config import BACKEND_DIR, Settings, get_settings
from app.infra.paths import index_dir, knowledge_dir, mcp_config_file, skills_dir

# 每个 check 的返回格式：{"name", "status", "detail"}
# status 取值：ok / warn / error / skip（skip 表示检查本身抛异常被隔离）
_STATUS = ("ok", "warn", "error", "skip")

# 由入口注入，工具路径（check_health）拿不到入口的局部变量
_mcp_manager = None


def set_mcp_manager(mgr) -> None:
    """由入口在创建 MCPManager 后调用，注入连接状态。"""
    global _mcp_manager
    _mcp_manager = mgr


# ── 单项检查 ──

def check_config() -> dict:
    """检查配置来源：.env / 环境变量是否提供了 API Key。"""
    s = get_settings()
    env_file = BACKEND_DIR / ".env"
    if not (s.api_key or "").strip():
        where = "backend/.env 不存在" if not env_file.exists() else "backend/.env 中 DASHSCOPE_API_KEY 为空"
        return {"name": "config", "status": "error",
                "detail": f"未配置 API Key（{where}，且环境变量未设置）"}
    return {"name": "config", "status": "ok", "detail": "API Key 已配置"}


def check_api() -> dict:
    """静态检查连接配置：base_url 合法 + key 已配置（不真发请求）。"""
    s = get_settings()
    if not (s.api_key or "").strip():
        return {"name": "api", "status": "error", "detail": "未配置 API Key"}
    if s.base_url and not s.base_url.startswith(("http://", "https://")):
        return {"name": "api", "status": "warn", "detail": f"base_url 格式可疑: {s.base_url}"}
    return {"name": "api", "status": "ok",
            "detail": f"base_url: {s.base_url or '(默认)'} · model: {s.model}"}


def check_mcp() -> dict:
    """检查 mcp.json 配置与实际连接状态。

    空文件 / 未配置服务器 = 未配置 MCP（OK），不算错误；
    非空但无法解析、或顶层不是 JSON 对象才算损坏。
    """
    path = str(mcp_config_file())
    if not os.path.exists(path):
        return {"name": "mcp", "status": "ok", "detail": "未配置 MCP（mcp.json 不存在）"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except (IOError, OSError) as e:
        return {"name": "mcp", "status": "error", "detail": f"mcp.json 读取失败: {e}"}
    if not raw.strip():
        return {"name": "mcp", "status": "ok", "detail": "未配置 MCP（mcp.json 为空）"}
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"name": "mcp", "status": "error", "detail": f"mcp.json 损坏: {e}"}
    if not isinstance(cfg, dict):
        return {"name": "mcp", "status": "error", "detail": "mcp.json 顶层不是 JSON 对象"}
    servers = dict(cfg.get("mcpServers", {}))
    if not servers:
        return {"name": "mcp", "status": "ok", "detail": "mcp.json 中未配置服务器"}

    connected = []
    if _mcp_manager is not None:
        try:
            connected = _mcp_manager.connected_servers()
        except Exception:
            connected = []

    if len(connected) == len(servers):
        status = "ok"
        detail = f"已连接 {len(connected)}/{len(servers)}: {', '.join(connected)}"
    elif connected:
        missing = ", ".join(sorted(set(servers) - set(connected)))
        status = "warn"
        detail = f"已连接 {len(connected)}/{len(servers)}，未连接: {missing}"
    else:
        status = "error"
        detail = f"配置了 {len(servers)} 个服务器，全部未连接: {', '.join(servers)}"
    return {"name": "mcp", "status": status, "detail": detail}


def check_knowledge() -> dict:
    """检查知识库索引：存在、块数/文件数、索引是否过期。"""
    kb_dir = str(knowledge_dir())
    idx_dir = str(index_dir())
    index_path = os.path.join(idx_dir, "index.faiss")
    chunks_path = os.path.join(idx_dir, "chunks.json")
    if not os.path.exists(index_path) or not os.path.exists(chunks_path):
        return {"name": "knowledge", "status": "warn",
                "detail": "索引未构建（运行 /kb rebuild）"}
    try:
        with open(chunks_path, "r", encoding="utf-8") as f:
            chunks = json.load(f)
    except Exception as e:
        return {"name": "knowledge", "status": "error", "detail": f"chunks.json 读取失败: {e}"}
    if not isinstance(chunks, list) or not chunks:
        return {"name": "knowledge", "status": "error", "detail": "chunks.json 内容为空或格式异常"}

    sources = sorted(set(c.get("source", "?") for c in chunks))
    detail = f"{len(chunks)} 块 · {len(sources)} 个文件"

    # 索引过期提醒：有源文件 mtime 晚于 index.faiss 则提示重建
    if os.path.isdir(kb_dir):
        try:
            index_mtime = os.path.getmtime(index_path)
            for fname in os.listdir(kb_dir):
                if fname.endswith((".txt", ".md")):
                    if os.path.getmtime(os.path.join(kb_dir, fname)) > index_mtime:
                        return {"name": "knowledge", "status": "warn",
                                "detail": detail + " · 索引过期（有源文件更新，建议重建）"}
        except OSError:
            pass
    return {"name": "knowledge", "status": "ok", "detail": detail}


def check_tools() -> dict:
    """检查工具定义与实现是否一一对应（防漏配）。"""
    from app.tools import TOOLS, TOOL_EXECUTORS

    defined = {t["function"]["name"] for t in TOOLS}
    implemented = set(TOOL_EXECUTORS.keys())
    missing = sorted(implemented - defined)
    dangling = sorted(defined - implemented)
    if missing or dangling:
        return {"name": "tools", "status": "error",
                "detail": f"定义与实现失配 · 定义缺: {missing or '无'} · 实现缺: {dangling or '无'}"}
    return {"name": "tools", "status": "ok", "detail": f"{len(defined)} 个工具定义与实现一一对应"}


def check_skills() -> dict:
    """检查 skills 目录：可解析、数量、有无截断。"""
    from app.infra.skills import list_skills

    root = str(skills_dir())
    if not os.path.isdir(root):
        return {"name": "skills", "status": "ok", "detail": "未配置 skill 目录"}
    try:
        skills = list_skills()
    except Exception as e:
        return {"name": "skills", "status": "error", "detail": f"解析失败: {e}"}
    if not skills:
        return {"name": "skills", "status": "warn", "detail": "skills 目录为空"}
    names = [s["name"] for s in skills]
    detail = f"{len(skills)} 个: {', '.join(names)}"
    truncated = [s["name"] for s in skills
                 if not s["description"] or s["description"].endswith("...")]
    if truncated:
        return {"name": "skills", "status": "warn",
                "detail": detail + f" · 以下 description 为空或超限被截断: {', '.join(truncated)}"}
    return {"name": "skills", "status": "ok", "detail": detail}


# ── 汇总 ──

def check_all() -> list[dict]:
    """运行全部静态检查，逐项隔离异常，返回结果列表。"""
    checks = (
        check_config, check_api, check_mcp, check_knowledge, check_tools, check_skills,
    )
    results = []
    for fn in checks:
        try:
            results.append(fn())
        except Exception as e:
            results.append({
                "name": fn.__name__.replace("check_", ""),
                "status": "skip",
                "detail": f"检查本身异常: {e}",
            })
    return results


def problems(results: list[dict]) -> list[dict]:
    """从 check_all 结果中筛出非 ok 的项（自动诊断只展示这些）。"""
    return [r for r in results if r["status"] in ("warn", "error")]


# ── 异常归属 ──

def classify_exception(e: Exception | str) -> list[dict]:
    """根据异常类型推断疑似模块，返回 [{'module', 'reason', 'suggestion'}]。

    参数可以是异常对象，也可以是异常类名（事件流里的 error 事件只带类名，
    CLI 侧据此做同样的自动诊断）。
    """
    name = e if isinstance(e, str) else type(e).__name__
    message = "" if isinstance(e, str) else str(e)

    if name in ("APIConnectionError", "APITimeoutError"):
        return [{"module": "api/网络", "reason": "LLM API 连接失败或超时",
                 "suggestion": "运行 /ping 验证网络与 API 可用性"}]
    if name == "RateLimitError":
        return [{"module": "api/配额", "reason": "触发限流或配额不足",
                 "suggestion": "稍后重试，或检查阿里百炼账户额度"}]
    if name == "InternalServerError":
        return [{"module": "api/服务端", "reason": "模型服务端错误",
                 "suggestion": "稍后重试"}]
    if name == "AuthenticationError":
        return [{"module": "config", "reason": "API 鉴权失败（key 无效）",
                 "suggestion": "检查 backend/.env 中的 DASHSCOPE_API_KEY"}]
    if name == "JSONDecodeError":
        return [{"module": "config", "reason": "JSON 解析失败",
                 "suggestion": "检查对应的配置文件格式"}]
    return [{"module": "未知", "reason": message or name,
             "suggestion": "运行 /health 查看各模块体检状态"}]


# ── /ping 活体探测 ──

def ping(settings: Settings | None = None) -> list[dict]:
    """三探针活体探测，逐条独立 try/except，返回 [{name, ok, detail, ms}]。"""
    s = settings or get_settings()
    results = []

    start = time.time()
    ok, detail = _probe_net(s.base_url)
    results.append({"name": "net", "ok": ok, "detail": detail,
                    "ms": int((time.time() - start) * 1000)})

    start = time.time()
    ok, detail = _probe_llm(s)
    results.append({"name": "llm", "ok": ok, "detail": detail,
                    "ms": int((time.time() - start) * 1000)})

    start = time.time()
    ok, detail = _probe_embedding(s)
    results.append({"name": "embedding", "ok": ok, "detail": detail,
                    "ms": int((time.time() - start) * 1000)})

    return results


def _probe_net(base_url: str) -> tuple[bool, str]:
    """检查 base_url 主机的 TCP+TLS 可达性（不发送业务请求，0 token）。"""
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.hostname
    if not host:
        return False, f"无法解析 base_url: {base_url}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                return True, f"可达 {host}:{port} (TLS {tls.version()})"
    except Exception as e:
        return False, f"不可达: {e}"


def _probe_llm(settings: Settings) -> tuple[bool, str]:
    """最小 chat.completions 请求（max_tokens=1），验证 key/模型/额度。"""
    from openai import OpenAI

    try:
        client = OpenAI(api_key=settings.api_key, base_url=settings.base_url)
        client.chat.completions.create(
            model=settings.model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        return True, f"OK ({settings.model})"
    except Exception as e:
        return False, f"失败: {e}"


def _probe_embedding(settings: Settings) -> tuple[bool, str]:
    """单个文本调 embedding API，验证 embedding 模型与额度。"""
    from app.infra.knowledge import _get_embedding

    try:
        _get_embedding(["ping"], settings)
        return True, "OK (multimodal-embedding-v1)"
    except Exception as e:
        return False, f"失败: {e}"
