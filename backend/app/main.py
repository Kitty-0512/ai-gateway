"""
AI 统一分析网关。

合并两个子服务：
- SQL 数据分析（Text-to-SQL）：routers/sql_analyze.py
- 日志诊断（LogSense）：routers/log_diagnose.py

统一入口：POST /api/chat
    - 自动路由：文件后缀 → 日志格式特征 → 关键词 → LLM 分类 → 兜底
    - 也可手动指定 mode="sql"|"log" 跳过自动路由

MCP Server 暴露：通过 fastapi_mcp 把核心端点包装为 MCP 工具，
    供外部 AI Agent（Claude Desktop / Cursor / etc.）直接调用。
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.router import resolve_tool
from app.core.session_store import store
from app.routers import log_diagnose, sql_analyze

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"=== {settings.app_name} 启动 ===")
    logger.info(f"  LLM: {'已配置' if settings.llm_configured else '未配置'}")
    logger.info(f"  Model: {settings.openai_model}")
    if settings.mysql_host:
        logger.info(f"  MySQL: {settings.mysql_host}:{settings.mysql_port}/{settings.mysql_database}")

    # ── 自动建表（元数据表）──
    if settings.mysql_host and settings.mysql_password:
        try:
            from sqlalchemy import text
            from app.services.sql.db_utils import get_db_sync
            db = get_db_sync()
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS datasets (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    file_name VARCHAR(255) NOT NULL,
                    file_path VARCHAR(500) NOT NULL,
                    file_size BIGINT NOT NULL DEFAULT 0,
                    table_name VARCHAR(128) NOT NULL,
                    schema_json TEXT,
                    row_count INT NOT NULL DEFAULT 0,
                    file_type VARCHAR(20) NOT NULL,
                    uploaded_by INT NOT NULL DEFAULT 1,
                    status INT NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.execute(text("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(200) NOT NULL DEFAULT '新对话',
                    user_id INT NOT NULL DEFAULT 1,
                    dataset_ids TEXT,
                    status INT NOT NULL DEFAULT 1,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """))
            db.commit()
            db.close()
            logger.info("  元数据表: ✅ 已就绪 (datasets, conversations)")
        except Exception as e:
            logger.warning("  元数据表: ⚠️ 建表失败 — %s", e)

    # ── MCP Server 健康检查 ──
    try:
        from app.core.mcp_client import get_mcp_client
        mcp = get_mcp_client()
        if mcp.is_available:
            status = mcp.health_check()
            if status["ok"]:
                logger.info(
                    "  MCP Server (MySQL): ✅ 可用 (%d 工具, %dms): %s",
                    len(status["tools"]), status["latency_ms"], status["tools"],
                )
            else:
                logger.warning(
                    "  MCP Server (MySQL): ⚠️ 不可用 — %s",
                    status.get("error", "未知错误"),
                )
        else:
            logger.info("  MCP Server (MySQL): ⊘ 已禁用或 SDK 未安装")
    except Exception as e:
        logger.warning("  MCP Server (MySQL): ⚠️ 健康检查异常 — %s", e)

    # ── Filesystem MCP Server 健康检查 ──
    try:
        from app.core.fs_mcp_client import get_fs_mcp_client
        fs = get_fs_mcp_client()
        if fs.is_available:
            fs_status = fs.health_check()
            if fs_status["ok"]:
                logger.info(
                    "  MCP Server (Filesystem): ✅ 可用 (%dms), 沙箱: %s",
                    fs_status["latency_ms"], settings.fs_sandbox_root,
                )
            else:
                logger.warning(
                    "  MCP Server (Filesystem): ⚠️ 不可用 — %s",
                    fs_status.get("error", "未知错误"),
                )
        else:
            logger.info("  MCP Server (Filesystem): ⊘ 已禁用或 SDK 未安装")
    except Exception as e:
        logger.warning("  MCP Server (Filesystem): ⚠️ 健康检查异常 — %s", e)

    # ── 沙箱清理调度器 ──
    try:
        from app.core.sandbox import start_cleanup_scheduler
        start_cleanup_scheduler(interval_seconds=300)
        logger.info(
            "  沙箱清理: ✅ 已启动 (TTL %dmin, 目录: %s)",
            settings.fs_session_ttl_minutes, settings.fs_sandbox_root,
        )
    except Exception as e:
        logger.warning("  沙箱清理: ⚠️ 启动失败 — %s", e)

    yield

    # ── 关闭清理 ──
    try:
        from app.core.sandbox import stop_cleanup_scheduler
        stop_cleanup_scheduler()
    except Exception:
        pass
    logger.info(f"=== {settings.app_name} 关闭 ===")


