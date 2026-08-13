"""
Log Tool —— 包装现有日志诊断 pipeline（核心工具）。

diagnose_stream 本身就是**原生 async 生成器**，无需线程桥接，直接 async for。

status 翻译：
- 拿到 ("result", {...})   → success（含 mock_fallback，属降级成功）
- 拿到 ("error", {...})    → failed（基础设施类，外层可 retry）
- 全程无终止事件           → failed（infra）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.core.tools.registry import (
    ERROR_KIND_INFRA,
    STATUS_FAILED,
    STATUS_SUCCESS,
    ToolContext,
    ToolResult,
)
from app.models.log_schemas import LogType
from app.services.log.diagnoser import diagnose_stream
from app.services.log.log_parser import clean_log


def _summarize(observation: dict[str, Any]) -> str:
    result = observation.get("result") or {}
    anomaly = result.get("anomaly_type") or "未知异常"
    risk = result.get("risk_level") or ""
    return f"诊断完成：{anomaly}" + (f"（风险 {risk}）" if risk else "")


class LogTool:
    name = "log"
    description = "日志诊断（LogSense）：分析报错/堆栈/Docker/Nginx 等日志，定位根因，需要 log_content。"

    async def run(
        self, tool_input: dict[str, Any], ctx: ToolContext
    ) -> AsyncIterator[tuple[str, Any]]:
        content = tool_input.get("content") or ctx.log_content or ""
        if not clean_log(content):
            yield "result", ToolResult(
                tool=self.name,
                status=STATUS_FAILED,
                summary="日志内容为空",
                error="empty log content",
                error_kind="logic",
            )
            return

        raw_log_type = tool_input.get("log_type") or ctx.log_type
        log_type = None
        if raw_log_type:
            try:
                log_type = LogType(raw_log_type)
            except ValueError:
                log_type = None
        extra_context = tool_input.get("extra_context") or ctx.extra_context

        final_obs: dict[str, Any] | None = None
        error_msg: str | None = None
        try:
            async for event, data in diagnose_stream(content, log_type, extra_context):
                if event == "result":
                    final_obs = data
                    continue
                if event == "error":
                    error_msg = data.get("message", "诊断失败")
                    continue
                yield event, data
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)

        if final_obs is not None:
            yield "result", ToolResult(
                tool=self.name,
                status=STATUS_SUCCESS,
                summary=_summarize(final_obs),
                observation=final_obs,
            )
            return

        yield "result", ToolResult(
            tool=self.name,
            status=STATUS_FAILED,
            summary="日志诊断失败",
            error=error_msg or "诊断未返回结果",
            error_kind=ERROR_KIND_INFRA,
        )
