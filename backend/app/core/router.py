"""
自动路由决策模块。

根据用户输入（文本内容 + 可选文件名）自动判断应该路由到哪个工具。

优先级（由高到低）：
1. 文件名后缀匹配   → .xlsx/.csv → sql, .log/.txt/.out → log
2. 日志格式特征匹配 → 命中 Docker/Nginx/堆栈特征 → log
3. SQL 意图关键词   → 问题中包含"查询/统计/排名/分析"等 → sql
4. LLM 轻量分类     → 以上都无法判断时，调用一次简短 LLM 分类

设计原则：独立、可扩展。新增工具只需在 TOOLS 列表加一项即可。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# =============================================================================
# 工具注册表（扩展点：新增工具在这里加一行即可）
# =============================================================================

@dataclass
class ToolDef:
    """工具定义"""
    name: str                                    # "sql" | "log"
    file_extensions: tuple[str, ...] = ()        # 关联的文件后缀
    keywords: tuple[str, ...] = ()               # 关联的关键词（用于快速匹配）
    description: str = ""                        # 人类可读描述


TOOLS: list[ToolDef] = [
    ToolDef(
        name="sql",
        file_extensions=(".xlsx", ".xls", ".csv"),
        keywords=(
            "查询", "统计", "排名", "分析", "汇总", "分组", "对比",
            "销售额", "利润", "数据", "图表", "最高", "最低", "平均",
            "趋势", "占比", "增长", "下降", "字段", "table", "sheet",
            "帮我查", "帮我分析", "有几个", "有多少", "哪些",
            "select", "group by", "order by",
        ),
        description="SQL 数据分析 (Text-to-SQL)",
    ),
    ToolDef(
        name="log",
        file_extensions=(".log", ".txt", ".out"),
        keywords=(
            "日志", "报错", "错误", "故障", "诊断", "排查", "崩溃",
            "exception", "error", "traceback", "docker", "nginx",
            "容器", "退出", "超时", "502", "504", "503", "500",
            "oom", "killed", "panic", "stack", "堆栈",
            "connect() failed", "upstream", "cgroup",
        ),
        description="日志诊断 (LogSense)",
    ),
]

# =============================================================================
# 日志格式检测（来自 log_parser.detect_log_type 的轻量版）
# =============================================================================

_LOG_SIGNALS = {
    "docker": [
        "container id", "docker", "oci runtime", "exited with code",
        "level=error", "msg=", "com.docker", "oom-kill", "cgroup",
    ],
    "nginx": [
        "nginx", '"get ', '"post ', " upstream:", "connect() failed",
        " 502 ", " 504 ", " 503 ", 'open() "', "fastcgi",
        "upstream timed out",
    ],
    "stack": [
        "traceback (most recent call last)", "exception in thread",
        "caused by:", "at java.", "at com.", "nullpointerexception",
        "typeerror:", "referenceerror:", "panic:", "goroutine ",
        "stacktrace", "indexerror:",
    ],
}

# 日志特征的最小命中数：单条强特征（traceback/exited with code）即触发
_LOG_MIN_HITS = 1


def _looks_like_log(text: str) -> bool:
    """快速判断文本是否"看起来像日志"。"""
    if not text or len(text) < 20:
        return False
    sample = text[:4000].lower()
    hits = 0
    for signals in _LOG_SIGNALS.values():
        for sig in signals:
            if sig in sample:
                hits += 1
                if hits >= _LOG_MIN_HITS:
                    return True
    # 额外规则：大量时间戳行、大量 IP 地址 = 日志
    timestamp_lines = len(re.findall(r"\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}", sample))
    ip_matches = len(re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", sample))
    if ip_matches >= 3 or timestamp_lines >= 5:
        return True
    return False


# =============================================================================
# 意图分类 Prompt（轻量，只返回一个单词）
# =============================================================================

_INTENT_CLASSIFY_PROMPT = """你是一个请求路由分类器。根据用户消息判断应该调用哪个工具。
只回复一个单词：sql 或 log。

