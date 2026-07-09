"""Tests for DraftChangeAgent."""

from __future__ import annotations

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
        status=DraftStatus.DRAFT,
        objective="Test study",
        study_type="cylinder",
        geometry={"type": "cylinder"},
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


class TestDraftChangeAgent:
    def test_set_parameter_detected(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "把 Re 改成 5000")
        set_changes = [c for c in proposal.changes if c.change_type == "set_parameter"]
        assert len(set_changes) >= 1
        assert set_changes[0].new_value == 5000.0

    def test_add_parameter_detected(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "viscosity=0.001")
        add_changes = [c for c in proposal.changes if c.change_type == "add_parameter"]
        assert len(add_changes) >= 1

    def test_bc_change_detected(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "入口边界改成压力入口")
        bc_changes = [
            c for c in proposal.changes if c.change_type == "change_boundary_condition"
        ]
        assert len(bc_changes) == 1
        assert "inlet" in bc_changes[0].target_path

    def test_physics_model_change_detected(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "湍流模型换成 LES")
        phys_changes = [
            c for c in proposal.changes if c.change_type == "change_physics_model"
        ]
        assert len(phys_changes) == 1
        assert phys_changes[0].new_value == "LES"

    def test_add_output_detected(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "增加升力输出")
        add_out = [c for c in proposal.changes if c.change_type == "add_output"]
        assert len(add_out) >= 1

    def test_question_detected(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "为什么 Re=3900？")
        question_changes = [c for c in proposal.changes if c.change_type == "question"]
        assert len(question_changes) == 1

    def test_clarification_when_no_intent(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "你好")
        assert len(proposal.clarification_required) > 0

    def test_proposal_has_correct_base_version(self) -> None:
        draft = _make_draft(version=3)
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "Re=5000")
        assert proposal.base_draft_version == 3

    def test_proposal_has_summary(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "Re=5000")
        assert proposal.summary != ""

    def test_invalidates_set_for_bc_change(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "入口边界改成压力入口")
        assert "case_files" in proposal.invalidates

    def test_solver_change_detected(self) -> None:
        draft = _make_draft()
        agent = DraftChangeAgent()
        proposal = agent.generate(draft, "求解器换成 pimpleFoam")
        solver_changes = [
            c for c in proposal.changes if c.change_type == "change_solver"
        ]
        assert len(solver_changes) == 1
