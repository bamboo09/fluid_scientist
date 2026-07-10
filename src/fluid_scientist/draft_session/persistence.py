"""JSON-file-backed persistence for draft sessions.

Provides :class:`JsonSessionPersistence`, which serialises
:class:`DraftSession`, :class:`SessionMessage` and :class:`ResearchState`
to individual JSON files under a configurable storage directory.  Each
session occupies a single ``{session_id}.json`` file containing three
top-level keys: ``session``, ``messages`` and ``research_state``.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from typing import Any

from fluid_scientist.draft_session.models import (
    DraftSession,
    ResearchState,
    SessionMessage,
)


class JsonSessionPersistence:
    """JSON-file-backed persistence for DraftSession, messages, and ResearchState.

    Each session is stored as a single JSON file named ``{session_id}.json``
    inside :attr:`_storage_dir`.  The file contains a dictionary with three
    keys:

    * ``"session"`` – the serialised :class:`DraftSession` (or ``null``)
    * ``"messages"`` – a list of serialised :class:`SessionMessage` objects
    * ``"research_state"`` – the serialised :class:`ResearchState` (or ``null``)

    Datetime fields are stored as ISO-8601 strings and reconstructed on load.
    """

    def __init__(self, storage_dir: str | None = None) -> None:
        self._storage_dir = storage_dir or os.path.join(
            tempfile.gettempdir(), "fluid_scientist_sessions"
        )
        os.makedirs(self._storage_dir, exist_ok=True)

    # -- internal helpers ---------------------------------------------------

    def _session_path(self, session_id: str) -> str:
        """Return the absolute path to the JSON file for *session_id*."""
        return os.path.join(self._storage_dir, f"{session_id}.json")

    def _read_file(self, session_id: str) -> dict[str, Any]:
        """Read and parse the JSON file for *session_id*, or return empty structure."""
        path = self._session_path(session_id)
        if not os.path.exists(path):
            return {"session": None, "messages": [], "research_state": None}
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def _write_file(self, session_id: str, data: dict[str, Any]) -> None:
        """Write *data* to the JSON file for *session_id* atomically."""
        path = self._session_path(session_id)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False, default=_json_default)
        os.replace(tmp_path, path)

    # -- public API ---------------------------------------------------------

    def save_session(self, session: DraftSession) -> None:
        """Persist *session* (and any already-stored messages/state) to disk."""
        data = self._read_file(session.session_id)
        data["session"] = session.model_dump(mode="json")
        self._write_file(session.session_id, data)

    def load_session(self, session_id: str) -> DraftSession | None:
        """Load a :class:`DraftSession` from disk, or ``None`` if absent."""
        data = self._read_file(session_id)
        raw = data.get("session")
        if raw is None:
            return None
        return DraftSession(**raw)

    def save_messages(
        self, session_id: str, messages: list[SessionMessage]
    ) -> None:
        """Persist the full message list for *session_id*."""
        data = self._read_file(session_id)
        data["messages"] = [m.model_dump(mode="json") for m in messages]
        self._write_file(session_id, data)

    def load_messages(self, session_id: str) -> list[SessionMessage]:
        """Load all messages for *session_id* (empty list if none)."""
        data = self._read_file(session_id)
        raw_list = data.get("messages", [])
        return [SessionMessage(**raw) for raw in raw_list]

    def save_research_state(self, state: ResearchState) -> None:
        """Persist *state* (keyed by its ``research_state_id``)."""
        data = self._read_file(state.session_id)
        data["research_state"] = state.model_dump(mode="json")
        # Also track by research_state_id for direct lookups – store a mapping
        # in a special index file?  For simplicity we store the state under
        # its session file and additionally write a symlink-style pointer file.
        self._write_file(state.session_id, data)
        # Write a small pointer file keyed by state_id -> session_id
        self._write_state_pointer(state.research_state_id, state.session_id)

    def load_research_state(self, state_id: str) -> ResearchState | None:
        """Load a :class:`ResearchState` by its ``research_state_id``."""
        session_id = self._read_state_pointer(state_id)
        if session_id is None:
            return None
        data = self._read_file(session_id)
        raw = data.get("research_state")
        if raw is None:
            return None
        return ResearchState(**raw)

    def list_sessions(self) -> list[str]:
        """Return a sorted list of all persisted session IDs."""
        sessions: list[str] = []
        for fname in os.listdir(self._storage_dir):
            if fname.endswith(".json") and not fname.endswith(".stateptr.json"):
                sessions.append(fname[:-5])  # strip ".json"
        sessions.sort()
        return sessions

    # -- state pointer helpers ----------------------------------------------
    #
    # Because ResearchState is keyed by research_state_id but lives inside
    # the session file, we maintain tiny pointer files that map
    # state_id -> session_id so that load_research_state can find the right
    # file without scanning every session.

    def _pointer_path(self, state_id: str) -> str:
        return os.path.join(self._storage_dir, f".{state_id}.stateptr.json")

    def _write_state_pointer(self, state_id: str, session_id: str) -> None:
        path = self._pointer_path(state_id)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"session_id": session_id}, fh)

    def _read_state_pointer(self, state_id: str) -> str | None:
        path = self._pointer_path(state_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj.get("session_id")


def _json_default(obj: Any) -> str:
    """JSON serialiser fallback for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")


__all__ = ["JsonSessionPersistence"]
