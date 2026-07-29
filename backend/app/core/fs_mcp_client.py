"""
Filesystem MCP Server 客户端。

通过 MCP 协议访问沙箱目录，提供：
- read_file(path)      → 读取文件内容（文本或 base64）
- list_directory(path) → 列出目录
- write_file(path, content) → 写入文件

只对沙箱目录授权，会话隔离。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_FS_MCP_AVAILABLE = False
try:
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.stdio import stdio_client              # noqa: F401
    _FS_MCP_AVAILABLE = True
except ImportError:
    logger.warning("mcp 包未安装，Filesystem MCP 不可用")


class FsMcpError(RuntimeError):
    """Filesystem MCP 错误。"""


# =============================================================================
# 客户端单例
# =============================================================================

class FsMcpClient:
    _instance: FsMcpClient | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._session: Any = None
        self._read: Any = None
        self._write: Any = None
        self._lock = threading.Lock()
        self._connecting = False
        self._settings = get_settings()

    @classmethod
    def get(cls) -> FsMcpClient:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 公开 API ──

    @property
    def is_available(self) -> bool:
        return _FS_MCP_AVAILABLE and self._settings.fs_mcp_server_enabled

    @property
    def connected(self) -> bool:
        return self._session is not None

    def ensure_connected(self) -> None:
        if not self.connected:
            self._run_async(self._connect_async())

    def health_check(self) -> dict[str, Any]:
        if not self.is_available:
            return {"ok": False, "error": "Filesystem MCP 已禁用或 SDK 未安装"}
        try:
            t0 = time.time()
            self.ensure_connected()
            elapsed = int((time.time() - t0) * 1000)
            return {"ok": True, "latency_ms": elapsed}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def read_file(self, path: str) -> bytes:
        """读取文件，返回原始字节。"""
        self._guard()
        return self._run_async(self._read_file_async(path))

    def read_text(self, path: str) -> str:
        """读取文本文件，返回字符串。"""
        return self.read_file(path).decode("utf-8")

    def list_directory(self, path: str) -> list[dict[str, Any]]:
        """列出目录内容。"""
        self._guard()
        return self._run_async(self._list_directory_async(path))

    def write_file(self, path: str, content: bytes) -> None:
        """写入文件到沙箱（MCP write_file 工具）。"""
        self._guard()
        self._run_async(self._write_file_async(path, content))

    def disconnect(self) -> None:
        self._run_async(self._disconnect_async())

    # ── 内部 async ──

    async def _connect_async(self) -> None:
        settings = self._settings

        with self._lock:
            if self._session is not None:
                return
            if self._connecting:
                raise FsMcpError("Filesystem MCP 正在连接中")
            self._connecting = True

        # 确定沙箱根目录的绝对路径
        sandbox_root = Path(settings.fs_sandbox_root).resolve()
        sandbox_root.mkdir(parents=True, exist_ok=True)

        args = settings.fs_mcp_server_args.split() + [str(sandbox_root)]
        cmd = settings.fs_mcp_server_command
        timeout = settings.fs_mcp_connect_timeout

        logger.info("正在启动 Filesystem MCP Server: %s %s", cmd, " ".join(args))

        try:
            params = StdioServerParameters(command=cmd, args=args, env=os.environ.copy())

            async with asyncio.timeout(timeout):
                read, write = await stdio_client(params).__aenter__()
                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()

            with self._lock:
                self._read = read
                self._write = write
                self._session = session
                self._connecting = False

            logger.info("✅ Filesystem MCP 已连接，沙箱: %s", sandbox_root)

        except asyncio.TimeoutError:
            self._connecting = False
            raise FsMcpError(f"Filesystem MCP 连接超时 ({timeout}s)")
        except Exception as exc:
            self._connecting = False
            raise FsMcpError(f"Filesystem MCP 连接失败: {exc}") from exc

    async def _disconnect_async(self) -> None:
        with self._lock:
            if self._session:
                try:
                    await self._session.__aexit__(None, None, None)
                except Exception:
                    pass
            self._session = None
            self._read = None
            self._write = None

    async def _read_file_async(self, path: str) -> bytes:
        await self._ensure_connected()
        result = await self._call_tool("read_file", {"path": path})
        # MCP read_file 通常返回文本内容或 base64
        text = self._extract_text(result)
        # 尝试作为 base64 解码，否则直接编码为 bytes
        try:
            return base64.b64decode(text)
        except Exception:
            return text.encode("utf-8")

    async def _read_text_async(self, path: str) -> str:
        await self._ensure_connected()
        result = await self._call_tool("read_file", {"path": path})
        return self._extract_text(result)

    async def _list_directory_async(self, path: str) -> list[dict[str, Any]]:
        await self._ensure_connected()
        result = await self._call_tool("list_directory", {"path": path})
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            for key in ("entries", "files", "contents"):
                if key in result:
                    return result[key]
        return []

    async def _write_file_async(self, path: str, content: bytes) -> None:
        await self._ensure_connected()
        # 尝试传 base64
        b64 = base64.b64encode(content).decode("ascii")
        await self._call_tool("write_file", {"path": path, "content": b64, "encoding": "base64"})

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        session = self._session
        if session is None:
            raise FsMcpError("Filesystem MCP 未连接")

        timeout = self._settings.fs_mcp_call_timeout
        try:
            async with asyncio.timeout(timeout):
                result = await session.call_tool(name, arguments)
        except asyncio.TimeoutError:
            raise FsMcpError(f"Filesystem MCP 调用超时 ({timeout}s): {name}")
        except Exception as exc:
            raise FsMcpError(f"Filesystem MCP 调用失败: {name} — {exc}") from exc

        if result.content:
            text = result.content[0].text
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
        return result.content

    async def _ensure_connected(self) -> None:
        if not self.connected:
            await self._connect_async()

    def _guard(self) -> None:
        if not self._settings.fs_mcp_server_enabled:
            raise FsMcpError("Filesystem MCP 已禁用")
        if not _FS_MCP_AVAILABLE:
            raise FsMcpError("MCP SDK 未安装")

    @staticmethod
    def _extract_text(raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            for key in ("content", "text", "data"):
                if key in raw:
                    return str(raw[key])
        if isinstance(raw, list) and raw:
            return str(raw[0])
        return str(raw)

    # ── 同步桥接 ──

    def _run_async(self, coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, coro).result()


# =============================================================================
# 模块入口
# =============================================================================

_client: FsMcpClient | None = None
_client_lock = threading.Lock()


def get_fs_mcp_client() -> FsMcpClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = FsMcpClient.get()
    return _client
