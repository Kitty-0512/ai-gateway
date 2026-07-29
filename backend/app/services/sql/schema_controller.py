"""
Schema Token 控制器。

解决大表（100+ 列）场景下 Schema 超出 LLM 上下文窗口的问题：
1. 列数上限：单表最多 N 列进入 Prompt（默认 30）
2. 智能排序：按用户问题关键词匹配度 → 数值列 → 日期列 → 其他 排序
3. Token 估算：基于字符数粗略估算 token 数
4. 截断标记：被截断的列名以注释形式告知 LLM
"""

import re
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 配置
MAX_COLUMNS_PER_TABLE = 30     # 单表最多进入 Prompt 的列数
MAX_SCHEMA_CHARS = 6000        # Schema 文本的最大字符数（约 1500 tokens）
MAX_SAMPLE_CHARS = 3000        # 样本数据最大字符数（约 750 tokens）
TOKEN_ESTIMATE_RATIO = 0.25    # 粗略 token 估算比例（中文约 1 字符 ≈ 0.4 token，英文约 0.25）

# 数值类型关键词（用于自动识别指标列）
NUMERIC_TYPE_KEYWORDS = (
    "int", "bigint", "smallint", "tinyint", "mediumint",
    "decimal", "numeric", "float", "double", "real",
    "number",
)
# 日期/时间类型关键词（用于自动识别维度列）
DATE_TYPE_KEYWORDS = (
    "date", "datetime", "timestamp", "time", "year",
)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数量（1 中文字符 ≈ 0.4 token，其他 ≈ 0.25 token）"""
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 0.4 + other_chars * TOKEN_ESTIMATE_RATIO)


def _extract_question_keywords(question: str) -> set[str]:
    """从用户问题中提取关键词（用于列名匹配）"""
    # 去除标点，分词（简单按空格和常见分隔符切分）
    cleaned = re.sub(r'[，。！？、；：""''（）【】《》\s]+', ' ', question)
    words = cleaned.split()
    # 过滤短词和纯数字
    keywords = {w.lower() for w in words if len(w) >= 2 and not w.isdigit()}
    return keywords


def _score_column(
    col: dict[str, str],
    question_keywords: set[str],
) -> tuple[int, int, int, str]:
    """
    为列打分，用于智能排序。分数越高越重要。

    打分规则：
    - 列名匹配用户问题关键词: +100 分/每个匹配关键词
    - 数值类型列（潜在指标）: +50 分
    - 日期/时间类型列（潜在维度）: +40 分
    - 主键列: +30 分（可能需要用于关联）
    - 其余列: 0 分

    Returns:
        (总分, 是否数值, 是否日期, 列名) 用于排序
    """
    score = 0
    col_name = col.get("name", "")
    col_type = col.get("type", "").lower()
    col_name_lower = col_name.lower()

    # 1. 关键词匹配
    for kw in question_keywords:
        if kw in col_name_lower:
            score += 100

    # 2. 数值类型
    is_numeric = any(kw in col_type for kw in NUMERIC_TYPE_KEYWORDS)
    if is_numeric:
        score += 50

    # 3. 日期类型
    is_date = any(kw in col_type for kw in DATE_TYPE_KEYWORDS)
    if is_date:
        score += 40

    # 4. 主键
    if col.get("key") == "PRI":
        score += 30

    # 排序用: 负数分数让数值列在同类中排前面（-is_numeric: True=-1, False=0）
    return (score, -int(is_numeric), -int(is_date), col_name)


def _truncate_text(text: str, max_chars: int, label: str = "") -> str:
    """截断文本到 max_chars，超出部分加省略标记"""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    omitted = len(text) - max_chars
    return f"{truncated}\n... ({label}内容过长，已截断 {omitted} 字符)"


def build_schema_for_prompt(
    table_info: list[dict],
    user_question: str,
    max_columns: int = MAX_COLUMNS_PER_TABLE,
) -> str:
    """
    构建优化后的 Schema + 样本数据 Prompt 文本。

    - 智能排序字段：问题相关 → 数值 → 日期 → 其他
    - 截断超出上限的列
    - Token 估算日志

    Args:
        table_info: list_user_tables() 返回的表信息列表
        user_question: 用户问题文本
        max_columns: 单表最大列数

    Returns:
        格式化后的 Schema Prompt 文本，可直接拼入各类提示词模板
    """
    if not table_info:
        return "(未找到数据表)"

    keywords = _extract_question_keywords(user_question)
    logger.info(
        f"[SchemaCtrl] 用户关键词: {keywords}, "
        f"共 {sum(len(t.get('columns', [])) for t in table_info)} 列, "
        f"上限 {max_columns} 列/表"
    )

    parts = []

    for t in table_info:
        table_name = t.get("table_name", "unknown")
        all_columns = t.get("columns", [])

        if not all_columns:
            parts.append(f"表名: {table_name}\n(无法获取字段信息)")
            continue

        # 排序：得分高 → 前面
        scored = sorted(
            all_columns,
            key=lambda c: _score_column(c, keywords),
            reverse=True,
        )

        # 截断
        selected = scored[:max_columns]
        omitted = scored[max_columns:]

        # 格式化选中的列
        lines = [f"表名: {table_name}"]
        lines.append("字段:")
        for col in selected:
            parts_line = f"  - {col['name']} ({col['type']})"
            if col.get("key") == "PRI":
                parts_line += " PRIMARY KEY"
            if col.get("comment"):
                parts_line += f" -- {col['comment']}"
            lines.append(parts_line)

        if omitted:
            omitted_names = [c["name"] for c in omitted]
            lines.append(f"  (+ {len(omitted)} 列未显示: {', '.join(omitted_names[:10])}"
                         f"{'...' if len(omitted_names) > 10 else ''})")

        # 样本数据
        sample = t.get("formatted_sample", "")
        if sample:
            sample = _truncate_text(sample, MAX_SAMPLE_CHARS, "样本数据")
            lines.append(f"\n示例数据(前3行):\n{sample}")

        schema_text = "\n".join(lines)
        schema_text = _truncate_text(schema_text, MAX_SCHEMA_CHARS, "Schema")

        parts.append(schema_text)

        estimated_tokens = _estimate_tokens(schema_text)
        logger.info(
            f"[SchemaCtrl] 表 '{table_name}': "
            f"选中 {len(selected)}/{len(all_columns)} 列, "
            f"约 {estimated_tokens} tokens"
        )

    return "\n\n".join(parts)
