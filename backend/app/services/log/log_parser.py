"""日志解析：脱敏 → 清洗 → 类型检测 → 证据提取 → 上下文窗口。"""

import re

from app.core.config import get_settings
from app.models.log_schemas import LogType
from app.services.log.sanitizer import sanitize

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# ── #5 精准堆栈收集：根据错误类型判断后续行 ──────────────────────

# 行首噪声：纯分隔线 / healthcheck 等
_NOISE_RE = re.compile(
    r"^(\s*$|#{3,}|={3,}|-{3,}|\*{3,}|DEBUG\s+healthcheck|GET /health\b)",
    re.IGNORECASE,
)

# Java 堆栈特征
_JAVA_STACK_SIGNALS = (
    "at ", "Caused by:", "Suppressed:", "\t", "    ",
    "Exception in thread",
    ".java:", ".kt:", ".scala:",
    "org.", "com.", "net.", "io.",
    "java.", "javax.", "sun.",
)

# Python 堆栈特征
_PYTHON_STACK_SIGNALS = (
    "Traceback (most recent call last)",
    'File "', "File '",
    "  File ",
    "IndexError:", "TypeError:", "ValueError:", "KeyError:",
    "AttributeError:", "ImportError:", "RuntimeError:",
    "ZeroDivisionError:", "OSError:", "IOError:",
    "Exception in ASGI", "exception in asgi",
)

# 内核 / Docker / 通用系统堆栈特征
_KERNEL_STACK_SIGNALS = (
    "<TASK>", "</TASK>",
    "+0x", "RIP:", "RSP:", "RAX:", "RBX:", "RCX:", "RDX:",
    "RSI:", "RDI:", "RBP:", "R8: ", "R9: ", "R10:",
    "Call Trace:", "call trace",
    "stack segment:", "Code:", "CR2:",
    "level=error", "msg=", "time=",
    "panic:", "goroutine ", "[signal",
)


def _detect_stack_type(first_error_line: str) -> str:
    """根据错误首行判断堆栈类型：java / python / kernel / generic。"""
    lower = first_error_line.lower()
    if any(s.lower() in lower for s in _JAVA_STACK_SIGNALS[4:8]):  # Exception/org/com/java
        if "exception in thread" in lower or "caused by" in lower or ".java:" in lower:
            return "java"
    if "traceback" in lower or 'file "' in lower or "file '" in lower:
        return "python"
    if any(s.lower() in lower for s in ("call trace", "<task>", "panic:", "goroutine ")):
        return "kernel"
    for s in ("level=error", "msg=", "docker", "container"):
        if s in lower:
            return "generic"
    return "generic"


def _should_keep_stack_line(line: str, stack_type: str) -> bool:
    """判断一行是否属于指定类型的堆栈跟踪。"""
    if not line or not line.strip():
        return False
    if stack_type == "java":
        return any(s in line for s in _JAVA_STACK_SIGNALS)
    if stack_type == "python":
        return any(s in line for s in _PYTHON_STACK_SIGNALS)
    if stack_type == "kernel":
        return any(s in line for s in _KERNEL_STACK_SIGNALS)
    # generic: 缩进行 / 包含关键错误词
    if line[0] in (" ", "\t"):
        return True
    return any(kw in line.lower() for kw in ("error", "fail", "exception", "killed"))


# ── #4 上下文窗口提取 ──────────────────────────────────────

_ERROR_KEYWORDS = (
    "error", "exception", "traceback", "fail", "fatal", "panic",
    "oom", "exited", "timeout", "upstream", "502", "504", "503",
    "killed", "caused by", "warn", "critical", "fault",
    "segmentation", "core dumped",
)


def _is_error_line(line: str) -> bool:
    """判断一行是否为错误行。"""
    lower = line.lower()
    return any(kw in lower for kw in _ERROR_KEYWORDS)


def extract_context_lines(
    lines: list[str],
    context_before: int = 5,
    context_after: int = 5,
) -> str:
    """
    从日志行中提取错误行前后各 N 行的上下文。

    Args:
        lines: 原始日志行列表（已清洗的）
        context_before: 错误行前面取的行数
        context_after: 错误行后面取的行数

    Returns:
        拼好的上下文字符串，多条错误用 "---" 分隔。
        如果行数不超过 2*context，直接返回所有原文。
    """
    if len(lines) <= context_before + context_after + 1:
        # 日志很短，不需要提取上下文
        return ""

    error_indices = [i for i, ln in enumerate(lines) if _is_error_line(ln)]
    if not error_indices:
        return ""

    blocks: list[str] = []
    seen_ranges: set[tuple[int, int]] = set()

    for idx in error_indices:
        start = max(0, idx - context_before)
        end = min(len(lines), idx + context_after + 1)
        # 去重：跳过与已收集块重叠超过 50% 的范围
        overlap_key = (start, end)
        skip = False
        for prev_start, prev_end in seen_ranges:
            overlap = min(end, prev_end) - max(start, prev_start)
            if overlap > (end - start) // 2:
                skip = True
                break
        if skip:
            continue
        seen_ranges.add(overlap_key)

        block_lines = lines[start:end]
        # 给错误行加个直观前缀
        marked = []
        for i, ln in enumerate(block_lines):
            actual_idx = start + i
            prefix = ">>> " if actual_idx == idx else "    "
            marked.append(f"{prefix}{ln}")
        blocks.append("\n".join(marked))

    if not blocks:
        return ""

    header = f"（上下文窗口：前后各 {context_before} 行）\n"
    return header + "\n---\n".join(blocks) + "\n"


