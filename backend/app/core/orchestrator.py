"""
Orchestrator —— 统一编排主流程。

    /api/chat/stream
          ↓
      Router（意图初判）
          ↓
      Planner（一次性规划，≤3 步）
          ↓
      Tool Registry → SQL Tool / Log Tool
          ↓
      Tool Observations
          ↓
   单工具 → 直接返回 ；多工具 → Synthesizer
          ↓
      统一 SSE 事件流

失败策略（写死）：
- 单工具：失败（含外层 retry≤2）→ 整个任务失败，返回结构化错误。
- 多工具：允许部分成功（SQL ✓ / Log ✗），进入 Synthesizer 并说明。
- 多工具全失败：跳过 Synthesizer，返回结构化错误。

外层 retry：仅对 error_kind == infra 的失败生效，最多 2 次。
SQL 逻辑错的自愈由底层 execute_sql_with_repair 负责，两层不叠加。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

from app.core.planner import PlannerContext, make_plan
from app.core.router import resolve_tool
from app.core.synthesizer import synthesize_stream
from app.core.tools.registry import (
    STATUS_FAILED,
    ToolContext,
    ToolResult,
    get_registry,
)
from app.core.trace import TraceCollector

logger = logging.getLogger(__name__)

MAX_INFRA_RETRIES = 2

Event = tuple[str, dict[str, Any]]


def _build_tool_context(payload: dict[str, Any]) -> ToolContext:
    return ToolContext(
        question=payload.get("question") or payload.get("content") or "",
        dataset_ids=payload.get("dataset_ids") or [],
        conversation_id=payload.get("conversation_id"),
        history=payload.get("history") or [],
        log_content=payload.get("content"),
        log_type=payload.get("log_type"),
        extra_context=payload.get("extra_context"),
    )


def _build_planner_context(ctx: ToolContext) -> PlannerContext:
    has_datasets = bool(ctx.dataset_ids)
    dataset_brief = f"{len(ctx.dataset_ids)} 个数据集" if has_datasets else ""

    # 统一入口里 content 常常等于 question（SQL 模式冗余填充），
    # 不能只凭"content 非空"判定有日志，必须内容真的像日志才算。
    has_log = _content_is_log(ctx.log_content, ctx.question)
    log_brief = ""
    if has_log:
        try:
            from app.services.log.log_parser import detect_log_type

            line_count = ctx.log_content.count("\n") + 1
            detected = detect_log_type(ctx.log_content[:4000]).value
            log_brief = f"约 {line_count} 行，疑似 {detected} 日志"
        except Exception:  # noqa: BLE001
            log_brief = "已提供日志内容"

    return PlannerContext(
        has_datasets=has_datasets,
        dataset_brief=dataset_brief,
        has_log=has_log,
        log_brief=log_brief,
    )


def _content_is_log(content: str | None, question: str) -> bool:
    """判断 content 是否真的是一段日志（而非与 question 重复的自然语言）。"""
    if not content or not content.strip():
        return False
    if content.strip() == (question or "").strip():
        # 与问题完全相同 —— 只有当它本身看起来像日志时才承认
        from app.core.router import _looks_like_log

        return _looks_like_log(content)
    from app.core.router import _looks_like_log

    return _looks_like_log(content) or len(content) > 200


def _tool_input_for(tool: str, step_input: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """按工具注入实际数据（重数据从 ctx 来，不从 Planner 来）。"""
    if tool == "sql":
        return {
            "question": step_input.get("question") or ctx.question,
            "dataset_ids": ctx.dataset_ids,
            "conversation_id": ctx.conversation_id,
        }
    if tool == "log":
        return {
            "content": ctx.log_content,
            "log_type": ctx.log_type,
            "extra_context": ctx.extra_context,
        }
    return dict(step_input)


async def _execute_step(
    step: int,
    tool_name: str,
    tool_input: dict[str, Any],
    ctx: ToolContext,
    trace: TraceCollector,
) -> AsyncIterator[Event]:
    """
    执行单个步骤，含外层 infra retry（≤2）。

    以 (event, data) 产出透传事件；终止时产出 ("__result__", {"result": ToolResult}).
    """
    registry = get_registry()
    tool = registry.get(tool_name)
    if tool is None:
        result = ToolResult(
            tool=tool_name,
            status=STATUS_FAILED,
            summary=f"未注册的工具: {tool_name}",
            error=f"unknown tool {tool_name}",
            error_kind="logic",
        )
        yield "__result__", {"result": result}
        return

    trace.start_tool(step, tool_name)
    yield "stage", {
        "step": step,
        "tool": tool_name,
        "stage": "tool_start",
        "label": f"开始执行：{tool.description.split('：')[0].split('（')[0]}",
    }

    attempt = 0
    result: ToolResult | None = None
    while attempt <= MAX_INFRA_RETRIES:
        attempt += 1
        if attempt > 1:
            yield "stage", {
                "step": step,
                "tool": tool_name,
                "stage": "retrying",
                "label": f"服务异常，正在重试（第 {attempt - 1} 次）…",
            }
        try:
            async for event, data in tool.run(tool_input, ctx):
                if event == "result":
                    result = data  # ToolResult
                    continue
                # 透传，打上 step/tool 标签
                tagged = {"step": step, "tool": tool_name, **data}
                if event == "delta":
                    tagged.setdefault("source", "tool")
                if event == "stage" and data.get("stage"):
                    trace.add_stage(step, data["stage"])
                if event == "sql" and data.get("sql"):
                    trace.set_tool_field(step, "sql", data["sql"])
                    if data.get("repaired"):
                        trace.set_tool_field(step, "sqlRepaired", True)
                yield event, tagged
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(
                tool=tool_name,
                status=STATUS_FAILED,
                summary=f"{tool_name} 执行异常",
                error=str(exc),
                error_kind="infra",
            )

        if result is not None and result.retryable and attempt <= MAX_INFRA_RETRIES:
            result = None  # 清空以便重试
            continue
        break

    if result is None:
        result = ToolResult(
            tool=tool_name,
            status=STATUS_FAILED,
            summary=f"{tool_name} 未返回结果",
            error="no result",
            error_kind="infra",
        )

    duration_ms = trace.finish_tool(
        step, result.status, summary=result.summary, error=result.error
    )
    yield "tool_done", {
        "step": step,
        "tool": tool_name,
        "status": result.status,
        "summary": result.summary,
        "duration_ms": duration_ms,
        "error": result.error,
    }
    yield "__result__", {"result": result}


def _single_tool_result(
    result: ToolResult, routed_tool: dict[str, Any], trace: TraceCollector
) -> dict[str, Any]:
    """单工具场景：把工具观测原样铺到顶层，兼容既有前端渲染。"""
    base: dict[str, Any] = {
        "routed_tool": routed_tool,
        "trace": trace.to_dict(),
        "tool_results": [result.to_dict()],
    }
    if result.status == STATUS_FAILED:
        base["answer"] = result.summary or "分析失败"
        base["error"] = result.error
        return base
    # success / needs_input：观测即业务响应（SQL 的 QueryResponse / Log 的 {result,meta}）
    base.update(result.observation or {})
    return base


async def run_stream(payload: dict[str, Any]) -> AsyncIterator[Event]:
    """统一编排入口，产出 (event, data) 事件流供 SSE 下发。"""
    trace = TraceCollector()
    ctx = _build_tool_context(payload)

    # ---- 1. Router：意图初判 ----
    manual_mode = (payload.get("mode") or "").strip().lower()
    if manual_mode in ("sql", "log"):
        hint_tool, hint_reason, hint_conf = manual_mode, "用户手动指定 mode", "high"
    else:
        routing = await resolve_tool(
            text=ctx.question, file_name=payload.get("file_name")
        )
        hint_tool, hint_reason, hint_conf = (
            routing.tool,
            routing.reason,
            routing.confidence,
        )

    trace.set_routing(hint_tool, hint_reason, hint_conf)
    yield "routing", {"tool": hint_tool, "reason": hint_reason, "confidence": hint_conf}

    # ---- 2. Planner：一次性规划 ----
    if manual_mode in ("sql", "log"):
        from app.core.planner import Plan, PlanStep

        plan = Plan(steps=[PlanStep(step=1, tool=manual_mode, input={})], source="manual")
    else:
        pctx = _build_planner_context(ctx)
        plan = await make_plan(ctx.question, hint_tool, hint_reason, pctx)

    trace.set_plan(plan.to_events())
    yield "plan", {
        "steps": plan.to_events(),
        "needs_synthesis": plan.needs_synthesis,
        "source": plan.source,
    }

    routed_tool = {
        "tool": hint_tool if len(plan.steps) == 1 else "multi",
        "reason": hint_reason,
        "confidence": hint_conf,
    }

    # ---- 3. 顺序执行各步骤 ----
    results: list[ToolResult] = []
    for pstep in plan.steps:
        tool_input = _tool_input_for(pstep.tool, pstep.input, ctx)
        step_result: ToolResult | None = None
        async for event, data in _execute_step(
            pstep.step, pstep.tool, tool_input, ctx, trace
        ):
            if event == "__result__":
                step_result = data["result"]
                continue
            yield event, data

        if step_result is not None:
            results.append(step_result)

        # 单工具任务：失败即整任务失败
        if len(plan.steps) == 1 and step_result and step_result.status == STATUS_FAILED:
            yield "trace", trace.to_dict()
            yield "result", _single_tool_result(step_result, routed_tool, trace)
            return

    # ---- 4. 汇总决策 ----
    if not plan.needs_synthesis:
        # 单工具：直接 passthrough
        final = results[0] if results else ToolResult(
            tool=hint_tool, status=STATUS_FAILED, summary="无结果", error_kind="infra"
        )
        yield "trace", trace.to_dict()
        yield "result", _single_tool_result(final, routed_tool, trace)
        return

    # 多工具
    successes = [r for r in results if r.ok]
    if not successes:
        # 全失败 → 跳过 Synthesizer
        yield "trace", trace.to_dict()
        yield "result", {
            "routed_tool": routed_tool,
            "trace": trace.to_dict(),
            "tool_results": [r.to_dict() for r in results],
            "answer": "多项分析均失败：" + "；".join(
                f"{r.tool}（{r.error or r.summary}）" for r in results
            ),
            "error": "all tools failed",
        }
        return

    # ---- 5. Synthesizer（仅多工具，允许部分成功）----
    from app.core.config import get_settings

    trace.start_synthesis(get_settings().openai_model)
    yield "stage", {"stage": "synthesizing", "label": "正在综合各项分析结果…"}

    answer_parts: list[str] = []
    async for piece in synthesize_stream(ctx.question, results):
        answer_parts.append(piece)
        yield "delta", {"text": piece, "source": "synthesizer"}
    trace.finish_synthesis()

    answer = "".join(answer_parts).strip() or "综合分析完成。"

    yield "trace", trace.to_dict()
    yield "result", {
        "routed_tool": routed_tool,
        "trace": trace.to_dict(),
        "tool_results": [r.to_dict() for r in results],
        "answer": answer,
        "synthesized": True,
    }
