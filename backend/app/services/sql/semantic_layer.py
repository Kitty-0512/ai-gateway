"""
指标语义层 — 加载 metrics.yaml，为 Text-to-SQL 提供业务指标映射。

在用户问题进入 SQL 生成前，解析相关指标别名与短语规则，
注入 Prompt，减少 LLM 对「流量」「SEO 表现」等说法的歧义理解。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _BACKEND_ROOT / "data"
_METRICS_FILE = _DATA_DIR / "metrics.yaml"

# 内置 SEO 表名前缀，用于判断是否启用语义层
SEO_TABLE_PREFIX = "ds_seo_"


@lru_cache(maxsize=1)
def load_semantic_config() -> dict[str, Any]:
    if not _METRICS_FILE.is_file():
        logger.warning("metrics.yaml 不存在: %s", _METRICS_FILE)
        return {}
    with _METRICS_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def is_seo_dataset(table_names: list[str]) -> bool:
    """当前数据集是否包含 SEO 内置表。"""
    return any(t.startswith(SEO_TABLE_PREFIX) for t in table_names)


def _match_phrase_rules(question: str, rules: dict[str, Any]) -> list[tuple[str, dict]]:
    matched: list[tuple[str, dict]] = []
    q = question.strip()
    for phrase, rule in (rules or {}).items():
        if phrase in q:
            matched.append((phrase, rule if isinstance(rule, dict) else {}))
    return matched


def _match_aliases(question: str, aliases: dict[str, str]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    q = question
    for alias, metric_key in sorted(aliases.items(), key=lambda x: -len(x[0])):
        if alias in q:
            hits.append((alias, metric_key))
    return hits


def build_semantic_context(question: str, table_info: list[dict]) -> str:
    """
    根据用户问题与表信息，生成注入 SQL Prompt 的语义层文本块。
    非 SEO 数据集返回空字符串。
    """
    table_names = [t.get("table_name", "") for t in table_info]
    if not is_seo_dataset(table_names):
        return ""

    cfg = load_semantic_config()
    if not cfg:
        return ""

    metrics = cfg.get("metrics") or {}
    aliases = cfg.get("aliases") or {}
    phrase_rules = cfg.get("phrase_rules") or {}
    tables = cfg.get("tables") or {}

    alias_hits = _match_aliases(question, aliases)
    phrase_hits = _match_phrase_rules(question, phrase_rules)

    lines = ["## 业务指标语义层（必须遵守）", ""]

    lines.append("### 标准指标定义")
    for key, meta in metrics.items():
        if not isinstance(meta, dict):
            continue
        label = meta.get("label", key)
        col = meta.get("column") or meta.get("formula", "")
        desc = meta.get("description", "")
        lines.append(f"- **{key}**（{label}）: {desc}；字段/公式: `{col}`")

    if alias_hits:
        lines.append("")
        lines.append("### 本问题识别的用户说法映射")
        for alias, metric_key in alias_hits:
            meta = metrics.get(metric_key, {})
            label = meta.get("label", metric_key) if isinstance(meta, dict) else metric_key
            lines.append(f"- 「{alias}」→ 标准指标 `{metric_key}`（{label}）")

    if phrase_hits:
        lines.append("")
        lines.append("### 本问题识别的分析意图")
        for phrase, rule in phrase_hits:
            hint = rule.get("hint", "")
            metric = rule.get("metric") or rule.get("metrics", "")
            sort = rule.get("sort", "")
            extra = f"，排序: {sort}" if sort else ""
            lines.append(f"- 「{phrase}」→ 指标 `{metric}`{extra}")
            if hint:
                lines.append(f"  - 生成 SQL 时: {hint}")

    relevant_tables = [t for t in table_names if t in tables]
    if relevant_tables:
        lines.append("")
        lines.append("### 可用数据表说明")
        for tname in relevant_tables:
            tmeta = tables.get(tname, {})
            if isinstance(tmeta, dict):
                lines.append(
                    f"- `{tname}`: {tmeta.get('description', '')} "
                    f"（粒度: {tmeta.get('grain', '')}）"
                )

    lines.append("")
    lines.append(
        "生成 SQL 时必须使用上述标准指标对应的列名，"
        "不要自行猜测「流量」= 其他字段。"
    )

    return "\n".join(lines)


def resolve_primary_metric(question: str) -> str | None:
    """从问题中解析最可能的主指标 key（供 Evaluation / 日志使用）。"""
    cfg = load_semantic_config()
    aliases = cfg.get("aliases") or {}
    hits = _match_aliases(question, aliases)
    if hits:
        return hits[0][1]
    for phrase, rule in (cfg.get("phrase_rules") or {}).items():
        if phrase in question and isinstance(rule, dict):
            m = rule.get("metric")
            if isinstance(m, str):
                return m
    return None
