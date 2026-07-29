"""
SQL 执行失败自动修复模块。

职责（仅限以下）：
- 捕获 SQL 执行异常
- 构造修复上下文（原始问题 + 失败SQL + 错误信息 + Schema + 重试次数）
- 调用 LLM 生成修复后的 SQL
- 控制重试次数（最多3次）
- 返回最终执行结果

明确不负责：
- SQL 首次生成（sql_generator.py）
- SQL 安全校验（sql_validator.py + db_utils.py）
- Schema 管理（schema_controller.py）
- 数据分析/图表推荐（sql_generator.py）
- 消息持久化（sql_generator.py）
"""

import re
import time
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.llm_client import call_llm
from app.services.sql.prompt_templates import SQL_REPAIR_PROMPT
from app.services.sql.schema_controller import build_schema_for_prompt
from app.services.sql.sql_validator import verify_columns_exist, build_table_schemas
from app.services.sql.db_utils import validate_sql_safe
from app.services.sql.mcp_tool import query_mysql, write_mcp_log

logger = logging.getLogger(__name__)

# =============================================================================
# 配置常量
# =============================================================================
MAX_RETRY_COUNT = 3  # 最大修复重试次数

# 不可修复的错误特征（匹配 MySQL 错误信息，命中任一即提前终止重试）
NON_RETRYABLE_PATTERNS: tuple[str, ...] = (
    "access denied",
    "select command denied",
    "connection refused",
    "can't connect",
    "lost connection",
    "max_execution_time",
    "lock wait timeout",
    "readonly",
    "server has gone away",
)


# =============================================================================
# 公开 API
# =============================================================================

def execute_sql_with_repair(
    sql: str,
    table_info: list[dict[str, Any]],
    question: str,
) -> dict[str, Any]:
    """
    执行 SQL，失败时自动调用 LLM 修复并重试。

    这是 sql_retry 模块的唯一对外入口。

    流程:
        1. 首次执行 SQL
        2. 成功 → 直接返回
        3. 失败 → 判断是否可修复
        4. 可修复 → 进入修复循环（最多 MAX_RETRY_COUNT 次）
           a. 构造 SQL_REPAIR_PROMPT
           b. LLM 生成修复 SQL
           c. 安全校验（validate_sql_safe）
           d. 字段校验（verify_columns_exist）
           e. 再次执行
        5. 成功/最终失败 → 写入修复汇总日志 → 返回

    Args:
        sql: 原始 SQL（由 sql_generator 生成并通过了首次安全校验）
        table_info: 表结构信息，格式 [{"table_name": "...", "columns": [...]}, ...]
        question: 用户原始自然语言问题

    Returns:
        {
            "success": bool,
            "data": list[dict],        # 查询结果行
            "row_count": int,          # 结果行数
            "final_sql": str,          # 最终成功的 SQL（可能已被修复）
            "error": str | None,       # 失败时的错误信息
            "retry_count": int,        # 实际修复次数（0 = 首次即成功）
        }
    """
    start_time = time.time()
    retry_count = 0
    repair_history: list[dict[str, Any]] = []
    current_sql = sql
    last_error: str | None = None

    # ---- 第一次执行 ----
    first_result = query_mysql.invoke({
        "sql": current_sql,
        "table_names": [t["table_name"] for t in table_info],
    })

    if not first_result.get("error"):
        # 首次成功，无需修复
        return {
            "success": True,
            "data": first_result.get("data", []),
            "row_count": first_result.get("row_count", 0),
            "final_sql": current_sql,
            "error": None,
            "retry_count": 0,
        }

    # 首次失败
    last_error = first_result.get("error", "")
    logger.warning(
        f"[sql_retry] SQL 首次执行失败 "
        f"(question='{question[:60]}...', error='{last_error[:150]}')"
    )

    # 判断是否可以修复
    if not _is_retryable(last_error):
        logger.info(
            f"[sql_retry] 错误类型不可修复，跳过重试 "
            f"(error='{last_error[:100]}')"
        )
        _write_summary_log(
            question=question,
            first_sql=sql,
            last_error=last_error,
            final_sql=None,
            retry_count=0,
            success=False,
            total_time_s=time.time() - start_time,
            repair_history=repair_history,
        )
        return {
            "success": False,
            "data": [],
            "row_count": 0,
            "final_sql": current_sql,
            "error": last_error,
            "retry_count": 0,
        }

    # ---- 修复循环 ----
    while retry_count < MAX_RETRY_COUNT:
        retry_count += 1
        logger.info(
            f"[sql_retry] 进入第 {retry_count}/{MAX_RETRY_COUNT} 次修复 "
            f"(question='{question[:60]}...')"
        )

        # 1. 调用 LLM 修复 SQL（携带之前所有尝试的历史）
        repaired_sql = _call_llm_repair(
            question=question,
            failed_sql=current_sql,
            error_msg=last_error,
            table_info=table_info,
            retry_count=retry_count,
            repair_history=repair_history,
        )

        if repaired_sql is None:
            logger.warning(f"[sql_retry] LLM 未能生成有效的修复 SQL，终止重试")
            repair_history.append({
                "attempt": retry_count,
                "error": last_error[:300],
                "repaired_sql": None,
                "result": "LLM返回CANNOT_FIX或无法解析",
            })
            break

        # 2. 安全校验（与首次 SQL 走完全相同的校验路径）
        if not _validate_repaired_sql(repaired_sql, table_info):
            # 校验失败时：用校验错误作为新的错误信息，继续下一轮重试
            # （LLM 可能在下一轮修复中修正这些问题）
            logger.warning(
                f"[sql_retry] 修复后 SQL 未通过校验，继续重试 "
                f"(sql='{repaired_sql[:100]}...')"
            )
            repair_history.append({
                "attempt": retry_count,
                "error": last_error[:300],
                "repaired_sql": repaired_sql[:300],
                "result": "安全/字段校验未通过",
            })
            current_sql = repaired_sql
            last_error = "修复后SQL未通过安全校验或包含不存在的字段"
            continue

        # 3. 重新执行
        result = query_mysql.invoke({
            "sql": repaired_sql,
            "table_names": [t["table_name"] for t in table_info],
        })

        if not result.get("error"):
            # 修复成功！
            elapsed = time.time() - start_time
            logger.info(
                f"[sql_retry] ✅ 第 {retry_count} 次修复成功 "
                f"(总耗时 {elapsed:.1f}s, SQL='{repaired_sql[:80]}...')"
            )
            _write_summary_log(
                question=question,
                first_sql=sql,
                last_error=None,
                final_sql=repaired_sql,
                retry_count=retry_count,
                success=True,
                total_time_s=elapsed,
                repair_history=repair_history,
            )
            return {
                "success": True,
                "data": result.get("data", []),
                "row_count": result.get("row_count", 0),
                "final_sql": repaired_sql,
                "error": None,
                "retry_count": retry_count,
            }

        # 修复后仍失败
        new_error = result.get("error", "")
        logger.warning(
            f"[sql_retry] 第 {retry_count} 次修复后执行仍失败 "
            f"(error='{new_error[:150]}')"
        )
        repair_history.append({
            "attempt": retry_count,
            "error": last_error[:300],
            "repaired_sql": repaired_sql[:300],
            "result": f"执行失败: {new_error[:200]}",
        })

        current_sql = repaired_sql
        last_error = new_error

        # 新错误是否可修复
        if not _is_retryable(new_error):
            logger.info(f"[sql_retry] 新错误不可修复，提前终止重试")
            break

    # ---- 所有重试均失败 ----
    elapsed = time.time() - start_time
    logger.error(
        f"[sql_retry] ❌ 修复失败：{retry_count} 次重试后仍无法执行 "
        f"(question='{question[:60]}...', final_error='{last_error[:150] if last_error else 'unknown'}')"
    )
    _write_summary_log(
        question=question,
        first_sql=sql,
        last_error=last_error,
        final_sql=current_sql,
        retry_count=retry_count,
        success=False,
        total_time_s=elapsed,
        repair_history=repair_history,
    )
    return {
        "success": False,
        "data": [],
        "row_count": 0,
        "final_sql": current_sql,
        "error": last_error,
        "retry_count": retry_count,
    }


