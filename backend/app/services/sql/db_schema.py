"""
数据库 Schema 查询模块。

通过 MCP 协议获取用户数据表结构、样本数据。
元数据表（datasets）仍走 SQLAlchemy 直连（网关自己的数据）。

MCP 不可用时优雅降级：返回清晰错误提示，不崩溃。
"""

import logging
from typing import Any

from sqlalchemy import inspect, text

from app.core.mcp_client import McpError, get_mcp_client
from app.services.sql.db_utils import get_db_sync

logger = logging.getLogger(__name__)


def _mcp_unavailable_msg(operation: str, detail: str = "") -> str:
    msg = f"数据库工具暂时不可用（{operation}）"
    if detail:
        msg += f": {detail}"
    return msg


def _get_table_schema_direct(table_name: str) -> list[dict[str, Any]]:
    db = get_db_sync()
    try:
        inspector = inspect(db.bind)
        columns = inspector.get_columns(table_name)
        pk_columns = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        result = []
        for col in columns:
            result.append({
                "name": str(col.get("name", "")),
                "type": str(col.get("type", "")),
                "nullable": "YES" if col.get("nullable", True) else "NO",
                "key": "PRI" if col.get("name") in pk_columns else "",
                "default": col.get("default"),
                "comment": str(col.get("comment") or ""),
            })
        return result
    finally:
        db.close()


def _get_sample_data_direct(table_name: str, n: int = 3) -> list[dict[str, Any]]:
    db = get_db_sync()
    try:
        result = db.execute(text(f"SELECT * FROM `{table_name}` LIMIT {int(n)}"))
        return [dict(row._mapping) for row in result]
    finally:
        db.close()


def get_table_schema(table_name: str) -> list[dict[str, str]]:
    """
    通过 MCP 获取表结构信息。

    Returns:
        [{name, type, nullable, key, default, comment}, ...]
    """
    mcp = get_mcp_client()
    if not mcp.is_available:
        return _get_table_schema_direct(table_name)
    try:
        return mcp.describe_table(table_name)
    except McpError:
        logger.warning("MCP 表结构查询失败，降级直连 MySQL: %s", table_name)
        return _get_table_schema_direct(table_name)
    except Exception as e:
        logger.exception("MCP describe_table 失败: %s", table_name)
        logger.warning(_mcp_unavailable_msg("表结构查询", str(e)))
        return _get_table_schema_direct(table_name)


def get_sample_data(table_name: str, n: int = 3) -> list[dict[str, Any]]:
    """通过 MCP 获取前 n 行样本数据。"""
    mcp = get_mcp_client()
    if not mcp.is_available:
        return _get_sample_data_direct(table_name, n)
    try:
        return mcp.sample_data(table_name, n)
    except McpError:
        logger.warning("MCP 样本查询失败，降级直连 MySQL: %s", table_name)
        return _get_sample_data_direct(table_name, n)
    except Exception as e:
        logger.exception("MCP sample_data 失败: %s", table_name)
        logger.warning(_mcp_unavailable_msg("样本数据查询", str(e)))
        return _get_sample_data_direct(table_name, n)


def format_schema_for_prompt(columns: list[dict[str, str]]) -> str:
    """格式化表结构为 Prompt 可读字符串。"""
    lines = []
    for col in columns:
        parts = f"- {col['name']} ({col['type']})"
        if col.get('key') == 'PRI':
            parts += " PRIMARY KEY"
        if col.get('comment'):
            parts += f" - {col['comment']}"
        lines.append(parts)
    return "\n".join(lines)


def format_sample_for_prompt(sample_data: list[dict[str, Any]]) -> str:
    """格式化样本数据为 Prompt 可读字符串（竖线分隔表格）。"""
    if not sample_data:
        return "(无数据)"
    headers = list(sample_data[0].keys())
    header_line = " | ".join(headers)
    separator = " | ".join(["---"] * len(headers))
    rows = []
    for row in sample_data:
        values = [str(v) if v is not None else "NULL" for v in row.values()]
        rows.append(" | ".join(values))
    return header_line + "\n" + separator + "\n" + "\n".join(rows)


def list_user_tables(dataset_ids: list[int]) -> list[dict]:
    """
    根据数据集 ID 列表查询对应表名和 schema。

    流程：
    1. 从 datasets 元数据表（SQLAlchemy 直连）查表名
    2. 通过 MCP 获取每张表的 schema + 样本数据
    3. MCP 失败时该表标记为不可用（不阻塞其他表）
    """
    if not dataset_ids:
        return []

    db = get_db_sync()
    try:
        ids_str = ",".join(str(did) for did in dataset_ids)
        sql = text(
            f"SELECT id, table_name, schema_json FROM datasets "
            f"WHERE id IN ({ids_str}) AND status = 1"
        )
        result = db.execute(sql)
        datasets = [dict(row._mapping) for row in result]

        result_list = []
        for ds in datasets:
            table_name = ds["table_name"]

            try:
                columns = get_table_schema(table_name)
                sample = get_sample_data(table_name, 3)
                result_list.append({
                    "dataset_id":       ds["id"],
                    "table_name":       table_name,
                    "columns":          columns,
                    "sample_data":      sample,
                    "formatted_schema": format_schema_for_prompt(columns),
                    "formatted_sample": format_sample_for_prompt(sample),
                })
            except McpError as e:
                logger.warning("MCP 获取表 %s 失败: %s", table_name, e)
                result_list.append({
                    "dataset_id":       ds["id"],
                    "table_name":       table_name,
                    "columns":          [],
                    "sample_data":      [],
                    "formatted_schema": f"(数据库工具暂时不可用: {e})",
                    "formatted_sample": "(无数据)",
                })

        return result_list
    finally:
        db.close()
