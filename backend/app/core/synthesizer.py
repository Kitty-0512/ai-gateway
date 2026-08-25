"""
Synthesizer —— 仅多工具场景调用（len(steps) > 1）。

职责：把多个工具的结果综合成一份清晰报告。
- 单工具任务：orchestrator 直接 passthrough，绝不进这里（省一次模型调用）。
- 部分成功：如 SQL ✓ / Log ✗，仍进行综合，但必须明说哪部分失败。
- 全部失败：由 orchestrator 判断并跳过 Synthesizer，直接返回结构化错误。

流式输出 delta（source="synthesizer"），最终返回完整文本。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.core.config import get_settings
from app.core.llm_client import chat_completion_stream
from app.core.tools.registry import ToolResult

logger = logging.getLogger(__name__)

_SYSTEM = "你是 Data Agent 经营分析助手，负责综合多个分析工具的结果，输出清晰、有业务价值的结论。"

_TOOL_LABEL = {"sql": "业务数据分析", "log": "运维日志诊断", "mcp": "数据/文件工具"}


def _render_observation(result: ToolResult) -> str:
    label = _TOOL_LABEL.get(result.tool, result.tool)
    if not result.ok:
        return f"### {label}（失败）\n该部分分析未成功：{result.error or result.summary}"

    obs = result.observation or {}
    lines = [f"### {label}（成功）"]

    if result.tool == "sql":
        if obs.get("sql"):
            lines.append(f"执行 SQL：{obs['sql']}")
        rows = obs.get("result") or []
        lines.append(f"返回 {len(rows)} 行数据。")
        if rows:
            preview = rows[:10]
            lines.append(f"数据预览：{preview}")
        if obs.get("answer"):
            lines.append(f"分析：{obs['answer']}")
    elif result.tool == "log":
        diag = obs.get("result") or {}
        if diag.get("anomaly_type"):
            lines.append(f"异常类型：{diag['anomaly_type']}")
        if diag.get("root_cause"):
            lines.append(f"根因：{diag['root_cause']}")
        if diag.get("risk_level"):
            lines.append(f"风险等级：{diag['risk_level']}")
        if diag.get("summary"):
            lines.append(f"摘要：{diag['summary']}")
    else:
        lines.append(result.summary or str(obs)[:500])

    return "\n".join(lines)


def _build_prompt(question: str, results: list[ToolResult]) -> str:
    blocks = "\n\n".join(_render_observation(r) for r in results)
    ok_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count

    guidance = [
        "请基于以上各工具的结果，输出一份综合分析报告：",
        "- 突出业务结论，先说最重要的发现。",
        "- 明确区分「业务数据发现」与「运维/日志发现」。",
    ]
    if fail_count:
        guidance.append(
            f"- 有 {fail_count} 项分析失败，必须在报告中明确说明哪部分完成、哪部分失败，不要编造失败部分的结论。"
        )
    if ok_count >= 2:
        guidance.append("- 如两部分结论之间存在关联或矛盾，请点明可能原因。")

    return (
        f"用户的原始需求：{question}\n\n"
        f"以下是各分析工具的执行结果：\n\n{blocks}\n\n"
        + "\n".join(guidance)
    )


async def synthesize_stream(
    question: str, results: list[ToolResult]
) -> AsyncIterator[str]:
    """流式产出综合报告文本增量。LLM 未配置时给出降级拼接文本。"""
    if not get_settings().llm_configured:
        for r in results:
            yield _render_observation(r) + "\n\n"
        return

    prompt = _build_prompt(question, results)
    async for piece in chat_completion_stream(_SYSTEM, prompt):
        yield piece
