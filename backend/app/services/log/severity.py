"""
日志事件严重性评分 — 纯规则，不调用 LLM。

参考 logai collector/processor.go:calculateSeverityScore。
评分范围 0-10，10 为最高严重性。

加权策略：
1. 关键词匹配（查表）
2. 堆栈跟踪加分
3. 多错误关键词叠加
4. 上限锁死 10
"""

from app.models.log_schemas import LogType

# =============================================================================
# 严重性基础分映射
# =============================================================================

_SEVERITY_BASE: dict[str, int] = {
    "fatal": 10,       "critical": 9,
    "error": 8,        "exception": 7,
    "panic": 7,        "oom": 8,
    "outofmemory": 8,  "killed": 6,
    "failed": 5,       "fail": 4,
    "timeout": 4,      "connection refused": 3,
    "segmentation fault": 9, "core dumped": 8,
    "blocked for more than": 8,
    "hung_task_timeout_secs": 5,
}

# =============================================================================
# 主入口
# =============================================================================


def calculate_severity(log_text: str, log_type: LogType | str = "unknown") -> int:
    """
    计算日志事件的严重性评分 (0-10)。

    Args:
        log_text: 已清洗的日志文本
        log_type: 日志类型

    Returns:
        0-10 整数评分
    """
    text = log_text[:5000]
    upper = text.upper()
    score = 0
    max_single = 0

    # ── 1) 关键词匹配 ──
    for keyword, base_score in _SEVERITY_BASE.items():
        if keyword.upper() in upper:
            score += base_score
            if base_score > max_single:
                max_single = base_score

    # ── 2) 堆栈跟踪加分 ──
    if "STACK" in upper or "TRACEBACK" in upper:
        score += 2
    if "CALL TRACE" in upper or "CALL TRACE:" in text:
        score += 4

    # ── 3) 内核异常加分 ──
    has_task_open = "<TASK>" in text
    has_task_close = "</TASK>" in text
    if has_task_open and has_task_close:
        score += 3
    if has_task_open or has_task_close:
        score += 1

    # ── 4) 多错误关键词叠加 ──
    error_kw_count = 0
    for kw in ("ERROR", "EXCEPTION", "FAILED", "FATAL", "SEGMENTATION FAULT",
               "CORE DUMPED", "CALL TRACE", "BLOCKED FOR MORE THAN"):
        if kw in upper:
            error_kw_count += 1
    if error_kw_count > 1:
        score += error_kw_count

    # ── 5) JSON 日志中的 error ──
    if '"level":"error"' in text.lower() or '"error":' in text.lower():
        score += 3

    # ── 6) Docker 特定加分 ──
    log_type_str = log_type.value if isinstance(log_type, LogType) else str(log_type)
    if log_type_str == "docker":
        if "OOM" in upper or "KILLED" in upper:
            score += 2
    if log_type_str == "app_stack":
        if "PANIC" in upper or "SEGMENTATION" in upper:
            score += 2

    # ── 7) 确保不低于最高单项分 ──
    if score < max_single:
        score = max_single

    # ── 8) 上限锁死 10 ──
    return min(score, 10)


def severity_label(score: int) -> str:
    """评分 → 中文标签。"""
    if score >= 9:
        return "严重"
    if score >= 7:
        return "高"
    if score >= 5:
        return "中"
    if score >= 3:
        return "低"
    return "信息"
