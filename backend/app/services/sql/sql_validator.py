"""
SQL 字段存在性校验工具。

在 LLM 生成 SQL 后、执行前，提取 SQL 中引用的字段名，
与目标表的实际字段列表对比，防止 LLM 使用不存在的字段。
"""

import re
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)


def extract_columns_from_sql(sql: str) -> List[str]:
    """
    从 SQL 语句中提取引用的字段名。

    从 SELECT、WHERE、GROUP BY、ORDER BY、HAVING、JOIN ON 等子句中
    提取字段名。只处理反引号包裹或非关键字的纯名字段。

    Args:
        sql: 原始 SQL 语句

    Returns:
        去重后的字段名列表
    """
    columns = set()

    # 1. 匹配反引号包裹的字段: `字段名`
    backtick_cols = re.findall(r"`([^`]+)`", sql)
    columns.update(backtick_cols)

    # 2. 匹配聚合函数中的字段: SUM(`col`), COUNT(col), MAX(col)
    agg_pattern = re.findall(
        r"(SUM|COUNT|AVG|MAX|MIN|ROUND|STDDEV|STD|VARIANCE|GROUP_CONCAT)\s*\(\s*(?:DISTINCT\s+)?`?([a-z_\u4e00-\u9fff]+)`?\s*\)",
        sql,
        re.IGNORECASE,
    )
    for _, col in agg_pattern:
        if col.upper() not in ("DISTINCT", "ALL"):
            columns.add(col)

    # 3. 从 WHERE/ORDER BY/GROUP BY/HAVING/JOIN ON 中提取裸字段名
    #    (不含反引号的字段，排除 SQL 关键字)
    sql_keywords = {
        "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "IS", "NULL",
        "AS", "ON", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS",
        "ORDER", "BY", "GROUP", "HAVING", "LIMIT", "OFFSET", "ASC", "DESC",
        "DISTINCT", "ALL", "UNION", "BETWEEN", "LIKE", "EXISTS", "CASE",
        "WHEN", "THEN", "ELSE", "END", "TRUE", "FALSE",
        "SUM", "COUNT", "AVG", "MAX", "MIN", "ROUND", "STDDEV", "STD",
        "VARIANCE", "GROUP_CONCAT", "COALESCE", "IFNULL", "CAST", "CONVERT",
        "DATE", "YEAR", "MONTH", "DAY", "HOUR", "MINUTE", "SECOND",
        "DATE_FORMAT", "STR_TO_DATE", "NOW", "CURDATE",
        "GREATEST", "LEAST", "ABS", "FLOOR", "CEIL", "POWER", "MOD",
        "CONCAT", "SUBSTRING", "TRIM", "UPPER", "LOWER", "LENGTH",
        "REPLACE", "LOCATE", "INSTR",
    }

    # 移除字符串字面量中的内容，避免误匹配
    cleaned = re.sub(r"'[^']*'", "", sql)
    cleaned = re.sub(r'"[^"]*"', "", cleaned)

    # 提取 SELECT 之后的字段列表（逗号分隔，排除函数调用）
    select_match = re.search(
        r"SELECT\s+(.*?)\s+FROM",
        cleaned,
        re.IGNORECASE | re.DOTALL,
    )
    if select_match:
        select_part = select_match.group(1)
        # 替换函数调用为占位符，避免函数名被当字段
        select_part = re.sub(
            r"\b(SUM|COUNT|AVG|MAX|MIN|ROUND|STDDEV|GROUP_CONCAT|COALESCE|IFNULL|CAST|DATE_FORMAT|CONCAT|GREATEST|LEAST|ABS|FLOOR|CEIL|YEAR|MONTH|DAY)\s*\([^)]*\)",
            "",
            select_part,
            flags=re.IGNORECASE,
        )
        # 分割逗号，提取可能的字段名
        parts = re.split(r",", select_part)
        for part in parts:
            part = part.strip()
            # 处理别名: col AS alias 或 col alias
            part = re.sub(r"\s+AS\s+.*", "", part, flags=re.IGNORECASE)
            part = part.strip()
            # 只取纯字母数字中文下划线字段
            bare = re.findall(r"([a-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)", part, re.IGNORECASE)
            for token in bare:
                if token.upper() not in sql_keywords:
                    columns.add(token)

    # 4. 从 WHERE 子句中提取
    where_match = re.search(
        r"WHERE\s+(.*?)(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT\s|$)",
        cleaned,
        re.IGNORECASE | re.DOTALL,
    )
    if where_match:
        where_part = where_match.group(1)
        # 找 field operator value 模式
        where_cols = re.findall(r"`?([a-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)`?\s*(?:=|>|<|>=|<=|!=|<>|LIKE|IN|BETWEEN|IS)",
                                where_part, re.IGNORECASE)
        for col in where_cols:
            if col.upper() not in sql_keywords:
                columns.add(col)

    # 5. 从 GROUP BY / ORDER BY 中提取
    for clause in ["GROUP\\s+BY", "ORDER\\s+BY"]:
        clause_match = re.search(
            rf"{clause}\s+(.*?)(?:HAVING|LIMIT\s|$)",
            cleaned,
            re.IGNORECASE | re.DOTALL,
        )
        if clause_match:
            clause_part = clause_match.group(1)
            clause_cols = re.findall(r"`?([a-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)`?", clause_part)
            for col in clause_cols:
                if col.upper() not in sql_keywords:
                    columns.add(col)

    result = list(columns)
    logger.debug(f"[extract_columns_from_sql] SQL={sql[:80]}... → extracted={result}")
    return result


