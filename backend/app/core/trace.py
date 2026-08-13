"""
轻量执行追踪（Execution Trace）。

目标只有一个：让用户/前端看到"这次分析系统到底做了什么"。

刻意不做：OpenTelemetry / Prometheus / Span / Exporter / 分布式追踪。
只是一个可 JSON 序列化的普通 dict，跟随 orchestrator 全程累积，
最终作为 SSE `trace` 事件与 `result` 一起下发给前端 ExecutionTrace 组件。

结构：
{
  "trace_id": "abc123",
  "routing": { "tool", "reason", "confidence" },
  "plan": [ { "step", "tool", "status" } ],
  "tool_calls": [
     { "step", "tool", "status", "duration_ms", "stages": [...],
       "sql?": "...", "summary?": "...", "error?": "..." }
  ],
  "synthesis": { "invoked": bool, "model?": str, "duration_ms?": int }
}
"""

from __future__ import annotations

import time
import uuid
from typing import Any


class TraceCollector:
    """收集一次分析任务的执行轨迹。非线程安全，单任务内顺序写入。"""

    def __init__(self) -> None:
        self.trace_id: str = uuid.uuid4().hex[:12]
        self._routing: dict[str, Any] = {}
        self._plan: list[dict[str, Any]] = []
        self._tool_calls: list[dict[str, Any]] = []
        self._synthesis: dict[str, Any] = {"invoked": False}
        self._tool_starts: dict[int, float] = {}

    # ---- 路由 ----
    def set_routing(self, tool: str, reason: str, confidence: str) -> None:
        self._routing = {"tool": tool, "reason": reason, "confidence": confidence}

    # ---- 计划 ----
    def set_plan(self, steps: list[dict[str, Any]]) -> None:
        self._plan = [
            {"step": s.get("step"), "tool": s.get("tool"), "status": "pending"}
            for s in steps
        ]

    def mark_plan_step(self, step: int, status: str) -> None:
        for item in self._plan:
            if item.get("step") == step:
                item["status"] = status
                break

    # ---- 工具调用 ----
    def start_tool(self, step: int, tool: str) -> None:
        self._tool_starts[step] = time.time()
        self._tool_calls.append(
            {"step": step, "tool": tool, "status": "running", "stages": []}
        )
        self.mark_plan_step(step, "running")

    def add_stage(self, step: int, stage: str) -> None:
        call = self._find_call(step)
        if call is not None and stage and stage not in call["stages"]:
            call["stages"].append(stage)

    def set_tool_field(self, step: int, key: str, value: Any) -> None:
        call = self._find_call(step)
        if call is not None:
            call[key] = value

    def finish_tool(
        self,
        step: int,
        status: str,
        summary: str | None = None,
        error: str | None = None,
    ) -> int:
        call = self._find_call(step)
        started = self._tool_starts.get(step, time.time())
        duration_ms = int((time.time() - started) * 1000)
        if call is not None:
            call["status"] = status
            call["duration_ms"] = duration_ms
            if summary is not None:
                call["summary"] = summary
            if error is not None:
                call["error"] = error
        self.mark_plan_step(step, status)
        return duration_ms

    # ---- 综合 ----
    def start_synthesis(self, model: str) -> None:
        self._synthesis = {"invoked": True, "model": model}
        self._synthesis["_start"] = time.time()

    def finish_synthesis(self) -> int:
        start = self._synthesis.pop("_start", time.time())
        duration_ms = int((time.time() - start) * 1000)
        self._synthesis["duration_ms"] = duration_ms
        return duration_ms

    # ---- 导出 ----
    def to_dict(self) -> dict[str, Any]:
        synthesis = {k: v for k, v in self._synthesis.items() if not k.startswith("_")}
        return {
            "trace_id": self.trace_id,
            "routing": self._routing,
            "plan": self._plan,
            "tool_calls": self._tool_calls,
            "synthesis": synthesis,
        }

    def _find_call(self, step: int) -> dict[str, Any] | None:
        for call in reversed(self._tool_calls):
            if call.get("step") == step:
                return call
        return None
