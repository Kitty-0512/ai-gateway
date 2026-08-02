# AI Analysis Console

面向企业数据分析与运维诊断的统一 AI 分析控制台。

**在线访问**：[https://ai-gateway-1qp6.onrender.com](https://ai-gateway-1qp6.onrender.com)

**技术栈**：FastAPI · LangChain · MCP SDK · Vue 3 · Element Plus · ECharts · MySQL

---

## 功能概览

### 数据概览
首页仪表盘，展示分析任务、查询记录、诊断报告、数据集等统计信息，以及最近任务和快捷操作入口。

### 分析中心
自然语言数据分析（Text-to-SQL）核心工作区，Pipeline 风格展示完整分析链路：

```
用户请求 → 分析流程 → 生成 SQL → 分析结果（数据表 / 图表 / SQL）
```

- 上传 Excel / CSV 数据集，自然语言提问自动生成并执行 SQL
- SSE 流式推送每一步执行状态，前端实时展示
- 结果区 Tab 切换：数据表、图表、SQL 三视图
- 支持图表 PNG 导出、数据表 PDF 导出、截图保存

### SQL 工作台
独立 SQL 编辑器，可直接编写和执行查询语句，查看结果表格与图表。

### 日志诊断
运维日志智能诊断，上传日志文件或粘贴内容后自动分析：

- 异常类型识别
- 关键证据提取
- 根因分析
- 处理建议
- 风险等级与严重度评分

---

## 核心链路

```
请求到达 → 自动路由决策 → MCP 工具编排 → LLM 生成 + 自我纠错 → 安全过滤 → SSE 流式输出
```

### 技术特性

- **统一入口** `POST /api/chat`：根据文件后缀、日志格式特征、关键词、LLM 意图分类逐级自动路由到 SQL 分析或日志诊断
- **MCP 双向协议**：网关既作为 MCP Client 编排 MySQL / Filesystem / Fetch 工具，也通过 `fastapi_mcp` 对外暴露标准工具
- **SQL 安全与稳定性**：列级字段白名单、行级 WHERE 条件动态注入、敏感信息脱敏；执行失败时携带历史上下文重试
- **可观测执行轨迹**：SSE 推送每一步决策、工具调用与修复历史，前端以折叠时间线面板完整展示

---

## 目录结构

```
ai-gateway/
├── backend/                     FastAPI 后端
│   ├── app/
│   │   ├── main.py              应用入口、统一 /api/chat、MCP Server 暴露
│   │   ├── core/                配置、路由决策、LLM 与 MCP 客户端、沙箱、SSE 工具
│   │   ├── models/              Pydantic / SQLAlchemy 数据模型
│   │   ├── routers/             sql_analyze、log_diagnose 子路由
│   │   └── services/
│   │       ├── sql/             SQL 生成、校验、重试、权限、Schema 管理
│   │       └── log/             日志解析、脱敏、严重性评分、诊断、报告
│   ├── demo/                    示例日志与数据文件
│   ├── requirements.txt
│   └── .env.example
└── frontend/                    Vue 3 前端
    ├── src/
    │   ├── api/client.js        统一 API 客户端（含 SSE 流式解析）
    │   ├── components/
    │   │   ├── AppLayout.vue    全局侧边导航布局
    │   │   ├── ExecutionTrace.vue  执行过程时间线面板
    │   │   └── NewSessionDialog.vue 新建会话对话框
    │   ├── views/
    │   │   ├── DashboardView.vue     数据概览
    │   │   ├── AnalysisView.vue      分析中心（Pipeline 风格）
    │   │   ├── SQLWorkspaceView.vue  SQL 工作台
    │   │   └── LogDiagnosisView.vue  日志诊断
    │   ├── store/sessions.js    Pinia 会话状态
    │   └── styles/main.css      全局样式
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

cp .env.example .env    # 填入 API Key
uvicorn app.main:app --reload --port 8001
```

后端启动在 `http://127.0.0.1:8001`，API 文档见 `http://127.0.0.1:8001/docs`。

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端启动在 `http://localhost:3000`，Vite 代理将 `/api` 请求转发到线上 Render 服务，无需本地后端。

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

---

## 部署

项目采用**单服务部署**：Docker 多阶段构建，先用 Node 构建前端，再把产物拷进 Python 镜像的 `static/` 目录，由 FastAPI 一并托管。前后端同源，无需处理 CORS，SSE 流式输出不会被反向代理缓冲。

```bash
docker build -t ai-gateway .
docker run -p 7860:7860 -e OPENAI_API_KEY=sk-xxx ai-gateway
```

打开 `http://localhost:7860` 即可。

### 数据库（仅 SQL 分析模式需要）

日志诊断模式不依赖数据库，可以直接上线。SQL 分析模式需要 MySQL 兼容数据库，[TiDB Cloud](https://tidbcloud.com/) 提供免费额度。创建集群后配置：

```
MYSQL_HOST=gateway01.<region>.prod.aws.tidbcloud.com
MYSQL_PORT=4000
MYSQL_USER=<前缀>.root
MYSQL_PASSWORD=<密码>
MYSQL_DATABASE=ai_analyzer
MYSQL_SSL_ENABLED=true
```

TiDB Cloud 强制 TLS 连接，`MYSQL_SSL_ENABLED=true` 必须设置。

---

## 许可证

MIT License
