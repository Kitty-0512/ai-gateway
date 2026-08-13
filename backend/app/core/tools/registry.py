"""
Tool Registry —— 业务层与执行层解耦。

Planner 只认识工具名（"sql" / "log" / "mcp"），不需要知道底层是
sql_generator.py / diagnoser.py / mcp_client.py。

每个工具实现 BaseTool 协议：
    async def run(tool_input, ctx) -> AsyncIterator[tuple[str, Any]]

工具以 (event, data) 元组流式产出，约定事件：
    ("stage", {...})            阶段进度（透传给前端流水线 UI）
    ("sql",   {...})            SQL 生成/修复（SQL 工具专用）
    ("delta", {"text": ...})    流式文本增量（打字机）
    ("result", ToolResult)      终止事件，携带结构化 ToolResult

orchestrator 负责把 stage/sql/delta 打上 step/tool 标签转发到统一 SSE，
并从终止事件里取出 ToolResult 决定后续（成功/失败/是否综合）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# 工具执行状态
STATUS_SUCCESS = "success"
STATUS_NEEDS_INPUT = "needs_input"
STATUS_FAILED = "failed"

# 失败类别：区分"业务逻辑失败"与"基础设施失败"，决定外层是否 retry
ERROR_KIND_LOGIC = "logic"   # SQL 逻辑错、日志无法诊断等 —— 外层不 retry
ERROR_KIND_INFRA = "infra"   # 超时/连接/LLM 不可用 —— 外层可 retry（≤2）


@dataclass
class ToolResult:
    """工具执行的结构化结果。"""

    tool: str
    status: str
    summary: str = ""
    observation: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS

    @property
    def retryable(self) -> bool:
        return self.status == STATUS_FAILED and self.error_kind == ERROR_KIND_INFRA

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "status": self.status,
            "summary": self.summary,
            "observation": self.observation,
            "error": self.error,
            "error_kind": self.error_kind,
        }


@dataclass
class ToolContext:
    """一次工具调用的上下文（由 orchestrator 组装）。"""

    question: str = ""
    dataset_ids: list[int] = field(default_factory=list)
    conversation_id: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    log_content: str | None = None
    log_type: str | None = None
    extra_context: str | None = None


@runtime_checkable
class BaseTool(Protocol):
    """工具协议。"""

    name: str
    description: str

    def run(
        self, tool_input: dict[str, Any], ctx: ToolContext
    ) -> AsyncIterator[tuple[str, Any]]:
        ...


class ToolRegistry:
    """工具注册表（单例）。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def describe(self) -> list[dict[str, str]]:
        """供 Planner 参考的工具能力清单。"""
        return [
            {"name": t.name, "description": t.description}
            for t in self._tools.values()
        ]


_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """获取全局工具注册表，首次调用时注册内置工具（sql / log）。"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        # 延迟导入避免循环依赖
        from app.core.tools.log_tool import LogTool
        from app.core.tools.sql_tool import SqlTool

        _registry.register(SqlTool())
        _registry.register(LogTool())

        # MCP Tool 为可选辅助能力：默认不注册，保持 Planner 只在 sql/log 间选择。
        # 需要时通过 settings.mcp_tools_enabled=true 开启（见 mcp_tool.py）。
        try:
            from app.core.config import get_settings

            if get_settings().mcp_tools_enabled:
                from app.core.tools.mcp_tool import register_mcp_tool

                register_mcp_tool(_registry)
        except Exception:  # noqa: BLE001 —— 配置缺失不应阻断核心工具注册
            pass
    return _registry
