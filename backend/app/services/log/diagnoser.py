"""诊断编排：优先 LLM 结构化结论，失败可回退 mock；支持 SSE 流式。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.models.log_schemas import DiagnosisResult, LogType, RiskLevel
from app.services.log.json_parser import parse_diagnosis
from app.core.llm_client import chat_completion_async, chat_completion_stream
from app.services.log.log_parser import (
    clean_log, clean_log_with_context,
    detect_log_type, extract_candidate_evidence,
)
from app.services.log.prompts import SYSTEM_PROMPT, build_user_prompt
from app.services.log.web_search import search_error_context
from app.services.log.severity import calculate_severity

logger = logging.getLogger(__name__)


@dataclass
class DiagnoseOutcome:
    result: DiagnosisResult
    mode: str  # llm | mock | mock_fallback
    error: str | None = None


def _pick_evidence(content: str, keywords: list[str], limit: int = 3) -> list[str]:
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    hits: list[str] = []
    for ln in lines:
        lower = ln.lower()
        if any(k.lower() in lower for k in keywords):
            hits.append(ln[:240])
            if len(hits) >= limit:
                break
    if not hits:
        hits = lines[: min(2, len(lines))]
    return hits


def mock_diagnose(
    content: str,
    log_type: LogType | None = None,
    extra_context: str | None = None,
) -> DiagnosisResult:
    cleaned = clean_log(content)
    detected = log_type or detect_log_type(cleaned)

    templates: dict[LogType, DiagnosisResult] = {
        LogType.docker: DiagnosisResult(
            log_type=LogType.docker,
            anomaly_type="容器异常退出 / 运行时错误",
            root_cause="容器进程非零退出，常见于配置错误、依赖服务不可达或 OOM。",
            investigation_steps=[
                "确认容器退出码：docker inspect / docker ps -a",
                "查看同时间段宿主机资源（内存、磁盘）",
                "核对环境变量与挂载卷是否齐全",
                "检查依赖服务网络连通性",
            ],
            risk_level=RiskLevel.high,
            evidence=_pick_evidence(cleaned, ["error", "exited", "killed", "oom", "fail"]),
            summary="检测到 Docker 相关错误信号，建议优先核对退出码与资源。",
            follow_up_questions=[
                "该容器是刚发版后才出现问题吗？",
                "是偶发还是持续重启？",
                "影响的是哪个服务/容器名？",
            ],
            raw_preview=cleaned[:800],
        ),
        LogType.nginx: DiagnosisResult(
            log_type=LogType.nginx,
            anomaly_type="网关/反向代理错误（5xx / upstream）",
            root_cause="上游服务超时、拒绝连接或 nginx 配置错误导致请求失败。",
            investigation_steps=[
                "定位 5xx / connect() failed 对应的 upstream",
                "检查 upstream 健康状态与超时配置",
                "核对 proxy_pass / upstream 地址是否变更",
                "对照应用侧同时段错误日志",
            ],
            risk_level=RiskLevel.medium,
            evidence=_pick_evidence(cleaned, ["502", "504", "upstream", "connect() failed", "error"]),
            summary="检测到 Nginx 访问/错误日志中的异常模式。",
            follow_up_questions=[
                "故障是全站还是单个路径？",
                "upstream 最近是否变更过？",
                "是否仅高峰期出现？",
            ],
            raw_preview=cleaned[:800],
        ),
        LogType.app_stack: DiagnosisResult(
            log_type=LogType.app_stack,
            anomaly_type="应用未捕获异常 / 堆栈崩溃",
            root_cause="业务代码抛出未处理异常，可能由空值、依赖调用失败或数据异常触发。",
            investigation_steps=[
                "定位堆栈顶层业务帧（非框架内部）",
                "核对异常类型与入参/依赖返回值",
                "检查近期发布的相关改动",
                "确认是否有重试/降级策略",
            ],
            risk_level=RiskLevel.high,
            evidence=_pick_evidence(
                cleaned,
                ["traceback", "exception", "error", "caused by", "nullpointer", "typeerror", "panic"],
            ),
            summary="识别到应用异常栈，建议结合业务帧定位根因。",
            follow_up_questions=[
                "该异常是否刚发版后出现？",
                "是偶发还是批量用户复现？",
                "影响哪个服务/模块？",
            ],
            raw_preview=cleaned[:800],
        ),
        LogType.unknown: DiagnosisResult(
            log_type=LogType.unknown,
            anomaly_type="未识别日志类型",
            root_cause="当前样本特征不足以归类为 Docker / Nginx / 应用异常栈。",
            investigation_steps=[
                "补充更完整的日志片段（含时间戳与错误关键字）",
                "标明日志来源（容器/网关/应用）",
                "提供故障时间窗与影响范围",
            ],
            risk_level=RiskLevel.low,
            evidence=_pick_evidence(cleaned, ["error", "fail", "warn", "exception"]),
            summary="未能自动识别日志类型，请补充上下文后重试。",
            follow_up_questions=[
                "这段日志来自 Docker、Nginx 还是应用服务？",
                "故障现象是什么（报错页/重启/延迟）？",
                "大概发生在什么时间？",
            ],
            raw_preview=cleaned[:800],
        ),
    }

    result = templates[detected]
    result.severity_score = calculate_severity(cleaned, detected)
    if extra_context and extra_context.strip():
        ctx = extra_context.strip()[:400]
        result = result.model_copy(
            update={
                "summary": (
                    f"关于「{ctx}」：容器退出码 137 通常表示进程被 SIGKILL 终止，"
                    f"结合日志中的 OOM killer 与 cgroup memory limit exceeded，可判断为内存超限被杀。"
                ),
                "investigation_steps": [],
                "follow_up_questions": [],
            }
        )
    return result


def _meta(
    *,
    mode: str,
    content: str,
    cleaned: str,
    error: str | None = None,
    extra_context: str | None = None,
) -> dict[str, Any]:
    has_ctx = bool(extra_context and extra_context.strip())
    meta: dict[str, Any] = {
        "mode": mode,
        "detected_type": detect_log_type(cleaned).value,
        "input_chars": len(content),
        "cleaned_chars": len(cleaned),
        "truncated": len(content) > len(cleaned) or "[已截断" in cleaned,
        "stream": True,
        "round": 2 if has_ctx else 1,
        "has_extra_context": has_ctx,
    }
    if error:
        meta["fallback_error"] = error[:300]
    return meta


async def llm_diagnose(
    cleaned: str,
    log_type: LogType | None = None,
    extra_context: str | None = None,
) -> DiagnosisResult:
    detected = log_type or detect_log_type(cleaned)
    candidates = extract_candidate_evidence(cleaned)
    user_prompt = build_user_prompt(cleaned, detected, candidates, extra_context)
    raw = await chat_completion_async(SYSTEM_PROMPT, user_prompt)
    return parse_diagnosis(
        raw,
        cleaned_log=cleaned,
        detected_type=detected,
        candidate_evidence=candidates,
    )


async def diagnose(
    content: str,
    log_type: LogType | None = None,
    extra_context: str | None = None,
) -> DiagnoseOutcome:
    """统一入口：有 Key 走 LLM；否则或失败时按配置回退 mock。"""
    settings = get_settings()
    cleaned = clean_log_with_context(content)

    if not settings.llm_configured:
        return DiagnoseOutcome(result=mock_diagnose(cleaned, log_type, extra_context), mode="mock")

    try:
        result = await llm_diagnose(cleaned, log_type, extra_context)
        result.severity_score = calculate_severity(cleaned, log_type or detect_log_type(cleaned))
        return DiagnoseOutcome(result=result, mode="llm")
    except Exception as exc:  # noqa: BLE001 - 演示闭环需要兜底
        logger.exception("LLM 诊断失败，准备回退: %s", exc)
        if settings.llm_fallback_mock:
            return DiagnoseOutcome(
                result=mock_diagnose(cleaned, log_type, extra_context),
                mode="mock_fallback",
                error=str(exc),
            )
        raise


async def _stream_text_as_delta(text: str, chunk_size: int = 8) -> AsyncIterator[str]:
    """把整段文本拆成小块，模拟打字机（mock / fallback 用）。"""
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]
        await asyncio.sleep(0.02)


async def diagnose_stream(
    content: str,
    log_type: LogType | None = None,
    extra_context: str | None = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """
    SSE 事件流（对齐项目一）：
    - stage  → {stage, label}
    - delta  → {text}
    - result → {success, result, meta}
    - error  → {message}
    """
    settings = get_settings()
    is_refine = bool(extra_context and extra_context.strip())

    if is_refine:
        yield ("stage", {"stage": "refining", "label": "正在结合补充信息二次诊断…"})
    else:
        yield ("stage", {"stage": "cleaning", "label": "正在清洗日志…"})
    await asyncio.sleep(0.05)
    cleaned = clean_log_with_context(content)
    if not cleaned:
        yield ("error", {"message": "日志内容为空"})
        return

    yield ("stage", {"stage": "identifying", "label": "正在识别日志类型…"})
    await asyncio.sleep(0.05)
    detected = log_type or detect_log_type(cleaned)
    yield (
        "stage",
        {
            "stage": "identified",
            "label": f"已识别为 {detected.value}",
            "log_type": detected.value,
        },
    )

    locate_label = "正在融合补充信息定位根因…" if is_refine else "正在定位根因…"
    yield ("stage", {"stage": "locating", "label": locate_label})
    candidates = extract_candidate_evidence(cleaned)

    # ── 外部检索（最多 1 次，仅首次诊断）──
    search_result = None
    if not is_refine and settings.web_search_enabled:
        yield ("stage", {"stage": "searching", "label": "检索外部知识库…"})
        try:
            search_result = await search_error_context(
                error_text=cleaned,
                log_type=detected.value,
                max_results=settings.web_search_max_results,
            )
            if search_result and search_result.fetched_count > 0:
                yield (
                    "stage",
                    {
                        "stage": "search_done",
                        "label": f"已检索到 {search_result.fetched_count} 条参考资料",
                        "search_trace": search_result.to_trace(),
                    },
                )
            else:
                yield ("stage", {"stage": "search_done", "label": "未找到相关外部资料"})
        except Exception as exc:
            logger.warning("外部检索异常: %s", exc)
            yield ("stage", {"stage": "search_done", "label": "外部检索失败，继续本地诊断"})

    # 构建 prompt（如有搜索结果则注入）
    search_context = search_result.to_prompt_context() if search_result and search_result.fetched_count > 0 else None
    user_prompt = build_user_prompt(cleaned, detected, candidates, extra_context, search_context)

    mode = "mock"
    error: str | None = None
    result: DiagnosisResult | None = None

    gen_label = "正在生成二次诊断建议…" if is_refine else "正在生成建议…"
    if settings.llm_configured:
        yield ("stage", {"stage": "generating", "label": gen_label})
        buffer = ""
        try:
            async for piece in chat_completion_stream(SYSTEM_PROMPT, user_prompt):
                buffer += piece
                yield ("delta", {"text": piece})
            result = parse_diagnosis(
                buffer,
                cleaned_log=cleaned,
                detected_type=detected,
                candidate_evidence=candidates,
            )
            result.severity_score = calculate_severity(cleaned, detected)
            mode = "llm"
        except Exception as exc:  # noqa: BLE001
            logger.exception("流式 LLM 诊断失败: %s", exc)
            error = str(exc)
            if not settings.llm_fallback_mock:
                yield ("error", {"message": f"诊断失败: {exc}"})
                return
            mode = "mock_fallback"
            yield ("stage", {"stage": "fallback", "label": "LLM 失败，切换本地诊断…"})
            result = mock_diagnose(cleaned, log_type, extra_context)
            narrative = f"{result.summary}\n根因：{result.root_cause}"
            async for piece in _stream_text_as_delta(narrative):
                yield ("delta", {"text": piece})
    else:
        yield (
            "stage",
            {
                "stage": "generating",
                "label": "正在生成建议（本地模式）…" if not is_refine else "正在二次诊断（本地模式）…",
            },
        )
        result = mock_diagnose(cleaned, log_type, extra_context)
        narrative = f"{result.summary}\n根因：{result.root_cause}"
        async for piece in _stream_text_as_delta(narrative):
            yield ("delta", {"text": piece})

    assert result is not None
    yield (
        "result",
        {
            "success": True,
            "result": result.model_dump(mode="json"),
            "meta": _meta(
                mode=mode,
                content=content,
                cleaned=cleaned,
                error=error,
                extra_context=extra_context,
            ),
        },
    )
