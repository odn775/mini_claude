"""知识库服务：索引状态查询与后台重建（同一时间只允许一个重建任务）。"""

from __future__ import annotations

import threading

from app.infra.config import Settings
from app.infra.knowledge import build_index, get_index_info

_lock = threading.Lock()
_building = False
_last_error: str | None = None


def status(settings: Settings) -> dict:
    with _lock:
        building = _building
        last_error = _last_error
    info = get_index_info(settings)
    return {
        "building": building,
        "last_error": last_error,
        "exists": info.get("exists", False),
        "total_chunks": info.get("total_chunks", 0),
        "files": info.get("files", []),
    }


def rebuild(settings: Settings) -> bool:
    """触发索引重建；已有任务在跑则返回 False。"""
    global _building, _last_error
    with _lock:
        if _building:
            return False
        _building = True
        _last_error = None

    def _build():
        global _building, _last_error
        try:
            build_index(settings)
            _last_error = None
        except Exception as e:
            _last_error = str(e)
        finally:
            _building = False

    threading.Thread(target=_build, daemon=True).start()
    return True
