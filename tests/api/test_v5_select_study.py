"""Tests for the ``/api/v5/batches/{batch_id}/select-study`` endpoint.

Verifies that:

1. Selecting a study with ``readiness_level == "not_compilable_yet"`` returns
   HTTP 422 with a structured error body that includes blocking issues.
2. Selecting a study with ``readiness_level == "draftable"`` runs the
   compile-ready pipeline instead of producing a legacy draft.
3. Selecting a study with ``readiness_level == "needs_clarification"`` also
   runs the compile-ready pipeline; it may fail clearly if OpenFOAM is missing.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

# Import study_decomposition.models BEFORE v5_router to work around a
# pre-existing circular import between capabilities.models, case_plan.generator,
# and study_decomposition.capability_checker.
# ruff: noqa: I001
from fluid_scientist.study_decomposition.models import (
    AmbiguityItem,
    BatchStudyPlan,
    StudyIntent,
)
from fluid_scientist.api import v5_router
from fluid_scientist.draft.models import DraftStatus, ExperimentDraft
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
    tmp_dir = tempfile.mkdtemp(prefix="v5_select_study_")
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


def _make_study(
    readiness_level: str = "draftable",
    blocking_ambiguities: int = 0,
    blocking_caps: list[dict] | None = None,
    study_id: str | None = None,
) -> StudyIntent:
    sid = study_id or f"study_{uuid.uuid4().hex[:8]}"
    ambiguities = [
        AmbiguityItem(
            field=f"blocking_field_{i}",
            issue=f"blocking issue {i}",
            severity="blocking_for_case_generation",
            reason=f"blocking reason {i}",
        )
        for i in range(blocking_ambiguities)
    ]
    return StudyIntent(
        study_id=sid,
        title=f"Study {sid}",
        raw_text="test study text",
        study_type="cfd_simulation",
        research_objective="test objective",
        geometry={"type": "cavity"},
        physical_models={"dimension": "2d", "temporal": "steady", "turbulent": False},
        ambiguity_report=ambiguities,
        readiness_level=readiness_level,  # type: ignore[arg-type]
        likely_missing_capabilities=list(blocking_caps or []),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSelectStudyReadinessCheck:
    def test_not_compilable_yet_returns_422(
        self, isolated_router: None
    ) -> None:
        """Selecting a not_compilable_yet study must return 422 with details."""
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study(
            readiness_level="not_compilable_yet",
            blocking_ambiguities=2,
            blocking_caps=[
                {
                    "capability_id": "missing_cap",
                    "capability_type": "solver",
                    "reason": "missing solver",
                    "severity": "blocking",
                }
            ],
        )
        batch = BatchStudyPlan(
            batch_id=f"batch_{uuid.uuid4().hex[:8]}",
            input_type="single_study",
            studies=[study],
        )
        v5_router._batch_store[batch.batch_id] = batch

        response = client.post(
            f"/api/v5/batches/{batch.batch_id}/select-study",
            json={"session_id": session_id, "study_id": study.study_id},
        )
        assert response.status_code == 422, response.text
        body = response.json()
        detail = body["detail"]
        assert detail["message"] == "Study is not compilable yet"
        assert detail["study_id"] == study.study_id
        assert detail["recommendation"] == (
            "Resolve blocking issues or select a different study"
        )
        # Must include both blocking ambiguities and blocking capabilities
        assert isinstance(detail["blocking_issues"], list)
        # 2 ambiguities + 1 capability = 3 blocking issues
        assert len(detail["blocking_issues"]) == 3
        # Verify an ambiguity is present
        amb_fields = {b.get("field") for b in detail["blocking_issues"] if "field" in b}
        assert "blocking_field_0" in amb_fields
        # Verify the blocking capability is present
        cap_ids = {
            b.get("capability_id") for b in detail["blocking_issues"]
            if "capability_id" in b
        }
        assert "missing_cap" in cap_ids

    def test_draftable_study_proceeds_normally(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A draftable study must run the compile-ready pipeline, not legacy draft generation."""
        monkeypatch.setenv("FLUID_SCIENTIST_LLM_MODE", "mock")
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study(readiness_level="draftable")
        batch = BatchStudyPlan(
            batch_id=f"batch_{uuid.uuid4().hex[:8]}",
            input_type="single_study",
            studies=[study],
        )
        v5_router._batch_store[batch.batch_id] = batch

        response = client.post(
            f"/api/v5/batches/{batch.batch_id}/select-study",
            json={"session_id": session_id, "study_id": study.study_id},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["selected_study_id"] == study.study_id
        if body["type"] == "pipeline_failed":
            assert body["failure"]
            assert "draft" not in body
            assert not v5_router._draft_store
        else:
            assert body["type"] == "draft_ready"
            assert body["compile_ready_view"]["status"] == "compile_ready"
            assert "draft" in body

    def test_needs_clarification_study_proceeds(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A needs_clarification study should still run the compile-ready pipeline."""
        monkeypatch.setenv("FLUID_SCIENTIST_LLM_MODE", "mock")
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study(readiness_level="needs_clarification")
        batch = BatchStudyPlan(
            batch_id=f"batch_{uuid.uuid4().hex[:8]}",
            input_type="single_study",
            studies=[study],
        )
        v5_router._batch_store[batch.batch_id] = batch

        response = client.post(
            f"/api/v5/batches/{batch.batch_id}/select-study",
            json={"session_id": session_id, "study_id": study.study_id},
        )
        assert response.status_code == 200, response.text
        assert response.json()["type"] in {"pipeline_failed", "draft_ready"}

    def test_not_compilable_yet_with_only_capability_blocks(
        self, isolated_router: None
    ) -> None:
        """Even without ambiguity blocks, missing blocking capabilities must block."""
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study(
            readiness_level="not_compilable_yet",
            blocking_ambiguities=0,
            blocking_caps=[
                {
                    "capability_id": "thermal_solver",
                    "capability_type": "solver",
                    "reason": "thermal not supported",
                    "severity": "blocking",
                },
                {
                    "capability_id": "warning_cap",
                    "capability_type": "geometry_generator",
                    "reason": "warning only",
                    "severity": "warning",
                },
            ],
        )
        batch = BatchStudyPlan(
            batch_id=f"batch_{uuid.uuid4().hex[:8]}",
            input_type="single_study",
            studies=[study],
        )
        v5_router._batch_store[batch.batch_id] = batch

        response = client.post(
            f"/api/v5/batches/{batch.batch_id}/select-study",
            json={"session_id": session_id, "study_id": study.study_id},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        # Only the blocking cap should appear; warning caps excluded
        assert len(detail["blocking_issues"]) == 1
        assert detail["blocking_issues"][0]["capability_id"] == "thermal_solver"

    def test_study_not_found_returns_404(
        self, isolated_router: None
    ) -> None:
        """Requesting a nonexistent study_id must return 404."""
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study(readiness_level="draftable")
        batch = BatchStudyPlan(
            batch_id=f"batch_{uuid.uuid4().hex[:8]}",
            input_type="single_study",
            studies=[study],
        )
        v5_router._batch_store[batch.batch_id] = batch

        response = client.post(
            f"/api/v5/batches/{batch.batch_id}/select-study",
            json={"session_id": session_id, "study_id": "nonexistent"},
        )
        assert response.status_code == 404

    def test_non_compile_ready_draft_cannot_be_confirmed(
        self, isolated_router: None
    ) -> None:
        client = _build_client()
        session_id = _create_session(client)
        draft = ExperimentDraft(
            draft_id="legacy_draft",
            session_id=session_id,
            status=DraftStatus.READY,
            objective="legacy empty draft",
            validation_result={"compile_ready": False, "openfoam_available": False},
        )
        v5_router._draft_store[draft.draft_id] = draft

        response = client.post(
            f"/api/v5/drafts/{draft.draft_id}/confirm",
            json={"session_id": session_id, "draft_id": draft.draft_id},
        )

        assert response.status_code == 409
        assert "not compile-ready" in response.json()["detail"]

    def test_non_compile_ready_draft_cannot_generate_case_plan(
        self, isolated_router: None
    ) -> None:
        client = _build_client()
        session_id = _create_session(client)
        draft = ExperimentDraft(
            draft_id="legacy_draft",
            session_id=session_id,
            status=DraftStatus.CONFIRMED,
            objective="legacy empty draft",
            validation_result={"compile_ready": False, "openfoam_available": False},
        )
        v5_router._draft_store[draft.draft_id] = draft

        response = client.post(
            "/api/v5/case-plans/generate",
            json={"session_id": session_id, "draft_id": draft.draft_id},
        )

        assert response.status_code == 409
        assert "not compile-ready" in response.json()["detail"]