判断规则：
- 如果用户想查询/分析/统计数据（表格、销售额、排名、趋势等）→ sql
- 如果用户提供了一段日志、报错、堆栈信息，想诊断故障 → log
- 如果用户问了关于数据库、Excel、CSV、图表的问题 → sql
- 如果用户粘贴了服务异常、容器退出、nginx 错误等内容 → log

重要：只回复一个单词，不要任何解释。"""


async def _llm_classify(text: str) -> str | None:
    """
    调用 LLM 进行轻量意图分类。返回 "sql" | "log" | None。

    失败时返回 None，调用方应使用默认值。
    """
    settings = get_settings()
    if not settings.llm_configured:
        return None

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.openai_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.openai_model,
                    "temperature": 0,
                    "max_tokens": 4,
                    "messages": [
                        {"role": "system", "content": _INTENT_CLASSIFY_PROMPT},
                        {"role": "user", "content": text[:2000]},
                    ],
                },
            )
            if resp.status_code != 200:
                logger.warning(f"LLM 分类请求失败 status={resp.status_code}")
                return None

            data = resp.json()
            choice = data["choices"][0]["message"]["content"].strip().lower()
            if "sql" in choice:
                return "sql"
            if "log" in choice:
                return "log"
            logger.warning(f"LLM 分类返回未知值: {choice}")
            return None

    except Exception as exc:
        logger.warning(f"LLM 分类异常: {exc}")
        return None


# =============================================================================
# 路由决策（默认兜底 → sql）
# =============================================================================

_DEFAULT_TOOL = "sql"


@dataclass
class ToolRouting:
    """路由决策结果"""
    tool: str                                    # "sql" | "log"
    reason: str                                  # 人类可读的路由原因
    confidence: str = "medium"                   # "high" | "medium"
    file_name: str | None = None


async def resolve_tool(
    text: str | None = None,
    file_name: str | None = None,
) -> ToolRouting:
    """
    自动判断应该路由到哪个工具。

    调用顺序（短路求值，命中即返回）：
    1. 文件名后缀 → high confidence
    2. 日志格式特征 → high confidence
    3. SQL/日志关键词 → medium confidence
    4. LLM 轻量分类 → medium confidence
    5. 兜底 → sql

    Args:
        text: 用户消息文本（可为 None）
        file_name: 上传文件的原始文件名（可为 None）

    Returns:
        ToolRouting 包含 tool、reason、confidence
    """
    # ---- 第 1 层：文件名后缀 ----
    if file_name:
        file_lower = file_name.lower()
        for tool_def in TOOLS:
            for ext in tool_def.file_extensions:
                if file_lower.endswith(ext):
                    return ToolRouting(
                        tool=tool_def.name,
                        reason=f"文件名后缀匹配 ({ext})",
                        confidence="high",
                        file_name=file_name,
                    )

    # ---- 第 2 层：日志格式特征 ----
    if text and _looks_like_log(text):
        return ToolRouting(
            tool="log",
            reason="内容匹配日志格式特征（堆栈/Docker/Nginx/时间戳+IP）",
            confidence="high",
            file_name=file_name,
        )

    # ---- 第 3 层：关键词匹配 ----
    if text:
        text_lower = text.lower()
        scores: dict[str, int] = {}
        for tool_def in TOOLS:
            score = sum(1 for kw in tool_def.keywords if kw in text_lower)
            if score > 0:
                scores[tool_def.name] = score

        if scores:
            best = max(scores, key=scores.get)
            return ToolRouting(
                tool=best,
                reason=f"关键词匹配 ({scores[best]} 个命中, 其他: { {k:v for k,v in scores.items() if k!=best} })",
                confidence="medium",
                file_name=file_name,
            )

    # ---- 第 4 层：LLM 分类 ----
    if text and len(text) >= 10:
        llm_result = await _llm_classify(text)
        if llm_result:
            return ToolRouting(
                tool=llm_result,
                reason="LLM 意图分类",
                confidence="medium",
                file_name=file_name,
            )

    # ---- 第 5 层：兜底 ----
    return ToolRouting(
        tool=_DEFAULT_TOOL,
        reason=f"无法自动判断，使用默认工具 ({_DEFAULT_TOOL})",
        confidence="medium",
        file_name=file_name,
    )
