"""
统一 LLM 客户端。

同时提供：
- OpenAI 兼容的异步客户端（AsyncOpenAI），用于日志诊断等简单调用
- LangChain ChatOpenAI 包装，用于 Text-to-SQL 链式调用
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ---- 可序列化重试配置 -------------------------------------------------------
# LangChain 的 with_retry 需要可 pickle 的 callable，闭包/局部函数不行。
# 方案：关闭内置 retry + 应用层循环重试（见 _call_with_retry）。


def get_openai_client() -> AsyncOpenAI:
    """获取 OpenAI 兼容异步客户端。"""
    settings = get_settings()
    if not settings.llm_configured:
        raise RuntimeError("未配置 OPENAI_API_KEY")
    return AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.llm_timeout_seconds,
    )


def get_langchain_llm() -> ChatOpenAI:
    """
    获取 LangChain ChatOpenAI 实例（用于 SQL 生成链）。

    2 层保护：
    1) max_retries=0 禁用 SDK 内置重试
    2) request_timeout 兜底
    应用层可通过 core.llm_client._call_with_retry() 获得重试能力。
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        openai_api_key=settings.openai_api_key,
        openai_api_base=settings.openai_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        max_retries=0,
        request_timeout=settings.llm_timeout_seconds,
    )


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---- 同步：LangChain 风格（供 Text-to-SQL 链使用）--------------------------

def chat_completion_sync(system: str, user: str) -> str:
    """同步非流式聊天补全（LangChain 后端）。"""
    llm = get_langchain_llm()
    response = llm.invoke(_messages(system, user))
    content = response.content if hasattr(response, "content") else str(response)
    if not content or not content.strip():
        raise RuntimeError("LLM 返回空内容")
    return content.strip()


def _call_with_retry(
    llm: ChatOpenAI,
    messages: list[dict[str, str]],
    *,
    max_attempts: int = 3,
) -> str:
    """
    同步调用 LLM 并在外层重试。

    这替代了 LangChain 内置的 with_retry，解决 Tenacity.Retrying
    不可 pickle 的问题，确保与 Tool 机制兼容。
    """
    import time

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = llm.invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)
            if content and content.strip():
                return content.strip()
            last_exc = RuntimeError("LLM 返回空内容")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("LLM 调用失败 (第 %d/%d 次): %s", attempt, max_attempts, exc)

        if attempt < max_attempts:
            time.sleep(1.5 * attempt)

    raise last_exc or RuntimeError("LLM 调用失败（已达最大重试次数）")


# ---- 异步：OpenAI SDK 风格（供日志诊断使用）--------------------------------

async def chat_completion_async(system: str, user: str) -> str:
    """异步非流式聊天补全。优先 JSON mode，不支持则降级。"""
    settings = get_settings()
    client = get_openai_client()
    logger.info("LLM request model=%s base=%s", settings.openai_model, settings.openai_base_url)

    common = {
        "model": settings.openai_model,
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
        "messages": _messages(system, user),
    }

    try:
        response = await client.chat.completions.create(
            **common, response_format={"type": "json_object"}
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("JSON mode 不可用，降级普通补全: %s", exc)
        response = await client.chat.completions.create(**common)

    content = response.choices[0].message.content or ""
    if not content.strip():
        raise RuntimeError("LLM 返回空内容")
    return content


async def chat_completion_stream(system: str, user: str) -> AsyncIterator[str]:
    """异步流式聊天补全，逐块 yield 文本。"""
    settings = get_settings()
    client = get_openai_client()
    logger.info(
        "LLM stream model=%s base=%s", settings.openai_model, settings.openai_base_url
    )

    stream = await client.chat.completions.create(
        model=settings.openai_model,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        messages=_messages(system, user),
        stream=True,
    )

    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        text = getattr(delta, "content", None) or ""
        if text:
            yield text


# ---- LangChain 兼容层（供 SQL 分析服务使用）-------------------------------

# 重试配置
_MAX_RETRIES = 3
_BASE_DELAY = 1.0
_REQUEST_TIMEOUT = 90

# 可重试的异常关键词
_RETRYABLE_ERRORS = (
    "timeout", "timed out", "connection", "connect",
    "rate limit", "rate_limit", "too many requests",
    "server error", "internal server error", "service unavailable",
    "503", "502", "504", "429", "overloaded", "capacity",
    "temporarily", "busy",
)


def _is_retryable(error: Exception) -> bool:
    msg = str(error).lower()
    for keyword in _RETRYABLE_ERRORS:
        if keyword in msg:
            return True
    exc_name = type(error).__name__.lower()
    return any(k in exc_name for k in ("timeout", "connection", "retry", "rate"))


def _build_langchain_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        openai_api_key=settings.openai_api_key,
        openai_api_base=settings.openai_base_url,
        temperature=0.1,
        max_tokens=2048,
        request_timeout=_REQUEST_TIMEOUT,
    )


def call_llm(
    messages: list[BaseMessage],
    max_retries: int = _MAX_RETRIES,
    base_delay: float = _BASE_DELAY,
) -> Any:
    """
    同步调用 LLM（LangChain 消息格式），带自动重试和指数退避。

    供 Text-to-SQL 服务链使用。
    """
    llm = _build_langchain_llm()
    last_error = None

    for attempt in range(max_retries):
        try:
            start = time.time()
            response = llm.invoke(messages)
            elapsed = (time.time() - start) * 1000
            if attempt > 0:
                logger.info(f"LLM 调用成功 (第 {attempt + 1} 次尝试, {elapsed:.0f}ms)")
            else:
                logger.debug(f"LLM 调用成功 ({elapsed:.0f}ms)")
            return response
        except Exception as e:
            last_error = e
            if not _is_retryable(e):
                logger.error(f"LLM 调用失败（不可重试）: {type(e).__name__}: {e}")
                raise RuntimeError(
                    f"AI 服务调用失败: {e}\n请检查 API Key 是否有效。"
                ) from e
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"LLM 调用失败 ({attempt + 1}/{max_retries}): "
                    f"{type(e).__name__}: {str(e)[:200]}\n  → {delay:.0f}s 后重试..."
                )
                time.sleep(delay)
            else:
                logger.error(
                    f"LLM 调用最终失败 ({attempt + 1}/{max_retries}): "
                    f"{type(e).__name__}: {str(e)[:300]}"
                )

    raise RuntimeError(
        f"AI 服务暂时不可用，已重试 {max_retries} 次。\n"
        f"原因: {last_error}\n请稍后重试。"
    )


def call_llm_stream(messages: list[BaseMessage]) -> Iterator[str]:
    """
    流式调用 LLM（LangChain 消息格式），逐块 yield 文本。

    供 Text-to-SQL 服务的 SSE 打字机输出使用。
    """
    llm = _build_langchain_llm()
    start = time.time()
    for chunk in llm.stream(messages):
        if chunk.content:
            yield chunk.content
    elapsed = (time.time() - start) * 1000
    logger.debug(f"LLM 流式调用完成 ({elapsed:.0f}ms)")
