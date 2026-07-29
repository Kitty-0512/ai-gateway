"""
Text-to-SQL 分析路由。

提供：
- 专门接口: /api/sql/query, /api/sql/query/stream, /api/sql/upload, /api/sql/health
- 被统一入口 /api/chat (mode="sql") 调用的 handler 函数
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from queue import Empty, Queue
from threading import Thread
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.fs_mcp_client import FsMcpError, get_fs_mcp_client
from app.core.sandbox import cleanup_session, relative_path, save_upload
from app.core.session_store import store
from app.models.sql_schemas import (
    FileUploadResponse,
    QueryRequest,
    QueryResponse,
    UploadResponse,
)
from app.services.sql.file_parser import parse_file_content
from app.services.sql.sql_generator import (
    create_or_get_conversation,
    generate_and_execute,
    generate_and_execute_stream,
    load_conversation_history,
)
from app.services.sql.db_utils import get_db_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sql", tags=["SQL 数据分析"])

settings = get_settings()


# ---- 专门接口 ----

@router.post("/query", response_model=QueryResponse)
async def query_data(request: QueryRequest):
    """非流式自然语言查询（兼容旧接口）。"""
    try:
        return generate_and_execute(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error(f"AI 服务调用失败: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("查询处理失败")
        raise HTTPException(status_code=500, detail=f"查询处理失败: {str(e)}")


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.post("/query/stream")
def query_data_stream(request: QueryRequest):
    """SSE 流式自然语言查询。"""
    event_queue = Queue()
    done = object()

    def produce_events():
        try:
            for event, data in generate_and_execute_stream(request):
                event_queue.put((event, data))
        except Exception as e:
            logger.exception("流式查询处理失败")
            event_queue.put(("error", {"message": f"查询处理失败: {str(e)}"}))
        finally:
            event_queue.put(done)

    def event_generator():
        worker = Thread(target=produce_events, daemon=True)
        worker.start()
        while True:
            try:
                item = event_queue.get(timeout=15)
            except Empty:
                yield ": keep-alive\n\n"
                continue
            if item is done:
                break
            event, data = item
            yield _sse_frame(event, data)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """
    上传 Excel/CSV 文件，解析入库。

    流程：
    1. 保存到会话沙箱目录
    2. 通过 Filesystem MCP 读取文件内容
    3. pandas 解析 → 建表 → 入库
    """
    import uuid

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        raise HTTPException(status_code=400, detail="仅支持 .csv / .xlsx / .xls 文件")

    # 临时会话 ID（仅用于本次上传的沙箱隔离）
    upload_sid = uuid.uuid4().hex[:12]
    raw_bytes = await file.read()

    try:
        # 1) 保存到沙箱
        saved_path = save_upload(upload_sid, file.filename or "upload", raw_bytes)
        rel_path = relative_path(saved_path)

        # 2) 通过 MCP 读取文件内容
        fs = get_fs_mcp_client()
        if fs.is_available:
            fs.ensure_connected()
            content = fs.read_file(rel_path)
        else:
            # MCP 不可用时直接读取本地文件（降级）
            content = saved_path.read_bytes()

        # 3) pandas 解析（业务逻辑不变）
        result = parse_file_content(content, file.filename or "upload")
        schema_json = json.dumps(result.columns, ensure_ascii=False)
        db = get_db_sync()
        try:
            insert_sql = text("""
                INSERT INTO datasets (file_name, file_path, file_size, table_name,
                                       schema_json, row_count, file_type, uploaded_by, status)
                VALUES (:file_name, :file_path, :file_size, :table_name,
                        :schema_json, :row_count, :file_type, 1, 1)
            """)
            db.execute(insert_sql, {
                "file_name": file.filename or "upload",
                "file_path": str(saved_path),
                "file_size": len(raw_bytes),
                "table_name": result.table_name,
                "schema_json": schema_json,
                "row_count": result.row_count,
                "file_type": ext.lstrip("."),
            })
            db.commit()
            dataset_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
        except Exception as e:
            db.rollback()
            logger.exception("保存数据集元信息失败")
            raise HTTPException(status_code=500, detail=f"保存数据集元信息失败: {str(e)}")
        finally:
            db.close()

        return UploadResponse(
            success=True,
            dataset_id=dataset_id,
            table_name=result.table_name,
            row_count=result.row_count,
            columns=result.columns,
        )
    except (ValueError, FsMcpError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("文件上传失败")
        raise HTTPException(status_code=500, detail=f"文件处理失败: {str(e)}")
    finally:
        cleanup_session(upload_sid)


@router.get("/health")
async def health_check():
    """SQL 分析服务健康检查。"""
    import httpx

    info = {"status": "ok", "service": "sql-analyzer"}
    db = get_db_sync()
    try:
        from sqlalchemy import text
        db.execute(text("SELECT 1"))
        info["database"] = "connected"
    except Exception as e:
        info["database"] = f"error: {str(e)}"
        info["status"] = "degraded"
    finally:
        db.close()

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                f"{settings.openai_base_url}/models",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            )
            info["llm_api"] = "connected" if resp.status_code == 200 else f"status_{resp.status_code}"
    except Exception as e:
        info["llm_api"] = f"unreachable: {str(e)}"
        info["status"] = "degraded"

    return info


# ---- 统一入口 handler ----

def handle_sql_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """
    处理 mode="sql" 的统一聊天请求。

    从 payload 中提取:
        question, dataset_ids, conversation_id, history
    """
    request = QueryRequest(
        question=payload["question"],
        dataset_ids=payload.get("dataset_ids", []),
        conversation_id=payload.get("conversation_id"),
        history=payload.get("history", []),
    )
    return generate_and_execute(request).model_dump(mode="json")
