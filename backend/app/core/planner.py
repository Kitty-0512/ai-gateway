"""
One-shot Planner —— 任务拆解 + 工具选择（只规划一次，绝不回环）。

硬边界（写死在代码里，不交给 LLM 自由决定）：
- 最多 3 步
- 每一步必须是已注册工具
- `needs_synthesis` 由 `len(steps) > 1` 推导，Planner 的 JSON 里根本没有这个字段
- 输入按工具裁剪：只给 Planner 轻量能力上下文（有没有数据集 / 有没有日志），
  不塞完整 schema / 整段日志
- 任何异常 / 非法 JSON / 空计划 → fallback 到 Router 的单工具结果

这样永远不会退化成 ReAct 式的 Planner→Tool→Planner 循环。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.llm_client import chat_completion_async
from app.core.tools.registry import get_registry

logger = logging.getLogger(__name__)

MAX_STEPS = 3


@dataclass
class PlanStep:
    step: int
    tool: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    steps: list[PlanStep]
    source: str  # "llm" | "fallback"

    @property
    def needs_synthesis(self) -> bool:
        # 单向推导，不接受 LLM 输出该字段
        return len(self.steps) > 1

    def to_events(self) -> list[dict[str, Any]]:
        return [{"step": s.step, "tool": s.tool} for s in self.steps]


@dataclass
class PlannerContext:
    """喂给 Planner 的轻量上下文（刻意不含完整数据）。"""

    has_datasets: bool = False
    dataset_brief: str = ""       # 例如 "1 个数据集（表: sales_2024）"
    has_log: bool = False
    log_brief: str = ""           # 例如 "疑似 nginx 日志，约 120 行"


def _build_prompt(question: str, hint: str, pctx: PlannerContext) -> str:
    tools = get_registry().describe()
    tool_lines = "\n".join(f"- {t['name']}: {t['description']}" for t in tools)

    availability = []
    availability.append(
        f"- 数据集: {'可用 — ' + pctx.dataset_brief if pctx.has_datasets else '不可用'}"
    )
    availability.append(
        f"- 日志内容: {'可用 — ' + pctx.log_brief if pctx.has_log else '不可用'}"
    )
    avail_text = "\n".join(availability)

    return f"""你是 Data Agent 智能问数平台的任务规划器。根据用户问题，输出一个 JSON 执行计划。

可用工具：
{tool_lines}

当前可用数据：
{avail_text}

路由初判：{hint}

规则：
1. 只使用上面列出的工具名。
2. 事实查询（单个指标/单次聚合）→ 必须 1 步。
3. 趋势或对比：仅当需要多个独立数据集（例如站点流量表 + 关键词排名表）才能回答时，才拆成 2 步；同一张表内的趋势/对比保持 1 步。
4. 原因分析（为什么下降/上升）：先 1 步查总体趋势或对比，再 1～2 步按结果下钻（如关键词排名、分站点）；每步必须产出对下一步有价值的证据。
5. 禁止多步重复查询相同指标或几乎相同的子问题；不要为了凑步数而拆分。
6. 跨领域（既要查业务数据、又要看日志）→ 可多步，但仍最多 {MAX_STEPS} 步。
7. 最多 {MAX_STEPS} 步。
8. 只在对应数据可用时才安排该工具（如日志内容不可用则不要安排 log）。
9. 同工具可连续多步（例如多个 sql）；每步 input.question 必须是可独立执行的子问题。
10. 严格只输出 JSON，不要任何解释。

输出格式（严格遵守，不要包含其他字段）：
{{"steps": [{{"step": 1, "tool": "sql", "input": {{"question": "子问题"}}}}]}}

用户问题：{question}"""


def _fallback(hint_tool: str) -> Plan:
    return Plan(steps=[PlanStep(step=1, tool=hint_tool, input={})], source="fallback")


def _parse_plan(raw: str) -> list[PlanStep]:
    """解析 LLM 输出为受约束的步骤列表。非法则抛异常。"""
    data = json.loads(raw)
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("plan.steps 缺失或为空")

    registry = get_registry()
    steps: list[PlanStep] = []
    for idx, item in enumerate(raw_steps[:MAX_STEPS], start=1):
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool", "")).strip().lower()
        if not registry.has(tool):
            continue
        tool_input = item.get("input") if isinstance(item.get("input"), dict) else {}
        steps.append(PlanStep(step=idx, tool=tool, input=tool_input))

    if not steps:
        raise ValueError("plan 中没有有效的已注册工具步骤")

    # 重新编号，保证连续
    for i, s in enumerate(steps, start=1):
        s.step = i
    return steps


async def make_plan(
    question: str,
    hint_tool: str,
    hint_reason: str,
    pctx: PlannerContext,
) -> Plan:
    """
    生成一次性执行计划。

    任何失败都安全 fallback 到 Router 单工具（hint_tool），保证主流程不中断。
    """
    from app.core.config import get_settings

    if not get_settings().llm_configured:
        logger.info("Planner: LLM 未配置，fallback 到单工具 %s", hint_tool)
        return _fallback(hint_tool)

    prompt = _build_prompt(question, f"{hint_tool}（{hint_reason}）", pctx)
    try:
        raw = await chat_completion_async(
            "你是一个只输出 JSON 的任务规划器。", prompt
        )
        steps = _parse_plan(raw)
        # 过滤掉数据不可用的步骤
        filtered = _filter_available(steps, pctx)
        if not filtered:
            logger.warning("Planner: 过滤后无可执行步骤，fallback 到 %s", hint_tool)
            return _fallback(hint_tool)
        return Plan(steps=filtered, source="llm")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Planner: 规划失败（%s），fallback 到单工具 %s", exc, hint_tool)
        return _fallback(hint_tool)


def _filter_available(steps: list[PlanStep], pctx: PlannerContext) -> list[PlanStep]:
    out: list[PlanStep] = []
    for s in steps:
        if s.tool == "sql" and not pctx.has_datasets:
            continue
        if s.tool == "log" and not pctx.has_log:
            continue
        out.append(s)
    for i, s in enumerate(out, start=1):
        s.step = i
    return out