# =============================================================================
# 内部函数
# =============================================================================

def _is_retryable(error_msg: str) -> bool:
    """
    判断数据库错误是否值得通过 LLM 修复重试。

    可修复的错误类型（LLM 可调整 SQL 修正）:
        - Unknown column（字段不存在/写错）
        - Table doesn't exist（表名错误）
        - Truncated incorrect（类型转换错误）
        - SQL syntax error（语法错误）
        - GROUP BY 错误
        - 聚合函数使用错误

    不可修复的错误类型（SQL 修改无法解决，需运维介入）:
        - 权限错误
        - 数据库连接失败
        - 查询超时
        - 锁等待超时
    """
    for pattern in NON_RETRYABLE_PATTERNS:
        if pattern in error_msg.lower():
            return False
    return True


def _call_llm_repair(
    question: str,
    failed_sql: str,
    error_msg: str,
    table_info: list[dict[str, Any]],
    retry_count: int,
    repair_history: list[dict[str, Any]] | None = None,
) -> str | None:
    """
    调用 LLM 生成修复后的 SQL。

    Args:
        question: 用户原始问题
        failed_sql: 执行失败的 SQL
        error_msg: 数据库返回的错误信息
        table_info: 表结构信息
        retry_count: 当前重试次数（1-based）
        repair_history: 之前的修复尝试记录 [{attempt, error, repaired_sql, result}, ...]

    Returns:
        修复后的 SQL 字符串，或 None（LLM 返回 CANNOT_FIX 或解析失败）
    """
    primary_table = table_info[0]["table_name"]
    schema_text = build_schema_for_prompt(table_info, question)

    # 构建修复历史块
    history_block = ""
    if repair_history:
        lines = ["\n## 之前尝试过的修复（请不要再生成相同的 SQL）"]
        for h in repair_history:
            lines.append(f"\n### 第 {h.get('attempt', '?')} 次尝试")
            if h.get("repaired_sql"):
                lines.append(f"生成的 SQL: ```sql\n{h['repaired_sql'][:500]}\n```")
            if h.get("error"):
                lines.append(f"报错信息: {h['error'][:300]}")
            if h.get("result"):
                lines.append(f"结果: {h['result'][:200]}")
        history_block = "\n".join(lines)

    repair_prompt = SQL_REPAIR_PROMPT.format(
        question=question,
        sql=failed_sql,
        error=error_msg,
        table_name=primary_table,
        schema=schema_text,
        retry_count=retry_count,
        repair_history=history_block,
    )

    messages = [
        SystemMessage(content="你是一个 MySQL SQL 修复专家。只输出修复后的 SQL 语句，不要任何解释、注释或 markdown。"),
        HumanMessage(content=repair_prompt),
    ]

    try:
        llm_response = call_llm(messages)
        raw = llm_response.content.strip()
        logger.debug(f"[sql_retry] LLM 修复响应 (前200字符): {raw[:200]}")
    except Exception as e:
        logger.error(f"[sql_retry] LLM 修复调用异常: {e}")
        return None

    return _extract_sql(raw)


