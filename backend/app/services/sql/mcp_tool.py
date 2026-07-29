"""
SQL 查询工具层。

- query_mysql: 通过 MCP Server 执行 SQL（LangChain @tool）
- write_mcp_log:  审计日志写入 mcp_logs 表（SQLAlchemy 直连）
"""

import json
import logging
import time
from typing import Any

from langchain_core.tools import tool
from sqlalchemy import text

from app.core.mcp_client import get_mcp_client
from app.services.sql.db_utils import get_db_sync, validate_sql_safe

logger = logging.getLogger(__name__)


def _execute_query_direct(sql: str) -> list[dict[str, Any]]:
    db = get_db_sync()
    try:
        result = db.execute(text(sql))
        return [dict(row._mapping) for row in result]
    finally:
        db.close()


def write_mcp_log(
    message_id: int | None,
    tool_name: str,
    input_params: dict,
    output_result: dict | list | None,
    duration_ms: int,
    is_success: bool,
    error_message: str | None = None,
):
    """审计日志写入 mcp_logs 表（网关自己的元数据，不走 MCP）。"""
    db = get_db_sync()
    try:
        sql = text("""
            INSERT INTO mcp_logs (message_id, tool_name, input_params, output_result,
                                   duration_ms, is_success, error_message)
            VALUES (:message_id, :tool_name, :input_params, :output_result,
                    :duration_ms, :is_success, :error_message)
        """)
        db.execute(sql, {
            "message_id":     message_id,
            "tool_name":      tool_name,
            "input_params":   json.dumps(input_params, ensure_ascii=False, default=str),
            "output_result":  json.dumps(output_result, ensure_ascii=False, default=str) if output_result else None,
            "duration_ms":    duration_ms,
            "is_success":     1 if is_success else 0,
            "error_message":  error_message[:2000] if error_message else None,
        })
        db.commit()
    except Exception as e:
        logger.error(f"写入 MCP 日志失败: {e}")
        db.rollback()
    finally:
        db.close()


@tool
def query_mysql(sql: str, table_names: list[str] | None = None) -> dict[str, Any]:
    """
    通过 MCP Server 执行 MySQL SELECT 查询并返回结果。

    安全校验（SELECT-only 检查）仍在本地执行，
    实际的数据库查询通过 MCP 协议转发给 MySQL MCP Server。
    """
    start_time = time.time()
    input_params = {"sql": sql[:500], "table_names": table_names}

    try:
        validate_sql_safe(sql)

        mcp = get_mcp_client()
        if mcp.is_available:
            try:
                mcp.ensure_connected()
                result = mcp.execute_query(sql)
            except Exception as exc:
                logger.warning("MCP 查询失败，降级直连 MySQL: %s", exc)
                result = _execute_query_direct(sql)
        else:
            result = _execute_query_direct(sql)
        elapsed = int((time.time() - start_time) * 1000)

        write_mcp_log(
            message_id=None,
            tool_name="query_mysql",
            input_params=input_params,
            output_result={"row_count": len(result)},
            duration_ms=elapsed,
            is_success=True,
        )

        return {
            "success": True,
            "data": result,
            "row_count": len(result),
            "error": None,
        }

    except Exception as e:
        elapsed = int((time.time() - start_time) * 1000)
        error_msg = str(e)

        write_mcp_log(
            message_id=None,
            tool_name="query_mysql",
            input_params=input_params,
            output_result=None,
            duration_ms=elapsed,
            is_success=False,
            error_message=error_msg,
        )

        return {
            "success": False,
            "data": [],
            "row_count": 0,
            "error": error_msg,
        }
