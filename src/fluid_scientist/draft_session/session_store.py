"""In-memory persistence for draft sessions.

The :class:`DraftSessionStore` is a deliberately simple, process-local
store that keeps :class:`DraftSession`, :class:`SessionMessage` and
:class:`ResearchState` instances in dictionaries.  It exists so the
draft workflow can be exercised end-to-end in tests and single-process
deployments without pulling in a database; production deployments are
expected to layer a persistent repository on top of the same interface.
"""

from __future__ import annotations

from fluid_scientist.draft_session.models import (
    DraftSession,
    ResearchState,
    SessionMessage,
)


class DraftSessionStore:
    """In-memory store for draft sessions, messages and research state.

    The store is intentionally not thread-safe; callers that need
    cross-thread access should wrap it or provide an alternative
    repository implementation.  All lookups are O(1) dictionary reads.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, DraftSession] = {}
        self._messages: dict[str, list[SessionMessage]] = {}
        self._research_states: dict[str, ResearchState] = {}

    # -- sessions -----------------------------------------------------------

    def create_session(self, session: DraftSession) -> None:
        """Insert a new :class:`DraftSession`.

        Args:
            session: The session to persist.

        Raises:
            ValueError: If a session with the same ``session_id`` already
                exists.
        """
        if session.session_id in self._sessions:
            raise ValueError(
                f"session_id {session.session_id!r} already exists"
            )
        self._sessions[session.session_id] = session

    def get_session(self, session_id: str) -> DraftSession | None:
        """Return the session for ``session_id`` or ``None`` if absent."""
        return self._sessions.get(session_id)

    def update_session(self, session: DraftSession) -> None:
        """Replace the stored session with ``session``.

        The ``updated_at`` timestamp is refreshed so callers do not have
        to remember to bump it manually.

        Raises:
            KeyError: If no session with the given id exists.
        """
        if session.session_id not in self._sessions:
            raise KeyError(session.session_id)
        self._sessions[session.session_id] = session

    # -- messages -----------------------------------------------------------

    def add_message(self, message: SessionMessage) -> None:
        """Append ``message`` to the conversation log of its session."""
        self._messages.setdefault(message.session_id, []).append(message)

    def get_messages(self, session_id: str) -> list[SessionMessage]:
        """Return all messages for ``session_id`` in insertion order.

        Returns an empty list for unknown sessions so callers can treat
        the result uniformly.
        """
        return list(self._messages.get(session_id, []))

    # -- research state -----------------------------------------------------

    def save_research_state(self, state: ResearchState) -> None:
        """Insert or replace a :class:`ResearchState` by its id."""
        self._research_states[state.research_state_id] = state

    def get_research_state(self, state_id: str) -> ResearchState | None:
        """Return the research state for ``state_id`` or ``None``."""
        return self._research_states.get(state_id)

    def get_research_state_by_session(
        self, session_id: str
    ) -> ResearchState | None:
        """Return the research state attached to ``session_id``.

        If several states happen to share a session id, the most
        recently stored one is returned.
        """
        for state in reversed(self._research_states.values()):
            if state.session_id == session_id:
                return state
        return None


__all__ = ["DraftSessionStore"]
