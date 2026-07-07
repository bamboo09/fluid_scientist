"""研究会话的内存存储。"""

from __future__ import annotations

from fluid_scientist.research.models import ResearchSession


class SessionStore:
    """研究会话的内存存储实现。"""

    def __init__(self) -> None:
        self._sessions: dict[str, ResearchSession] = {}

    def create(self, session: ResearchSession) -> ResearchSession:
        """创建并存储一个新的研究会话。

        Args:
            session: 要创建的会话对象。

        Returns:
            已存储的会话对象。

        Raises:
            ValueError: 如果 session_id 已存在。
        """
        if session.session_id in self._sessions:
            raise ValueError(f"session_id {session.session_id!r} already exists")
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> ResearchSession:
        """根据 ID 获取研究会话。

        Args:
            session_id: 会话 ID。

        Returns:
            对应的会话对象。

        Raises:
            KeyError: 如果会话不存在。
        """
        if session_id not in self._sessions:
            raise KeyError(session_id)
        return self._sessions[session_id]

    def update(self, session_id: str, **updates: object) -> ResearchSession:
        """更新已有研究会话的字段。

        Args:
            session_id: 要更新的会话 ID。
            **updates: 要更新的字段键值对。

        Returns:
            更新后的会话对象。

        Raises:
            KeyError: 如果会话不存在。
        """
        if session_id not in self._sessions:
            raise KeyError(session_id)
        session = self._sessions[session_id]
        updated = session.model_copy(update=updates)
        self._sessions[session_id] = updated
        return updated

    def list_by_project(self, project_id: str) -> list[ResearchSession]:
        """列出指定项目下的所有研究会话。

        Args:
            project_id: 项目 ID。

        Returns:
            该项目下的会话列表（按创建时间排序）。
        """
        return sorted(
            (s for s in self._sessions.values() if s.project_id == project_id),
            key=lambda s: s.created_at,
        )


__all__ = ["SessionStore"]
