"""会话运行时与注册表。

解决旧实现的两个问题：
1. 数据竞争：旧代码用一把全局锁保护 _sessions 字典，但 ChatSession.messages 本身
   被 worker 线程和读接口并发读写。这里每个会话有独立的一把锁，一轮对话期间
   独占，且内存缓冲是该会话的唯一权威副本（读接口走数据库，不读内存缓冲）。
2. 会话无 TTL 导致内存泄漏：运行时按「空闲超时」与「最大数量」回收，
   回收时释放消息缓冲（只回收当前没人在用的，正在跑的会话不动）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

DEFAULT_TTL_SECONDS = 30 * 60      # 空闲 30 分钟回收运行时
DEFAULT_MAX_RUNTIMES = 200         # 运行时数量上限


@dataclass
class SessionRuntime:
    """内存中的会话运行时：消息缓冲 + 独占锁。"""

    session_id: str
    messages: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = field(default_factory=time.monotonic)


class SessionRegistry:
    """进程级会话运行时表（重启即失，历史以数据库为准）。"""

    def __init__(
        self,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_runtimes: int = DEFAULT_MAX_RUNTIMES,
    ):
        self._items: dict[str, SessionRuntime] = {}
        self._guard = threading.Lock()
        self._ttl = ttl_seconds
        self._max = max_runtimes

    # ── 取用 ──

    def acquire(self, session_id: str, *, blocking: bool = False) -> SessionRuntime | None:
        """取运行时并上锁；锁被占用说明该会话正在跑，返回 None（由调用方回 409）。"""
        runtime = self._obtain(session_id)
        if not runtime.lock.acquire(blocking=blocking):
            return None
        runtime.last_used = time.monotonic()
        return runtime

    def _obtain(self, session_id: str) -> SessionRuntime:
        with self._guard:
            runtime = self._items.get(session_id)
            if runtime is None:
                runtime = SessionRuntime(session_id=session_id)
                self._items[session_id] = runtime
                self._evict_locked()
            return runtime

    # ── 回收 ──

    def invalidate(self, session_id: str) -> None:
        """会话被删除时丢弃其运行时。"""
        with self._guard:
            self._items.pop(session_id, None)

    def evict_idle(self) -> int:
        """回收空闲超时的运行时，返回回收数量。"""
        with self._guard:
            return self._evict_idle_locked()

    def _evict_locked(self) -> None:
        self._evict_idle_locked()
        if len(self._items) <= self._max:
            return
        # 超出容量：最久未用的先回收
        for session_id, runtime in sorted(self._items.items(), key=lambda kv: kv[1].last_used):
            if len(self._items) <= self._max:
                break
            if self._is_free(runtime):
                self._items.pop(session_id, None)

    def _evict_idle_locked(self) -> int:
        now = time.monotonic()
        removed = 0
        for session_id, runtime in list(self._items.items()):
            if now - runtime.last_used <= self._ttl:
                continue
            if self._is_free(runtime):
                self._items.pop(session_id, None)
                removed += 1
        return removed

    @staticmethod
    def _is_free(runtime: SessionRuntime) -> bool:
        """能立刻拿到锁，说明这一轮没人在跑，可以安全回收。"""
        if runtime.lock.acquire(blocking=False):
            runtime.lock.release()
            return True
        return False

    # ── 观测 ──

    def stats(self) -> dict:
        with self._guard:
            return {
                "runtimes": len(self._items),
                "cached_messages": sum(len(rt.messages) for rt in self._items.values()),
                "ttl_seconds": self._ttl,
                "max_runtimes": self._max,
            }