app = FastAPI(
    title=settings.app_name,
    description="Text-to-SQL 数据分析 + 日志诊断 统一网关",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册子路由（保留专门的端点供直接调用）
app.include_router(sql_analyze.router)
app.include_router(log_diagnose.router)

# ═══ MCP Server 暴露（#7） ═══
_mcp_enabled = False
try:
    from fastapi_mcp import FastApiMCP

    _mcp_enabled = True
    mcp = FastApiMCP(
        app,
        name="AI 统一分析网关 MCP Server",
        description="Text-to-SQL 数据分析 + 日志诊断 统一网关",
        describe_all_responses=True,
        include_operations=[
            # SQL 分析
            "query_data_api_sql_query_post",
            "query_data_stream_api_sql_query_stream_post",
            "upload_file_api_sql_upload_post",
            # 日志诊断
            "health_api_log_health_get",
            "analyze_api_log_analyze_post",
            "analyze_stream_api_log_analyze_stream_post",
            "analyze_upload_api_log_upload_post",
            "export_report_api_log_report_post",
        ],
        headers=["Authorization", "X-Forwarded-For", "X-Real-IP"],
    )
    mcp._base_url = settings.mcp_base_url
    logger.info("MCP Server: fastapi_mcp 已加载, base_url=%s, %d 个工具", settings.mcp_base_url, 8)
except ImportError:
    logger.info("MCP Server: fastapi_mcp 未安装，跳过 MCP 暴露。安装: pip install fastapi-mcp")


# ---- 统一入口 ChatRequest ----

class ChatRequest(BaseModel):
    """统一聊天请求。mode 可选，不传则自动路由。"""
    mode: str | None = Field(None, description="手动指定模式: sql | log，留空则自动路由")
    session_id: str | None = None
    question: str | None = None
    content: str | None = None
    file_name: str | None = Field(None, description="附件文件名，用于辅助路由判断")
    dataset_ids: list[int] = []
    conversation_id: int | None = None
    history: list[dict] = []
    log_type: str | None = None
    extra_context: str | None = None


@app.post("/api/chat")
async def unified_chat(payload: ChatRequest):
    """
    统一聊天入口 — 自动路由版。

    mode 为可选字段：
    - mode="sql" 或 mode="log"  → 直接路由（跳过自动判断）
    - mode 留空                   → 自动路由：
        1. file_name 后缀 (.xlsx/.csv → sql, .log/.txt → log)
        2. content/question 内容匹配日志格式特征
        3. 关键词匹配
        4. LLM 轻量意图分类
        5. 兜底 → sql

    响应中 always 包含 routed_tool 字段，说明实际路由结果和原因。
    """
    body = payload.model_dump()

    # ---- 1. 决定路由目标 ----
    if body.get("mode"):
        # 手动指定：跳过自动检测
        tool = body["mode"].strip().lower()
        routing = None  # 标记为手动
    else:
        # 自动路由：取 question 或 content 作为文本，file_name 辅助判断
        user_text = body.get("question") or body.get("content") or ""
        routing = await resolve_tool(
            text=user_text,
            file_name=body.get("file_name"),
        )
        tool = routing.tool

    if tool not in ("sql", "log"):
        raise HTTPException(status_code=400, detail=f"不支持的模式: {tool}，可用: sql, log")

    # ---- 2. 构建路由信息（统一格式） ----
    if routing:
        # 自动路由
        routed_tool = {
            "tool": routing.tool,
            "reason": routing.reason,
            "confidence": routing.confidence,
        }
        if routing.file_name:
            routed_tool["file_name"] = routing.file_name
    else:
        # 手动指定
        routed_tool = {
            "tool": tool,
            "reason": "用户手动指定 mode",
            "confidence": "high",
        }

    # ---- 3. 会话管理 ----
    session_id = body.get("session_id")
    sess = store.get_or_create(session_id, tool)

    logger.info(f"路由决策: tool={tool}, reason={routed_tool['reason']}, session={sess['session_id']}")

    # ---- 4. 分发执行 ----
    if tool == "sql":
        if not body.get("question"):
            raise HTTPException(status_code=400, detail="SQL 模式需要提供 question 字段")
        if not body.get("dataset_ids"):
            raise HTTPException(status_code=400, detail="SQL 模式需要提供 dataset_ids 字段")

        store.append_message(sess["session_id"], "user", body["question"])
        result = sql_analyze.handle_sql_chat(body)
        store.append_message(sess["session_id"], "assistant", result.get("answer", ""))
        store.update(sess["session_id"], meta={"last_tool": "sql"})

        return {
            "session_id": sess["session_id"],
            "routed_tool": routed_tool,
            **result,
        }

    elif tool == "log":
        if not body.get("content"):
            raise HTTPException(status_code=400, detail="日志模式需要提供 content 字段")

        store.append_message(sess["session_id"], "user", body["content"][:500])
        result = await log_diagnose.handle_log_chat(body)
        store.append_message(
            sess["session_id"], "assistant",
            json.dumps(result.get("result", {}), ensure_ascii=False, default=str)[:1000],
        )
        store.update(sess["session_id"], meta={"last_tool": "log"})

        return {
            "session_id": sess["session_id"],
            "routed_tool": routed_tool,
            **result,
        }


# ---- 会话管理 ----

@app.get("/api/sessions")
async def list_sessions(mode: str | None = None):
    """列出所有会话。"""
    return {"sessions": store.list_sessions(mode)}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """获取会话详情。"""
    sess = store.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话不存在")
    return sess


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话。"""
    store.delete(session_id)
    return {"message": "已删除"}


# ---- 服务信息 & 异常处理 ----

@app.get("/api/info")
async def service_info():
    return {
        "service": settings.app_name,
        "version": "2.0.0",
        "tools": ["sql", "log"],
        "docs": "/docs",
        "unified_chat": "/api/chat",
        "endpoints": {
            "sql": [
                "/api/sql/query",
                "/api/sql/query/stream",
                "/api/sql/upload",
                "/api/sql/health",
            ],
            "log": [
                "/api/log/analyze",
                "/api/log/analyze/stream",
                "/api/log/upload",
                "/api/log/report",
                "/api/log/health",
            ],
        },
        "sessions": "/api/sessions",
    }


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    logger.warning(f"请求参数错误 [{request.method} {request.url.path}]: {exc}")
    return JSONResponse(status_code=400, content={"detail": str(exc), "code": "BAD_REQUEST"})


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.exception(f"服务内部错误 [{request.method} {request.url.path}]")
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误", "code": "INTERNAL_ERROR"})


# ---- 前端托管（单服务部署）----
# 必须注册在所有 API 路由之后，否则通配路由会抢先匹配掉接口请求。

_static_dir = Path(settings.static_dir)

if _static_dir.is_dir():
    _static_root = _static_dir.resolve()
    _index_file = _static_root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        """命中真实文件则直接返回，否则回退 index.html 交给前端路由处理。"""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        candidate = (_static_root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(_static_root):
            return FileResponse(candidate)
        return FileResponse(_index_file)

    logger.info("前端静态资源: 已挂载 %s", _static_root)
else:
    @app.get("/")
    async def root():
        return await service_info()

    logger.info("前端静态资源: 未找到 %s，仅提供 API", _static_dir)
