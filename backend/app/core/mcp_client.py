"""
MCP (Model Context Protocol) 客户端封装。

管理 MySQL MCP Server 的 stdio 连接生命周期，提供：
- list_tables()       → 列出所有用户表
- describe_table(tbl)  → 获取表结构
- execute_query(sql)   → 执行 SELECT 并返回行列表
- health_check()       → 启动时探测 MCP Server 是否可用

设计要点：
- 单例 + 线程安全：整个进程只有一个 MCP 连接，避免重复拉起进程
- 懒连接：首次调用时才建立，启动健康检查可提前预热
- 超时控制：connect / call 均受配置中的超时限制
- 优雅降级：连接失败抛出 McpError，调用方捕获后提示"数据库工具暂时不可用"
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# =============================================================================
# MCP SDK 延迟导入
# =============================================================================

_MCP_AVAILABLE = False
try:
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.stdio import stdio_client              # noqa: F401

    _MCP_AVAILABLE = True
except ImportError:
    logger.warning("mcp 包未安装，MCP 模式不可用。安装: pip install mcp")


# =============================================================================
# 错误类型
# =============================================================================

class McpError(RuntimeError):
    """MCP 相关错误：连接失败、超时、工具不可用等。"""


# =============================================================================
# 连接状态
# =============================================================================

class _McpConnection:
    """线程安全的 MCP 连接状态。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._session: Any = None
        self._read: Any = None
        self._write: Any = None
        self._tools: dict[str, Any] = {}
        self._created_at: float = 0.0
        self._error: str | None = None          # 最近一次连接失败的原因
        self._connecting: bool = False           # 是否正在连接中（防并发建连）

    @property
    def connected(self) -> bool:
        return self._session is not None


# =============================================================================
# 客户端
# =============================================================================

