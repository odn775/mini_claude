"""配置层：统一从 .env 读取，本地开发与容器运行行为一致。

环境变量优先于 .env 文件；字段沿用项目既有的环境变量名，便于从旧版本平滑迁移。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录（本文件位于 backend/app/infra/config.py）
BACKEND_DIR = Path(__file__).resolve().parents[2]

DEFAULT_MODEL = "qwen3.7-max"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MAX_TOKENS = 16384
DEFAULT_DATABASE_URL = (
    "mysql+pymysql://mini_claude:mini_claude@127.0.0.1:3306/mini_claude?charset=utf8mb4"
)


class ConfigError(RuntimeError):
    """配置缺失或不合法，消息中必须包含可直接照做的下一步指引。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(BACKEND_DIR / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    api_key: str = Field("", validation_alias="DASHSCOPE_API_KEY")
    model: str = Field(DEFAULT_MODEL, validation_alias="MINI_CLAUDE_MODEL")
    base_url: str = Field(DEFAULT_BASE_URL, validation_alias="MINI_CLAUDE_BASE_URL")
    max_tokens: int = Field(DEFAULT_MAX_TOKENS, validation_alias="MINI_CLAUDE_MAX_TOKENS")

    # --- 数据库 ---
    database_url: str = Field(DEFAULT_DATABASE_URL, validation_alias="DATABASE_URL")

    # --- 服务 ---
    host: str = Field("127.0.0.1", validation_alias="MINI_CLAUDE_HOST")
    port: int = Field(8000, validation_alias="MINI_CLAUDE_PORT")

    # --- 运行数据（skills 目录、知识库索引、mcp.json）---
    data_dir: Path = Field(Path.home() / ".mini_claude", validation_alias="MINI_CLAUDE_DATA_DIR")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_api_key(settings: Settings | None = None) -> str:
    """取出 api_key；未配置时抛出带操作指引的 ConfigError。"""
    s = settings or get_settings()
    key = (s.api_key or "").strip()
    if not key:
        raise ConfigError(
            "未配置 LLM API Key。请任选一种方式：\n"
            f"  1) 复制 {BACKEND_DIR / '.env.example'} 为 {BACKEND_DIR / '.env'}，"
            "填入 DASHSCOPE_API_KEY\n"
            '  2) 直接设置环境变量：$env:DASHSCOPE_API_KEY="sk-..."'
        )
    return key
