"""Tests for the candidate template library lifecycle."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.candidate_templates.models import (
    CandidateState,
    CandidateTransitionError,
    assert_transition,
)
from fluid_scientist.db import GeneratedCaseDraftRow
from fluid_scientist.ports import StoredCandidateTemplate
from tests.adapters.test_sql_repository import seed_generated_draft


def repository(tmp_path) -> SQLWorkflowRepository:
    return SQLWorkflowRepository(f"sqlite:///{tmp_path / 'workflow.db'}")


def seed_candidate(repo: SQLWorkflowRepository) -> StoredCandidateTemplate:
    """Create and store a minimal candidate template in DRAFT state."""
    draft = seed_generated_draft(repo)
    # seed_generated_draft only constructs the dataclass; persist it so the
    # candidate template FK has a valid target.
    repo.store_generated_case_draft(draft)
    now = datetime.now(UTC).isoformat()
    template = StoredCandidateTemplate(
        candidate_id="cand-001",
        draft_id=draft.draft_id,
        project_id=draft.project_id,
        plan_id=draft.plan_id,
        plan_version=draft.plan_version,
        draft_version=draft.version,
        archive_sha256=draft.archive_sha256,
        state=CandidateState.DRAFT.value,
        rejection_reason=None,
        created_at=now,
        updated_at=now,
    )
    repo.save_candidate_template(template)
    return template


# ---------------------------------------------------------------------------
# State machine unit tests
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_draft_can_transition_to_static_validated(self) -> None:
        assert_transition(CandidateState.DRAFT, CandidateState.STATIC_VALIDATED)

    def test_static_validated_can_transition_to_pilot_passed(self) -> None:
        assert_transition(
            CandidateState.STATIC_VALIDATED, CandidateState.PILOT_PASSED
        )

    def test_pilot_passed_can_transition_to_candidate_approved(self) -> None:
        assert_transition(
            CandidateState.PILOT_PASSED, CandidateState.CANDIDATE_APPROVED
        )

    def test_candidate_approved_can_transition_to_regression_passed(self) -> None:
        assert_transition(
            CandidateState.CANDIDATE_APPROVED,
            CandidateState.REGRESSION_PASSED,
        )

    def test_regression_passed_can_transition_to_published(self) -> None:
        assert_transition(
            CandidateState.REGRESSION_PASSED, CandidateState.PUBLISHED
        )

    def test_any_pre_terminal_state_can_be_rejected(self) -> None:
        for state in [
            CandidateState.DRAFT,
            CandidateState.STATIC_VALIDATED,
            CandidateState.PILOT_PASSED,
            CandidateState.CANDIDATE_APPROVED,
            CandidateState.REGRESSION_PASSED,
        ]:
            assert_transition(state, CandidateState.REJECTED)

    def test_published_is_terminal(self) -> None:
        with pytest.raises(CandidateTransitionError):
            assert_transition(CandidateState.PUBLISHED, CandidateState.DRAFT)

    def test_rejected_is_terminal(self) -> None:
        with pytest.raises(CandidateTransitionError):
            assert_transition(CandidateState.REJECTED, CandidateState.DRAFT)

    def test_cannot_skip_states(self) -> None:
        with pytest.raises(CandidateTransitionError):
            assert_transition(CandidateState.DRAFT, CandidateState.PUBLISHED)

    def test_cannot_go_backwards(self) -> None:
        with pytest.raises(CandidateTransitionError):
            assert_transition(
                CandidateState.STATIC_VALIDATED, CandidateState.DRAFT
            )


# ---------------------------------------------------------------------------
# Repository tests
# ---------------------------------------------------------------------------


class TestRepository:
    def test_save_and_load_candidate_template(self, tmp_path) -> None:
        repo = repository(tmp_path)
        seed_candidate(repo)

        loaded = repo.load_candidate_template("cand-001")
        assert loaded.candidate_id == "cand-001"
        assert loaded.state == CandidateState.DRAFT.value
        assert loaded.draft_id == "draft-1"
        assert loaded.project_id == "project-1"
        assert loaded.rejection_reason is None

    def test_load_nonexistent_candidate_raises_keyerror(self, tmp_path) -> None:
        repo = repository(tmp_path)
        with pytest.raises(KeyError):
            repo.load_candidate_template("nonexistent")

    def test_duplicate_candidate_id_raises_integrity_error(
        self, tmp_path
    ) -> None:
        repo = repository(tmp_path)
        seed_candidate(repo)

        now = datetime.now(UTC).isoformat()
        duplicate = StoredCandidateTemplate(
            candidate_id="cand-001",
            draft_id="draft-1",
            project_id="project-1",
            plan_id="plan-1",
            plan_version=2,
            draft_version=1,
            archive_sha256=f"sha256:{'a' * 64}",
            state=CandidateState.DRAFT.value,
            rejection_reason=None,
            created_at=now,
            updated_at=now,
        )
        with pytest.raises(SAIntegrityError):
            repo.save_candidate_template(duplicate)

    def test_list_candidate_templates_by_project(self, tmp_path) -> None:
        repo = repository(tmp_path)
        seed_candidate(repo)

        templates = repo.list_candidate_templates(project_id="project-1")
        assert len(templates) == 1
        assert templates[0].candidate_id == "cand-001"

    def test_list_candidate_templates_filtered_by_state(
        self, tmp_path
    ) -> None:
        repo = repository(tmp_path)
        seed_candidate(repo)

        repo.update_candidate_template_state(
            "cand-001",
            new_state=CandidateState.STATIC_VALIDATED.value,
            rejection_reason=None,
            updated_at=datetime.now(UTC).isoformat(),
        )

        draft_templates = repo.list_candidate_templates(
            project_id="project-1", state=CandidateState.DRAFT.value
        )
        assert len(draft_templates) == 0

        validated_templates = repo.list_candidate_templates(
            project_id="project-1",
            state=CandidateState.STATIC_VALIDATED.value,
        )
        assert len(validated_templates) == 1

    def test_update_candidate_template_state(self, tmp_path) -> None:
        repo = repository(tmp_path)
        seed_candidate(repo)

        updated = repo.update_candidate_template_state(
            "cand-001",
            new_state=CandidateState.STATIC_VALIDATED.value,
            rejection_reason=None,
            updated_at="2026-07-05T12:00:00+00:00",
        )
        assert updated.state == CandidateState.STATIC_VALIDATED.value
        assert updated.updated_at == "2026-07-05T12:00:00+00:00"

    def test_update_candidate_template_rejection(self, tmp_path) -> None:
        repo = repository(tmp_path)
        seed_candidate(repo)

        updated = repo.update_candidate_template_state(
            "cand-001",
            new_state=CandidateState.REJECTED.value,
            rejection_reason="Static validation failed: unsafe solver directive",
            updated_at=datetime.now(UTC).isoformat(),
        )
        assert updated.state == CandidateState.REJECTED.value
        assert "unsafe solver directive" in updated.rejection_reason

    def test_candidate_template_cascades_with_draft_deletion(
        self, tmp_path
    ) -> None:
        """When a draft is deleted, its candidate should cascade."""
        repo = repository(tmp_path)
        seed_candidate(repo)

        loaded = repo.load_candidate_template("cand-001")
        assert loaded is not None

        with repo._sessions() as session:
            row = session.scalar(
                select(GeneratedCaseDraftRow).where(
                    GeneratedCaseDraftRow.draft_id == "draft-1"
                )
            )
            if row is not None:
                session.delete(row)
                session.commit()

        with pytest.raises(KeyError):
            repo.load_candidate_template("cand-001")


# ---------------------------------------------------------------------------
# StoredCandidateTemplate validation tests
# ---------------------------------------------------------------------------


class TestStoredCandidateTemplateValidation:
    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _make(
        self,
        *,
        candidate_id: str = "cand-1",
        draft_id: str = "draft-1",
        project_id: str = "proj-1",
        plan_id: str = "plan-1",
        plan_version: int = 1,
        draft_version: int = 1,
        archive_sha256: str | None = None,
        state: str = "draft",
        rejection_reason: str | None = None,
    ) -> StoredCandidateTemplate:
        return StoredCandidateTemplate(
            candidate_id=candidate_id,
            draft_id=draft_id,
            project_id=project_id,
            plan_id=plan_id,
            plan_version=plan_version,
            draft_version=draft_version,
            archive_sha256=archive_sha256 or f"sha256:{'a' * 64}",
            state=state,
            rejection_reason=rejection_reason,
            created_at=self._now(),
            updated_at=self._now(),
        )

    def test_valid_template(self) -> None:
        t = self._make()
        assert t.candidate_id == "cand-1"

    def test_empty_candidate_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="candidate_id"):
            self._make(candidate_id="")

    def test_negative_plan_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="plan_version"):
            self._make(plan_version=0)

    def test_empty_state_rejected(self) -> None:
        with pytest.raises(ValueError, match="state"):
            self._make(state="")

    def test_whitespace_rejection_reason_rejected(self) -> None:
        with pytest.raises(ValueError, match="rejection_reason"):
            self._make(state="rejected", rejection_reason="   ")
