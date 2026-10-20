# 后端镜像：Python 3.12 + FastAPI 应用（构建上下文为仓库根目录）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖再拷代码，改代码不会让依赖层失效
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./

# skills / 知识库索引 / mcp.json 都放在这里，由 compose 挂卷持久化
ENV MINI_CLAUDE_DATA_DIR=/data

EXPOSE 8000

# 迁移由 compose 的 command 在启动前执行（见 deploy/docker-compose.yml）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
