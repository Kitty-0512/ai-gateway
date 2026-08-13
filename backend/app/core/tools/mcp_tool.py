"""
MCP Tool —— 辅助工具（可选，默认不注册）。

定位：SQL 分析 / 日志诊断是核心；MCP 是"锦上添花"的补充能力，
只提供 3 个明确、收敛的动作，绝不做成"什么都能调"的万能工具：

    - mysql_query   : 执行一条只读 SQL（ad-hoc，非 Text-to-SQL 流程）
    - describe_table: 查看某张表结构
    - read_file     : 读取沙箱内已上传文件

按计划默认 **不** 注册进 Registry（保持 Planner 只在 sql/log 间选择）。
需要时通过 settings.mcp_tools_enabled=true 或显式调用 register_mcp_tool() 开启。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from app.core.tools.registry import (
    STATUS_FAILED,
    STATUS_SUCCESS,
    ToolContext,
    ToolResult,
)

_VALID_ACTIONS = ("mysql_query", "describe_table", "read_file")


def _infer_action(tool_input: dict[str, Any]) -> str | None:
    action = str(tool_input.get("action", "")).strip().lower()
    if action in _VALID_ACTIONS:
        return action
    if tool_input.get("sql"):
        return "mysql_query"
    if tool_input.get("table") or tool_input.get("table_name"):
        return "describe_table"
    if tool_input.get("path"):
        return "read_file"
    return None


def _run_mysql_query(sql: str) -> list[dict[str, Any]]:
    from app.services.sql.db_utils import validate_sql_safe

    validate_sql_safe(sql)  # 仅允许只读 SELECT
    from app.core.mcp_client import get_mcp_client

    mcp = get_mcp_client()
    if mcp.is_available:
        mcp.ensure_connected()
        return mcp.execute_query(sql)
    from app.services.sql.mcp_tool import _execute_query_direct

    return _execute_query_direct(sql)


def _describe_table(table: str) -> list[dict[str, Any]]:
    from app.services.sql.db_schema import get_table_schema

    return get_table_schema(table)


def _read_file(path: str) -> str:
    from app.core.fs_mcp_client import get_fs_mcp_client

    fs = get_fs_mcp_client()
    fs.ensure_connected()
    raw = fs.read_file(path)
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


class McpTool:
    name = "mcp"
    description = (
        "数据/文件辅助工具：执行只读 SQL(mysql_query)、查看表结构(describe_table)、"
        "读取已上传文件(read_file)。仅在用户明确要求原始 SQL/文件操作时使用。"
    )

    async def run(
        self, tool_input: dict[str, Any], ctx: ToolContext
    ) -> AsyncIterator[tuple[str, Any]]:
        action = _infer_action(tool_input)
        if action is None:
            yield "result", ToolResult(
                tool=self.name,
                status=STATUS_FAILED,
                summary="未指定有效的 MCP 动作",
                error=f"action 必须是 {_VALID_ACTIONS} 之一",
                error_kind="logic",
            )
            return

        yield "stage", {"stage": "executing", "label": f"MCP 执行：{action}"}

        try:
            if action == "mysql_query":
                data = await asyncio.to_thread(
                    _run_mysql_query, tool_input.get("sql", "")
                )
                summary = f"查询返回 {len(data)} 行"
                observation = {"action": action, "rows": data}
            elif action == "describe_table":
                table = tool_input.get("table") or tool_input.get("table_name") or ""
                data = await asyncio.to_thread(_describe_table, table)
                summary = f"表 {table} 共 {len(data)} 个字段"
                observation = {"action": action, "schema": data}
            else:  # read_file
                text = await asyncio.to_thread(_read_file, tool_input.get("path", ""))
                summary = f"读取文件 {len(text)} 字符"
                observation = {"action": action, "content": text[:4000]}
        except Exception as exc:  # noqa: BLE001
            yield "result", ToolResult(
                tool=self.name,
                status=STATUS_FAILED,
                summary=f"MCP {action} 执行失败",
                error=str(exc),
                error_kind="infra",
            )
            return

        yield "result", ToolResult(
            tool=self.name,
            status=STATUS_SUCCESS,
            summary=summary,
            observation=observation,
        )


def register_mcp_tool(registry) -> None:
    """显式把 MCP 辅助工具注册进 Registry（opt-in）。"""
    registry.register(McpTool())
