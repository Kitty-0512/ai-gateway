"""
外部检索模块 — 为日志诊断提供补充上下文。

流程：
1. DuckDuckGo 搜索错误关键词 → 获取前 N 个 URL
2. 通过 httpx（优先）或 Fetch MCP Server 抓取页面内容
3. 返回精简摘要，注入诊断 Prompt

免费、无需 API Key。每轮诊断最多触发 1 次。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# ── HTML 标签清理 ──────────────────────────────────────
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_SPACE = re.compile(r"\s{2,}")


def _strip_html(text: str) -> str:
    text = _HTML_TAG.sub(" ", text)
    text = _HTML_SPACE.sub(" ", text)
    return text.strip()[:3000]


# =============================================================================
# 搜索
# =============================================================================

async def _duckduckgo_search(query: str, max_results: int = 3) -> list[dict[str, str]]:
    """DuckDuckGo 文本搜索，返回 [{title, url, snippet}]。"""
    settings = get_settings()
    try:
        from duckduckgo_search import DDGS

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None,
            lambda: list(DDGS().text(query, max_results=max_results)),
        )
        return [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in results
        ]
    except ImportError:
        logger.warning("duckduckgo_search 未安装，跳过搜索。安装: pip install duckduckgo_search")
        return []
    except Exception as exc:
        logger.warning("DuckDuckGo 搜索失败: %s", exc)
        return []


# =============================================================================
# 抓取
# =============================================================================

async def _fetch_url(url: str, timeout: float = 8.0) -> str | None:
    """用 httpx 抓取 URL 内容，返回纯文本摘要。"""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AI-Gateway/2.0)",
                    "Accept": "text/html,application/xhtml+xml",
                },
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return None
            ct = resp.headers.get("content-type", "")
            if "text/html" in ct:
                return _strip_html(resp.text)
            return resp.text[:3000]
    except Exception as exc:
        logger.debug("抓取 %s 失败: %s", url[:60], exc)
        return None


# =============================================================================
# 对外接口
# =============================================================================

@dataclass
class SearchResult:
    """一次检索的结果。"""
    query: str
    results: list[dict[str, Any]]  # [{title, url, snippet, fetched_text}]
    source_count: int
    fetched_count: int

    def to_trace(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "source_count": self.source_count,
            "fetched_count": self.fetched_count,
            "sources": [
                {"title": r["title"], "url": r["url"]}
                for r in self.results if r.get("fetched_text")
            ],
        }

    def to_prompt_context(self) -> str:
        """转为 Prompt 可用的上下文片段。"""
        parts = [f'以下是从网络检索到的与错误相关的信息（关键词: "{self.query}"）：\n']
        for r in self.results:
            if r.get("fetched_text"):
                parts.append(
                    f"### 来源: {r['title']}\n"
                    f"URL: {r['url']}\n"
                    f"内容摘要:\n{r['fetched_text'][:800]}\n"
                )
        if len(parts) == 1:
            parts.append("（未获取到有效网页内容）")
        return "\n".join(parts)


async def search_error_context(
    error_text: str,
    log_type: str = "",
    max_results: int = 3,
) -> SearchResult | None:
    """
    根据日志中的错误信息搜索外部资料。

    Args:
        error_text: 日志或错误信息文本
        log_type: docker / nginx / app_stack / unknown
        max_results: 最大搜索结果数

    Returns:
        SearchResult，搜索失败返回 None
    """
    settings = get_settings()
    if not settings.web_search_enabled:
        logger.debug("Web 搜索已禁用")
        return None

    # 构建搜索查询：日志类型 + 关键错误行
    parts = [log_type] if log_type and log_type != "unknown" else []
    # 提取关键错误行（取前两行非空、非标点行）
    for line in error_text.splitlines():
        clean = line.strip()
        if clean and len(clean) > 10 and not clean.startswith(("#", "//", "at ")):
            parts.append(clean[:120])
            if len(parts) >= 3:
                break
    query = " ".join(parts)[:300]
    if not query.strip():
        return None

    logger.info("外部检索: %s", query[:100])

    # 1) DuckDuckGo 搜索
    sources = await _duckduckgo_search(query, max_results=max_results)
    if not sources:
        return None

    # 2) 并发抓取前 2 个 URL
    settings2 = get_settings()
    timeout = settings2.web_search_timeout
    tasks = [asyncio.create_task(_fetch_url(s["url"], timeout)) for s in sources[:2]]
    fetched = await asyncio.gather(*tasks, return_exceptions=True)

    fetched_count = 0
    for i, text in enumerate(fetched):
        if isinstance(text, str) and text:
            sources[i]["fetched_text"] = text
            fetched_count += 1
        elif isinstance(text, Exception):
            logger.debug("抓取异常: %s", text)

    return SearchResult(
        query=query,
        results=sources[:max_results],
        source_count=len(sources),
        fetched_count=fetched_count,
    )
