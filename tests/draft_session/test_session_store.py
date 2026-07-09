"""Tests for draft_session.session_store."""

from __future__ import annotations

from datetime import datetime

import pytest

from fluid_scientist.compat import UTC
from fluid_scientist.draft_session.models import (
    DraftSession,
    ResearchState,
    SessionMessage,
)
from fluid_scientist.draft_session.session_store import DraftSessionStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> DraftSessionStore:
    return DraftSessionStore()


@pytest.fixture()
def session() -> DraftSession:
    return DraftSession(session_id="sess-1")


def _make_message(
    session_id: str,
    message_id: str,
    *,
    content: str = "hello",
    message_type: str = "research_request",
) -> SessionMessage:
    return SessionMessage(
        message_id=message_id,
        session_id=session_id,
        role="user",
        message_type=message_type,  # type: ignore[arg-type]
        content=content,
    )


def _make_state(state_id: str, session_id: str) -> ResearchState:
    now = datetime.now(UTC)
    return ResearchState(
        research_state_id=state_id,
        session_id=session_id,
        original_user_request="研究后台阶流动",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------


class TestSessionCRUD:
    def test_create_and_get(self, store: DraftSessionStore, session: DraftSession) -> None:
        store.create_session(session)
        assert store.get_session("sess-1") is session

    def test_get_missing_returns_none(self, store: DraftSessionStore) -> None:
        assert store.get_session("missing") is None

    def test_create_duplicate_raises(self, store: DraftSessionStore, session: DraftSession) -> None:
        store.create_session(session)
        with pytest.raises(ValueError, match="already exists"):
            store.create_session(session)

    def test_update_replaces_session(self, store: DraftSessionStore, session: DraftSession) -> None:
        store.create_session(session)
        updated = session.model_copy(update={"user_id": "user-1"})
        store.update_session(updated)
        fetched = store.get_session("sess-1")
        assert fetched is not None
        assert fetched.user_id == "user-1"

    def test_update_missing_raises(self, store: DraftSessionStore, session: DraftSession) -> None:
        with pytest.raises(KeyError):
            store.update_session(session)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestMessages:
    def test_add_and_get_messages(
        self, store: DraftSessionStore, session: DraftSession
    ) -> None:
        store.create_session(session)
        m1 = _make_message("sess-1", "m-1")
        m2 = _make_message("sess-1", "m-2", content="second")
        store.add_message(m1)
        store.add_message(m2)

        messages = store.get_messages("sess-1")
        assert len(messages) == 2
        assert messages[0] is m1
        assert messages[1] is m2

    def test_get_messages_preserves_order(
        self, store: DraftSessionStore, session: DraftSession
    ) -> None:
        store.create_session(session)
        for i in range(5):
            store.add_message(_make_message("sess-1", f"m-{i}"))
        ids = [m.message_id for m in store.get_messages("sess-1")]
        assert ids == [f"m-{i}" for i in range(5)]

    def test_get_messages_unknown_session_returns_empty(
        self, store: DraftSessionStore
    ) -> None:
        assert store.get_messages("unknown") == []

    def test_get_messages_returns_copy(
        self, store: DraftSessionStore, session: DraftSession
    ) -> None:
        """Mutating the returned list must not affect the store."""
        store.create_session(session)
        store.add_message(_make_message("sess-1", "m-1"))
        first = store.get_messages("sess-1")
        first.clear()
        assert len(store.get_messages("sess-1")) == 1


# ---------------------------------------------------------------------------
# Research state
# ---------------------------------------------------------------------------


class TestResearchState:
    def test_save_and_get_by_id(
        self, store: DraftSessionStore, session: DraftSession
    ) -> None:
        store.create_session(session)
        state = _make_state("rs-1", "sess-1")
        store.save_research_state(state)
        assert store.get_research_state("rs-1") is state

    def test_get_missing_state_returns_none(self, store: DraftSessionStore) -> None:
        assert store.get_research_state("missing") is None

    def test_save_overwrites_existing(
        self, store: DraftSessionStore, session: DraftSession
    ) -> None:
        store.create_session(session)
        state = _make_state("rs-1", "sess-1")
        store.save_research_state(state)
        updated = state.model_copy(update={"version": 5})
        store.save_research_state(updated)
        fetched = store.get_research_state("rs-1")
        assert fetched is not None
        assert fetched.version == 5

    def test_get_research_state_by_session(
        self, store: DraftSessionStore, session: DraftSession
    ) -> None:
        store.create_session(session)
        state = _make_state("rs-1", "sess-1")
        store.save_research_state(state)
        assert store.get_research_state_by_session("sess-1") is state

    def test_get_research_state_by_session_returns_latest(
        self, store: DraftSessionStore, session: DraftSession
    ) -> None:
        store.create_session(session)
        first = _make_state("rs-1", "sess-1")
        second = _make_state("rs-2", "sess-1")
        store.save_research_state(first)
        store.save_research_state(second)
        result = store.get_research_state_by_session("sess-1")
        # The most recently stored state wins.
        assert result is second

    def test_get_research_state_by_session_missing(
        self, store: DraftSessionStore
    ) -> None:
        assert store.get_research_state_by_session("unknown") is None

    def test_research_state_isolated_across_sessions(
        self, store: DraftSessionStore
    ) -> None:
        s1 = DraftSession(session_id="sess-1")
        s2 = DraftSession(session_id="sess-2")
        store.create_session(s1)
        store.create_session(s2)
        store.save_research_state(_make_state("rs-1", "sess-1"))
        store.save_research_state(_make_state("rs-2", "sess-2"))
        assert store.get_research_state_by_session("sess-1").research_state_id == "rs-1"  # type: ignore[union-attr]
        assert store.get_research_state_by_session("sess-2").research_state_id == "rs-2"  # type: ignore[union-attr]
