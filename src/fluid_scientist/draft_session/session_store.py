"""In-memory persistence for draft sessions.

The :class:`DraftSessionStore` is a deliberately simple, process-local
store that keeps :class:`DraftSession`, :class:`SessionMessage` and
:class:`ResearchState` instances in dictionaries.  It exists so the
draft workflow can be exercised end-to-end in tests and single-process
deployments without pulling in a database; production deployments are
expected to layer a persistent repository on top of the same interface.

When a :class:`~fluid_scientist.draft_session.persistence.JsonSessionPersistence`
is supplied, every write is mirrored to disk and the first access to a
session lazily loads it from disk, providing transparent JSON-file-backed
persistence while retaining full backward compatibility (default is
purely in-memory).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fluid_scientist.draft_session.models import (
    DraftSession,
    ResearchState,
    SessionMessage,
)

if TYPE_CHECKING:
    from fluid_scientist.draft_session.persistence import JsonSessionPersistence


class DraftSessionStore:
    """In-memory store for draft sessions, messages and research state.

    The store is intentionally not thread-safe; callers that need
    cross-thread access should wrap it or provide an alternative
    repository implementation.  All lookups are O(1) dictionary reads.

    Args:
        persistence: Optional :class:`JsonSessionPersistence` instance.
            When provided, every mutation is automatically persisted to
            disk and sessions are lazily loaded from disk on first access.
            Defaults to ``None`` (purely in-memory).
    """

    def __init__(
        self,
        persistence: JsonSessionPersistence | None = None,
    ) -> None:
        self._sessions: dict[str, DraftSession] = {}
        self._messages: dict[str, list[SessionMessage]] = {}
        self._research_states: dict[str, ResearchState] = {}
        self._persistence = persistence
        # Track which sessions have been loaded from persistence so we
        # don't re-load on every access.
        self._loaded_from_persistence: set[str] = set()

    # -- internal lazy-loading helper ---------------------------------------

    def _ensure_loaded(self, session_id: str) -> None:
        """If *session_id* exists on disk but not in memory, load it."""
        if self._persistence is None:
            return
        if session_id in self._loaded_from_persistence:
            return
        if session_id in self._sessions:
            self._loaded_from_persistence.add(session_id)
            return
        session = self._persistence.load_session(session_id)
        if session is not None:
            self._sessions[session_id] = session
            messages = self._persistence.load_messages(session_id)
            if messages:
                self._messages[session_id] = messages
        self._loaded_from_persistence.add(session_id)

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
        if self._persistence is not None:
            self._persistence.save_session(session)
            # Persist any (empty) message list alongside the session
            self._persistence.save_messages(session.session_id, [])

    def get_session(self, session_id: str) -> DraftSession | None:
        """Return the session for ``session_id`` or ``None`` if absent."""
        self._ensure_loaded(session_id)
        return self._sessions.get(session_id)

    def update_session(self, session: DraftSession) -> None:
        """Replace the stored session with ``session``.

        The ``updated_at`` timestamp is refreshed so callers do not have
        to remember to bump it manually.

        Raises:
            KeyError: If no session with the given id exists.
        """
        self._ensure_loaded(session.session_id)
        if session.session_id not in self._sessions:
            raise KeyError(session.session_id)
        self._sessions[session.session_id] = session
        if self._persistence is not None:
            self._persistence.save_session(session)

    # -- messages -----------------------------------------------------------

    def add_message(self, message: SessionMessage) -> None:
        """Append ``message`` to the conversation log of its session."""
        self._ensure_loaded(message.session_id)
        self._messages.setdefault(message.session_id, []).append(message)
        if self._persistence is not None:
            self._persistence.save_messages(
                message.session_id, self._messages[message.session_id]
            )

    def get_messages(self, session_id: str) -> list[SessionMessage]:
        """Return all messages for ``session_id`` in insertion order.

        Returns an empty list for unknown sessions so callers can treat
        the result uniformly.
        """
        self._ensure_loaded(session_id)
        return list(self._messages.get(session_id, []))

    # -- research state -----------------------------------------------------

    def save_research_state(self, state: ResearchState) -> None:
        """Insert or replace a :class:`ResearchState` by its id."""
        self._research_states[state.research_state_id] = state
        if self._persistence is not None:
            self._persistence.save_research_state(state)

    def get_research_state(self, state_id: str) -> ResearchState | None:
        """Return the research state for ``state_id`` or ``None``."""
        if state_id in self._research_states:
            return self._research_states[state_id]
        if self._persistence is not None:
            state = self._persistence.load_research_state(state_id)
            if state is not None:
                self._research_states[state_id] = state
                # Also ensure the session-side cache knows about it
                if state.session_id not in self._sessions:
                    self._ensure_loaded(state.session_id)
            return state
        return None

    def get_research_state_by_session(
        self, session_id: str
    ) -> ResearchState | None:
        """Return the research state attached to ``session_id``.

        If several states happen to share a session id, the most
        recently stored one is returned.
        """
        self._ensure_loaded(session_id)
        for state in reversed(self._research_states.values()):
            if state.session_id == session_id:
                return state
        return None


__all__ = ["DraftSessionStore"]
