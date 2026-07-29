"""将 LLM 文本解析为 DiagnosisResult，并校验证据来自原文。"""

from __future__ import annotations

import json
import re
from typing import Any

from app.models.log_schemas import DiagnosisResult, LogType, RiskLevel

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    fence = _CODE_FENCE_RE.search(raw)
    if fence:
        raw = fence.group(1).strip()

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("无法从模型输出中解析 JSON 对象")


def _normalize_log_type(value: Any, fallback: LogType) -> LogType:
    if isinstance(value, LogType):
        return value
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "app": LogType.app_stack,
        "application": LogType.app_stack,
        "stack": LogType.app_stack,
        "traceback": LogType.app_stack,
        "exception": LogType.app_stack,
    }
    if text in aliases:
        return aliases[text]
    try:
        return LogType(text)
    except ValueError:
        return fallback


def _normalize_risk(value: Any) -> RiskLevel:
    text = str(value or "medium").strip().lower()
    mapping = {
        "低": RiskLevel.low,
        "中": RiskLevel.medium,
        "高": RiskLevel.high,
        "严重": RiskLevel.critical,
        "critical": RiskLevel.critical,
        "high": RiskLevel.high,
        "medium": RiskLevel.medium,
        "low": RiskLevel.low,
    }
    return mapping.get(text, RiskLevel.medium)


def _as_str_list(value: Any, *, limit: int = 8) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = [str(x) for x in value if str(x).strip()]
    else:
        items = [str(value)]
    return [x.strip()[:400] for x in items if x.strip()][:limit]


def _evidence_in_log(snippet: str, log_text: str) -> bool:
    s = snippet.strip()
    if not s:
        return False
    if s in log_text:
        return True
    # 允许轻微空白差异
    compact_s = re.sub(r"\s+", "", s)
    compact_log = re.sub(r"\s+", "", log_text)
    if len(compact_s) >= 12 and compact_s in compact_log:
        return True
    # 取中间一段再匹配，兼容模型截断
    if len(s) > 40:
        mid = s[10:-10].strip()
        if mid and mid in log_text:
            return True
    return False


def filter_evidence(evidence: list[str], log_text: str, fallback: list[str]) -> list[str]:
    valid = [e for e in evidence if _evidence_in_log(e, log_text)]
    if valid:
        return valid[:5]
    # 模型胡编时回退到本地候选证据
    return [e[:300] for e in fallback[:3] if e.strip()]


def parse_diagnosis(
    text: str,
    *,
    cleaned_log: str,
    detected_type: LogType,
    candidate_evidence: list[str],
) -> DiagnosisResult:
    data = extract_json_object(text)
    evidence = filter_evidence(
        _as_str_list(data.get("evidence")),
        cleaned_log,
        candidate_evidence,
    )
    steps = _as_str_list(data.get("investigation_steps"), limit=8)
    if not steps:
        steps = ["核对日志时间窗与影响范围", "对照近期变更与依赖服务状态"]

    return DiagnosisResult(
        log_type=_normalize_log_type(data.get("log_type"), detected_type),
        anomaly_type=str(data.get("anomaly_type") or "未命名异常").strip()[:120],
        root_cause=str(data.get("root_cause") or "信息不足，暂无法判断根因").strip()[:800],
        investigation_steps=steps,
        risk_level=_normalize_risk(data.get("risk_level")),
        evidence=evidence,
        summary=str(data.get("summary") or "").strip()[:400],
        follow_up_questions=_as_str_list(data.get("follow_up_questions"), limit=3),
        raw_preview=cleaned_log[:800],
    )
