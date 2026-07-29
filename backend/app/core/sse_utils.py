"""
SSE 流式响应工具。

统一的 Server-Sent Events 格式化和流式辅助函数，
供 sql_analyze 和 log_diagnose 两个 router 共享。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


def sse_frame(event: str, data: dict[str, Any]) -> str:
    """构建单帧 SSE 消息：event + data。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


async def sse_event_generator(
    producer: AsyncIterator[tuple[str, dict[str, Any]]],
    keep_alive_seconds: int = 15,
) -> AsyncIterator[str]:
    """
    从异步生成器中消费 (event, data) 元组，转为 SSE 字节流。

    支持 keep-alive：若 producer 超过 keep_alive_seconds 无产出，
    自动插入 SSE 注释行（: keep-alive）防止连接断开。
    """
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

    async def _produce() -> None:
        try:
            async for event, data in producer:
                await queue.put((event, data))
        except Exception as exc:  # noqa: BLE001
            await queue.put(("error", {"message": str(exc)}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(_produce())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=keep_alive_seconds)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue

            if item is None:
                break
            event, data = item
            yield sse_frame(event, data)
    finally:
        if not task.done():
            task.cancel()


async def text_to_delta_stream(text: str, chunk_size: int = 8) -> AsyncIterator[str]:
    """将整段文本拆为小块，模拟打字机效果（供 mock 模式使用）。"""
    for i in range(0, len(text), chunk_size):
        yield text[i : i + chunk_size]
        await asyncio.sleep(0.02)
