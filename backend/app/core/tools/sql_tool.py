"""
SQL Tool —— 包装现有 Text-to-SQL pipeline（核心工具）。

两个关键点（对应计划里的两个代码级坑）：

1. 同步 → async 桥接：
   generate_and_execute_stream 是**同步生成器**，直接在事件循环里迭代会阻塞。
   这里用后台线程 + asyncio.Queue 把它转成 async 事件流。

2. status 翻译层：
   现有 pipeline 几乎从不 raise —— 字段缺失/无数据/生成失败都是
   `yield "result", QueryResponse(...)` 收尾，只有 call_llm 彻底失败才 raise。
   因此不能靠 try/except 判断成败，必须从 QueryResponse 内容翻译出
   success / needs_input / failed。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

from app.core.tools.registry import (
    ERROR_KIND_INFRA,
    STATUS_FAILED,
    STATUS_NEEDS_INPUT,
    STATUS_SUCCESS,
    ToolContext,
    ToolResult,
)
from app.models.sql_schemas import Message, QueryRequest
from app.services.sql.sql_generator import generate_and_execute_stream

_SENTINEL = object()


async def _iter_sync_generator(gen_factory) -> AsyncIterator[Any]:
    """在后台线程运行同步生成器，转为 async 迭代。异常在主协程侧重新抛出。"""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def worker() -> None:
        try:
            for item in gen_factory():
                loop.call_soon_threadsafe(queue.put_nowait, ("item", item))
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ("done", _SENTINEL))

    threading.Thread(target=worker, daemon=True).start()

    while True:
        kind, payload = await queue.get()
        if kind == "done":
            break
        if kind == "error":
            raise payload
        yield payload


def _summarize(response: dict[str, Any]) -> str:
    rows = response.get("result") or []
    if rows:
        return f"查询返回 {len(rows)} 行数据"
    return (response.get("answer") or "")[:120]


def _translate_status(response: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """
    从 QueryResponse dict 翻译工具状态。

    - clarification_needed=True → needs_input
    - 生成了有效 SQL           → success（含空结果，pipeline 已给出分析文本）
    - 其余（sql 为 None）       → failed（逻辑类，外层不 retry）
    """
    if response.get("clarification_needed"):
        return STATUS_NEEDS_INPUT, None, None
    if response.get("sql"):
        return STATUS_SUCCESS, None, None
    return STATUS_FAILED, response.get("answer") or "未能生成有效 SQL", "logic"


class SqlTool:
    name = "sql"
    description = "SQL 数据分析（Text-to-SQL）：查询/统计/排名/趋势等结构化业务数据，需要 dataset_ids。"

    async def run(
        self, tool_input: dict[str, Any], ctx: ToolContext
    ) -> AsyncIterator[tuple[str, Any]]:
        question = tool_input.get("question") or ctx.question
        dataset_ids = tool_input.get("dataset_ids") or ctx.dataset_ids

        if not dataset_ids:
            yield "result", ToolResult(
                tool=self.name,
                status=STATUS_FAILED,
                summary="SQL 分析需要先选择/上传数据集",
                error="缺少 dataset_ids",
                error_kind="logic",
            )
            return

        request = QueryRequest(
            question=question,
            dataset_ids=dataset_ids,
            conversation_id=tool_input.get("conversation_id") or ctx.conversation_id,
            history=[Message(**m) if isinstance(m, dict) else m for m in ctx.history],
        )

        final_response: dict[str, Any] | None = None
        try:
            async for event, data in _iter_sync_generator(
                lambda: generate_and_execute_stream(request)
            ):
                if event == "result":
                    final_response = data
                    continue
                # stage / sql / delta 原样透传给 orchestrator
                yield event, data
        except Exception as exc:  # noqa: BLE001 —— 仅 call_llm 彻底失败等基础设施异常
            yield "result", ToolResult(
                tool=self.name,
                status=STATUS_FAILED,
                summary="SQL 分析失败（服务调用异常）",
                error=str(exc),
                error_kind=ERROR_KIND_INFRA,
            )
            return

        if final_response is None:
            yield "result", ToolResult(
                tool=self.name,
                status=STATUS_FAILED,
                summary="SQL 分析未返回结果",
                error="pipeline 未产出 result 事件",
                error_kind=ERROR_KIND_INFRA,
            )
            return

        status, error, error_kind = _translate_status(final_response)
        yield "result", ToolResult(
            tool=self.name,
            status=status,
            summary=_summarize(final_response),
            observation=final_response,
            error=error,
            error_kind=error_kind,
        )
