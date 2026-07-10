"""Tests that LLM-suggested studies are merged into the batch during ``send_message``.

When the LLM returns additional studies that the deterministic splitter did
not identify, they should be appended to ``batch.studies`` (deduplicated by
title, case-insensitive).
"""

from __future__ import annotations

import shutil
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

# Import study_decomposition.models BEFORE v5_router to work around a
# pre-existing circular import.
# ruff: noqa: I001
from fluid_scientist.study_decomposition.models import (
    BatchStudyPlan,
    StudyIntent,
)
from fluid_scientist.api import v5_router
from fluid_scientist.draft_session.models import LLMCallRecord
from fluid_scientist.draft_session.persistence import JsonSessionPersistence
from fluid_scientist.draft_session.session_store import DraftSessionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_client() -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(v5_router.router)
    return TestClient(app)


@pytest.fixture
def isolated_router() -> None:
    """Isolate the module-level v5 router state for one test."""
    tmp_dir = tempfile.mkdtemp(prefix="v5_llm_decomp_")
    persistence = JsonSessionPersistence(storage_dir=tmp_dir)
    store = DraftSessionStore(persistence=persistence)

    original_persistence = v5_router._session_persistence
    original_store = v5_router._session_store
    original_drafts = dict(v5_router._draft_store)
    original_batches = dict(v5_router._batch_store)
    original_proposals = dict(v5_router._proposal_store)

    v5_router._session_persistence = persistence
    v5_router._session_store = store
    v5_router._draft_store.clear()
    v5_router._batch_store.clear()
    v5_router._proposal_store.clear()
    try:
        yield
    finally:
        v5_router._session_persistence = original_persistence
        v5_router._session_store = original_store
        v5_router._draft_store.clear()
        v5_router._draft_store.update(original_drafts)
        v5_router._batch_store.clear()
        v5_router._batch_store.update(original_batches)
        v5_router._proposal_store.clear()
        v5_router._proposal_store.update(original_proposals)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _create_session(client: TestClient) -> str:
    response = client.post("/api/v5/sessions", json={"user_id": "test"})
    assert response.status_code == 201, response.text
    return response.json()["session"]["session_id"]


def _make_batch(studies: list[StudyIntent], batch_id: str | None = None) -> BatchStudyPlan:
    bid = batch_id or f"batch_{uuid.uuid4().hex[:8]}"
    batch = BatchStudyPlan(
        batch_id=bid,
        input_type="batch_study" if len(studies) > 1 else "single_study",
        studies=list(studies),
        batch_summary=f"{len(studies)} studies",
    )
    # Store it so the endpoint can find it
    v5_router._batch_store[bid] = batch
    return batch


def _make_study(
    title: str,
    study_id: str | None = None,
) -> StudyIntent:
    sid = study_id or f"study_{uuid.uuid4().hex[:8]}"
    return StudyIntent(
        study_id=sid,
        title=title,
        raw_text=title,
        study_type="cfd_simulation",
        research_objective=title,
        geometry={"type": "cavity"},
        physical_models={"dimension": "2d", "temporal": "steady", "turbulent": False},
    )


def _make_llm_record(purpose: str = "study_decomposition") -> LLMCallRecord:
    return LLMCallRecord(
        call_id=f"llm_{uuid.uuid4().hex[:12]}",
        session_id="sess-test",
        purpose=purpose,  # type: ignore[arg-type]
        provider="mock",
        model_name="mock-v1",
        prompt_name="study_decomposer",
    )


