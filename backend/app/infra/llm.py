"""LLM 客户端：统一构造 OpenAI 兼容客户端与带重试的补全调用。

所有需要调模型的模块（agent 循环、上下文压缩、查询改写、健康探测）都走这里，
避免各自 new OpenAI、各自读配置。
"""

from __future__ import annotations

from openai import OpenAI

from app.infra.config import Settings, get_settings, require_api_key
from app.infra.retry import with_retry


def build_client(settings: Settings | None = None) -> OpenAI:
    """构造客户端；未配置 api_key 时抛出带指引的 ConfigError。"""
    s = settings or get_settings()
    return OpenAI(api_key=require_api_key(s), base_url=s.base_url)


def chat_completion(messages: list[dict], settings: Settings | None = None, **kwargs):
    """非流式补全（默认模型取配置），网络类错误按指数退避重试。"""
    s = settings or get_settings()
    client = build_client(s)
    return with_retry(
        lambda: client.chat.completions.create(model=s.model, messages=messages, **kwargs)
    )
