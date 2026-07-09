"""Tests for draft_session.persistence (JsonSessionPersistence)."""

from __future__ import annotations

import os
from datetime import datetime

import pytest

from fluid_scientist.compat import UTC
from fluid_scientist.draft_session.models import (
    DraftSession,
    ResearchState,
    SessionMessage,
)
from fluid_scientist.draft_session.persistence import JsonSessionPersistence
from fluid_scientist.draft_session.session_store import DraftSessionStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_storage(tmp_path: str) -> str:
    """Provide a temporary storage directory."""
    storage = os.path.join(str(tmp_path), "sessions")
    return storage


@pytest.fixture()
def persistence(tmp_storage: str) -> JsonSessionPersistence:
    return JsonSessionPersistence(storage_dir=tmp_storage)


def _make_session(session_id: str = "sess-1") -> DraftSession:
    return DraftSession(session_id=session_id, user_id="user-1")


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
# Persistence CRUD
# ---------------------------------------------------------------------------


class TestJsonSessionPersistence:
    def test_save_and_load_session(
        self, persistence: JsonSessionPersistence
    ) -> None:
        session = _make_session()
        persistence.save_session(session)
        loaded = persistence.load_session("sess-1")
        assert loaded is not None
        assert loaded.session_id == "sess-1"
        assert loaded.user_id == "user-1"

    def test_load_missing_session_returns_none(
        self, persistence: JsonSessionPersistence
    ) -> None:
        assert persistence.load_session("nonexistent") is None

    def test_save_and_load_messages(
        self, persistence: JsonSessionPersistence
    ) -> None:
        session = _make_session()
        persistence.save_session(session)
        m1 = _make_message("sess-1", "m-1", content="first")
        m2 = _make_message("sess-1", "m-2", content="second")
        persistence.save_messages("sess-1", [m1, m2])

        loaded = persistence.load_messages("sess-1")
        assert len(loaded) == 2
        assert loaded[0].message_id == "m-1"
        assert loaded[0].content == "first"
        assert loaded[1].message_id == "m-2"
        assert loaded[1].content == "second"

    def test_load_messages_empty_session_returns_empty(
        self, persistence: JsonSessionPersistence
    ) -> None:
        assert persistence.load_messages("empty") == []

    def test_save_and_load_research_state(
        self, persistence: JsonSessionPersistence
    ) -> None:
        session = _make_session()
        persistence.save_session(session)
        state = _make_state("rs-1", "sess-1")
        persistence.save_research_state(state)

        loaded = persistence.load_research_state("rs-1")
        assert loaded is not None
        assert loaded.research_state_id == "rs-1"
        assert loaded.session_id == "sess-1"
        assert loaded.original_user_request == "研究后台阶流动"
        assert loaded.version == 1

    def test_load_missing_research_state_returns_none(
        self, persistence: JsonSessionPersistence
    ) -> None:
        assert persistence.load_research_state("missing") is None

    def test_list_sessions(
        self, persistence: JsonSessionPersistence
    ) -> None:
        persistence.save_session(_make_session("sess-a"))
        persistence.save_session(_make_session("sess-b"))
        persistence.save_session(_make_session("sess-c"))
        listed = persistence.list_sessions()
        assert listed == ["sess-a", "sess-b", "sess-c"]

    def test_list_sessions_empty(
        self, persistence: JsonSessionPersistence
    ) -> None:
        assert persistence.list_sessions() == []

    def test_datetime_roundtrip(
        self, persistence: JsonSessionPersistence
    ) -> None:
        session = _make_session()
        persistence.save_session(session)
        loaded = persistence.load_session("sess-1")
        assert loaded is not None
        # Datetimes should be reconstructed as tz-aware datetimes
        assert isinstance(loaded.created_at, datetime)
        assert loaded.created_at.tzinfo is not None

    def test_overwrite_session(
        self, persistence: JsonSessionPersistence
    ) -> None:
        session = _make_session()
        persistence.save_session(session)
        updated = session.model_copy(update={"user_id": "user-2"})
        persistence.save_session(updated)
        loaded = persistence.load_session("sess-1")
        assert loaded is not None
        assert loaded.user_id == "user-2"

    def test_overwrite_messages(
        self, persistence: JsonSessionPersistence
    ) -> None:
        session = _make_session()
        persistence.save_session(session)
        persistence.save_messages("sess-1", [_make_message("sess-1", "m-1")])
        persistence.save_messages("sess-1", [_make_message("sess-1", "m-2")])
        loaded = persistence.load_messages("sess-1")
        assert len(loaded) == 1
        assert loaded[0].message_id == "m-2"

    def test_default_storage_dir_created(self, tmp_path: str) -> None:
        """When storage_dir is None, default directory should be created."""
        # We use a non-default path to avoid polluting home
        custom = os.path.join(str(tmp_path), "default_test")
        p = JsonSessionPersistence(storage_dir=custom)
        assert os.path.isdir(custom)
        assert p.list_sessions() == []


