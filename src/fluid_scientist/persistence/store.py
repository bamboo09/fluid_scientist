"""SQLite-backed persistence for sessions, specs, jobs, and LLM records.

Replaces the in-memory _spec_store dict with a persistent store that
survives service restarts.

Tables:
- specs: spec_id, session_id, spec_json, spec_version, draft_status, created_at, updated_at
- jobs: job_id, spec_id, status, result_json, created_at, updated_at
- llm_records: call_id, session_id, purpose, model, prompt_hash, input_summary, output_summary, latency_ms, success, created_at
- repair_records: job_id, attempt_number, phase, level, diagnosis_json, fixes_json, status, created_at
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default DB path — use LOCALAPPDATA for Windows compatibility
_DEFAULT_DB_DIR = os.environ.get("LOCALAPPDATA", os.environ.get("TEMP", "/tmp"))
_DEFAULT_DB_PATH = os.path.join(_DEFAULT_DB_DIR, "fluid_scientist", "v5_persistence.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS specs (
    spec_id TEXT PRIMARY KEY,
    session_id TEXT,
    spec_json TEXT NOT NULL,
    spec_version INTEGER DEFAULT 1,
    draft_status TEXT DEFAULT 'NEEDS_CLARIFICATION',
    user_input TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    spec_id TEXT,
    status TEXT DEFAULT 'PENDING',
    result_json TEXT,
    remote_case_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (spec_id) REFERENCES specs(spec_id)
);

CREATE TABLE IF NOT EXISTS llm_records (
    call_id TEXT PRIMARY KEY,
    session_id TEXT,
    purpose TEXT,
    model TEXT,
    prompt_name TEXT,
    prompt_version TEXT,
    input_summary TEXT,
    output_summary TEXT,
    latency_ms REAL,
    success INTEGER DEFAULT 0,
    fallback_used INTEGER DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repair_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT,
    attempt_number INTEGER,
    phase TEXT,
    level TEXT,
    diagnosis_json TEXT,
    fixes_json TEXT,
    status TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE INDEX IF NOT EXISTS idx_specs_session ON specs(session_id);
CREATE INDEX IF NOT EXISTS idx_jobs_spec ON jobs(spec_id);
CREATE INDEX IF NOT EXISTS idx_llm_session ON llm_records(session_id);
CREATE INDEX IF NOT EXISTS idx_repair_job ON repair_records(job_id);
"""