# ── 主清洗函数 ──────────────────────────────────────────────

def clean_log(content: str, *, apply_truncate: bool = True) -> str:
    """清洗日志：脱敏 → 去 ANSI → 统一换行 → 精准堆栈过滤 → 可选截断。"""
    # 0) 脱敏
    text = sanitize(content)
    text = text.replace("\x00", "")
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = [ln.rstrip() for ln in text.split("\n")]

    # 1) 检测错误行 + 堆栈类型
    error_stack_type = "generic"
    for ln in raw_lines:
        if _is_error_line(ln):
            error_stack_type = _detect_stack_type(ln)
            break

    # 2) 精准堆栈过滤：保留错误行 + 相邻空白 + 堆栈行
    filtered: list[str] = []
    in_stack = False
    blank_run = 0
    for ln in raw_lines:
        if not ln.strip():
            blank_run += 1
            # 堆栈内的空行保留（1 行）
            if in_stack and blank_run == 1:
                filtered.append("")
            # 错误行之后允许最多 2 个空行分割
            elif blank_run <= 2:
                pass  # 不添加，但也不重置
            continue
        blank_run = 0

        # 噪声行过滤
        if _NOISE_RE.match(ln):
            continue

        # 检测是否进入堆栈
        is_error = _is_error_line(ln)
        is_stack = _should_keep_stack_line(ln, error_stack_type)

        if is_error:
            in_stack = True
            filtered.append(ln)
        elif in_stack and is_stack:
            filtered.append(ln)
        elif in_stack and not is_stack and blank_run >= 2:
            # 连续两个空行 → 堆栈结束
            in_stack = False
            filtered.append(ln)
        else:
            # 普通行，堆栈已结束
            filtered.append(ln)

    text = "\n".join(filtered).strip()
    if apply_truncate:
        return truncate_log(text)
    return text


def clean_log_with_context(
    content: str,
    *,
    apply_truncate: bool = True,
    context_lines: int = 5,
) -> str:
    """
    清洗日志并附带错误行上下文窗口。

    返回 = cleaned_text + context_block（如果有多条分散的错误）。
    """
    cleaned = clean_log(content, apply_truncate=apply_truncate)
    raw_lines = [ln.rstrip() for ln in content.split("\n")]
    cleaned_lines = cleaned.split("\n")

    ctx = extract_context_lines(cleaned_lines, context_lines, context_lines)
    if ctx:
        return f"{cleaned}\n\n{ctx}"
    return cleaned


def truncate_log(content: str) -> str:
    """截断超长日志，保留头尾，避免 prompt 爆炸。"""
    settings = get_settings()
    limit = settings.max_log_chars
    if len(content) <= limit:
        return content
    head = limit * 2 // 3
    tail = limit - head - 120
    omitted = len(content) - head - max(tail, 0)
    return (
        content[:head]
        + f"\n\n...[已截断，中间省略 {omitted} 字符]...\n\n"
        + content[-max(tail, 0):]
    )


def detect_log_type(content: str) -> LogType:
    """启发式识别 3 类日志：Docker / Nginx / 应用异常栈。"""
    sample = content[:4000].lower()

    docker_signals = [
        "container id", "docker", "oci runtime", "exited with code",
        "level=error", "msg=", "com.docker", "oom-kill", "cgroup",
    ]
    nginx_signals = [
        "nginx", '"get ', '"post ', " upstream:", "connect() failed",
        " 502 ", " 504 ", 'open() "', "fastcgi", "upstream timed out",
    ]
    stack_signals = [
        "traceback (most recent call last)", "exception in thread",
        "caused by:", "at java.", "at com.", "nullpointerexception",
        "typeerror:", "referenceerror:", "panic:", "goroutine ",
        "stacktrace", "indexerror:", "exception in asgi",
    ]

    scores = {
        LogType.docker: sum(1 for s in docker_signals if s in sample),
        LogType.nginx: sum(1 for s in nginx_signals if s in sample),
        LogType.app_stack: sum(1 for s in stack_signals if s in sample),
    }
    if "traceback (most recent call last)" in sample:
        scores[LogType.app_stack] += 3
    if re.search(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}.+\"(get|post|put|delete)", sample):
        scores[LogType.nginx] += 2
    if "container" in sample and ("error" in sample or "exited" in sample or "oom" in sample):
        scores[LogType.docker] += 2
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return LogType.unknown
    return best


def extract_candidate_evidence(content: str, limit: int = 8) -> list[str]:
    """抽取可能作为证据的关键行，供 Prompt 约束与校验。"""
    keywords = (
        "error", "exception", "traceback", "fail", "fatal", "panic",
        "oom", "exited", "timeout", "upstream", "502", "504", "503",
        "killed", "caused by", "warn",
    )
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    hits: list[str] = []
    for ln in lines:
        lower = ln.lower()
        if any(k in lower for k in keywords):
            hits.append(ln[:300])
            if len(hits) >= limit:
                break
    if not hits:
        return [ln[:300] for ln in lines[: min(3, len(lines))]]
    return hits
