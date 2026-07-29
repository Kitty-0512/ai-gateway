"""
日志诊断路由。

提供：
- 专门接口: /api/log/analyze, /api/log/analyze/stream, /api/log/upload, /api/log/report, /api/log/health
- 被统一入口 /api/chat (mode="log") 调用的 handler 函数
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.fs_mcp_client import FsMcpError, get_fs_mcp_client
from app.core.sandbox import cleanup_session, relative_path, save_upload
from app.core.session_store import store
from app.models.log_schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    HealthResponse,
    LogType,
    ReportRequest,
    ReportResponse,
)
from app.services.log.diagnoser import diagnose, diagnose_stream
from app.services.log.log_parser import clean_log, detect_log_type
from app.services.log.report import build_report_payload, render_markdown

router = APIRouter(prefix="/api/log", tags=["日志诊断"])

settings = get_settings()


def _build_meta(
    *,
    mode: str,
    content: str,
    cleaned: str,
    error: str | None = None,
    filename: str | None = None,
    extra_context: str | None = None,
) -> dict:
    has_ctx = bool(extra_context and extra_context.strip())
    meta = {
        "mode": mode,
        "detected_type": detect_log_type(cleaned).value,
        "input_chars": len(content),
        "cleaned_chars": len(cleaned),
        "truncated": len(content) > len(cleaned) or "[已截断" in cleaned,
        "round": 2 if has_ctx else 1,
        "has_extra_context": has_ctx,
    }
    if filename:
        meta["filename"] = filename
    if error:
        meta["fallback_error"] = error[:300]
    return meta


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


# ---- 专门接口 ----

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        app="LogSense - 日志诊断",
        llm_configured=settings.llm_configured,
        llm_model=settings.openai_model if settings.llm_configured else None,
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(payload: AnalyzeRequest) -> AnalyzeResponse:
    cleaned = clean_log(payload.content)
    if not cleaned:
        raise HTTPException(status_code=400, detail="日志内容为空")
    try:
        outcome = await diagnose(payload.content, payload.log_type, payload.extra_context)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"诊断失败: {exc}") from exc
    return AnalyzeResponse(
        success=True,
        result=outcome.result,
        meta=_build_meta(
            mode=outcome.mode,
            content=payload.content,
            cleaned=cleaned,
            error=outcome.error,
            extra_context=payload.extra_context,
        ),
    )


@router.post("/analyze/stream")
async def analyze_stream(payload: AnalyzeRequest) -> StreamingResponse:
    """SSE 流式诊断。"""
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="日志内容为空")

    async def event_generator() -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def produce() -> None:
            try:
                async for event, data in diagnose_stream(
                    payload.content, payload.log_type, payload.extra_context
                ):
                    await queue.put((event, data))
            except Exception as exc:  # noqa: BLE001
                await queue.put(("error", {"message": f"诊断失败: {exc}"}))
            finally:
                await queue.put(None)

        task = asyncio.create_task(produce())
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    break
                event, data = item
                yield _sse(event, data)
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/upload", response_model=AnalyzeResponse)
async def analyze_upload(
    file: UploadFile = File(...),
    log_type: LogType | None = Form(None),
    extra_context: str | None = Form(None),
) -> AnalyzeResponse:
    filename = file.filename or "upload.log"
    if not filename.lower().endswith((".log", ".txt", ".out")):
        raise HTTPException(status_code=400, detail="仅支持 .log / .txt / .out 文件")

    raw_bytes = await file.read()
    if len(raw_bytes) > settings.max_upload_bytes:
        raise HTTPException(status_code=400, detail="文件过大（上限 2MB）")

    # 临时会话 ID（仅用于沙箱隔离）
    upload_sid = uuid.uuid4().hex[:12]

    try:
        # 1) 保存到沙箱
        saved_path = save_upload(upload_sid, filename, raw_bytes)
        rel_path = relative_path(saved_path)

        # 2) 通过 MCP 读取文件内容
        fs = get_fs_mcp_client()
        if fs.is_available:
            fs.ensure_connected()
            raw_content = fs.read_file(rel_path)
            try:
                content = raw_content.decode("utf-8")
            except UnicodeDecodeError:
                content = raw_content.decode("utf-8", errors="replace")
        else:
            # MCP 不可用时直接读本地文件（降级）
            content = saved_path.read_text(encoding="utf-8")
    except (FsMcpError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"文件读取失败: {e}")
    finally:
        cleanup_session(upload_sid)

    cleaned = clean_log(content)
    if not cleaned:
        raise HTTPException(status_code=400, detail="文件内容为空")

    try:
        outcome = await diagnose(content, log_type, extra_context)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"诊断失败: {exc}") from exc

    return AnalyzeResponse(
        success=True,
        result=outcome.result,
        meta=_build_meta(
            mode=outcome.mode,
            content=content,
            cleaned=cleaned,
            error=outcome.error,
            filename=filename,
            extra_context=extra_context,
        ),
    )


@router.post("/report", response_model=ReportResponse)
async def export_report(payload: ReportRequest) -> ReportResponse:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if payload.format == "json":
        data = build_report_payload(payload.result, payload.meta)
        return ReportResponse(
            format="json",
            filename=f"logsense-report-{stamp}.json",
            content=json.dumps(data, ensure_ascii=False, indent=2),
            payload=data,
        )
    md = render_markdown(payload.result, payload.meta)
    return ReportResponse(
        format="markdown",
        filename=f"logsense-report-{stamp}.md",
        content=md,
        payload=None,
    )


# ---- 统一入口 handler ----

async def handle_log_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """
    处理 mode="log" 的统一聊天请求。

    从 payload 中提取:
        content, log_type, extra_context
    """
    cleaned = clean_log(payload["content"])
    if not cleaned:
        return {"success": False, "answer": "日志内容为空"}

    outcome = await diagnose(
        payload["content"],
        payload.get("log_type"),
        payload.get("extra_context"),
    )
    result = outcome.result.model_dump(mode="json")
    return {
        "success": True,
        "result": result,
        "meta": _build_meta(
            mode=outcome.mode,
            content=payload["content"],
            cleaned=cleaned,
            error=outcome.error,
            extra_context=payload.get("extra_context"),
        ),
    }
