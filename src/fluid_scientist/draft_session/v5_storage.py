"""SQLite-backed persistent storage for all V5 workflow entities.

This module replaces the in-memory dictionaries that were used in
``v5_router.py`` for drafts, proposals, case plans, batches, compiled
cases, and code extensions.  It also provides SQLite-backed storage
for sessions and session messages, superseding the JSON-file approach.

Design principles
------------------
* **Single source of truth**: every V5 entity lives in one SQLite file.
* **JSON-in-TEXT**: Pydantic models are serialised to JSON and stored
  in ``TEXT`` columns, mirroring the pattern already used by the V2/V3
  tables in :mod:`fluid_scientist.db`.
* **Auto-migration**: tables are created automatically on first use;
  a ``schema_version`` table tracks the migration level.
* **Thread-safe**: each call opens a short-lived connection (SQLite
  handles serialised writes via the global lock).  ``check_same_thread``
  is set to ``False`` so FastAPI's thread-pool can share the DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from fluid_scientist.case_plan.models import CasePlan
from fluid_scientist.code_extension.spec import CodeExtensionSpec
from fluid_scientist.draft.models import ChangeProposal, ExperimentDraft
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
    SessionMessage,
)
from fluid_scientist.study_decomposition.models import BatchStudyPlan

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 1

_DEFAULT_DB_DIR = os.path.join(os.path.expanduser("~"), ".fluid_scientist")
_DEFAULT_DB_NAME = "v5_workflow.db"


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_CREATE_SCHEMA_VERSION = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS v5_sessions (
    session_id   TEXT PRIMARY KEY,
    session_json TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
)
"""

_CREATE_SESSION_MESSAGES = """
CREATE TABLE IF NOT EXISTS v5_session_messages (
    message_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    message_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES v5_sessions(session_id) ON DELETE CASCADE
)
"""

_CREATE_DRAFTS = """
CREATE TABLE IF NOT EXISTS v5_drafts (
    draft_id     TEXT PRIMARY KEY,
    session_id   TEXT,
    version      INTEGER NOT NULL,
    status       TEXT NOT NULL,
    draft_json   TEXT NOT NULL,
    created_at   TEXT NOT NULL
)
"""

_CREATE_PROPOSALS = """
CREATE TABLE IF NOT EXISTS v5_proposals (
    proposal_id        TEXT PRIMARY KEY,
    draft_id           TEXT NOT NULL,
    session_id         TEXT,
    status             TEXT NOT NULL,
    base_draft_version INTEGER NOT NULL,
    proposal_json      TEXT NOT NULL,
    created_at         TEXT NOT NULL
)
"""

_CREATE_CASE_PLANS = """
CREATE TABLE IF NOT EXISTS v5_case_plans (
    case_plan_id   TEXT PRIMARY KEY,
    draft_id       TEXT NOT NULL,
    session_id     TEXT,
    case_plan_json TEXT NOT NULL,
    created_at     TEXT NOT NULL
)
"""

