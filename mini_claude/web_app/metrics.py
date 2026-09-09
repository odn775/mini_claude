"""用量埋点存储：SQLite 记录每轮回答的 token 用量与缓存命中。

表 turns：一行 = 一次 run_turn 的累计 usage（agent 的 usage 事件原样落库）。
图表按会话聚合时只取「当前内存中的会话」，即只展示本次启动以来的对话；
历史行继续留在库里，供后续跨重启统计使用。
"""

import os
import sqlite3
import threading
import time
from contextlib import closing

DB_PATH = os.path.join(os.path.expanduser("~"), ".mini_claude", "usage.db")

# 写操作串行化（读走 SQLite 自身的文件锁即可）
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def _init() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(_conn()) as c:
        with c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cached_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                )
                """
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id)"
            )


_init()


def record_turn(session_id: str, usage: dict) -> None:
    """一次 run_turn 结束后写入其累计 usage。"""
    with _lock, closing(_conn()) as c:
        with c:
            c.execute(
                """
                INSERT INTO turns
                    (session_id, input_tokens, output_tokens, cached_tokens, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("cached_tokens", 0),
                    time.time(),
                ),
            )


def session_usage(session_ids: list[str]) -> dict[str, dict]:
    """按会话聚合 usage 总量；只返回有数据行的会话。

    返回 {session_id: {input_tokens, output_tokens, cached_tokens}}
    """
    if not session_ids:
        return {}
    placeholders = ",".join("?" * len(session_ids))
    with closing(_conn()) as c:
        rows = c.execute(
            f"""
            SELECT session_id,
                   SUM(input_tokens), SUM(output_tokens), SUM(cached_tokens)
            FROM turns
            WHERE session_id IN ({placeholders})
            GROUP BY session_id
            """,
            session_ids,
        ).fetchall()
    return {
        sid: {
            "input_tokens": i or 0,
            "output_tokens": o or 0,
            "cached_tokens": ch or 0,
        }
        for sid, i, o, ch in rows
    }
