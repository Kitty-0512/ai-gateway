"""
日志诊断模式的数据模型（Pydantic schemas）。

来源：next-2/backend/app/models/schemas.py
"""

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class LogType(str, Enum):
    docker = "docker"
    nginx = "nginx"
    app_stack = "app_stack"
    unknown = "unknown"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class AnalyzeRequest(BaseModel):
    content: str = Field(..., min_length=1, description="日志原文")
    log_type: LogType | None = Field(None, description="可选：指定日志类型")
    extra_context: str | None = Field(None, description="用户补充信息（追问后二次诊断）")


class DiagnosisResult(BaseModel):
    log_type: LogType
    anomaly_type: str
    root_cause: str
    investigation_steps: list[str]
    risk_level: RiskLevel
    evidence: list[str] = Field(default_factory=list)
    summary: str = ""
    follow_up_questions: list[str] = Field(default_factory=list)
    raw_preview: str = ""
    severity_score: int = 0        # 0-10 严重性评分，纯规则计算


class AnalyzeResponse(BaseModel):
    success: bool = True
    result: DiagnosisResult
    meta: dict[str, Any] = Field(default_factory=dict)


class ReportRequest(BaseModel):
    result: DiagnosisResult
    meta: dict[str, Any] = Field(default_factory=dict)
    format: Literal["markdown", "json"] = "markdown"


class ReportResponse(BaseModel):
    format: str
    filename: str
    content: str
    payload: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str = "ok"
    app: str
    llm_configured: bool = False
    llm_model: str | None = None