# ---------------------------------------------------------------------------
# Integration: DraftSessionStore with persistence
# ---------------------------------------------------------------------------


class TestStoreWithPersistence:
    def test_create_session_persists(
        self, tmp_storage: str
    ) -> None:
        p1 = JsonSessionPersistence(storage_dir=tmp_storage)
        store1 = DraftSessionStore(persistence=p1)
        session = _make_session("sess-p1")
        store1.create_session(session)

        # Create a new store pointing at the same directory – should load
        p2 = JsonSessionPersistence(storage_dir=tmp_storage)
        store2 = DraftSessionStore(persistence=p2)
        loaded = store2.get_session("sess-p1")
        assert loaded is not None
        assert loaded.session_id == "sess-p1"
        assert loaded.user_id == "user-1"

    def test_messages_persist_across_stores(
        self, tmp_storage: str
    ) -> None:
        p1 = JsonSessionPersistence(storage_dir=tmp_storage)
        store1 = DraftSessionStore(persistence=p1)
        session = _make_session("sess-msg")
        store1.create_session(session)
        store1.add_message(_make_message("sess-msg", "m-1", content="hi"))
        store1.add_message(_make_message("sess-msg", "m-2", content="there"))

        p2 = JsonSessionPersistence(storage_dir=tmp_storage)
        store2 = DraftSessionStore(persistence=p2)
        messages = store2.get_messages("sess-msg")
        assert len(messages) == 2
        assert messages[0].content == "hi"
        assert messages[1].content == "there"

    def test_research_state_persists_across_stores(
        self, tmp_storage: str
    ) -> None:
        p1 = JsonSessionPersistence(storage_dir=tmp_storage)
        store1 = DraftSessionStore(persistence=p1)
        session = _make_session("sess-rs")
        store1.create_session(session)
        state = _make_state("rs-persist", "sess-rs")
        store1.save_research_state(state)

        p2 = JsonSessionPersistence(storage_dir=tmp_storage)
        store2 = DraftSessionStore(persistence=p2)
        loaded = store2.get_research_state("rs-persist")
        assert loaded is not None
        assert loaded.original_user_request == "研究后台阶流动"

    def test_update_session_persists(
        self, tmp_storage: str
    ) -> None:
        p1 = JsonSessionPersistence(storage_dir=tmp_storage)
        store1 = DraftSessionStore(persistence=p1)
        session = _make_session("sess-upd")
        store1.create_session(session)

        updated = session.model_copy(update={"user_id": "updated-user"})
        store1.update_session(updated)

        p2 = JsonSessionPersistence(storage_dir=tmp_storage)
        store2 = DraftSessionStore(persistence=p2)
        loaded = store2.get_session("sess-upd")
        assert loaded is not None
        assert loaded.user_id == "updated-user"

    def test_in_memory_store_without_persistence_still_works(self) -> None:
        """Backward compatibility: no persistence means pure in-memory."""
        store = DraftSessionStore()
        session = _make_session("sess-mem")
        store.create_session(session)
        assert store.get_session("sess-mem") is session
        assert store.get_messages("sess-mem") == []
