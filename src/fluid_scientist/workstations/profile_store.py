"""SQLite-backed persistence for :class:`WorkstationProfile` records.

The store mirrors the persistence pattern used by
:mod:`fluid_scientist.draft_session.v5_storage`: each profile is serialised
to JSON and stored in a ``TEXT`` column, short-lived SQLite connections are
opened per call with ``check_same_thread=False`` so FastAPI's thread pool
can share the database, WAL journaling is enabled, and a module-level
:class:`threading.Lock` serialises writes.

Security constraints
--------------------
* No private keys, key paths, passwords, passphrases, or raw credentials are
  ever persisted.  :class:`WorkstationProfile` does not model such fields,
  and :meth:`_sanitize` defensively strips any of those keys from the JSON
  payload before it is written, so a future model change cannot accidentally
  leak a secret.
* Deleting a profile only removes the local database row.  It never touches
  ``~/.ssh/config``, ``known_hosts``, or any remote file.
* Profiles survive service restarts because they live in SQLite on disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from fluid_scientist.workstations.models import WorkstationProfile

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_DIR = os.path.join(os.path.expanduser("~"), ".fluid_scientist")
_DEFAULT_DB_NAME = "workstations.db"

_CREATE_PROFILES = """
CREATE TABLE IF NOT EXISTS workstation_profiles (
    profile_id  TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    is_default  INTEGER DEFAULT 0,
    updated_at  TEXT NOT NULL
)
"""

# Credential fields that must never be persisted.  ``WorkstationProfile``
# does not declare any of these, but we strip them defensively so a future
# model change cannot leak secrets through the JSON payload.
_SENSITIVE_KEYS = (
    "private_key",
    "private_key_path",
    "password",
    "passphrase",
    "raw_credential",
)


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _strip_sensitive(obj: object) -> None:
    """Recursively remove sensitive keys from *obj* (in place)."""
    if isinstance(obj, dict):
        for key in list(obj.keys()):
            if key in _SENSITIVE_KEYS:
                del obj[key]
            else:
                _strip_sensitive(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_sensitive(item)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class WorkstationProfileStore:
    """SQLite-backed repository for workstation profiles.

    Args:
        db_path: Path to the SQLite database file.  Defaults to
            ``~/.fluid_scientist/workstations.db``.  The parent directory
            is created automatically and the schema is auto-migrated on
            first use.
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
        """Open a new short-lived connection with WAL journaling."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        """Create the profile table if it does not exist."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(_CREATE_PROFILES)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _sanitize(payload: str) -> str:
        """Strip any sensitive keys from a JSON payload before storage."""
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            return payload
        _strip_sensitive(data)
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    def _load(data: str, is_default: int) -> WorkstationProfile:
        """Deserialise a profile row and sync its ``is_default`` flag."""
        profile = WorkstationProfile.model_validate_json(data)
        try:
            profile.is_default = bool(is_default)
        except Exception:
            # Field is frozen or absent; the column remains authoritative.
            pass
        return profile

    # -- public API ---------------------------------------------------------

    def save(self, profile: WorkstationProfile) -> None:
        """Insert or replace a profile.

        If ``profile.is_default`` is true, any other profile's default flag
        is cleared.  When updating an existing profile that is not marked
        default, its previous default flag is preserved.
        """
        profile_id = profile.profile_id
        payload = self._sanitize(profile.model_dump_json())
        is_default = 1 if getattr(profile, "is_default", False) else 0
        now = _utcnow_iso()
        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT is_default FROM workstation_profiles WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
                if existing is None:
                    default_col = is_default
                else:
                    default_col = is_default if is_default else existing[0]
                conn.execute(
                    "INSERT OR REPLACE INTO workstation_profiles "
                    "(profile_id, data, is_default, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (profile_id, payload, default_col, now),
                )
                if default_col:
                    conn.execute(
                        "UPDATE workstation_profiles SET is_default = 0 "
                        "WHERE profile_id <> ?",
                        (profile_id,),
                    )
                conn.commit()
            finally:
                conn.close()

    def get(self, profile_id: str) -> WorkstationProfile | None:
        """Load a profile by ID, or ``None`` if it does not exist."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data, is_default FROM workstation_profiles "
                    "WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return self._load(row[0], row[1])

    def list_all(self) -> list[WorkstationProfile]:
        """Return all profiles ordered by last update time."""
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT data, is_default FROM workstation_profiles "
                    "ORDER BY updated_at"
                ).fetchall()
            finally:
                conn.close()
        return [self._load(data, default) for data, default in rows]

    def list_profiles(self) -> list[WorkstationProfile]:
        """Compatibility alias used by discovery/bootstrap services."""
        return self.list_all()

    def get_default(self) -> WorkstationProfile | None:
        """Return the profile currently marked default, or ``None``."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT data, is_default FROM workstation_profiles "
                    "WHERE is_default = 1 LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
        if row is None:
            return None
        return self._load(row[0], row[1])

    def set_default(self, profile_id: str) -> None:
        """Mark *profile_id* as the default profile.

        Raises:
            KeyError: if no profile with that ID exists.
        """
        with self._lock:
            conn = self._connect()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM workstation_profiles WHERE profile_id = ?",
                    (profile_id,),
                ).fetchone()
                if exists is None:
                    raise KeyError(profile_id)
                conn.execute("UPDATE workstation_profiles SET is_default = 0")
                conn.execute(
                    "UPDATE workstation_profiles SET is_default = 1 "
                    "WHERE profile_id = ?",
                    (profile_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def delete(self, profile_id: str) -> None:
        """Delete a local profile record.

        This only removes the database row; it never modifies
        ``~/.ssh/config``, ``known_hosts``, or any remote file.
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM workstation_profiles WHERE profile_id = ?",
                    (profile_id,),
                )
                conn.commit()
            finally:
                conn.close()

    def clear_all(self) -> None:
        """Delete every profile row (used for test isolation)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM workstation_profiles")
                conn.commit()
            finally:
                conn.close()


__all__ = ["WorkstationProfileStore"]