class SQLitePersistence:
    """SQLite-backed persistence layer for V5 data.

    Thread-safe: each method opens its own connection.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or _DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        try:
            conn = sqlite3.connect(self._db_path)
            conn.executescript(_SCHEMA)
            conn.commit()
            conn.close()
            logger.info("Persistence DB initialized at %s", self._db_path)
        except Exception as e:
            logger.error("Failed to initialize persistence DB: %s", e)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Specs
    # ------------------------------------------------------------------

    def save_spec(
        self,
        spec_id: str,
        spec: Any,
        session_id: str = "",
        user_input: str = "",
    ) -> None:
        """Save or update a spec."""
        spec_json = json.dumps(spec.model_dump() if hasattr(spec, "model_dump") else spec,
                               ensure_ascii=False, default=str)
        spec_version = getattr(spec, "spec_version", 1)
        draft_status = getattr(spec, "draft_status", "NEEDS_CLARIFICATION")
        if hasattr(draft_status, "value"):
            draft_status = draft_status.value

        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO specs
               (spec_id, session_id, spec_json, spec_version, draft_status, user_input, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM specs WHERE spec_id=?), ?), ?)""",
            (spec_id, session_id, spec_json, spec_version, draft_status, user_input,
             spec_id, now, now),
        )
        conn.commit()
        conn.close()

    def load_spec(self, spec_id: str) -> dict | None:
        """Load a spec by ID."""
        conn = self._get_conn()
        row = conn.execute("SELECT spec_json FROM specs WHERE spec_id=?", (spec_id,)).fetchone()
        conn.close()
        if row:
            return json.loads(row["spec_json"])
        return None

    def list_specs(self, session_id: str | None = None) -> list[dict]:
        """List specs, optionally filtered by session."""
        conn = self._get_conn()
        if session_id:
            rows = conn.execute(
                "SELECT spec_id, session_id, draft_status, created_at, updated_at FROM specs WHERE session_id=? ORDER BY updated_at DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT spec_id, session_id, draft_status, created_at, updated_at FROM specs ORDER BY updated_at DESC",
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_spec(self, spec_id: str) -> None:
        """Delete a spec."""
        conn = self._get_conn()
        conn.execute("DELETE FROM specs WHERE spec_id=?", (spec_id,))
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def save_job(
        self,
        job_id: str,
        spec_id: str,
        status: str = "PENDING",
        result: dict | None = None,
        remote_case_path: str = "",
    ) -> None:
        """Save or update a job."""
        result_json = json.dumps(result, ensure_ascii=False, default=str) if result else None
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO jobs
               (job_id, spec_id, status, result_json, remote_case_path, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM jobs WHERE job_id=?), ?), ?)""",
            (job_id, spec_id, status, result_json, remote_case_path, job_id, now, now),
        )
        conn.commit()
        conn.close()

    def load_job(self, job_id: str) -> dict | None:
        """Load a job by ID."""
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        conn.close()
        if row:
            result = dict(row)
            if result.get("result_json"):
                result["result"] = json.loads(result["result_json"])
            return result
        return None

    def list_jobs(self, spec_id: str | None = None) -> list[dict]:
        """List jobs, optionally filtered by spec."""
        conn = self._get_conn()
        if spec_id:
            rows = conn.execute(
                "SELECT job_id, spec_id, status, created_at, updated_at FROM jobs WHERE spec_id=? ORDER BY updated_at DESC",
                (spec_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT job_id, spec_id, status, created_at, updated_at FROM jobs ORDER BY updated_at DESC",
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # LLM Records
    # ------------------------------------------------------------------

    def save_llm_record(
        self,
        call_id: str,
        session_id: str,
        purpose: str,
        model: str,
        prompt_name: str = "",
        prompt_version: str = "",
        input_summary: str = "",
        output_summary: str = "",
        latency_ms: float = 0.0,
        success: bool = False,
        fallback_used: bool = False,
        error: str | None = None,
    ) -> None:
        """Save an LLM call record."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO llm_records
               (call_id, session_id, purpose, model, prompt_name, prompt_version,
                input_summary, output_summary, latency_ms, success, fallback_used, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (call_id, session_id, purpose, model, prompt_name, prompt_version,
             input_summary[:500], output_summary[:500], latency_ms,
             int(success), int(fallback_used), error, now),
        )
        conn.commit()
        conn.close()

    def list_llm_records(self, session_id: str | None = None) -> list[dict]:
        """List LLM records, optionally filtered by session."""
        conn = self._get_conn()
        if session_id:
            rows = conn.execute(
                "SELECT * FROM llm_records WHERE session_id=? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM llm_records ORDER BY created_at DESC LIMIT 100",
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Repair Records
    # ------------------------------------------------------------------

    def save_repair_record(
        self,
        job_id: str,
        attempt_number: int,
        phase: str,
        level: str,
        diagnosis: dict | None = None,
        fixes: list | None = None,
        status: str = "pending",
    ) -> None:
        """Save a repair attempt record."""
        now = datetime.now(timezone.utc).isoformat()
        diagnosis_json = json.dumps(diagnosis, ensure_ascii=False, default=str) if diagnosis else None
        fixes_json = json.dumps(fixes, ensure_ascii=False, default=str) if fixes else None
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO repair_records
               (job_id, attempt_number, phase, level, diagnosis_json, fixes_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, attempt_number, phase, level, diagnosis_json, fixes_json, status, now),
        )
        conn.commit()
        conn.close()

    def list_repair_records(self, job_id: str) -> list[dict]:
        """List repair records for a job."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM repair_records WHERE job_id=? ORDER BY attempt_number ASC",
            (job_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def recover_all_specs(self) -> dict[str, dict]:
        """Recover all specs from DB (for restart recovery).

        Returns a dict mapping spec_id to spec dict.
        """
        conn = self._get_conn()
        rows = conn.execute("SELECT spec_id, spec_json FROM specs").fetchall()
        conn.close()
        result: dict[str, dict] = {}
        for row in rows:
            try:
                result[row["spec_id"]] = json.loads(row["spec_json"])
            except Exception:
                logger.warning("Failed to deserialize spec %s", row["spec_id"])
        return result

    def recover_jobs_for_spec(self, spec_id: str) -> list[dict]:
        """Recover all jobs for a spec (for restart recovery)."""
        return self.list_jobs(spec_id)


# Singleton instance
_persistence: SQLitePersistence | None = None


def get_persistence() -> SQLitePersistence:
    """Get the singleton persistence instance."""
    global _persistence
    if _persistence is None:
        _persistence = SQLitePersistence()
    return _persistence
