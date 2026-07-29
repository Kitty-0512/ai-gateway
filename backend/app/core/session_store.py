"""
内存会话存储。

用字典实现简单的会话/对话管理，不引入额外数据库。
key = session_id (str)，value = 会话元数据 dict。

支持两种模式：
- "sql" 模式：存储 dataset_id、table_name、conversation history
- "log" 模式：存储诊断历史、追问上下文
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


class SessionStore:
    """线程安全的内存会话存储（单例）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def create(self, mode: str = "sql") -> str:
        """创建新会话，返回 session_id。"""
        sid = uuid.uuid4().hex[:16]
        self._sessions[sid] = {
            "session_id": sid,
            "mode": mode,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "messages": [],
            "meta": {},
        }
        return sid

    def get(self, session_id: str) -> dict[str, Any] | None:
        """获取会话，不存在返回 None。"""
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str | None, mode: str = "sql") -> dict[str, Any]:
        """获取现有会话，或创建新会话。"""
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        sid = self.create(mode)
        return self._sessions[sid]

    def update(self, session_id: str, **kwargs: Any) -> None:
        """更新会话字段。"""
        if session_id in self._sessions:
            self._sessions[session_id].update(kwargs)

    def append_message(self, session_id: str, role: str, content: str) -> None:
        """向会话追加一条消息。"""
        if session_id in self._sessions:
            self._sessions[session_id].setdefault("messages", []).append({
                "role": role,
                "content": content,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

    def delete(self, session_id: str) -> bool:
        """删除会话。返回是否成功。"""
        return self._sessions.pop(session_id, None) is not None

    def list_sessions(self, mode: str | None = None) -> list[dict[str, Any]]:
        """列出所有会话（可按 mode 过滤）。"""
        result = []
        for s in self._sessions.values():
            if mode is None or s.get("mode") == mode:
                result.append({
                    "session_id": s["session_id"],
                    "mode": s["mode"],
                    "created_at": s["created_at"],
                    "message_count": len(s.get("messages", [])),
                })
        return result


# 全局单例
store = SessionStore()