class _FakeLLMCall:
    """Helper that returns a predetermined output when ``call()`` is invoked."""

    def __init__(self, output: dict | None = None, should_raise: bool = False) -> None:
        self._output = output or {"status": "fallback", "message": "no studies"}
        self._should_raise = should_raise
        self.calls: list[tuple] = []

    def __call__(self, **kwargs) -> tuple[dict, LLMCallRecord]:
        self.calls.append(tuple(kwargs.items()))
        if self._should_raise:
            raise RuntimeError("LLM simulated failure")
        return self._output, _make_llm_record()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLLMDecompositionMerge:
    def test_llm_additional_studies_are_merged(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When LLM returns additional studies not found by deterministic
        splitting, they must be appended to the batch with ``llm_`` prefix IDs."""
        client = _build_client()
        session_id = _create_session(client)

        # Deterministic decomposition returns one study
        det_study = _make_study(title="Cavity flow at Re=100", study_id="det_1")
        det_batch = _make_batch([det_study])
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: det_batch)

        # LLM returns two studies: one duplicate (cavity flow, case-insensitive)
        # and one brand-new study (heat transfer).
        llm_output = {
            "status": "decomposed",
            "studies": [
                {
                    "title": "cavity flow at re=100",  # duplicate (case-insensitive)
                    "study_type": "cfd_simulation",
                    "research_objective": "duplicate",
                },
                {
                    "title": "Heat transfer analysis of the cavity walls",
                    "study_type": "thermal_cfd",
                    "research_objective": "Analyze heat transfer on cavity walls",
                    "physical_models": {"thermal": True},
                    "geometry": {"type": "cavity"},
                    "confidence": 0.4,
                },
            ],
        }
        fake_llm = _FakeLLMCall(output=llm_output)
        monkeypatch.setattr(v5_router._llm_client, "call", fake_llm)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. Cavity flow at Re=100"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        actions = body["actions"]
        batch_action = [a for a in actions if a["action"] == "batch_review"]
        assert len(batch_action) == 1
        batch_data = batch_action[0]["batch"]
        studies = batch_data["studies"]

        # Original deterministic study must be present
        titles = [s["title"] for s in studies]
        assert "Cavity flow at Re=100" in titles
        # LLM-added study must be appended
        assert any("Heat transfer" in t for t in titles)
        # The duplicate must NOT create a second cavity entry
        cavity_titles = [t for t in titles if "cavity" in t.lower() and "Heat" not in t]
        assert len(cavity_titles) == 1
        # The new study must have an llm_-prefixed study_id
        llm_studies = [s for s in studies if s["study_id"].startswith("llm_")]
        assert len(llm_studies) == 1
        assert llm_studies[0]["study_type"] == "thermal_cfd"

    def test_llm_failure_does_not_break_endpoint(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the LLM call raises, the deterministic batch must still be returned."""
        client = _build_client()
        session_id = _create_session(client)

        det_study = _make_study(title="Pipe flow", study_id="det_pipe")
        det_batch = _make_batch([det_study])
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: det_batch)

        fake_llm = _FakeLLMCall(should_raise=True)
        monkeypatch.setattr(v5_router._llm_client, "call", fake_llm)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. Pipe flow"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        batch_action = [a for a in body["actions"] if a["action"] == "batch_review"]
        assert len(batch_action) == 1
        studies = batch_action[0]["batch"]["studies"]
        assert len(studies) == 1
        assert studies[0]["title"] == "Pipe flow"

    def test_llm_returns_no_studies_key(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If LLM output has no 'studies' list, batch stays unchanged."""
        client = _build_client()
        session_id = _create_session(client)

        det_study = _make_study(title="Backward-facing step", study_id="det_step")
        det_batch = _make_batch([det_study])
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: det_batch)

        fake_llm = _FakeLLMCall(output={"status": "fallback", "message": "no data"})
        monkeypatch.setattr(v5_router._llm_client, "call", fake_llm)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. Backward-facing step"},
        )
        assert response.status_code == 200
        studies = [
            a for a in response.json()["actions"] if a["action"] == "batch_review"
        ][0]["batch"]["studies"]
        assert len(studies) == 1
        assert studies[0]["title"] == "Backward-facing step"

    def test_llm_duplicate_titles_are_deduplicated_case_insensitive(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Titles differing only in case/whitespace must not produce duplicates."""
        client = _build_client()
        session_id = _create_session(client)

        det_study = _make_study(title="Cylinder Flow Re=200", study_id="det_cyl")
        det_batch = _make_batch([det_study])
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: det_batch)

        llm_output = {
            "status": "decomposed",
            "studies": [
                {"title": "  cylinder flow re=200  "},  # whitespace + case dup
                {"title": "CYLINDER FLOW RE=200"},       # all-caps dup
            ],
        }
        fake_llm = _FakeLLMCall(output=llm_output)
        monkeypatch.setattr(v5_router._llm_client, "call", fake_llm)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. Cylinder Flow Re=200"},
        )
        assert response.status_code == 200
        studies = [
            a for a in response.json()["actions"] if a["action"] == "batch_review"
        ][0]["batch"]["studies"]
        # Only the deterministic study; no llm_ duplicates added
        assert len(studies) == 1
        assert not any(s["study_id"].startswith("llm_") for s in studies)

    def test_llm_empty_title_is_skipped(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LLM entries with empty/whitespace titles must be skipped."""
        client = _build_client()
        session_id = _create_session(client)

        det_study = _make_study(title="Cavity", study_id="det_cav")
        det_batch = _make_batch([det_study])
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: det_batch)

        llm_output = {
            "status": "decomposed",
            "studies": [
                {"title": "   "},
                {"title": ""},
                {"title": "Valid new study"},
            ],
        }
        fake_llm = _FakeLLMCall(output=llm_output)
        monkeypatch.setattr(v5_router._llm_client, "call", fake_llm)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. Cavity"},
        )
        assert response.status_code == 200
        studies = [
            a for a in response.json()["actions"] if a["action"] == "batch_review"
        ][0]["batch"]["studies"]
        titles = [s["title"] for s in studies]
        assert "Cavity" in titles
        assert "Valid new study" in titles
        assert len(studies) == 2  # deterministic + 1 valid llm, empty ones skipped