class McpClient:
    """
    MCP 客户端单例（线程安全）。

    用法:
        from app.core.mcp_client import get_mcp_client
        client = get_mcp_client()
        client.health_check()                      # 启动时调用
        rows = client.execute_query("SELECT ...")
    """

    _instance: McpClient | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._conn = _McpConnection()
        self._settings = get_settings()

    @classmethod
    def get(cls) -> McpClient:
        """双重检查锁定，确保全局只有一个实例。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 健康检查 ──────────────────────────────────────

    def health_check(self) -> dict[str, Any]:
        """
        启动时健康检查，返回 MCP 状态。

        Returns:
            {"ok": bool, "tools": [...], "error": str|null, "latency_ms": int}
        """
        if not _MCP_AVAILABLE:
            return {"ok": False, "tools": [], "error": "MCP SDK 未安装", "latency_ms": 0}
        if not self._settings.mcp_server_enabled:
            return {"ok": False, "tools": [], "error": "MCP 已通过配置禁用", "latency_ms": 0}

        try:
            t0 = time.time()
            self.ensure_connected()
            elapsed = int((time.time() - t0) * 1000)
            tools = list(self._conn._tools.keys()) if self._conn._tools else []
            return {"ok": True, "tools": tools, "error": None, "latency_ms": elapsed}
        except McpError as e:
            return {"ok": False, "tools": [], "error": str(e), "latency_ms": 0}
        except Exception as e:
            return {"ok": False, "tools": [], "error": f"未知错误: {e}", "latency_ms": 0}

    # ── 公开 API（同步） ──────────────────────────────

    def list_tables(self) -> list[str]:
        self._guard_disabled()
        return self._run_async(self._list_tables_async())

    def describe_table(self, table_name: str) -> list[dict[str, Any]]:
        self._guard_disabled()
        return self._run_async(self._describe_table_async(table_name))

    def execute_query(self, sql: str) -> list[dict[str, Any]]:
        self._guard_disabled()
        return self._run_async(self._execute_query_async(sql))

    def sample_data(self, table_name: str, n: int = 3) -> list[dict[str, Any]]:
        return self.execute_query(f"SELECT * FROM `{table_name}` LIMIT {n}")

    @property
    def is_available(self) -> bool:
        """MCP 是否可用（SDK 已安装 + 配置启用）。"""
        return _MCP_AVAILABLE and self._settings.mcp_server_enabled

    # ── 连接管理 ────────────────────────────────────

    def ensure_connected(self) -> None:
        """确保连接已建立。失败抛出 McpError。"""
        self._guard_disabled()
        if self._conn.connected:
            return
        self._run_async(self._connect_async())

    def disconnect(self) -> None:
        self._run_async(self._disconnect_async())

    # ── 内部 Async ───────────────────────────────────

    async def _connect_async(self) -> None:
        if not _MCP_AVAILABLE:
            raise McpError("MCP SDK 未安装，无法建立 MCP 连接")

        with self._conn._lock:
            if self._conn.connected:
                return

            # 防并发建连
            if self._conn._connecting:
                raise McpError("MCP 正在连接中，请稍后")
            self._conn._connecting = True

        settings = self._settings
        timeout = settings.mcp_connect_timeout

        # 构建环境变量
        env = os.environ.copy()
        if settings.mcp_server_env:
            try:
                env.update(json.loads(settings.mcp_server_env))
            except json.JSONDecodeError:
                logger.warning("mcp_server_env 不是合法 JSON，已忽略")
        else:
            env.setdefault("MYSQL_HOST", settings.mysql_host)
            env.setdefault("MYSQL_PORT", str(settings.mysql_port))
            env.setdefault("MYSQL_USER", settings.mysql_user)
            env.setdefault("MYSQL_PASSWORD", settings.mysql_password)
            env.setdefault("MYSQL_DATABASE", settings.mysql_database)

        args = settings.mcp_server_args.split()
        cmd = settings.mcp_server_command

        logger.info("正在启动 MCP Server: %s %s (超时 %ss)", cmd, " ".join(args), timeout)

        try:
            params = StdioServerParameters(command=cmd, args=args, env=env)

            async with asyncio.timeout(timeout):
                read, write = await stdio_client(params).__aenter__()
                session = ClientSession(read, write)
                await session.__aenter__()
                await session.initialize()

                tools_result = await session.list_tools()
                tools = {t.name: t for t in tools_result.tools}

            with self._conn._lock:
                self._conn._read = read
                self._conn._write = write
                self._conn._session = session
                self._conn._tools = tools
                self._conn._created_at = time.time()
                self._conn._error = None
                self._conn._connecting = False

            logger.info(
                "✅ MCP 已连接，%d 个工具可用: %s (耗时 %.1fs)",
                len(tools), list(tools.keys()), time.time() - self._conn._created_at,
            )

        except asyncio.TimeoutError:
            self._conn._connecting = False
            self._conn._error = f"MCP Server 启动超时 ({timeout}s)"
            logger.error("❌ %s", self._conn._error)
            raise McpError(self._conn._error)

        except Exception as exc:
            self._conn._connecting = False
            self._conn._error = f"MCP Server 启动失败: {exc}"
            logger.error("❌ %s", self._conn._error)
            raise McpError(self._conn._error) from exc

    async def _disconnect_async(self) -> None:
        with self._conn._lock:
            if self._conn._session:
                try:
                    await self._conn._session.__aexit__(None, None, None)
                except Exception:
                    pass
            self._conn._session = None
            self._conn._read = None
            self._conn._write = None
            self._conn._tools = {}
            self._conn._error = None

    # ── 工具调用 ────────────────────────────────────

    async def _list_tables_async(self) -> list[str]:
        await self._ensure_connected_async()
        result = await self._call_tool("list_tables", {})
        return self._extract_table_list(result)

    async def _describe_table_async(self, table_name: str) -> list[dict[str, Any]]:
        await self._ensure_connected_async()
        result = await self._call_tool("describe_table", {"name": table_name})
        return self._normalize_schema(result)

    async def _execute_query_async(self, sql: str) -> list[dict[str, Any]]:
        await self._ensure_connected_async()
        result = await self._call_tool("execute_query", {"query": sql})
        return self._normalize_rows(result)

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        session = self._conn._session
        if session is None:
            raise McpError("MCP 未连接")

        timeout = self._settings.mcp_call_timeout
        try:
            async with asyncio.timeout(timeout):
                result = await session.call_tool(name, arguments)
        except asyncio.TimeoutError:
            raise McpError(f"MCP 工具调用超时 ({timeout}s): {name}")
        except Exception as exc:
            logger.error("MCP 工具 %s 调用失败: %s", name, exc)
            raise McpError(f"MCP 工具调用失败: {name} — {exc}") from exc

        if result.content:
            text = result.content[0].text
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
        return result.content

    async def _ensure_connected_async(self) -> None:
        if not self._conn.connected:
            await self._connect_async()

    # ── 禁用检查 ────────────────────────────────────

    def _guard_disabled(self) -> None:
        if not self._settings.mcp_server_enabled:
            raise McpError("MCP 模式已禁用 (mcp_server_enabled=false)")
        if not _MCP_AVAILABLE:
            raise McpError("MCP SDK 未安装")

    # ── 结果规范化 ───────────────────────────────────

    @staticmethod
    def _extract_table_list(raw: Any) -> list[str]:
        if isinstance(raw, list):
            if raw and isinstance(raw[0], str):
                return raw
            if raw and isinstance(raw[0], dict):
                return [r.get("name", r.get("table_name", str(r))) for r in raw]
        if isinstance(raw, dict):
            if "tables" in raw:
                return McpClient._extract_table_list(raw["tables"])
        logger.warning("无法解析 list_tables 结果: %s", str(raw)[:200])
        return []

    @staticmethod
    def _normalize_schema(raw: Any) -> list[dict[str, Any]]:
        rows = raw if isinstance(raw, list) else (
            raw.get("columns", raw.get("schema", [])) if isinstance(raw, dict) else []
        )
        result = []
        for r in rows:
            if isinstance(r, dict):
                result.append({
                    "name":     str(r.get("name", r.get("column_name", r.get("Field", "")))),
                    "type":     str(r.get("type", r.get("column_type", r.get("Type", "")))),
                    "nullable": str(r.get("nullable", r.get("is_nullable", r.get("Null", "YES")))),
                    "key":      str(r.get("key", r.get("column_key", r.get("Key", "")))),
                    "default":  r.get("default", r.get("column_default", r.get("Default"))),
                    "comment":  str(r.get("comment", r.get("column_comment", r.get("Comment", "")))),
                })
        return result

    @staticmethod
    def _normalize_rows(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            return raw.get("rows", raw.get("data", raw.get("results", [])))
        return []

    # ── 同步桥接 ────────────────────────────────────

    def _run_async(self, coro):
        """在调用线程中同步执行 async coroutine。"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, coro).result()


# =============================================================================
# 模块级单例入口
# =============================================================================

_client: McpClient | None = None
_client_lock = threading.Lock()


def get_mcp_client() -> McpClient:
    """获取 MCP 客户端单例（线程安全）。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = McpClient.get()
    return _client
