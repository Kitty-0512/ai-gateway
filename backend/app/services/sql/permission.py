"""
行/列级权限过滤 — 最小可行版本。

支持范围：
- ✅ 简单 SELECT ... FROM ... WHERE ...（单层）
- ✅ JOIN（INNER/LEFT/RIGHT）
- ✅ 列白名单过滤（结果返回前裁剪字段）

明确不支持（检测到后跳过权限注入，记录 warning）：
- ❌ 子查询（WHERE col IN (SELECT ...) / FROM (SELECT ...)）
- ❌ UNION / UNION ALL
- ❌ CTE（WITH ... AS (...)）
- ❌ 嵌套 SELECT 表达式

设计原则：宁可跳过权限过滤，也不破坏 SQL。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# =============================================================================
# SQL 复杂度检测
# =============================================================================

# 子查询特征：SELECT 出现在括号中、或关键字后跟 SELECT
_SUBQUERY_PATTERN = re.compile(
    r"\(\s*SELECT\b|IN\s*\(\s*SELECT\b|=\s*\(\s*SELECT\b",
    re.IGNORECASE,
)
_UNION_PATTERN = re.compile(r"\bUNION\s+(ALL\s+)?SELECT\b", re.IGNORECASE)
_CTE_PATTERN = re.compile(r"\bWITH\s+\w+\s+AS\s*\(", re.IGNORECASE)


def is_simple_select(sql: str) -> bool:
    """
    检查是否为"简单"SELECT（可用权限过滤）。
    返回 False 表示 SQL 太复杂，跳过权限注入。
    """
    stripped = sql.strip()
    if not stripped.upper().startswith("SELECT"):
        return False
    if _SUBQUERY_PATTERN.search(stripped):
        logger.info("[permission] 检测到子查询，跳过权限过滤")
        return False
    if _UNION_PATTERN.search(stripped):
        logger.info("[permission] 检测到 UNION，跳过权限过滤")
        return False
    if _CTE_PATTERN.search(stripped):
        logger.info("[permission] 检测到 CTE (WITH)，跳过权限过滤")
        return False
    return True


# =============================================================================
# 行级权限：WHERE 条件注入
# =============================================================================

def inject_row_filter(sql: str, row_filter: str, table_name: str) -> str:
    """
    向 SQL 注入行级 WHERE 条件。

    注入策略：
    - SELECT ... FROM t WHERE existing → SELECT ... FROM t WHERE existing AND (filter)
    - SELECT ... FROM t (无 WHERE)    → SELECT ... FROM t WHERE (filter)
    - 仅注入到与 table_name 匹配的 FROM / JOIN 子句之后的 WHERE 中

    Args:
        sql: 原始 SQL
        row_filter: 要注入的 WHERE 条件（如 "workspace_id = 1"）
        table_name: 目标表名（用于未来扩展多表注入，当前仅注入到全局 WHERE）

    Returns:
        注入后的 SQL
    """
    if not row_filter or not row_filter.strip():
        return sql
    if not is_simple_select(sql):
        logger.warning(
            "[permission] SQL 不是简单 SELECT，跳过行级权限注入 "
            "(sql=%s...)", sql[:80]
        )
        return sql

    filter_clause = f"({row_filter.strip()})"

    # 策略 1: 已有 WHERE → 用括号包裹原 WHERE 子句，再追加 AND (filter)
    # 关键：必须用括号包裹，防止 OR 运算符优先级导致越权
    #   错误: WHERE a=1 OR b=2 AND filter → a=1 OR (b=2 AND filter)  ← 越权!
    #   正确: WHERE (a=1 OR b=2) AND (filter)
    where_pattern = re.compile(r"\bWHERE\b", re.IGNORECASE)
    tail_pattern = re.compile(
        r"\b(GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET)\b",
        re.IGNORECASE,
    )

    if where_pattern.search(sql):
        where_match = where_pattern.search(sql)
        where_pos = where_match.end()

        # 找 WHERE 子句结束位置
        tail_match = tail_pattern.search(sql[where_pos:])
        if tail_match:
            where_end = where_pos + tail_match.start()
        else:
            where_end = len(sql)

        original_where = sql[where_pos:where_end].strip()
        before_where = sql[:where_pos].rstrip()
        after_where = sql[where_end:]

        # 关键修复：用括号包裹原始 WHERE 条件
        return f"{before_where} ({original_where}) AND {filter_clause}{after_where}"

    # 策略 2: 没有 WHERE → 在 FROM 之后、GROUP BY 之前插入 WHERE
    from_pattern = re.compile(r"\bFROM\s+(\w+|`[^`]+`)\b", re.IGNORECASE)
    from_match = from_pattern.search(sql)
    if not from_match:
        logger.warning("[permission] 找不到 FROM 子句，跳过行级权限注入")
        return sql

    # FROM t 之后插入 WHERE
    from_end = from_match.end()

    tail_match = tail_pattern.search(sql[from_end:])
    if tail_match:
        insert_pos = from_end + tail_match.start()
        before = sql[:insert_pos].rstrip()
        after = sql[insert_pos:]
        return f"{before}\nWHERE {filter_clause}\n{after}"
    else:
        return f"{sql.rstrip().rstrip(';')}\nWHERE {filter_clause}"


# =============================================================================
# 列级权限：字段白名单过滤
# =============================================================================

def filter_result_columns(
    data: list[dict],
    allowed_columns: list[str] | None,
) -> list[dict]:
    """
    从结果中移除不在白名单中的列。

    Args:
        data: 查询结果行列表
        allowed_columns: 允许展示的列名白名单，None/空 = 不过滤

    Returns:
        过滤后的行列表（每行仅保留白名单字段）
    """
    if not allowed_columns or not data:
        return data

    allowed_set = {c.lower() for c in allowed_columns}
    # 需要保留的列：取白名单与实际列的交集
    actual_cols = list(data[0].keys())
    keep_cols = [c for c in actual_cols if c.lower() in allowed_set]

    if len(keep_cols) == len(actual_cols):
        # 全部通过，无需过滤
        return data

    removed = [c for c in actual_cols if c.lower() not in allowed_set]
    logger.info(
        "[permission] 列级过滤: 保留 %d/%d 列, 移除: %s",
        len(keep_cols), len(actual_cols), removed,
    )

    return [{c: row[c] for c in keep_cols} for row in data]
