import json
import os

CONFIG_DIR = os.path.expanduser("~/.mini_claude")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_MODEL = "qwen3.7-max"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MAX_TOKENS = 16384


def _load_config_file():
    """从 ~/.mini_claude/config.json 读取配置。"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def get_config():
    """
    读取配置，优先级：环境变量 > 配置文件 > 默认值。
    首次运行如果没有配置文件，会自动创建模板。
    """
    file_cfg = _load_config_file()

    api_key = os.environ.get("DASHSCOPE_API_KEY") or file_cfg.get("api_key")
    if not api_key:
        # 自动创建配置文件模板
        os.makedirs(CONFIG_DIR, exist_ok=True)
        template = {
            "api_key": "在此填入你的阿里百炼 API Key",
            "model": DEFAULT_MODEL,
            "base_url": DEFAULT_BASE_URL,
            "max_tokens": DEFAULT_MAX_TOKENS,
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
        raise RuntimeError(
            f"首次运行，已在 {CONFIG_FILE} 创建配置文件。\n"
            "请编辑该文件，将 api_key 改为你的真实 Key，然后重新启动。"
        )

    return {
        "api_key": api_key,
        "model": os.environ.get("MINI_CLAUDE_MODEL") or file_cfg.get("model", DEFAULT_MODEL),
        "base_url": os.environ.get("MINI_CLAUDE_BASE_URL") or file_cfg.get("base_url", DEFAULT_BASE_URL),
        "max_tokens": int(
            os.environ.get("MINI_CLAUDE_MAX_TOKENS")
            or file_cfg.get("max_tokens", DEFAULT_MAX_TOKENS)
        ),
    }
