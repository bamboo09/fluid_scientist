"""Tests for the draft data models."""

from __future__ import annotations

from fluid_scientist.draft.models import (
    ChangeProposal,
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
    ValidationResult,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestDraftStatus:
    def test_values(self) -> None:
        assert DraftStatus.DRAFT == "draft"
        assert DraftStatus.READY == "ready"
        assert DraftStatus.CONFIRMED == "confirmed"
        assert DraftStatus.COMPILED == "compiled"
        assert DraftStatus.RUNNING == "running"
        assert DraftStatus.COMPLETED == "completed"
        assert DraftStatus.FAILED == "failed"

    def test_is_str_enum(self) -> None:
        assert isinstance(DraftStatus.DRAFT, str)
        assert str(DraftStatus.CONFIRMED) == "confirmed"


class TestParameterSource:
    def test_values(self) -> None:
        assert ParameterSource.USER_PROVIDED == "user_provided"
        assert ParameterSource.DERIVED == "derived"
        assert ParameterSource.SYSTEM_RECOMMENDED == "system_recommended"
        assert ParameterSource.ASSUMPTION == "assumption"
        assert ParameterSource.UNKNOWN_REQUIRED == "unknown_required"
        assert ParameterSource.CAPABILITY_DEFAULT == "capability_default"


# ---------------------------------------------------------------------------
# DraftParameter
# ---------------------------------------------------------------------------


class TestDraftParameter:
    def test_create_minimal(self) -> None:
        p = DraftParameter(
            parameter_id="reynolds_number",
            display_name="Reynolds Number",
            source=ParameterSource.USER_PROVIDED,
        )
        assert p.parameter_id == "reynolds_number"
        assert p.value is None
        assert p.unit is None
        assert p.source == ParameterSource.USER_PROVIDED
        assert p.source_reason == ""
        assert p.category == ""
        assert p.editable is True

    def test_create_full(self) -> None:
        p = DraftParameter(
            parameter_id="cylinder_diameter",
            display_name="Cylinder Diameter D",
            value=0.1,
            unit="m",
            source=ParameterSource.USER_PROVIDED,
            source_reason="user stated D=0.1m",
            category="geometry",
            editable=False,
        )
        assert p.value == 0.1
        assert p.unit == "m"
        assert p.category == "geometry"
        assert p.editable is False


# ---------------------------------------------------------------------------
# ExperimentDraft
# ---------------------------------------------------------------------------


class TestExperimentDraft:
    def _make_draft(self, **overrides) -> ExperimentDraft:
        defaults = dict(
            draft_id="draft_001",
            session_id="session_001",
            study_id="study_001",
        )
        defaults.update(overrides)
        return ExperimentDraft(**defaults)

    def test_create_with_defaults(self) -> None:
        draft = self._make_draft()
        assert draft.draft_id == "draft_001"
        assert draft.session_id == "session_001"
        assert draft.study_id == "study_001"
        assert draft.version == 1
        assert draft.status == DraftStatus.DRAFT
        assert draft.locked is False
        assert draft.objective == ""
        assert draft.control_parameters == []
        assert draft.requested_outputs == []
        assert draft.analysis_goals == []
        assert draft.capability_preview is None
        assert draft.validation_result is None
        assert draft.created_at is not None
        assert draft.updated_at is not None

    def test_create_full(self) -> None:
        draft = self._make_draft(
            objective="Study cylinder wake",
            study_type="cylinder_wake",
            geometry={"type": "cylinder", "D": 0.1},
            control_parameters=[
                DraftParameter(
                    parameter_id="re",
                    display_name="Re",
                    value=3900,
                    source=ParameterSource.USER_PROVIDED,
                ),
            ],
        )
        assert draft.objective == "Study cylinder wake"
        assert draft.study_type == "cylinder_wake"
        assert draft.geometry["type"] == "cylinder"
        assert len(draft.control_parameters) == 1
        assert draft.control_parameters[0].value == 3900

    # -- is_read_only ------------------------------------------------------

    def test_is_read_only_draft_is_editable(self) -> None:
        draft = self._make_draft()
        assert draft.is_read_only() is False

    def test_is_read_only_ready_is_editable(self) -> None:
        draft = self._make_draft(status=DraftStatus.READY)
        assert draft.is_read_only() is False

    def test_is_read_only_failed_is_editable(self) -> None:
        draft = self._make_draft(status=DraftStatus.FAILED)
        assert draft.is_read_only() is False

    def test_is_read_only_confirmed(self) -> None:
        draft = self._make_draft(status=DraftStatus.CONFIRMED)
        assert draft.is_read_only() is True

    def test_is_read_only_compiled(self) -> None:
        draft = self._make_draft(status=DraftStatus.COMPILED)
        assert draft.is_read_only() is True

    def test_is_read_only_running(self) -> None:
        draft = self._make_draft(status=DraftStatus.RUNNING)
        assert draft.is_read_only() is True

    def test_is_read_only_completed(self) -> None:
        draft = self._make_draft(status=DraftStatus.COMPLETED)
        assert draft.is_read_only() is True

    def test_is_read_only_locked(self) -> None:
        draft = self._make_draft(locked=True)
        assert draft.is_read_only() is True

    def test_is_read_only_locked_overrides_draft(self) -> None:
        draft = self._make_draft(status=DraftStatus.DRAFT, locked=True)
        assert draft.is_read_only() is True

    # -- clone -------------------------------------------------------------

    def test_clone_increments_version_and_resets_state(self) -> None:
        draft = self._make_draft(
            status=DraftStatus.CONFIRMED,
            locked=True,
            version=3,
            objective="original",
        )
        clone = draft.clone("draft_002")

        assert clone.draft_id == "draft_002"
        assert clone.version == 4
        assert clone.status == DraftStatus.DRAFT
        assert clone.locked is False
        # Content is preserved.
        assert clone.objective == "original"
        assert clone.session_id == "session_001"
        assert clone.study_id == "study_001"
        # Original is untouched.
        assert draft.draft_id == "draft_001"
        assert draft.version == 3
        assert draft.status == DraftStatus.CONFIRMED
        assert draft.locked is True

    def test_clone_gets_fresh_timestamps(self) -> None:
        draft = self._make_draft()
        clone = draft.clone("draft_002")
        assert clone.created_at >= draft.created_at
        assert clone.updated_at >= draft.updated_at

    def test_clone_from_version_one(self) -> None:
        draft = self._make_draft()
        clone = draft.clone("draft_002")
        assert clone.version == 2

    # -- confirm -----------------------------------------------------------

    def test_confirm_sets_status_and_lock(self) -> None:
        draft = self._make_draft(status=DraftStatus.DRAFT, locked=False)
        confirmed = draft.confirm()

        assert confirmed.status == DraftStatus.CONFIRMED
        assert confirmed.locked is True
        assert confirmed.is_read_only() is True
        # Original untouched.
        assert draft.status == DraftStatus.DRAFT
        assert draft.locked is False

    def test_confirm_preserves_content(self) -> None:
        draft = self._make_draft(
            objective="keep me",
            version=2,
        )
        confirmed = draft.confirm()
        assert confirmed.objective == "keep me"
        assert confirmed.version == 2


# ---------------------------------------------------------------------------
# ChangeProposal
# ---------------------------------------------------------------------------


class TestChangeProposal:
    def test_create_defaults(self) -> None:
        proposal = ChangeProposal(
            proposal_id="prop_001",
            session_id="session_001",
            draft_id="draft_001",
            base_draft_version=1,
        )
        assert proposal.proposal_id == "prop_001"
        assert proposal.status == "pending"
        assert proposal.changes == []
        assert proposal.impact_summary == []
        assert proposal.invalidates == []
        assert proposal.requires_confirmation is True
        assert proposal.missing_capabilities == []
        assert proposal.clarification_required == []
        assert proposal.created_at is not None

    def test_create_with_changes(self) -> None:
        proposal = ChangeProposal(
            proposal_id="prop_002",
            session_id="session_001",
            draft_id="draft_001",
            base_draft_version=1,
            summary="Increase Reynolds number",
            changes=[
                {
                    "change_type": "update_parameter",
                    "target_path": "control_parameters[re]",
                    "old_value": 3900,
                    "new_value": 5000,
                    "reason": "user requested higher Re",
                },
            ],
            impact_summary=["Mesh must be refined"],
            invalidates=["mesh_plan_v1"],
        )
        assert proposal.summary == "Increase Reynolds number"
        assert len(proposal.changes) == 1
        assert proposal.changes[0]["new_value"] == 5000
        assert proposal.impact_summary == ["Mesh must be refined"]


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------


class TestValidationResult:
    def test_defaults_are_valid(self) -> None:
        result = ValidationResult()
        assert result.valid is True
        assert result.blocking_issues == []
        assert result.warnings == []
        assert result.errors == []

    def test_with_issues(self) -> None:
        result = ValidationResult(
            valid=False,
            blocking_issues=[{"check": "x", "message": "bad"}],
            warnings=["be careful"],
            errors=["boom"],
        )
        assert result.valid is False
        assert len(result.blocking_issues) == 1
        assert result.warnings == ["be careful"]
        assert result.errors == ["boom"]
