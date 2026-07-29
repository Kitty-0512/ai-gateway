"""故障报告导出：Markdown / JSON。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.log_schemas import DiagnosisResult

TYPE_LABEL = {
    "docker": "Docker",
    "nginx": "Nginx",
    "app_stack": "应用异常栈",
    "unknown": "未识别",
}

RISK_LABEL = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
}


def build_report_payload(
    result: DiagnosisResult,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """原始日志摘要 + 结论 JSON。"""
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "product": "LogSense",
        "meta": meta or {},
        "log_summary": {
            "log_type": result.log_type.value if hasattr(result.log_type, "value") else result.log_type,
            "raw_preview": result.raw_preview,
            "evidence": result.evidence,
        },
        "conclusion": {
            "anomaly_type": result.anomaly_type,
            "root_cause": result.root_cause,
            "investigation_steps": result.investigation_steps,
            "risk_level": result.risk_level.value if hasattr(result.risk_level, "value") else result.risk_level,
            "summary": result.summary,
            "follow_up_questions": result.follow_up_questions,
        },
    }


def render_markdown(
    result: DiagnosisResult,
    meta: dict[str, Any] | None = None,
) -> str:
    """现象 / 根因 / 建议 / 原文摘要。"""
    meta = meta or {}
    log_type = result.log_type.value if hasattr(result.log_type, "value") else str(result.log_type)
    risk = result.risk_level.value if hasattr(result.risk_level, "value") else str(result.risk_level)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    round_no = meta.get("round", 1)
    mode = meta.get("mode", "unknown")

    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(result.investigation_steps, 1)) or "- （无）"
    evidence = "\n".join(f"- `{e}`" for e in result.evidence) or "- （无）"
    questions = "\n".join(f"- {q}" for q in result.follow_up_questions) or "- （无）"
    preview = result.raw_preview.strip() or "（无）"

    return f"""# LogSense 故障诊断报告

- 导出时间：{now}
- 诊断轮次：第 {round_no} 轮
- 诊断模式：{mode}
- 日志类型：{TYPE_LABEL.get(log_type, log_type)}
- 风险等级：{RISK_LABEL.get(risk, risk)}

## 1. 现象

{result.summary or result.anomaly_type}

- 异常类型：{result.anomaly_type}

## 2. 可能根因

{result.root_cause}

## 3. 排查建议

{steps}

## 4. 日志证据

{evidence}

## 5. 原文摘要

```log
{preview}
```

## 6. 待补充问题

{questions}

---
*由 LogSense · AI 运维日志诊断平台自动生成*
"""
