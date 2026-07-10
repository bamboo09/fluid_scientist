"""Tests that verify the v5 router is wired up with JsonSessionPersistence.

These tests exercise the module-level wiring of the v5 router to make sure
the production code path uses a real :class:`JsonSessionPersistence` instance
(so sessions actually survive process restarts, as required by plan §8.1).
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.api import v5_router
from fluid_scientist.draft_session.models import DraftSession
from fluid_scientist.draft_session.persistence import JsonSessionPersistence
from fluid_scientist.draft_session.session_store import DraftSessionStore

# ---------------------------------------------------------------------------
# Module-level wiring
# ---------------------------------------------------------------------------


def test_module_exposes_session_persistence() -> None:
    """The v5 router module must expose a ``_session_persistence`` instance."""
    assert hasattr(v5_router, "_session_persistence")
    assert isinstance(v5_router._session_persistence, JsonSessionPersistence)


def test_session_store_uses_persistence() -> None:
    """The v5 router's :class:`DraftSessionStore` must have persistence wired in.

    This is the simplest check: the private ``_persistence`` attribute on the
    store must exist and must not be ``None`` (which would mean sessions would
    only live in-memory).
    """
    store = v5_router._session_store
    assert isinstance(store, DraftSessionStore)
    assert getattr(store, "_persistence", None) is not None
    assert isinstance(store._persistence, JsonSessionPersistence)


def test_get_session_persistence_returns_module_instance() -> None:
    """The helper ``get_session_persistence()`` must return the shared instance."""
    assert v5_router.get_session_persistence() is v5_router._session_persistence


# ---------------------------------------------------------------------------
# Behaviour: creating a session via the wired store must save to disk
# ---------------------------------------------------------------------------


def test_create_session_via_wired_store_writes_to_disk(
    tmp_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Creating a session through the router's store must persist to disk.

    We use a temporary storage directory for isolation, then create a new
    :class:`DraftSessionStore` pointing at the same directory to confirm the
    session file is actually written.
    """
    # Point the module-level persistence at an isolated temp directory
    storage_dir = os.path.join(str(tmp_path), "router_sessions")
    isolated_persistence = JsonSessionPersistence(storage_dir=storage_dir)
    isolated_store = DraftSessionStore(persistence=isolated_persistence)

    # Swap the router's module-level store for the isolated one
    monkeypatch.setattr(v5_router, "_session_persistence", isolated_persistence)
    monkeypatch.setattr(v5_router, "_session_store", isolated_store)

    # Now exercise the create_session path that the /api/v5/sessions endpoint
    # uses (the handler just calls _session_store.create_session).

    session = DraftSession(
        session_id=f"session_{uuid.uuid4().hex[:12]}",
        user_id="wired-test-user",
    )
    isolated_store.create_session(session)

    # File should exist on disk
    expected_path = os.path.join(storage_dir, f"{session.session_id}.json")
    assert os.path.exists(expected_path), (
        f"Expected persistence file at {expected_path}, but it was not created"
    )

    # And a fresh store pointed at the same directory must be able to read it
    fresh_persistence = JsonSessionPersistence(storage_dir=storage_dir)
    fresh_store = DraftSessionStore(persistence=fresh_persistence)
    loaded = fresh_store.get_session(session.session_id)
    assert loaded is not None
    assert loaded.session_id == session.session_id
    assert loaded.user_id == "wired-test-user"


# ---------------------------------------------------------------------------
# HTTP layer: the /api/v5/sessions-list endpoint
# ---------------------------------------------------------------------------


def _build_client_for_router() -> TestClient:
    """Build a TestClient that exercises the v5 router in isolation."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(v5_router.router)
    return TestClient(app)


def test_sessions_list_endpoint_returns_empty_initially() -> None:
    """A fresh router with no sessions must return an empty ``session_ids`` list."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Re-point the module-level persistence at a clean tmp dir
        original = v5_router._session_persistence
        v5_router._session_persistence = JsonSessionPersistence(storage_dir=tmp_dir)
        try:
            client = _build_client_for_router()
            response = client.get("/api/v5/sessions-list")
            assert response.status_code == 200
            assert response.json() == {"session_ids": []}
        finally:
            v5_router._session_persistence = original


def test_sessions_list_endpoint_lists_created_sessions() -> None:
    """Sessions created via the store must show up in the /sessions-list endpoint."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Re-point both the persistence and the store at a clean tmp dir so we
        # can observe the full create -> list flow without interference from
        # the default user-home persistence directory.
        persistence = JsonSessionPersistence(storage_dir=tmp_dir)
        store = DraftSessionStore(persistence=persistence)

        original_persistence = v5_router._session_persistence
        original_store = v5_router._session_store
        v5_router._session_persistence = persistence
        v5_router._session_store = store
        try:
            from fluid_scientist.draft_session.models import DraftSession

            for i in range(3):
                store.create_session(
                    DraftSession(
                        session_id=f"session_list_{i}",
                        user_id=f"user-{i}",
                    )
                )

            client = _build_client_for_router()
            response = client.get("/api/v5/sessions-list")
            assert response.status_code == 200
            ids = response.json()["session_ids"]
            # The list_sessions helper only strips the ".json" suffix; the
            # full session_id we used in the test (which already lacks the
            # suffix) should appear verbatim.
            assert "session_list_0" in ids
            assert "session_list_1" in ids
            assert "session_list_2" in ids
        finally:
            v5_router._session_persistence = original_persistence
            v5_router._session_store = original_store


def test_create_session_via_endpoint_persists_to_disk() -> None:
    """Hitting POST /api/v5/sessions must actually write to the persistence layer."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        persistence = JsonSessionPersistence(storage_dir=tmp_dir)
        store = DraftSessionStore(persistence=persistence)

        original_persistence = v5_router._session_persistence
        original_store = v5_router._session_store
        v5_router._session_persistence = persistence
        v5_router._session_store = store
        try:
            client = _build_client_for_router()
            response = client.post("/api/v5/sessions", json={"user_id": "alice"})
            assert response.status_code == 201
            body = response.json()
            new_session_id = body["session"]["session_id"]
            assert new_session_id

            # File should exist on disk
            expected_path = os.path.join(tmp_dir, f"{new_session_id}.json")
            assert os.path.exists(expected_path)

            # Listing must now include the new session
            list_response = client.get("/api/v5/sessions-list")
            assert list_response.status_code == 200
            assert new_session_id in list_response.json()["session_ids"]
        finally:
            v5_router._session_persistence = original_persistence
            v5_router._session_store = original_store
