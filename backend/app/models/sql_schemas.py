"""
Text-to-SQL 模式的数据模型（Pydantic schemas）。

来源：next/ai-service/app/models/schemas.py
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChartType(str, Enum):
    bar = "bar"
    line = "line"
    pie = "pie"
    scatter = "scatter"
    table = "table"


class ChartConfig(BaseModel):
    type: ChartType = Field(default=ChartType.table)
    x_field: Optional[str] = None
    y_field: Optional[str] = None
    category_field: Optional[str] = None
    value_field: Optional[str] = None
    title: Optional[str] = None


class FileUploadResponse(BaseModel):
    dataset_id: int | None = None
    table_name: str = Field(...)
    row_count: int = Field(...)
    columns: list[dict] = Field(...)


class Message(BaseModel):
    role: str = Field(...)
    content: str = Field(...)
    sql: Optional[str] = None
    chart_config: Optional[ChartConfig] = None


class QueryRequest(BaseModel):
    conversation_id: Optional[int] = None
    question: str = Field(..., min_length=1, max_length=2000)
    dataset_ids: list[int] = Field(..., min_length=1)
    history: list[Message] = Field(default_factory=list)


class QueryResponse(BaseModel):
    message_id: Optional[int] = None
    sql: Optional[str] = None
    result: Optional[list[dict]] = None
    chart_config: Optional[ChartConfig] = None
    answer: str = Field(...)
    token_usage: int = 0
    clarification_needed: bool = False


class ClarifyRequest(BaseModel):
    question: str = Field(...)
    original_question: str = Field(...)
    dataset_ids: list[int] = Field(...)
    context: Optional[str] = None


class UploadResponse(BaseModel):
    success: bool = True
    dataset_id: Optional[int] = None
    table_name: str
    row_count: int
    columns: list[dict]
