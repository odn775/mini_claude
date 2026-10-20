"""运行数据路径：skills 目录、知识库目录、索引目录、mcp.json 的唯一来源。

所有需要读写 ~/.mini_claude（可用 MINI_CLAUDE_DATA_DIR 覆盖）的模块都从这里取路径，
避免同一个目录在多处各写一份常量。
"""

from __future__ import annotations

from pathlib import Path

from app.infra.config import Settings, get_settings


def data_dir(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).data_dir


def skills_dir(settings: Settings | None = None) -> Path:
    return data_dir(settings) / "skills"


def knowledge_dir(settings: Settings | None = None) -> Path:
    return data_dir(settings) / "knowledge"


def index_dir(settings: Settings | None = None) -> Path:
    return data_dir(settings) / "faiss_index"


def mcp_config_file(settings: Settings | None = None) -> Path:
    return data_dir(settings) / "mcp.json"