def build_table_schemas(table_info: list[dict]) -> dict:
    """
    从 table_info 构建 {表名: [字段名列表]} 字典。

    Args:
        table_info: list_user_tables() 返回的列表

    Returns:
        { "table_name": ["col1", "col2", ...] }
    """
    schemas = {}
    for t in table_info:
        table_name = t["table_name"]
        col_names = [c["name"] for c in t.get("columns", [])]
        schemas[table_name] = col_names
    return schemas


def extract_select_aliases_from_sql(sql: str) -> set:
    """
    从 SQL 的 SELECT 子句中提取所有别名（AS 后面的名字）。

    例子:
        SELECT COUNT(*) AS 总行数 → {"总行数"}
        SELECT MAX(销售额) 最高销售额 → {"最高销售额"}
        SELECT 月份, SUM(利润) AS 总利润 → {"总利润"}

    Args:
        sql: 原始 SQL 语句

    Returns:
        别名集合
    """
    aliases = set()
    # 匹配 AS alias 或 函数(...) alias 模式（无 AS 隐式别名）
    alias_pattern = re.findall(
        r"(?:AS\s+)?`?([a-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)`?\s*$",
        sql,
        re.IGNORECASE | re.MULTILINE,
    )
    # 更精确的方式：从 SELECT 子句提取
    select_match = re.search(
        r"SELECT\s+(.*?)\s+FROM",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if select_match:
        select_part = select_match.group(1)
        # 匹配 AS alias (包括反引号包裹)
        as_aliases = re.findall(
            r"\bAS\s+`?([a-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)`?",
            select_part,
            re.IGNORECASE,
        )
        aliases.update(as_aliases)
        # 匹配函数调用后的隐式别名: MAX(利润) 最高利润 或 利润 利润
        func_aliases = re.findall(
            r"(?:SUM|COUNT|AVG|MAX|MIN|ROUND|STDDEV|GROUP_CONCAT|COALESCE)\s*\([^)]+\)\s+`?([a-z_\u4e00-\u9fff][\w\u4e00-\u9fff]*)`?",
            select_part,
            re.IGNORECASE,
        )
        aliases.update(func_aliases)
    return aliases


def verify_columns_exist(
    sql: str,
    table_schemas: dict,
    primary_table: str | None = None,
    extra_skip: set | None = None,
) -> Tuple[bool, List[str]]:
    """
    校验 SQL 中引用的字段是否都在目标表的实际字段列表中。

    流程:
    1. 从 SQL 中提取所有字段名
    2. 从 table_schemas 中获取所有实际字段
    3. 检查 SQL 字段是否都在实际字段中
    4. 跳过 SQL 函数生成的结果字段（如 `总行数`、`平均销售额` 等别名）

    Args:
        sql: 要校验的 SQL 语句
        table_schemas: {表名: [字段名列表]}
        primary_table: 主表名（可选，用于缩小检查范围）

    Returns:
        (是否全部存在, 不存在的字段列表)
    """
    sql_cols = extract_columns_from_sql(sql)

    if not sql_cols:
        return True, []

    # 构建所有实际字段的集合
    all_actual_cols = set()
    if primary_table and primary_table in table_schemas:
        all_actual_cols.update(table_schemas[primary_table])
    else:
        for cols in table_schemas.values():
            all_actual_cols.update(cols)

    # 表名（不参与校验：SQL 中可能使用 `table.column` 语法）
    table_names = set(table_schemas.keys())
    # 从 SQL 中提取别名（不参与校验）
    sql_aliases = extract_select_aliases_from_sql(sql)
    if extra_skip:
        sql_aliases |= extra_skip

    missing = []
    for col in sql_cols:
        if col in table_names or col in sql_aliases:
            continue
        if col not in all_actual_cols:
            missing.append(col)

    if missing:
        logger.warning(
            f"[verify_columns_exist] 发现不存在字段: {missing}, "
            f"SQL字段={sql_cols}, 实际字段={list(all_actual_cols)[:20]}"
        )

    return len(missing) == 0, missing