def _extract_sql(raw_response: str) -> str | None:
    """
    从 LLM 返回文本中提取纯 SQL。

    按优先级尝试：
        1. CANNOT_FIX 检测 → 返回 None
        2. ```sql ... ``` 代码块提取
        3. 以 SELECT 开头的纯 SQL
        4. 正则兜底：找到 SELECT ... 语句
    """
    content = raw_response.strip()

    # 1. LLM 声明无法修复
    if content.upper().startswith("CANNOT_FIX"):
        logger.info(f"[sql_retry] LLM 返回 CANNOT_FIX: {content[:150]}")
        return None

    # 2. 提取 markdown 代码块
    pattern = r"```(?:sql)?\s*\n?(.*?)\n?```"
    matches = re.findall(pattern, content, re.DOTALL)
    if matches:
        return _clean_sql(matches[0].strip())

    # 3. 直接以 SELECT 开头
    if content.upper().startswith("SELECT"):
        return _clean_sql(content)

    # 4. 正则兜底：找到 SELECT 语句（处理 LLM 前面加了解释文字的情况）
    sql_match = re.search(
        r'(SELECT\s+.+?)(?:;|\n\n|\Z)',
        content,
        re.IGNORECASE | re.DOTALL,
    )
    if sql_match:
        return _clean_sql(sql_match.group(1).strip())

    logger.warning(f"[sql_retry] 无法从 LLM 回复中提取 SQL: {content[:200]}")
    return None


def _clean_sql(sql: str) -> str:
    """清洗 SQL：去末尾分号、去首尾空白"""
    return sql.strip().rstrip(";")


def _validate_repaired_sql(
    sql: str,
    table_info: list[dict[str, Any]],
) -> bool:
    """
    对修复后的 SQL 执行双重校验（安全 + 字段存在性）。

    这与首次 SQL 执行的校验路径完全一致：
        - validate_sql_safe():  正则黑名单（禁止 DROP/ALTER/DELETE/...）
        - verify_columns_exist(): 字段存在性校验

    Returns:
        True 如果通过所有校验
    """
    # 校验 1：SQL 安全（只允许 SELECT）
    try:
        validate_sql_safe(sql)
    except ValueError as e:
        logger.warning(f"[sql_retry] SQL 安全校验失败: {e}")
        return False

    # 校验 2：字段存在性
    table_schemas = build_table_schemas(table_info)
    primary_table = table_info[0]["table_name"] if table_info else None
    cols_ok, missing_cols = verify_columns_exist(sql, table_schemas, primary_table)
    if not cols_ok:
        logger.warning(f"[sql_retry] 字段校验失败 - 不存在字段: {missing_cols}")
        return False

    return True


def _write_summary_log(
    question: str,
    first_sql: str,
    last_error: str | None,
    final_sql: str | None,
    retry_count: int,
    success: bool,
    total_time_s: float,
    repair_history: list[dict[str, Any]],
) -> None:
    """
    写入修复汇总日志到 mcp_logs 表。

    复用已有的 write_mcp_log() 函数和 mcp_logs 表结构。
    使用 tool_name="sql_repair" 与普通的 query_mysql 调用区分。
    """
    try:
        write_mcp_log(
            message_id=None,  # 由 sql_generator 的 update_mcp_logs_with_message_id 回填
            tool_name="sql_repair",
            input_params={
                "question": question[:500],
                "first_sql": first_sql[:500],
                "retry_count": retry_count,
                "max_retries": MAX_RETRY_COUNT,
                "repair_history": repair_history,
            },
            output_result={
                "success": success,
                "final_sql": final_sql[:500] if final_sql else None,
                "total_time_ms": int(total_time_s * 1000),
            },
            duration_ms=int(total_time_s * 1000),
            is_success=success,
            error_message=(last_error[:2000] if last_error else None),
        )
    except Exception as e:
        logger.error(f"[sql_retry] 写入修复汇总日志失败: {e}")