_CREATE_BATCHES = """
CREATE TABLE IF NOT EXISTS v5_batches (
    batch_id    TEXT PRIMARY KEY,
    session_id  TEXT,
    batch_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""

_CREATE_CASES = """
CREATE TABLE IF NOT EXISTS v5_compiled_cases (
    case_plan_id   TEXT PRIMARY KEY,
    case_dir       TEXT NOT NULL,
    case_json      TEXT NOT NULL,
    created_at     TEXT NOT NULL
)
"""

_CREATE_EXTENSIONS = """
CREATE TABLE IF NOT EXISTS v5_code_extensions (
    extension_id     TEXT PRIMARY KEY,
    session_id       TEXT,
    draft_id         TEXT,
    status           TEXT NOT NULL,
    extension_json   TEXT NOT NULL,
    created_at       TEXT NOT NULL
)
"""

_CREATE_AUDIT_EVENTS = """
CREATE TABLE IF NOT EXISTS v5_audit_events (
    event_id     TEXT PRIMARY KEY,
    session_id   TEXT,
    event_type   TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at  TEXT NOT NULL
)
"""

_ALL_DDL = [
    _CREATE_SCHEMA_VERSION,
    _CREATE_SESSIONS,
    _CREATE_SESSION_MESSAGES,
    _CREATE_DRAFTS,
    _CREATE_PROPOSALS,
    _CREATE_CASE_PLANS,
    _CREATE_BATCHES,
    _CREATE_CASES,
    _CREATE_EXTENSIONS,
    _CREATE_AUDIT_EVENTS,
]


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class V5Repository:
    """SQLite-backed repository for all V5 workflow entities.

    All Pydantic models are stored as JSON in ``TEXT`` columns.  The
    repository auto-creates tables on first use and supports a simple
    migration mechanism via the ``schema_version`` table.

    Args:
        db_path: Path to the SQLite database file.  Defaults to
            ``~/.fluid_scientist/v5_workflow.db``.
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            os.makedirs(_DEFAULT_DB_DIR, exist_ok=True)
            db_path = os.path.join(_DEFAULT_DB_DIR, _DEFAULT_DB_NAME)
        self._db_path = db_path
        self._lock = threading.Lock()
        self._migrate()

    # -- internal helpers ---------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a new short-lived connection."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        """Create tables if they don't exist and record schema version."""
        with self._lock:
            conn = self._connect()
            try:
                for ddl in _ALL_DDL:
                    conn.execute(ddl)
                # Check current schema version
                row = conn.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()
                current = row[0] if row and row[0] is not None else 0
                if current < _SCHEMA_VERSION:
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_version (version, applied_at) VALUES (?, ?)",
                        (_SCHEMA_VERSION, _utcnow_iso()),
                    )
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _dump(model: Any) -> str:
        """Serialise a Pydantic model to JSON string."""
        return model.model_dump_json()

    # -- audit events -------------------------------------------------------

    def log_audit(
        self,
        event_id: str,
        session_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Write an audit event."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_audit_events "
                    "(event_id, session_id, event_type, payload_json, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        event_id,
                        session_id,
                        event_type,
                        json.dumps(payload, default=str, ensure_ascii=False),
                        _utcnow_iso(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    # -- sessions -----------------------------------------------------------

    def save_session(self, session: DraftSession) -> None:
        """Insert or replace a session."""
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_sessions "
                    "(session_id, session_json, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        session.session_id,
                        self._dump(session),
                        session.status.value if isinstance(session.status, DraftSessionStatus) else str(session.status),
                        session.created_at.isoformat() if hasattr(session.created_at, 'isoformat') else now,
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_session(self, session_id: str) -> DraftSession | None:
        """Load a session by ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT session_json FROM v5_sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return DraftSession.model_validate_json(row[0])

    def list_sessions(self) -> list[str]:
        """Return all session IDs, sorted by creation time."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT session_id FROM v5_sessions ORDER BY created_at"
                ).fetchall()
            finally:
                conn.close()
        return [r[0] for r in rows]

    # -- session messages ---------------------------------------------------

    def add_message(self, message: SessionMessage) -> None:
        """Append a message to a session's conversation log."""
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_session_messages "
                    "(message_id, session_id, message_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        message.message_id,
                        message.session_id,
                        self._dump(message),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_messages(self, session_id: str) -> list[SessionMessage]:
        """Return all messages for a session in insertion order."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT message_json FROM v5_session_messages "
                    "WHERE session_id = ? ORDER BY created_at",
                    (session_id,),
                ).fetchall()
            finally:
                conn.close()
        return [SessionMessage.model_validate_json(r[0]) for r in rows]

    # -- drafts -------------------------------------------------------------

    def save_draft(self, draft: ExperimentDraft) -> None:
        """Insert or replace a draft."""
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_drafts "
                    "(draft_id, session_id, version, status, draft_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        draft.draft_id,
                        draft.session_id,
                        draft.version,
                        draft.status.value if hasattr(draft.status, 'value') else str(draft.status),
                        self._dump(draft),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_draft(self, draft_id: str) -> ExperimentDraft | None:
        """Load a draft by ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT draft_json FROM v5_drafts WHERE draft_id = ?",
                    (draft_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return ExperimentDraft.model_validate_json(row[0])

    # -- proposals ----------------------------------------------------------

    def save_proposal(self, proposal: ChangeProposal) -> None:
        """Insert or replace a proposal."""
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_proposals "
                    "(proposal_id, draft_id, session_id, status, base_draft_version, "
                    " proposal_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        proposal.proposal_id,
                        proposal.draft_id,
                        getattr(proposal, 'session_id', None),
                        proposal.status,
                        proposal.base_draft_version,
                        self._dump(proposal),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_proposal(self, proposal_id: str) -> ChangeProposal | None:
        """Load a proposal by ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT proposal_json FROM v5_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return ChangeProposal.model_validate_json(row[0])

    # -- case plans ---------------------------------------------------------

    def save_case_plan(self, case_plan: CasePlan) -> None:
        """Insert or replace a case plan."""
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_case_plans "
                    "(case_plan_id, draft_id, session_id, case_plan_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        case_plan.case_plan_id,
                        getattr(case_plan, 'draft_id', ''),
                        getattr(case_plan, 'session_id', None),
                        self._dump(case_plan),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_case_plan(self, case_plan_id: str) -> CasePlan | None:
        """Load a case plan by ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT case_plan_json FROM v5_case_plans WHERE case_plan_id = ?",
                    (case_plan_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return CasePlan.model_validate_json(row[0])

    # -- batches ------------------------------------------------------------

    def save_batch(self, batch: BatchStudyPlan) -> None:
        """Insert or replace a batch study plan."""
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_batches "
                    "(batch_id, session_id, batch_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        batch.batch_id,
                        None,
                        self._dump(batch),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_batch(self, batch_id: str) -> BatchStudyPlan | None:
        """Load a batch by ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT batch_json FROM v5_batches WHERE batch_id = ?",
                    (batch_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return BatchStudyPlan.model_validate_json(row[0])

    # -- compiled cases -----------------------------------------------------

    def save_compiled_case(
        self,
        case_plan_id: str,
        case_dir: str,
        compiled: dict[str, Any],
    ) -> None:
        """Insert or replace a compiled case record."""
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_compiled_cases "
                    "(case_plan_id, case_dir, case_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        case_plan_id,
                        case_dir,
                        json.dumps(compiled, default=str, ensure_ascii=False),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_compiled_case(self, case_plan_id: str) -> dict[str, Any] | None:
        """Load a compiled case record by case_plan_id."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT case_dir, case_json FROM v5_compiled_cases WHERE case_plan_id = ?",
                    (case_plan_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return {
            "case_plan_id": case_plan_id,
            "case_dir": row[0],
            "compiled_structure": json.loads(row[1]),
        }

    # -- code extensions ----------------------------------------------------

    def save_extension(self, spec: CodeExtensionSpec) -> None:
        """Insert or replace a code extension spec."""
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO v5_code_extensions "
                    "(extension_id, session_id, draft_id, status, extension_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        spec.extension_id,
                        spec.session_id,
                        getattr(spec, 'draft_id', None),
                        spec.status,
                        self._dump(spec),
                        now,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def get_extension(self, extension_id: str) -> CodeExtensionSpec | None:
        """Load a code extension spec by ID."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT extension_json FROM v5_code_extensions WHERE extension_id = ?",
                    (extension_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return CodeExtensionSpec.model_validate_json(row[0])


__all__ = ["V5Repository"]
