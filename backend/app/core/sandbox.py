"""
沙箱目录管理器。

会话文件隔离：/gateway/uploads/{session_id}/
- 上传时创建目录并写入文件
- 读取文件通过 Filesystem MCP Server
- 定时清理过期会话目录
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


def sandbox_root() -> Path:
    """沙箱根目录（绝对路径）。"""
    return Path(_settings.fs_sandbox_root).resolve()


def session_dir(session_id: str) -> Path:
    """会话沙箱目录。"""
    return sandbox_root() / session_id


def ensure_session_dir(session_id: str) -> Path:
    """创建并返回会话目录。"""
    d = session_dir(session_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_upload(session_id: str, filename: str, content: bytes) -> Path:
    """
    将上传文件保存到会话沙箱目录。

    Returns:
        保存后的文件绝对路径
    """
    d = ensure_session_dir(session_id)
    # 防路径穿越：只取文件名部分
    safe_name = os.path.basename(filename) or "upload"
    filepath = d / safe_name
    filepath.write_bytes(content)
    logger.info("沙箱写入: %s (%d bytes)", filepath, len(content))
    return filepath


def relative_path(absolute_path: str | Path) -> str:
    """将绝对路径转为相对于沙箱根的路径（MCP 调用需要）。"""
    return str(Path(absolute_path).relative_to(sandbox_root()))


def list_session_files(session_id: str) -> list[Path]:
    """列出会话目录下所有文件。"""
    d = session_dir(session_id)
    if not d.exists():
        return []
    return sorted(d.iterdir())


def cleanup_session(session_id: str) -> None:
    """清理单个会话目录。"""
    d = session_dir(session_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        logger.info("已清理会话沙箱: %s", session_id)


def cleanup_expired() -> int:
    """
    清理超过 TTL 的会话目录。

    Returns:
        清理的目录数量
    """
    ttl = _settings.fs_session_ttl_minutes
    root = sandbox_root()
    if not root.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ttl)
    cleaned = 0

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                cleaned += 1
                logger.info("清理过期沙箱: %s (修改时间 %s)", entry.name, mtime.isoformat())
        except OSError:
            pass

    if cleaned:
        logger.info("沙箱清理: %d 个过期目录已删除", cleaned)
    return cleaned


# =============================================================================
# 后台定时清理
# =============================================================================

_cleanup_thread: threading.Thread | None = None
_cleanup_stop = threading.Event()


def _cleanup_loop(interval_seconds: int = 300):
    """后台线程：每 N 秒扫描一次过期目录。"""
    while not _cleanup_stop.wait(interval_seconds):
        try:
            cleanup_expired()
        except Exception:
            logger.exception("沙箱清理异常")


def start_cleanup_scheduler(interval_seconds: int = 300) -> None:
    """启动后台定时清理线程。"""
    global _cleanup_thread
    if _cleanup_thread and _cleanup_thread.is_alive():
        return
    _cleanup_stop.clear()
    _cleanup_thread = threading.Thread(
        target=_cleanup_loop, args=(interval_seconds,), daemon=True, name="sandbox-cleanup"
    )
    _cleanup_thread.start()
    logger.info("沙箱清理调度器已启动 (间隔 %ds, TTL %dmin)", interval_seconds, _settings.fs_session_ttl_minutes)


def stop_cleanup_scheduler() -> None:
    """停止清理线程。"""
    _cleanup_stop.set()
    if _cleanup_thread:
        _cleanup_thread.join(timeout=5)
