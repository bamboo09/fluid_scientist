"""Tests for the v5 router's ClarificationPlanner wiring and the
``request_draft_change`` session-state transition fix.

These tests exercise the module-level helpers of
:mod:`fluid_scientist.api.v5_router` to make sure that:

1. ``send_message`` for a ``batch_research_request`` produces a
   ``clarification_questions`` action alongside the existing
   ``batch_review`` action.
2. Ambiguous input produces questions with severity
   ``blocking_for_case_generation``.
3. No-ambiguity input produces an empty ``clarification_questions``
   list.
4. Each study contributes at most 3 questions.
5. ``request_draft_change`` on a confirmed/locked draft transitions the
   session back to ``DRAFT_READY``.
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.api import v5_router
from fluid_scientist.draft.models import DraftStatus, ExperimentDraft
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
)
from fluid_scientist.draft_session.persistence import JsonSessionPersistence
from fluid_scientist.draft_session.session_store import DraftSessionStore
from fluid_scientist.study_decomposition.models import (
    AmbiguityItem,
    StudyIntent,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _build_client() -> TestClient:
    """Build a TestClient that exercises the v5 router in isolation."""
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(v5_router.router)
    return TestClient(app)


@pytest.fixture
def isolated_router() -> None:
    """Isolate the module-level v5 router state for one test.

    Re-points the shared persistence + store at a temporary directory and
    saves the originals so they can be restored at teardown.  Also clears
    the in-memory caches that may carry state across tests.
    """
    tmp_dir = tempfile.mkdtemp(prefix="v5_clarification_")
    persistence = JsonSessionPersistence(storage_dir=tmp_dir)
    store = DraftSessionStore(persistence=persistence)

    original_persistence = v5_router._session_persistence
    original_store = v5_router._session_store
    original_drafts = dict(v5_router._draft_store)
    original_batches = dict(v5_router._batch_store)

    v5_router._session_persistence = persistence
    v5_router._session_store = store
    v5_router._draft_store.clear()
    v5_router._batch_store.clear()
    try:
        yield
    finally:
        v5_router._session_persistence = original_persistence
        v5_router._session_store = original_store
        v5_router._draft_store.clear()
        v5_router._draft_store.update(original_drafts)
        v5_router._batch_store.clear()
        v5_router._batch_store.update(original_batches)
        # Best-effort cleanup of the temp dir
        import shutil

        shutil.rmtree(tmp_dir, ignore_errors=True)


def _create_session(client: TestClient) -> str:
    """Create a fresh draft session and return its session_id."""
    response = client.post("/api/v5/sessions", json={"user_id": "test"})
    assert response.status_code == 201, response.text
    return response.json()["session"]["session_id"]


class _FakeBatch:
    """Lightweight stand-in for :class:`BatchStudyPlan` with ``model_dump``."""

    def __init__(self, studies: list[StudyIntent], batch_id: str = "batch_test") -> None:
        self.studies = studies
        self.batch_id = batch_id

    def model_dump(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "studies": [s.model_dump() for s in self.studies],
        }


def _make_study_with_ambiguities(
    study_id: str | None = None,
    ambiguities: list[AmbiguityItem] | None = None,
) -> StudyIntent:
    """Build a StudyIntent with the supplied ambiguity list attached."""
    sid = study_id or f"study_{uuid.uuid4().hex[:8]}"
    return StudyIntent(
        study_id=sid,
        title="Test study",
        raw_text="cylinder Re=100",
        study_type="cfd_simulation",
        research_objective="cylinder Re=100",
        ambiguity_report=list(ambiguities or []),
    )


def _amb(
    field: str,
    severity: str,
    issue: str = "test issue",
    reason: str = "test reason",
) -> AmbiguityItem:
    return AmbiguityItem(
        field=field,
        issue=issue,
        severity=severity,  # type: ignore[arg-type]
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Tests: clarification_questions in send_message response
# ---------------------------------------------------------------------------


class TestSendMessageWiresClarificationPlanner:
    """``send_message`` for a batch request must surface clarification
    questions derived from each study's ``ambiguity_report``."""

    def test_batch_research_request_with_ambiguities_includes_clarification_questions(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A batch request whose studies have ambiguities must produce a
        ``clarification_questions`` action in the response."""
        client = _build_client()
        session_id = _create_session(client)

        # Patch the router's decompose helper to return a single study with
        # two blocking ambiguities.  This avoids relying on the production
        # extractor/splitter to produce the right shape.
        study = _make_study_with_ambiguities(
            ambiguities=[
                _amb("characteristic_length", "blocking_for_case_generation"),
                _amb("oscillation_parameters", "blocking_for_case_generation"),
            ],
        )
        fake_batch = _FakeBatch([study], batch_id="batch_test")
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: fake_batch)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. 任意研究"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        actions = body["actions"]

        # Must include the original batch_review action
        assert any(a["action"] == "batch_review" for a in actions)

        # Must also include a clarification_questions action
        clarification = [a for a in actions if a["action"] == "clarification_questions"]
        assert len(clarification) == 1
        questions = clarification[0]["questions"]
        assert isinstance(questions, list)
        assert len(questions) >= 1
        # Each question must carry the originating study_id
        for q in questions:
            assert q["study_id"] == study.study_id
            assert "question_id" in q
            assert "field" in q
            assert "question" in q
            assert "severity" in q

    def test_ambiguous_input_produces_blocking_severity(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An input whose studies have blocking ambiguities must produce
        questions with severity ``blocking_for_case_generation``."""
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study_with_ambiguities(
            ambiguities=[
                _amb("characteristic_length", "blocking_for_case_generation"),
                _amb("oscillation_parameters", "blocking_for_case_generation"),
                _amb("density_stratification_formula", "blocking_for_case_generation"),
            ],
        )
        fake_batch = _FakeBatch([study], batch_id="batch_blocking")
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: fake_batch)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. 任意研究"},
        )
        body = response.json()
        clarification = [
            a for a in body["actions"] if a["action"] == "clarification_questions"
        ]
        assert len(clarification) == 1
        questions = clarification[0]["questions"]
        # At least one question must be blocking
        severities = {q["severity"] for q in questions}
        assert "blocking_for_case_generation" in severities

    def test_no_ambiguity_input_produces_empty_questions(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If no study has ambiguities, the clarification_questions
        action must not be appended (i.e. no spurious action)."""
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study_with_ambiguities(ambiguities=[])
        fake_batch = _FakeBatch([study], batch_id="batch_empty")
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: fake_batch)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. 任意研究"},
        )
        body = response.json()
        actions = body["actions"]
        clarification = [a for a in actions if a["action"] == "clarification_questions"]
        assert clarification == []
        # The batch_review action must still be present
        assert any(a["action"] == "batch_review" for a in actions)

    def test_only_non_blocking_assumptions_produces_empty_questions(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Studies that only have ``non_blocking_assumption`` items must
        not produce any clarification questions."""
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study_with_ambiguities(
            ambiguities=[
                _amb("solver", "non_blocking_assumption"),
                _amb("time_step", "non_blocking_assumption"),
            ],
        )
        fake_batch = _FakeBatch([study], batch_id="batch_non_blocking")
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: fake_batch)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. 任意研究"},
        )
        body = response.json()
        clarification = [
            a for a in body["actions"] if a["action"] == "clarification_questions"
        ]
        assert clarification == []

    def test_max_three_questions_per_study(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even when a study has many blocking ambiguities, no more than
        3 questions are surfaced for that study."""
        client = _build_client()
        session_id = _create_session(client)

        study = _make_study_with_ambiguities(
            ambiguities=[
                _amb("characteristic_length", "blocking_for_case_generation"),
                _amb("oscillation_parameters", "blocking_for_case_generation"),
                _amb("density_stratification_formula", "blocking_for_case_generation"),
                _amb("froude_number_definition", "blocking_for_case_generation"),
                _amb("heat_flux_role", "blocking_for_case_generation"),
            ],
        )
        fake_batch = _FakeBatch([study], batch_id="batch_max_three")
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: fake_batch)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. 任意研究"},
        )
        body = response.json()
        clarification = [
            a for a in body["actions"] if a["action"] == "clarification_questions"
        ]
        assert len(clarification) == 1
        questions = clarification[0]["questions"]
        # ClarificationPlanner enforces MAX_QUESTIONS_PER_TURN = 3
        assert len(questions) == 3
        for q in questions:
            assert q["study_id"] == study.study_id

    def test_questions_aggregated_across_multiple_studies(
        self, isolated_router: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Questions from all selected studies must be aggregated, each
        tagged with its own study_id."""
        client = _build_client()
        session_id = _create_session(client)

        study_a = _make_study_with_ambiguities(
            study_id="study_a",
            ambiguities=[_amb("characteristic_length", "blocking_for_case_generation")],
        )
        study_b = _make_study_with_ambiguities(
            study_id="study_b",
            ambiguities=[_amb("oscillation_parameters", "blocking_for_case_generation")],
        )
        fake_batch = _FakeBatch([study_a, study_b], batch_id="batch_multi")
        monkeypatch.setattr(v5_router, "_decompose_message", lambda msg: fake_batch)

        response = client.post(
            f"/api/v5/sessions/{session_id}/messages",
            json={"session_id": session_id, "message": "1. 研究A 2. 研究B"},
        )
        body = response.json()
        clarification = [
            a for a in body["actions"] if a["action"] == "clarification_questions"
        ]
        assert len(clarification) == 1
        questions = clarification[0]["questions"]
        study_ids = {q["study_id"] for q in questions}
        # Both studies must be represented in the aggregated questions
        assert "study_a" in study_ids
        assert "study_b" in study_ids


# ---------------------------------------------------------------------------
# Tests: request_draft_change session transition
# ---------------------------------------------------------------------------


class TestRequestDraftChangeTransition:
    """``request_draft_change`` on a confirmed/locked draft must clone
    the draft and transition the session back to ``DRAFT_READY``."""

    def test_request_draft_change_on_confirmed_draft_transitions_to_draft_ready(
        self, isolated_router: None
    ) -> None:
        client = _build_client()
        session_id = _create_session(client)

        # Manually put the session into the CONFIRMED state so the
        # auto-clone branch in request_draft_change fires.
        session = v5_router._session_store.get_session(session_id)
        assert session is not None
        # Walking the state machine to CONFIRMED may not be possible in
        # one step; instead patch the session directly.
        confirmed_session = session.model_copy(
            update={"status": DraftSessionStatus.CONFIRMED}
        )
        v5_router._session_store.update_session(confirmed_session)

        # Build a confirmed (locked) draft and register it in the store.
        draft = ExperimentDraft(
            draft_id=f"draft_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            study_id="study_x",
            version=1,
            status=DraftStatus.CONFIRMED,
            locked=True,
            objective="Test objective",
            study_type="cfd_simulation",
        )
        v5_router._draft_store[draft.draft_id] = draft

        # Track the transitions made by the state machine.
        observed_statuses: list[DraftSessionStatus] = []
        original_transition = v5_router._state_machine.transition

        def _tracking_transition(
            sess: DraftSession, to_status: DraftSessionStatus
        ) -> DraftSession:
            observed_statuses.append(sess.status)
            return original_transition(sess, to_status)

        # Patch the state machine on the router module so we can observe
        # the exact transitions the endpoint performs.
        v5_router._state_machine.transition = _tracking_transition  # type: ignore[method-assign]
        try:
            response = client.post(
                f"/api/v5/drafts/{draft.draft_id}/changes",
                json={
                    "session_id": session_id,
                    "draft_id": draft.draft_id,
                    "user_message": "请增加一个新的边界条件",
                },
            )
        finally:
            v5_router._state_machine.transition = original_transition  # type: ignore[method-assign]
        assert response.status_code == 200, response.text

        # The endpoint must have transitioned the session through
        # DRAFT_READY as part of the CONFIRMED -> DRAFT_READY -> PROPOSAL_PENDING
        # flow that runs after the auto-clone.
        assert DraftSessionStatus.DRAFT_READY in observed_statuses, (
            f"expected the session to pass through DRAFT_READY after the "
            f"auto-clone, observed transitions from: {observed_statuses}"
        )
        # And the initial transition must have been FROM CONFIRMED.
        assert observed_statuses[0] == DraftSessionStatus.CONFIRMED, (
            f"the first transition after the auto-clone must originate "
            f"from CONFIRMED, got {observed_statuses[0]}"
        )

        # current_draft_id should reference the newly cloned draft, not
        # the original (which is read-only).
        updated = v5_router._session_store.get_session(session_id)
        assert updated is not None
        assert updated.current_draft_id is not None
        assert updated.current_draft_id != draft.draft_id
        # The cloned draft must be editable (not locked, not confirmed).
        cloned = v5_router._draft_store.get(updated.current_draft_id)
        assert cloned is not None
        assert cloned.locked is False
        assert cloned.status == DraftStatus.DRAFT

    def test_request_draft_change_on_draft_ready_draft_stays_in_draft_ready(
        self, isolated_router: None
    ) -> None:
        """When the draft is not read-only, no transition is needed; the
        session must stay in (or be transitioned to) DRAFT_READY."""
        client = _build_client()
        session_id = _create_session(client)

        # Force the session to DRAFT_READY to mirror a normal flow.
        session = v5_router._session_store.get_session(session_id)
        assert session is not None
        ready_session = session.model_copy(
            update={"status": DraftSessionStatus.DRAFT_READY}
        )
        v5_router._session_store.update_session(ready_session)

        # Build an editable draft (status=DRAFT, locked=False) so the
        # auto-clone branch does not fire.
        draft = ExperimentDraft(
            draft_id=f"draft_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            study_id="study_x",
            version=1,
            status=DraftStatus.DRAFT,
            locked=False,
            objective="Test objective",
            study_type="cfd_simulation",
        )
        v5_router._draft_store[draft.draft_id] = draft

        response = client.post(
            f"/api/v5/drafts/{draft.draft_id}/changes",
            json={
                "session_id": session_id,
                "draft_id": draft.draft_id,
                "user_message": "请增加一个新的边界条件",
            },
        )
        assert response.status_code == 200, response.text

        # The session must still be in DRAFT_READY (or transitioned to
        # PROPOSAL_PENDING after the change request).
        updated = v5_router._session_store.get_session(session_id)
        assert updated is not None
        # The endpoint then transitions to PROPOSAL_PENDING; we only
        # assert that we never regressed to an earlier state.
        assert updated.status in {
            DraftSessionStatus.DRAFT_READY,
            DraftSessionStatus.PROPOSAL_PENDING,
        }
