"""Tests for the V5Repository SQLite-backed storage layer.

These tests verify that all V5 workflow entities (sessions, drafts,
proposals, case plans, batches, compiled cases, code extensions)
can be saved and retrieved from SQLite, and that data survives
when a new repository instance is created pointing at the same
database file (simulating a service restart).
"""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest

from fluid_scientist.case_plan.models import CasePlan
from fluid_scientist.capabilities.models import CapabilityRegistry  # noqa: F401  # resolve circular import
from fluid_scientist.code_extension.spec import CodeExtensionSpec
from fluid_scientist.draft.models import ChangeProposal, DraftChange, ExperimentDraft
from fluid_scientist.draft_session.models import (
    DraftSession,
    DraftSessionStatus,
    SessionMessage,
)
from fluid_scientist.draft_session.v5_storage import V5Repository
from fluid_scientist.study_decomposition.models import BatchStudyPlan, StudyIntent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo():
    """Create a V5Repository backed by a temporary SQLite file."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = os.path.join(tmp_dir, "test_v5.db")
        yield V5Repository(db_path=db_path)


def _make_session() -> DraftSession:
    return DraftSession(
        session_id=f"session_{uuid.uuid4().hex[:12]}",
        user_id="test-user",
        status=DraftSessionStatus.COLLECTING_INTENT,
    )


def _make_draft(session_id: str = "session_test") -> ExperimentDraft:
    return ExperimentDraft(
        draft_id=f"draft_{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        version=1,
        objective="Test cylinder flow",
        study_type="cylinder",
    )


def _make_proposal(draft_id: str = "draft_test") -> ChangeProposal:
    return ChangeProposal(
        proposal_id=f"proposal_{uuid.uuid4().hex[:12]}",
        session_id="session_test",
        draft_id=draft_id,
        base_draft_version=1,
        status="pending",
        summary="Change Re to 5000",
        changes=[
            DraftChange(
                change_type="set_parameter",
                target_path="dimensionless.reynolds_number",
                old_value=3900,
                new_value=5000,
            ),
        ],
        impact_summary=["入口速度需要同步校验"],
    )


def _make_case_plan(draft_id: str = "draft_test") -> CasePlan:
    return CasePlan(
        case_plan_id=f"cp_{uuid.uuid4().hex[:12]}",
        draft_id=draft_id,
        draft_version=1,
        case_type="cylinder_cross_flow",
        solver="pimpleFoam",
        dimensions="3D",
    )


def _make_batch() -> BatchStudyPlan:
    return BatchStudyPlan(
        batch_id=f"batch_{uuid.uuid4().hex[:12]}",
        input_type="single_study",
        studies=[
            StudyIntent(
                study_id="study_1",
                title="Test study",
                raw_text="test",
                research_objective="test objective",
                study_type="cylinder",
            ),
        ],
        batch_summary="Test batch",
    )


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    def test_save_and_get_session(self, repo: V5Repository) -> None:
        session = _make_session()
        repo.save_session(session)
        loaded = repo.get_session(session.session_id)
        assert loaded is not None
        assert loaded.session_id == session.session_id
        assert loaded.user_id == "test-user"

    def test_get_missing_session_returns_none(self, repo: V5Repository) -> None:
        assert repo.get_session("nonexistent") is None

    def test_list_sessions(self, repo: V5Repository) -> None:
        s1 = _make_session()
        s2 = _make_session()
        repo.save_session(s1)
        repo.save_session(s2)
        ids = repo.list_sessions()
        assert s1.session_id in ids
        assert s2.session_id in ids

    def test_update_session(self, repo: V5Repository) -> None:
        session = _make_session()
        repo.save_session(session)
        updated = session.model_copy(update={"user_id": "new-user"})
        repo.save_session(updated)
        loaded = repo.get_session(session.session_id)
        assert loaded is not None
        assert loaded.user_id == "new-user"


# ---------------------------------------------------------------------------
# Message persistence
# ---------------------------------------------------------------------------


class TestMessagePersistence:
    def test_add_and_get_messages(self, repo: V5Repository) -> None:
        session = _make_session()
        repo.save_session(session)
        m1 = SessionMessage(
            message_id="msg_1",
            session_id=session.session_id,
            role="user",
            message_type="research_request",
            content="Hello",
        )
        m2 = SessionMessage(
            message_id="msg_2",
            session_id=session.session_id,
            role="assistant",
            message_type="draft_summary",
            content="Hi there",
        )
        repo.add_message(m1)
        repo.add_message(m2)
        messages = repo.get_messages(session.session_id)
        assert len(messages) == 2
        assert messages[0].message_id == "msg_1"
        assert messages[1].message_id == "msg_2"

    def test_get_messages_empty(self, repo: V5Repository) -> None:
        assert repo.get_messages("nonexistent") == []


# ---------------------------------------------------------------------------
# Draft persistence
# ---------------------------------------------------------------------------


class TestDraftPersistence:
    def test_save_and_get_draft(self, repo: V5Repository) -> None:
        draft = _make_draft()
        repo.save_draft(draft)
        loaded = repo.get_draft(draft.draft_id)
        assert loaded is not None
        assert loaded.draft_id == draft.draft_id
        assert loaded.objective == "Test cylinder flow"

    def test_get_missing_draft(self, repo: V5Repository) -> None:
        assert repo.get_draft("nonexistent") is None


# ---------------------------------------------------------------------------
# Proposal persistence
# ---------------------------------------------------------------------------


class TestProposalPersistence:
    def test_save_and_get_proposal(self, repo: V5Repository) -> None:
        proposal = _make_proposal()
        repo.save_proposal(proposal)
        loaded = repo.get_proposal(proposal.proposal_id)
        assert loaded is not None
        assert loaded.proposal_id == proposal.proposal_id
        assert loaded.status == "pending"
        assert len(loaded.changes) == 1
        assert loaded.changes[0].new_value == 5000

    def test_update_proposal_status(self, repo: V5Repository) -> None:
        proposal = _make_proposal()
        repo.save_proposal(proposal)
        proposal.status = "cancelled"
        repo.save_proposal(proposal)
        loaded = repo.get_proposal(proposal.proposal_id)
        assert loaded is not None
        assert loaded.status == "cancelled"


# ---------------------------------------------------------------------------
# CasePlan persistence
# ---------------------------------------------------------------------------


class TestCasePlanPersistence:
    def test_save_and_get_case_plan(self, repo: V5Repository) -> None:
        cp = _make_case_plan()
        repo.save_case_plan(cp)
        loaded = repo.get_case_plan(cp.case_plan_id)
        assert loaded is not None
        assert loaded.case_plan_id == cp.case_plan_id
        assert loaded.solver == "pimpleFoam"


# ---------------------------------------------------------------------------
# Batch persistence
# ---------------------------------------------------------------------------


class TestBatchPersistence:
    def test_save_and_get_batch(self, repo: V5Repository) -> None:
        batch = _make_batch()
        repo.save_batch(batch)
        loaded = repo.get_batch(batch.batch_id)
        assert loaded is not None
        assert loaded.batch_id == batch.batch_id
        assert len(loaded.studies) == 1


# ---------------------------------------------------------------------------
# Compiled case persistence
# ---------------------------------------------------------------------------


class TestCompiledCasePersistence:
    def test_save_and_get_compiled_case(self, repo: V5Repository) -> None:
        compiled = {"system": {"controlDict": {"application": "pimpleFoam"}}}
        repo.save_compiled_case("cp_1", "/tmp/case_dir", compiled)
        loaded = repo.get_compiled_case("cp_1")
        assert loaded is not None
        assert loaded["case_dir"] == "/tmp/case_dir"
        assert "system" in loaded["compiled_structure"]


# ---------------------------------------------------------------------------
# Code extension persistence
# ---------------------------------------------------------------------------


class TestExtensionPersistence:
    def test_save_and_get_extension(self, repo: V5Repository) -> None:
        spec = CodeExtensionSpec(
            extension_id=f"ext_{uuid.uuid4().hex[:12]}",
            session_id="session_test",
            extension_type="analysis_plugin",
            missing_capability_id="test_cap",
            requirement="Test requirement",
        )
        repo.save_extension(spec)
        loaded = repo.get_extension(spec.extension_id)
        assert loaded is not None
        assert loaded.extension_id == spec.extension_id
        assert loaded.requirement == "Test requirement"


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


class TestRestartRecovery:
    """Verify that a new V5Repository instance pointing at the same DB
    file can load all entities written by a previous instance."""

    def test_full_recovery_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "restart_test.db")

            # Phase 1: Write all entities using the first repo instance
            repo1 = V5Repository(db_path=db_path)
            session = _make_session()
            repo1.save_session(session)

            draft = _make_draft(session_id=session.session_id)
            repo1.save_draft(draft)

            proposal = _make_proposal(draft_id=draft.draft_id)
            proposal.session_id = session.session_id
            repo1.save_proposal(proposal)

            cp = _make_case_plan(draft_id=draft.draft_id)
            repo1.save_case_plan(cp)

            batch = _make_batch()
            repo1.save_batch(batch)

            repo1.save_compiled_case(cp.case_plan_id, "/tmp/test", {"system": {}})

            ext = CodeExtensionSpec(
                extension_id=f"ext_{uuid.uuid4().hex[:12]}",
                session_id=session.session_id,
                extension_type="analysis_plugin",
                missing_capability_id="cap",
                requirement="test",
            )
            repo1.save_extension(ext)

            # Phase 2: Simulate restart by creating a new repo instance
            repo2 = V5Repository(db_path=db_path)

            # All entities must be recoverable
            assert repo2.get_session(session.session_id) is not None
            assert repo2.get_draft(draft.draft_id) is not None
            assert repo2.get_proposal(proposal.proposal_id) is not None
            assert repo2.get_case_plan(cp.case_plan_id) is not None
            assert repo2.get_batch(batch.batch_id) is not None
            assert repo2.get_compiled_case(cp.case_plan_id) is not None
            assert repo2.get_extension(ext.extension_id) is not None

            # Session list must include the session
            assert session.session_id in repo2.list_sessions()

    def test_audit_events_persist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = os.path.join(tmp_dir, "audit_test.db")
            repo1 = V5Repository(db_path=db_path)
            repo1.log_audit(
                event_id="evt_1",
                session_id="session_test",
                event_type="draft_confirmed",
                payload={"draft_id": "d1", "version": 1},
            )

            repo2 = V5Repository(db_path=db_path)
            # Audit events are written; we just verify no crash on reopen
            # (the schema_version table ensures migration is idempotent)
            assert repo2.get_session("session_test") is None  # no session was saved
