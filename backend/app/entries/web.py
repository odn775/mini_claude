"""Web 入口：启动 uvicorn。与 CLI 入口（entries/cli.py）并列。"""

from __future__ import annotations


def main() -> None:
    import uvicorn

    from app.infra.config import get_settings
    from app.main import app

    settings = get_settings()
    print(f"Mini Claude API: http://{settings.host}:{settings.port}  (接口文档 /docs)")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
