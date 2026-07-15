"""Tests for DraftChangeAgent."""

from __future__ import annotations

from fluid_scientist.draft.change_agent import DraftChangeAgent
from fluid_scientist.draft.models import (
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
)
from fluid_scientist.llm import LLMClient


class _FieldMappingLLM(LLMClient):
    def __init__(self, changes: list[dict]) -> None:
        super().__init__()
        self._changes = changes

    def _mock_response(self, purpose, user_message, output_schema):
        return {"changes": self._changes}


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
            DraftParameter(
                parameter_id="end_time",
                display_name="end_time",
                value=20,
                source=ParameterSource.DERIVED,
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

    def test_chinese_and_compact_aliases_update_existing_parameters(self) -> None:
        draft = _make_draft()
        proposal = DraftChangeAgent().generate(
            draft, "把雷诺数修改为4000，endtime改为40"
        )

        assert [change.change_type for change in proposal.changes] == [
            "set_parameter",
            "set_parameter",
        ]
        assert [change.target_path for change in proposal.changes] == [
            "control_parameters.reynolds_number",
            "control_parameters.end_time",
        ]
        assert [change.new_value for change in proposal.changes] == [4000, 40]

    def test_llm_maps_only_to_existing_parameter_ids(self) -> None:
        agent = DraftChangeAgent(
            llm_client=_FieldMappingLLM(
                [
                    {
                        "target_parameter_id": "reynolds_number",
                        "new_value": 4000,
                        "reason": "雷诺数对应现有 Re 字段",
                    },
                    {
                        "target_parameter_id": "end_time",
                        "new_value": 40,
                    },
                ]
            )
        )
        proposal = agent.generate(
            _make_draft(), "把雷诺数修改为4000，endtime改为40"
        )

        assert [change.target_path for change in proposal.changes] == [
            "control_parameters.reynolds_number",
            "control_parameters.end_time",
        ]

    def test_llm_invented_field_is_rejected_and_falls_back(self) -> None:
        agent = DraftChangeAgent(
            llm_client=_FieldMappingLLM(
                [{"target_parameter_id": "invented_field", "new_value": 1}]
            )
        )
        proposal = agent.generate(_make_draft(), "Re=4200")

        assert len(proposal.changes) == 1
        assert proposal.changes[0].target_path == "control_parameters.reynolds_number"

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
