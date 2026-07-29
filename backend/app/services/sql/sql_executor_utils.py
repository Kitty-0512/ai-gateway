"""
SQL 查询结果数值计算工具。

将增长率、环比、排名、统计值等计算从 LLM 中剥离，
由纯代码计算后作为确定值传递给 LLM 做文字解释。
"""

import logging
from typing import Any, List

logger = logging.getLogger(__name__)


def calc_growth_rate(current: float, previous: float) -> float:
    """
    计算增长率 / 环比。

    Args:
        current: 当期值
        previous: 基期值

    Returns:
        百分比数值，保留 2 位小数。处理除零和负数。
    """
    if previous == 0:
        return 0.0
    return round((current - previous) / previous * 100, 2)


def calc_ranking(
    data: List[dict],
    value_field: str,
    ascending: bool = False,
) -> List[dict]:
    """
    给数据按某个字段排序并标注排名（会修改原对象添加 _rank 字段）。

    Args:
        data: 数据列表
        value_field: 排序依据的字段名
        ascending: 是否升序（默认降序，数值越高排名越前）

    Returns:
        排序并标注 _rank 后的新列表
    """
    sorted_data = sorted(
        data,
        key=lambda x: x.get(value_field, 0) if x.get(value_field) is not None else 0,
        reverse=not ascending,
    )
    result = []
    for i, item in enumerate(sorted_data, 1):
        new_item = dict(item)
        new_item["_rank"] = i
        result.append(new_item)
    return result


def calc_summary_stats(data: List[dict], value_field: str) -> dict:
    """
    计算数值字段的统计摘要。

    Args:
        data: 数据列表
        value_field: 要统计的字段名

    Returns:
        {max, min, avg, sum, median, count}
    """
    values = [
        float(row[value_field])
        for row in data
        if value_field in row
        and row[value_field] is not None
        and isinstance(row[value_field], (int, float))
    ]
    n = len(values)
    if n == 0:
        return {
            "max": None,
            "min": None,
            "avg": None,
            "sum": None,
            "median": None,
            "count": 0,
        }
    sorted_vals = sorted(values)
    if n % 2 == 1:
        median = sorted_vals[n // 2]
    else:
        median = (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0

    return {
        "max": max(values),
        "min": min(values),
        "avg": round(sum(values) / n, 2),
        "sum": round(sum(values), 2),
        "median": round(median, 2) if isinstance(median, float) else median,
        "count": n,
    }


def auto_detect_fields(result_data: List[dict]) -> dict:
    """
    从 SQL 查询结果中自动检测维度字段和数值字段。

    Args:
        result_data: SQL 查询结果的 data 列表

    Returns:
        {
            "dim_field": str | None,     # 维度字段（文本/日期）
            "value_field": str | None,   # 主数值字段
            "all_numeric": list[str],    # 所有数值字段
            "all_text": list[str],       # 所有文本字段
        }
    """
    if not result_data:
        return {
            "dim_field": None,
            "value_field": None,
            "all_numeric": [],
            "all_text": [],
        }

    fields = list(result_data[0].keys())
    numeric_fields = []
    text_fields = []

    for f in fields:
        if f.startswith("_") or f == "id":
            continue
        vals = [r.get(f) for r in result_data if r.get(f) is not None]
        if not vals:
            continue
        if all(isinstance(v, (int, float)) for v in vals):
            numeric_fields.append(f)
        else:
            text_fields.append(f)

    # 主维度 = 第一个文本字段；主数值 = 第一个非辅助数值字段
    dim = text_fields[0] if text_fields else None
    value = numeric_fields[0] if numeric_fields else None

    return {
        "dim_field": dim,
        "value_field": value,
        "all_numeric": numeric_fields,
        "all_text": text_fields,
    }


def build_pre_calculated_block(result_data: List[dict]) -> str:
    """
    基于 SQL 查询结果自动计算增长率、排名、统计值，
    生成结构化文本传递给 LLM，禁止 LLM 自行计算。

    Args:
        result_data: SQL 查询结果 data 列表

    Returns:
        格式化的字符串，包含已计算好的所有数值
    """
    if not result_data:
        return "无数据"

    fields = auto_detect_fields(result_data)
    parts = []
    parts.append("【以下数值已由系统预先计算完成，请严格基于这些数值撰写分析，不得自行计算或修改任何数字】")

    # 单行聚合结果（如 overview）→ 直接列出即可
    if len(result_data) == 1:
        row = result_data[0]
        parts.append("\n## 数据汇总（单行统计）")
        for key, val in row.items():
            if val is not None:
                parts.append(f"  {key}: {val}")
        return "\n".join(parts)

    dim = fields["dim_field"]
    value = fields["value_field"]

    # --- 所有数值字段的统计摘要 ---
    for num_field in fields["all_numeric"]:
        stats = calc_summary_stats(result_data, num_field)
        parts.append(
            f"\n## 字段「{num_field}」统计\n"
            f"  数据量: {stats['count']} 条\n"
            f"  最大值: {stats['max']}\n"
            f"  最小值: {stats['min']}\n"
            f"  平均值: {stats['avg']}\n"
            f"  中位数: {stats['median']}\n"
            f"  总和:   {stats['sum']}"
        )

    # --- 增长率 / 环比（至少 2 行且有维度和数值字段） ---
    if dim and value and len(result_data) >= 2:
        sorted_data = sorted(
            result_data,
            key=lambda x: str(x.get(dim, "")),
        )
        parts.append(f"\n## 环比增长率（按 {dim} 升序）")
        for i in range(1, len(sorted_data)):
            prev_val = sorted_data[i - 1].get(value)
            curr_val = sorted_data[i].get(value)
            if prev_val is None or curr_val is None:
                continue
            try:
                rate = calc_growth_rate(float(curr_val), float(prev_val))
            except (ValueError, TypeError):
                continue
            prev_label = sorted_data[i - 1].get(dim, f"第{i}期")
            curr_label = sorted_data[i].get(dim, f"第{i + 1}期")
            direction = "增长" if rate >= 0 else "下降"
            parts.append(f"  {prev_label} → {curr_label}: {abs(rate)}%（{direction}）")

    # --- 排名（有数值字段时） ---
    if value and len(result_data) >= 2:
        ranked = calc_ranking(result_data, value)
        parts.append(f"\n## 排名（按 {value} 从高到低）")
        for item in ranked[:10]:
            label = item.get(dim, f"第{item['_rank']}条")
            parts.append(f"  第{item['_rank']}名: {label} = {item.get(value, '')}")

    return "\n".join(parts)
