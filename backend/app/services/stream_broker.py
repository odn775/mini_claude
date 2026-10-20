"""SSE 事件流分发：有界队列 + 断线不中止对话。

为什么要限容量：旧的实现用了无界 queue.Queue，客户端断开或消费停滞时
事件会一直堆积在内存里。这里的策略是：
- 队列满时优先丢弃最老的 text_delta（少几段流式文本不影响最终 text 事件）；
- 若没有可丢的 text_delta，则丢最老的事件并置 dropped 标记，
  消费端会收到一条 notice 告知内容被截断。
"""

from __future__ import annotations

import queue
import threading
from typing import Any

DEFAULT_CAPACITY = 2000


class _Close:
    """关流哨兵（与「超时返回 None」区分开）。"""

    def __repr__(self) -> str:  # pragma: no cover
        return "<STREAM_CLOSED>"


CLOSED = _Close()


class EventStream:
    """一轮对话的事件队列：worker 线程写入，SSE 生成器消费。"""

    def __init__(self, capacity: int = DEFAULT_CAPACITY):
        self._capacity = capacity
        self._q: queue.Queue[Any] = queue.Queue(maxsize=capacity)
        self._dropped = False
        self._closed = threading.Event()

    def put(self, event: dict[str, Any]) -> None:
        """写入事件；满则按上面的策略腾位置，绝不阻塞 worker 线程。"""
        while True:
            try:
                self._q.put_nowait(event)
                return
            except queue.Full:
                self._make_room()

    def _make_room(self) -> None:
        """腾出一个位置：优先丢最老的 text_delta，否则丢最老的非哨兵事件。"""
        items: list[Any] = []
        while True:
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break

        drop_index = None
        for i, ev in enumerate(items):
            if isinstance(ev, dict) and ev.get("type") == "text_delta":
                drop_index = i
                break
        if drop_index is None:
            self._dropped = True
            for i, ev in enumerate(items):
                if ev is not CLOSED:
                    drop_index = i
                    break
        if drop_index is None:
            # 队列里只有哨兵，直接放回去即可
            for ev in items:
                self._q.put_nowait(ev)
            return

        for i, ev in enumerate(items):
            if i != drop_index:
                self._q.put_nowait(ev)

    def dropped_notice(self) -> dict[str, Any] | None:
        """内容被截断时给消费端的一条提示（每次只报一次）。"""
        if not self._dropped:
            return None
        self._dropped = False
        return {"type": "notice", "message": "事件过多，部分流式文本已丢弃（最终回答不受影响）"}

    def get(self, timeout: float = 1.0) -> Any:
        """消费一个事件；返回 dict / CLOSED 哨兵 / 超时的 None。"""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        """写入结束哨兵，让消费端正常收尾（幂等）。"""
        if self._closed.is_set():
            return
        self._closed.set()
        for _ in range(2):
            try:
                self._q.put_nowait(CLOSED)
                return
            except queue.Full:
                self._make_room()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()
