---
title: AI 统一分析网关
emoji: 🔍
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# AI 统一分析网关

面向自然语言数据分析（Text-to-SQL）与运维日志智能诊断的统一 AI Agent 网关。

**技术栈**：Python · FastAPI · LangChain · MCP SDK · Vue 3 · Element Plus · ECharts · MySQL

🌐 **在线访问**：[https://ai-gateway-1qp6.onrender.com](https://ai-gateway-1qp6.onrender.com)

核心链路：

```
请求到达 → 自动路由决策 → MCP 工具编排 → LLM 生成 + 自我纠错 → 安全过滤 → SSE 流式输出
```

---

## 功能

- **统一入口 `POST /api/chat`**：根据文件后缀、日志格式特征、关键词、LLM 意图分类逐级自动路由到 SQL 分析或日志诊断，也可通过 `mode` 手动指定。
- **Text-to-SQL 分析**：上传 Excel/CSV 或连接 MySQL，自然语言提问生成并执行 SQL，结果自动可视化。
- **日志智能诊断**：解析 Python traceback、Nginx、Docker 等日志，输出根因分析、严重性评分与修复建议，支持导出报告。
- **MCP 双向协议**：网关既作为 MCP Client 编排 MySQL / Filesystem / Fetch 工具子进程，也通过 `fastapi_mcp` 作为 MCP Server 对外暴露 8 个标准工具，供 Claude Desktop、Cursor 等外部 Agent 调用。
- **SQL 安全与稳定性**：列级字段白名单、行级 WHERE 条件动态注入、敏感信息脱敏；执行失败时携带历史上下文重试，并在最终结果上做独立校验。
- **可观测执行轨迹**：后端通过 SSE 推送每一步决策、工具调用与修复历史，前端以折叠面板完整展示。

---

## 目录结构

```
ai-gateway/
├── backend/                  FastAPI 后端
│   ├── app/
│   │   ├── main.py           应用入口、统一 /api/chat、MCP Server 暴露
│   │   ├── core/             配置、路由决策、LLM 与 MCP 客户端、沙箱、SSE 工具
│   │   ├── models/           Pydantic / SQLAlchemy 数据模型
│   │   ├── routers/          sql_analyze、log_diagnose 子路由
│   │   └── services/
│   │       ├── sql/          SQL 生成、校验、重试、权限、Schema 管理
│   │       └── log/          日志解析、脱敏、严重性评分、诊断、报告
│   ├── demo/                 示例日志与数据文件
│   ├── requirements.txt
│   └── .env.example
└── frontend/                 Vue 3 前端
    ├── src/
    │   ├── api/client.js     统一 API 客户端（含 SSE 流式解析）
    │   ├── components/       ChatPanel、ExecutionTrace、SessionSidebar 等
    │   ├── views/            HomeView
    │   └── store/            Pinia 会话状态
    ├── package.json
    └── vite.config.js
```

---

## 本地运行

### 环境要求

- Python 3.10+
- Node.js 18+
- MySQL 8.0（可选，仅 SQL 分析模式需要；日志诊断模式无需数据库）

### 后端

```bash
cd backend
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env    # 然后填入你的 API Key
uvicorn app.main:app --reload --port 8001
```

后端启动在 `http://127.0.0.1:8001`，交互式文档见 `http://127.0.0.1:8001/docs`。

### 前端（开发模式，API 代理到 Render）

```bash
cd frontend
npm install
npm run dev
```

前端启动在 `http://localhost:3000`，Vite 代理将 `/api` 请求转发到线上 Render 服务 `https://ai-gateway-1qp6.onrender.com`，无需本地后端。

> 如需本地后端，将 `frontend/vite.config.js` 中 `proxy.target` 改回 `http://127.0.0.1:8001`。

### 环境变量

复制 `backend/.env.example` 为 `backend/.env` 并填写：

| 变量 | 说明 | 必填 |
|---|---|---|
| `OPENAI_API_KEY` | DeepSeek 或任意 OpenAI 兼容接口的 Key | 是 |
| `OPENAI_BASE_URL` | 接口地址，默认 `https://api.deepseek.com/v1` | 是 |
| `OPENAI_MODEL` | 模型名，默认 `deepseek-chat` | 是 |
| `MYSQL_*` | 数据库连接信息，仅 SQL 分析模式需要 | 否 |
| `MYSQL_SSL_ENABLED` | 连接托管数据库（强制 TLS）时设为 `true` | 否 |
| `CORS_ORIGINS` | 允许的前端来源，逗号分隔 | 是 |
| `LLM_FALLBACK_MOCK` | LLM 不可用时是否回退到规则模板 | 否 |
| `STATIC_DIR` | 前端构建产物目录，存在时由后端一并托管 | 否 |

`.env` 已被 `.gitignore` 排除，不会提交到仓库。

---

## 部署

项目采用**单服务部署**：`Dockerfile` 分两阶段，先用 Node 构建前端，再把产物拷进 Python 镜像的 `static/` 目录，由 FastAPI 一并托管。这样前后端同源，无需处理 CORS，SSE 流式输出也不会被反向代理缓冲。

本地用 Docker 验证：

```bash
docker build -t ai-gateway .
docker run -p 7860:7860 -e OPENAI_API_KEY=sk-xxx ai-gateway
```

打开 `http://localhost:7860` 即可。

### Hugging Face Spaces

1. 在 [huggingface.co/new-space](https://huggingface.co/new-space) 新建 Space，SDK 选 **Docker** → **Blank**。
2. 把本仓库推送到 Space 的 Git 仓库（README 顶部的 YAML 配置块已写好 `sdk: docker` 和 `app_port: 7860`）：

```bash
git remote add hf https://huggingface.co/spaces/<用户名>/<Space 名>
git push hf main
```

3. 在 Space 的 **Settings → Variables and secrets** 添加 `OPENAI_API_KEY`（以及需要连数据库时的 `MYSQL_*`）。密钥必须加在这里，不要写进仓库。

镜像里没有安装 Node，因此 `Dockerfile` 中已将三个 MCP 工具子进程关闭；SQL 执行会自动降级为 SQLAlchemy 直连，功能不受影响。

### 数据库（仅 SQL 分析模式需要）

日志诊断模式不依赖数据库，可以直接上线。SQL 分析模式需要一个 MySQL 兼容的数据库，[TiDB Cloud Starter](https://tidbcloud.com/) 提供免费额度且无需信用卡。创建集群后在 Space 中配置：

```
MYSQL_HOST=gateway01.<region>.prod.aws.tidbcloud.com
MYSQL_PORT=4000
MYSQL_USER=<前缀>.root
MYSQL_PASSWORD=<密码>
MYSQL_DATABASE=ai_analyzer
MYSQL_SSL_ENABLED=true
```

TiDB Cloud 强制 TLS 连接，`MYSQL_SSL_ENABLED=true` 必须设置，否则会被服务端直接拒绝。证书默认使用 `certifi` 内置的根证书，无需额外下载；若需指定自定义 CA，用 `MYSQL_SSL_CA` 给出路径。
