"""
网络错误重试工具。

对 LLM API 调用（agent 循环、/compact）统一提供指数退避重试。
只重试可恢复的网络类错误，4xx（除 429 外）直接向上传播。
"""

import time
from openai import (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)

# 可重试的网络类错误
RETRYABLE_ERRORS = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
)


def with_retry(fn, max_retries: int = 3, base_delay: float = 1.0):
    """执行 fn，遇到网络类错误时指数退避重试。

    参数：
        fn: 无参可调用对象，包装了实际的 LLM 调用
        max_retries: 最大重试次数（默认 3）
        base_delay: 首次重试前等待秒数（默认 1s，后续翻倍）

    返回：
        fn() 的返回值

    抛出：
        fn() 抛出的最后一个异常（重试耗尽，或不可重试的 4xx 错误）
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except RETRYABLE_ERRORS as e:
            last_exception = e
            if attempt < max_retries:
                delay = base_delay * (2**attempt)
                time.sleep(delay)
        # 400/401/403 等不可重试错误不在这里捕获，直接向上传播
    raise last_exception  # type: ignore[misc]
