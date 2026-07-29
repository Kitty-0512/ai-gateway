# ── 阶段一：构建前端 ──
FROM node:20-slim AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── 阶段二：运行后端（并托管前端产物）──
FROM python:3.11-slim

# Hugging Face Spaces 的容器以 uid 1000 运行，需预先建好同 uid 的用户
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR $HOME/app
USER user

COPY --chown=user backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY --chown=user backend/ ./
COPY --chown=user --from=frontend-build /build/dist ./static

# MCP 工具子进程依赖 npx，运行镜像未安装 Node，因此关闭；
# SQL 执行会自动降级为 SQLAlchemy 直连（见 services/sql/mcp_tool.py）
ENV MCP_SERVER_ENABLED=false \
    FS_MCP_SERVER_ENABLED=false \
    FETCH_MCP_SERVER_ENABLED=false \
    STATIC_DIR=static \
    FS_SANDBOX_ROOT=/tmp/uploads

EXPOSE 10000
# Render 通过 PORT 环境变量动态分配端口（默认 10000），本地运行可自行覆盖
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000}"]
