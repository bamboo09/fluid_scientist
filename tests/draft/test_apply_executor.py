"""Tests for ApplyProposalExecutor and UnknownParameterMapper."""

from __future__ import annotations

import pytest

from fluid_scientist.draft.apply_executor import (
    ApplyProposalExecutor,
    ProposalNotPendingError,
    ProposalVersionMismatchError,
    UnknownParameterMapper,
)
from fluid_scientist.draft.change_agent import DraftChangeAgent
from fluid_scientist.draft.models import (
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
)


def _make_draft(**kwargs) -> ExperimentDraft:
    defaults = dict(
        draft_id="draft_001",
        session_id="session_001",
        version=1,
        status=DraftStatus.READY,
        objective="Test study",
        study_type="cylinder",
        geometry={"type": "cylinder", "D": 0.1},
        boundary_conditions={"inlet": {"type": "velocity_inlet"}},
        control_parameters=[
            DraftParameter(
                parameter_id="reynolds_number",
                display_name="Re",
                value=3900,
                source=ParameterSource.USER_PROVIDED,
            ),
            DraftParameter(
                parameter_id="cylinder_diameter",
                display_name="D",
                value=0.1,
                unit="m",
                source=ParameterSource.USER_PROVIDED,
            ),
        ],
        requested_outputs=[{"name": "drag"}],
        analysis_goals=["Study wake"],
    )
    defaults.update(kwargs)
    return ExperimentDraft(**defaults)


class TestUnknownParameterMapper:
    def test_known_parameter(self) -> None:
        mapper = UnknownParameterMapper()
        result = mapper.check_parameter("reynolds_number")
        assert result["status"] == "known"
        assert result["requires_extension"] is False

    def test_unknown_parameter(self) -> None:
        mapper = UnknownParameterMapper()
        result = mapper.check_parameter("custom_heat_flux")
        assert result["status"] == "missing"
        assert result["requires_extension"] is True

    def test_known_metric(self) -> None:
        mapper = UnknownParameterMapper()
        result = mapper.check_metric("drag")
        assert result["status"] == "known"

    def test_unknown_metric(self) -> None:
        mapper = UnknownParameterMapper()
        result = mapper.check_metric("custom_mixing_efficiency")
        assert result["requires_extension"] is True


class TestApplyProposalExecutor:
    def test_apply_set_parameter_creates_new_version(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()
        proposal = agent.generate(draft, "Re=5000")
        new_draft, result = executor.apply(draft, proposal)
        assert new_draft.version == 2
        assert new_draft.draft_id != draft.draft_id
        re_param = next(
            p for p in new_draft.control_parameters if p.parameter_id == "reynolds_number"
        )
        assert re_param.value == 5000.0

    def test_apply_version_mismatch_raises(self) -> None:
        draft = _make_draft(version=5)
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "Re=5000")
        proposal.base_draft_version = 3  # mismatch
        executor = ApplyProposalExecutor()
        with pytest.raises(ProposalVersionMismatchError):
            executor.apply(draft, proposal)

    def test_apply_non_pending_raises(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "Re=5000")
        proposal.status = "applied"
        executor = ApplyProposalExecutor()
        with pytest.raises(ProposalNotPendingError):
            executor.apply(draft, proposal)

    def test_apply_marks_proposal_as_applied(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()
        proposal = agent.generate(draft, "Re=5000")
        executor.apply(draft, proposal)
        assert proposal.status == "applied"

    def test_apply_add_parameter_known(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()
        proposal = agent.generate(draft, "viscosity=0.001")
        new_draft, _ = executor.apply(draft, proposal)
        visc = next(
            (p for p in new_draft.control_parameters if p.parameter_id == "viscosity"),
            None,
        )
        assert visc is not None
        assert visc.value == 0.001

    def test_apply_add_unknown_parameter_triggers_extension(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()
        proposal = agent.generate(draft, "custom_param=42")
        new_draft, _ = executor.apply(draft, proposal)
        custom = next(
            (p for p in new_draft.control_parameters if p.parameter_id == "custom_param"),
            None,
        )
        assert custom is not None
        assert custom.source == ParameterSource.UNKNOWN_REQUIRED
        assert len(new_draft.blocking_issues) > 0

    def test_apply_bc_change(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()
        proposal = agent.generate(draft, "入口边界改成压力入口")
        new_draft, _ = executor.apply(draft, proposal)
        assert "modified" in str(new_draft.boundary_conditions.get("inlet", {}))

    def test_apply_physics_model_change(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()
        proposal = agent.generate(draft, "湍流模型换成 LES")
        new_draft, _ = executor.apply(draft, proposal)
        assert new_draft.physics_models.get("turbulence_model") == "LES"

    def test_apply_validates_new_draft(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()
        proposal = agent.generate(draft, "Re=5000")
        new_draft, result = executor.apply(draft, proposal)
        assert result is not None

    def test_apply_original_draft_unchanged(self) -> None:
        draft = _make_draft()
        original_re = next(
            p for p in draft.control_parameters if p.parameter_id == "reynolds_number"
        ).value
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()
        proposal = agent.generate(draft, "Re=5000")
        executor.apply(draft, proposal)
        # Original draft's Re should be unchanged
        re_param = next(
            p for p in draft.control_parameters if p.parameter_id == "reynolds_number"
        )
        assert re_param.value == original_re

    def test_apply_chained_proposals(self) -> None:
        """Apply two proposals in sequence, version should increment."""
        draft = _make_draft()
        agent = DraftChangeAgent()
        executor = ApplyProposalExecutor()

        p1 = agent.generate(draft, "Re=5000")
        draft_v2, _ = executor.apply(draft, p1)

        p2 = agent.generate(draft_v2, "Re=10000")
        draft_v3, _ = executor.apply(draft_v2, p2)

        assert draft_v3.version == 3
        re_param = next(
            p for p in draft_v3.control_parameters if p.parameter_id == "reynolds_number"
        )
        assert re_param.value == 10000.0
