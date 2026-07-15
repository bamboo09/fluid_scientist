"""Tests for the SQLite persistence layer (P7 — Persistence).

This test suite verifies:
1. save_spec / load_spec roundtrip preserves spec data
2. list_specs returns all saved specs (optionally filtered by session)
3. delete_spec removes a spec from the store
4. save_job / load_job roundtrip preserves job data
5. save_llm_record / list_llm_records roundtrip
6. save_repair_record / list_repair_records roundtrip
7. recover_all_specs returns specs after a simulated restart (close & reopen DB)
8. All tests use a temporary DB file via pytest's tmp_path fixture

Plan reference: P7 — Persistence.
No running server is required — these are pure SQLite I/O tests.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from fluid_scientist.persistence.store import SQLitePersistence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class MockSpec:
    """A lightweight mock spec compatible with SQLitePersistence.save_spec().

    The real spec is a Pydantic model with ``model_dump()``, ``spec_version``
    and ``draft_status`` attributes.  This mock replicates that interface
    without importing the full model graph.
    """

    def __init__(
        self,
        spec_id: str = "spec_1",
        geometry: str = "cylinder",
        spec_version: int = 1,
        draft_status: str = "READY",
    ) -> None:
        self.spec_id = spec_id
        self._data: dict = {"spec_id": spec_id, "geometry": geometry}
        self.spec_version = spec_version
        self.draft_status = draft_status

    def model_dump(self) -> dict:
        return self._data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    """Return a fresh SQLitePersistence backed by a temp DB file."""
    db_path = str(tmp_path / "test_persistence.db")
    return SQLitePersistence(db_path=db_path)


# ---------------------------------------------------------------------------
# 1. save_spec / load_spec roundtrip
# ---------------------------------------------------------------------------

class TestSpecRoundtrip:
    """save_spec / load_spec preserves spec data."""

    def test_save_and_load_spec(self, store: SQLitePersistence) -> None:
        """A spec saved then loaded should preserve its data."""
        spec = MockSpec(spec_id="spec_roundtrip_1", geometry="cylinder")
        store.save_spec("spec_roundtrip_1", spec, session_id="sess_1")

        loaded = store.load_spec("spec_roundtrip_1")
        assert loaded is not None
        assert loaded["spec_id"] == "spec_roundtrip_1"
        assert loaded["geometry"] == "cylinder"

    def test_load_nonexistent_spec_returns_none(self, store: SQLitePersistence) -> None:
        """load_spec should return None for a spec_id that was never saved."""
        assert store.load_spec("does_not_exist") is None

    def test_save_spec_overwrites_on_same_id(self, store: SQLitePersistence) -> None:
        """Saving with the same spec_id should overwrite the previous data."""
        spec_v1 = MockSpec(spec_id="spec_overwrite", geometry="cylinder")
        store.save_spec("spec_overwrite", spec_v1)

        spec_v2 = MockSpec(spec_id="spec_overwrite", geometry="triangle")
        store.save_spec("spec_overwrite", spec_v2)

        loaded = store.load_spec("spec_overwrite")
        assert loaded is not None
        assert loaded["geometry"] == "triangle"

    def test_save_spec_preserves_draft_status(self, store: SQLitePersistence) -> None:
        """draft_status should be persisted in the specs table."""
        spec = MockSpec(spec_id="spec_status", draft_status="READY_TO_RUN")
        store.save_spec("spec_status", spec)

        specs = store.list_specs()
        matching = [s for s in specs if s["spec_id"] == "spec_status"]
        assert len(matching) == 1
        assert matching[0]["draft_status"] == "READY_TO_RUN"

    def test_save_spec_preserves_user_input(self, store: SQLitePersistence) -> None:
        """user_input should be persisted alongside the spec."""
        spec = MockSpec(spec_id="spec_input")
        store.save_spec("spec_input", spec, user_input="圆柱绕流 Re=100")

        # user_input is not returned by load_spec (only spec_json),
        # but it should be stored in the DB.
        specs = store.list_specs()
        # list_specs doesn't return user_input either, so verify via direct query
        conn = store._get_conn()
        row = conn.execute(
            "SELECT user_input FROM specs WHERE spec_id=?", ("spec_input",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["user_input"] == "圆柱绕流 Re=100"


# ---------------------------------------------------------------------------
# 2. list_specs returns all specs
# ---------------------------------------------------------------------------

class TestListSpecs:
    """list_specs returns all saved specs, optionally filtered by session."""

    def test_list_specs_returns_all(self, store: SQLitePersistence) -> None:
        """list_specs() with no filter should return every saved spec."""
        store.save_spec("list_1", MockSpec(spec_id="list_1"), session_id="sess_a")
        store.save_spec("list_2", MockSpec(spec_id="list_2"), session_id="sess_b")
        store.save_spec("list_3", MockSpec(spec_id="list_3"), session_id="sess_a")

        all_specs = store.list_specs()
        spec_ids = {s["spec_id"] for s in all_specs}
        assert spec_ids == {"list_1", "list_2", "list_3"}

    def test_list_specs_filtered_by_session(self, store: SQLitePersistence) -> None:
        """list_specs(session_id=...) should return only specs for that session."""
        store.save_spec("s1", MockSpec(spec_id="s1"), session_id="sess_a")
        store.save_spec("s2", MockSpec(spec_id="s2"), session_id="sess_b")
        store.save_spec("s3", MockSpec(spec_id="s3"), session_id="sess_a")

        sess_a_specs = store.list_specs(session_id="sess_a")
        spec_ids = {s["spec_id"] for s in sess_a_specs}
        assert spec_ids == {"s1", "s3"}

    def test_list_specs_empty_when_no_specs(self, store: SQLitePersistence) -> None:
        """list_specs() on an empty DB should return an empty list."""
        assert store.list_specs() == []

    def test_list_specs_returns_metadata_fields(self, store: SQLitePersistence) -> None:
        """list_specs entries should include spec_id, session_id, draft_status."""
        store.save_spec(
            "meta_spec", MockSpec(spec_id="meta_spec", draft_status="READY"),
            session_id="meta_sess",
        )
        specs = store.list_specs()
        matching = [s for s in specs if s["spec_id"] == "meta_spec"]
        assert len(matching) == 1
        entry = matching[0]
        assert "spec_id" in entry
        assert "session_id" in entry
        assert "draft_status" in entry
        assert "created_at" in entry
        assert "updated_at" in entry
        assert entry["session_id"] == "meta_sess"


# ---------------------------------------------------------------------------
# 3. delete_spec removes a spec
# ---------------------------------------------------------------------------

class TestDeleteSpec:
    """delete_spec removes a spec from the store."""

    def test_delete_spec_removes_it(self, store: SQLitePersistence) -> None:
        """After delete_spec, load_spec should return None."""
        store.save_spec("del_1", MockSpec(spec_id="del_1"))
        assert store.load_spec("del_1") is not None

        store.delete_spec("del_1")
        assert store.load_spec("del_1") is None

    def test_delete_spec_not_in_list(self, store: SQLitePersistence) -> None:
        """After delete_spec, the spec should not appear in list_specs."""
        store.save_spec("del_2", MockSpec(spec_id="del_2"))
        store.save_spec("keep_1", MockSpec(spec_id="keep_1"))

        store.delete_spec("del_2")

        specs = store.list_specs()
        spec_ids = {s["spec_id"] for s in specs}
        assert "del_2" not in spec_ids
        assert "keep_1" in spec_ids

    def test_delete_nonexistent_spec_no_error(self, store: SQLitePersistence) -> None:
        """Deleting a non-existent spec should not raise an error."""
        store.delete_spec("never_existed")  # should not raise

    def test_delete_all_specs(self, store: SQLitePersistence) -> None:
        """Deleting all specs should leave an empty list."""
        store.save_spec("del_a", MockSpec(spec_id="del_a"))
        store.save_spec("del_b", MockSpec(spec_id="del_b"))

        store.delete_spec("del_a")
        store.delete_spec("del_b")

        assert store.list_specs() == []


# ---------------------------------------------------------------------------
# 4. save_job / load_job roundtrip
# ---------------------------------------------------------------------------

class TestJobRoundtrip:
    """save_job / load_job preserves job data."""

    def test_save_and_load_job(self, store: SQLitePersistence) -> None:
        """A job saved then loaded should preserve its data."""
        store.save_spec("job_spec_1", MockSpec(spec_id="job_spec_1"))
        result_data = {"cd": 1.5, "cl": 0.3, "status": "completed"}
        store.save_job("job_1", "job_spec_1", status="RUNNING", result=result_data)

        loaded = store.load_job("job_1")
        assert loaded is not None
        assert loaded["job_id"] == "job_1"
        assert loaded["spec_id"] == "job_spec_1"
        assert loaded["status"] == "RUNNING"
        assert loaded["result"] == result_data

    def test_load_nonexistent_job_returns_none(self, store: SQLitePersistence) -> None:
        """load_job should return None for a job_id that was never saved."""
        assert store.load_job("no_such_job") is None

    def test_save_job_overwrites_on_same_id(self, store: SQLitePersistence) -> None:
        """Saving with the same job_id should overwrite the previous data."""
        store.save_spec("job_spec_2", MockSpec(spec_id="job_spec_2"))
        store.save_job("job_2", "job_spec_2", status="PENDING")
        store.save_job("job_2", "job_spec_2", status="COMPLETED", result={"ok": True})

        loaded = store.load_job("job_2")
        assert loaded is not None
        assert loaded["status"] == "COMPLETED"
        assert loaded["result"] == {"ok": True}

    def test_save_job_without_result(self, store: SQLitePersistence) -> None:
        """A job saved without a result should load with result=None."""
        store.save_spec("job_spec_3", MockSpec(spec_id="job_spec_3"))
        store.save_job("job_3", "job_spec_3", status="PENDING")

        loaded = store.load_job("job_3")
        assert loaded is not None
        assert loaded["status"] == "PENDING"
        assert loaded.get("result") is None

    def test_list_jobs_by_spec(self, store: SQLitePersistence) -> None:
        """list_jobs(spec_id=...) should return only jobs for that spec."""
        store.save_spec("job_spec_4", MockSpec(spec_id="job_spec_4"))
        store.save_spec("job_spec_5", MockSpec(spec_id="job_spec_5"))
        store.save_job("job_a", "job_spec_4")
        store.save_job("job_b", "job_spec_4")
        store.save_job("job_c", "job_spec_5")

        jobs = store.list_jobs(spec_id="job_spec_4")
        job_ids = {j["job_id"] for j in jobs}
        assert job_ids == {"job_a", "job_b"}

    def test_save_job_with_remote_case_path(self, store: SQLitePersistence) -> None:
        """remote_case_path should be persisted and loadable."""
        store.save_spec("job_spec_6", MockSpec(spec_id="job_spec_6"))
        store.save_job(
            "job_6", "job_spec_6", status="RUNNING",
            remote_case_path="/tmp/cases/case_6",
        )

        loaded = store.load_job("job_6")
        assert loaded is not None
        assert loaded["remote_case_path"] == "/tmp/cases/case_6"


# ---------------------------------------------------------------------------
# 5. save_llm_record / list_llm_records
# ---------------------------------------------------------------------------

class TestLLMRecords:
    """save_llm_record / list_llm_records roundtrip."""

    def test_save_and_list_llm_record(self, store: SQLitePersistence) -> None:
        """An LLM record saved then listed should preserve its data."""
        store.save_llm_record(
            call_id="llm_1",
            session_id="sess_llm_1",
            purpose="intent_parsing",
            model="gpt-4",
            prompt_name="intent_v2",
            prompt_version="1.0",
            input_summary="user input text",
            output_summary="parsed spec",
            latency_ms=1234.5,
            success=True,
        )

        records = store.list_llm_records(session_id="sess_llm_1")
        assert len(records) == 1
        r = records[0]
        assert r["call_id"] == "llm_1"
        assert r["session_id"] == "sess_llm_1"
        assert r["purpose"] == "intent_parsing"
        assert r["model"] == "gpt-4"
        assert r["prompt_name"] == "intent_v2"
        assert r["latency_ms"] == 1234.5
        assert r["success"] == 1  # stored as int in SQLite

    def test_list_llm_records_all_sessions(self, store: SQLitePersistence) -> None:
        """list_llm_records() without session_id should return all records."""
        store.save_llm_record("llm_a", "sess_1", "purpose_a", "model_a")
        store.save_llm_record("llm_b", "sess_2", "purpose_b", "model_b")

        all_records = store.list_llm_records()
        assert len(all_records) == 2

    def test_list_llm_records_filtered_by_session(self, store: SQLitePersistence) -> None:
        """list_llm_records(session_id=...) should filter by session."""
        store.save_llm_record("llm_x", "sess_filter", "p1", "m1")
        store.save_llm_record("llm_y", "sess_other", "p2", "m2")
        store.save_llm_record("llm_z", "sess_filter", "p3", "m3")

        filtered = store.list_llm_records(session_id="sess_filter")
        call_ids = {r["call_id"] for r in filtered}
        assert call_ids == {"llm_x", "llm_z"}

    def test_save_llm_record_with_error(self, store: SQLitePersistence) -> None:
        """An LLM record with an error should preserve the error text."""
        store.save_llm_record(
            call_id="llm_err",
            session_id="sess_err",
            purpose="intent_parsing",
            model="gpt-4",
            success=False,
            fallback_used=True,
            error="Rate limit exceeded",
        )

        records = store.list_llm_records(session_id="sess_err")
        assert len(records) == 1
        r = records[0]
        assert r["success"] == 0
        assert r["fallback_used"] == 1
        assert r["error"] == "Rate limit exceeded"

    def test_llm_record_overwrites_on_same_call_id(self, store: SQLitePersistence) -> None:
        """Saving with the same call_id should overwrite the previous record."""
        store.save_llm_record("llm_overwrite", "sess", "p1", "model_a", success=False)
        store.save_llm_record("llm_overwrite", "sess", "p1", "model_b", success=True)

        records = store.list_llm_records(session_id="sess")
        assert len(records) == 1
        assert records[0]["model"] == "model_b"
        assert records[0]["success"] == 1


# ---------------------------------------------------------------------------
# 6. save_repair_record / list_repair_records
# ---------------------------------------------------------------------------

class TestRepairRecords:
    """save_repair_record / list_repair_records roundtrip."""

    def test_save_and_list_repair_record(self, store: SQLitePersistence) -> None:
        """A repair record saved then listed should preserve its data."""
        store.save_spec("repair_spec_1", MockSpec(spec_id="repair_spec_1"))
        store.save_job("repair_job_1", "repair_spec_1", status="FAILED")

        diagnosis = {"error": "mesh too coarse", "location": "cylinder"}
        fixes = ["refine mesh around cylinder", "increase resolution"]
        store.save_repair_record(
            job_id="repair_job_1",
            attempt_number=1,
            phase="compile",
            level="error",
            diagnosis=diagnosis,
            fixes=fixes,
            status="applied",
        )

        records = store.list_repair_records("repair_job_1")
        assert len(records) == 1
        r = records[0]
        assert r["job_id"] == "repair_job_1"
        assert r["attempt_number"] == 1
        assert r["phase"] == "compile"
        assert r["level"] == "error"
        assert r["status"] == "applied"

        # diagnosis_json and fixes_json are stored as JSON strings
        import json as _json
        assert _json.loads(r["diagnosis_json"]) == diagnosis
        assert _json.loads(r["fixes_json"]) == fixes

    def test_multiple_repair_records_ordered_by_attempt(self, store: SQLitePersistence) -> None:
        """Multiple repair records should be ordered by attempt_number ASC."""
        store.save_spec("repair_spec_2", MockSpec(spec_id="repair_spec_2"))
        store.save_job("repair_job_2", "repair_spec_2", status="FAILED")

        store.save_repair_record("repair_job_2", 3, "compile", "error", status="failed")
        store.save_repair_record("repair_job_2", 1, "compile", "error", status="applied")
        store.save_repair_record("repair_job_2", 2, "run", "warning", status="applied")

        records = store.list_repair_records("repair_job_2")
        assert len(records) == 3
        attempts = [r["attempt_number"] for r in records]
        assert attempts == [1, 2, 3]

    def test_repair_record_without_diagnosis_and_fixes(self, store: SQLitePersistence) -> None:
        """A repair record without diagnosis/fixes should save with NULL values."""
        store.save_spec("repair_spec_3", MockSpec(spec_id="repair_spec_3"))
        store.save_job("repair_job_3", "repair_spec_3")

        store.save_repair_record(
            job_id="repair_job_3",
            attempt_number=1,
            phase="run",
            level="warning",
            status="pending",
        )

        records = store.list_repair_records("repair_job_3")
        assert len(records) == 1
        assert records[0]["diagnosis_json"] is None
        assert records[0]["fixes_json"] is None

    def test_list_repair_records_empty_for_unknown_job(self, store: SQLitePersistence) -> None:
        """list_repair_records for an unknown job_id should return an empty list."""
        assert store.list_repair_records("never_existed") == []


# ---------------------------------------------------------------------------
# 7. recover_all_specs after restart (close & reopen DB)
# ---------------------------------------------------------------------------

class TestRecovery:
    """recover_all_specs returns specs after a simulated restart."""

    def test_recover_all_specs_after_reopen(self, tmp_path) -> None:
        """Specs saved before 'restart' should be recoverable from a new store."""
        db_path = str(tmp_path / "restart_test.db")

        # Phase 1: save specs with the first store instance
        store1 = SQLitePersistence(db_path=db_path)
        spec_a = MockSpec(spec_id="restart_a", geometry="cylinder")
        spec_b = MockSpec(spec_id="restart_b", geometry="triangle")
        store1.save_spec("restart_a", spec_a, session_id="sess_restart")
        store1.save_spec("restart_b", spec_b, session_id="sess_restart")

        # Verify they were saved
        assert store1.load_spec("restart_a") is not None
        assert store1.load_spec("restart_b") is not None

        # Simulate restart: destroy old store, create a new one with the same DB path
        del store1
        store2 = SQLitePersistence(db_path=db_path)

        # Phase 2: recover all specs
        recovered = store2.recover_all_specs()
        assert "restart_a" in recovered
        assert "restart_b" in recovered
        assert recovered["restart_a"]["geometry"] == "cylinder"
        assert recovered["restart_b"]["geometry"] == "triangle"

    def test_recover_all_specs_empty_for_fresh_db(self, tmp_path) -> None:
        """recover_all_specs on a fresh DB should return an empty dict."""
        db_path = str(tmp_path / "fresh_test.db")
        store = SQLitePersistence(db_path=db_path)
        assert store.recover_all_specs() == {}

    def test_recover_jobs_for_spec_after_reopen(self, tmp_path) -> None:
        """Jobs should also be recoverable after a simulated restart."""
        db_path = str(tmp_path / "restart_jobs_test.db")

        store1 = SQLitePersistence(db_path=db_path)
        store1.save_spec("rj_spec", MockSpec(spec_id="rj_spec"))
        store1.save_job("rj_job_1", "rj_spec", status="COMPLETED", result={"cd": 1.2})
        store1.save_job("rj_job_2", "rj_spec", status="RUNNING")

        del store1
        store2 = SQLitePersistence(db_path=db_path)

        jobs = store2.recover_jobs_for_spec("rj_spec")
        assert len(jobs) == 2
        job_ids = {j["job_id"] for j in jobs}
        assert job_ids == {"rj_job_1", "rj_job_2"}

    def test_recover_all_specs_with_multiple_sessions(self, tmp_path) -> None:
        """recover_all_specs should return specs from all sessions."""
        db_path = str(tmp_path / "multi_session_test.db")

        store1 = SQLitePersistence(db_path=db_path)
        store1.save_spec("ms_1", MockSpec(spec_id="ms_1"), session_id="sess_1")
        store1.save_spec("ms_2", MockSpec(spec_id="ms_2"), session_id="sess_2")
        store1.save_spec("ms_3", MockSpec(spec_id="ms_3"), session_id="sess_1")

        del store1
        store2 = SQLitePersistence(db_path=db_path)

        recovered = store2.recover_all_specs()
        assert len(recovered) == 3
        assert set(recovered.keys()) == {"ms_1", "ms_2", "ms_3"}

    def test_recover_preserves_spec_data_integrity(self, tmp_path) -> None:
        """Recovered spec data should be identical to what was saved."""
        db_path = str(tmp_path / "integrity_test.db")

        original_data = {
            "spec_id": "integ_1",
            "geometry": "cylinder",
            "reynolds": 100,
            "velocity": 1.0,
            "nested": {"key": "value", "list": [1, 2, 3]},
        }

        store1 = SQLitePersistence(db_path=db_path)
        # Save a plain dict (no model_dump) — save_spec handles this via default=str
        store1.save_spec("integ_1", original_data, session_id="sess_integ")

        del store1
        store2 = SQLitePersistence(db_path=db_path)

        recovered = store2.recover_all_specs()
        assert "integ_1" in recovered
        recovered_spec = recovered["integ_1"]
        assert recovered_spec["spec_id"] == "integ_1"
        assert recovered_spec["geometry"] == "cylinder"
        assert recovered_spec["reynolds"] == 100


# ---------------------------------------------------------------------------
# 8. Edge cases with temporary DB
# ---------------------------------------------------------------------------

class TestTempDbEdgeCases:
    """Additional edge-case tests using temporary DB files."""

    def test_db_file_created_on_init(self, tmp_path) -> None:
        """The DB file should be created when SQLitePersistence is initialised."""
        db_path = str(tmp_path / "edge_create.db")
        assert not Path_exists(db_path)

        SQLitePersistence(db_path=db_path)
        assert Path_exists(db_path)

    def test_spec_and_job_coexist(self, store: SQLitePersistence) -> None:
        """Specs and jobs can coexist in the same DB without interference."""
        store.save_spec("coexist_spec", MockSpec(spec_id="coexist_spec"))
        store.save_job("coexist_job", "coexist_spec", status="RUNNING")
        store.save_llm_record("coexist_llm", "sess", "p", "m")
        store.save_repair_record("coexist_job", 1, "compile", "error")

        # All three data types should be independently retrievable
        assert store.load_spec("coexist_spec") is not None
        assert store.load_job("coexist_job") is not None
        assert len(store.list_llm_records()) >= 1
        assert len(store.list_repair_records("coexist_job")) >= 1

    def test_delete_spec_does_not_delete_jobs(self, store: SQLitePersistence) -> None:
        """Deleting a spec should not cascade-delete its jobs (no FK enforcement)."""
        store.save_spec("cascade_spec", MockSpec(spec_id="cascade_spec"))
        store.save_job("cascade_job", "cascade_spec", status="RUNNING")

        store.delete_spec("cascade_spec")

        # Spec is gone
        assert store.load_spec("cascade_spec") is None
        # Job still exists (SQLite FK not enforced by default)
        job = store.load_job("cascade_job")
        assert job is not None
        assert job["spec_id"] == "cascade_spec"


def Path_exists(path: str) -> bool:
    """Check if a file exists (avoids importing pathlib just for this)."""
    import os
    return os.path.exists(path)
