"""Tests for the :class:`DraftGenerator`."""

from __future__ import annotations

import uuid

from fluid_scientist.draft.draft_generator import DraftGenerator
from fluid_scientist.draft.models import (
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
)
from fluid_scientist.study_decomposition.models import (
    AmbiguityItem,
    ExtractedParameter,
    ObservableSpec,
    StudyIntent,
)


def _make_study() -> StudyIntent:
    return StudyIntent(
        study_id="study_001",
        title="Near-wall inclined cylinder wake",
        raw_text="近壁倾斜圆柱 Re=3900 三维湍流尾迹",
        study_type="near_wall_inclined_cylinder_wake",
        research_objective="Study 3D turbulent wake of inclined cylinder near wall",
        geometry={"type": "cylinder", "D": 0.1, "inclined": True},
        physical_models={
            "dimension": "3D",
            "temporal": "transient",
            "turbulent": True,
        },
        initial_conditions=[
            {"field": "velocity", "value": 0.0, "unit": "m/s"},
            {"field": "pressure", "value": 0.0, "unit": "Pa"},
        ],
        boundary_conditions=[
            {"type": "inlet", "velocity": 1.0, "unit": "m/s"},
            {"type": "outlet", "pressure": 0.0, "unit": "Pa"},
            {"type": "wall", "no_slip": True},
        ],
        known_parameters=[
            ExtractedParameter(
                canonical_id="reynolds_number",
                display_name="Re",
                value=3900,
                dimensionless=True,
                source_text="Re=3900",
                source="user_provided",
                affects=["solver", "turbulence_model"],
                confidence=0.99,
            ),
        ],
        derived_parameters=[
            ExtractedParameter(
                canonical_id="domain_length",
                display_name="Domain Length",
                value=2.0,
                unit="m",
                source_text="derived from D",
                source="derived",
                affects=["geometry"],
                confidence=0.9,
            ),
        ],
        assumed_parameters=[
            ExtractedParameter(
                canonical_id="wall_roughness",
                display_name="Wall Roughness",
                value=0.0,
                unit="m",
                source_text="assumed smooth wall",
                source="assumed",
                affects=["boundary_condition"],
                confidence=0.5,
            ),
        ],
        unknown_required_parameters=[
            ExtractedParameter(
                canonical_id="cylinder_diameter",
                display_name="Cylinder Diameter D",
                value=None,
                unit="m",
                source_text="",
                source="unknown_required",
                affects=["geometry", "mesh"],
                confidence=0.0,
            ),
        ],
        observables=[
            ObservableSpec(
                observable_id="drag",
                display_name="Drag Coefficient",
                category="force",
                required_fields=["Cd"],
            ),
        ],
        analysis_goals=["Compute drag coefficient", "Compare wake profiles"],
        ambiguity_report=[
            AmbiguityItem(
                field="cylinder_diameter",
                issue="D not specified",
                severity="blocking_for_case_generation",
                reason="D is required for mesh generation",
            ),
            AmbiguityItem(
                field="domain_length",
                issue="Domain length not specified",
                severity="non_blocking_assumption",
                reason="Can be derived from D",
                recommended_default="20D",
            ),
        ],
        readiness_level="draftable",
    )


class TestDraftGeneratorBasic:
    def test_returns_experiment_draft(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        assert isinstance(draft, ExperimentDraft)

    def test_generates_uuid_draft_id(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        # Must be a valid UUID string and unique per call.
        uuid.UUID(draft.draft_id)
        draft2 = DraftGenerator().generate(_make_study())
        assert draft.draft_id != draft2.draft_id

    def test_version_and_status(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        assert draft.version == 1
        assert draft.status == DraftStatus.DRAFT
        assert draft.locked is False

    def test_objective_and_study_type_copied(self) -> None:
        study = _make_study()
        draft = DraftGenerator().generate(study)
        assert draft.objective == study.research_objective
        assert draft.study_type == study.study_type

    def test_study_id_copied(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        assert draft.study_id == "study_001"

    def test_geometry_copied(self) -> None:
        study = _make_study()
        draft = DraftGenerator().generate(study)
        assert draft.geometry == study.geometry


class TestDraftGeneratorParameters:
    def test_all_parameter_lists_merged(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        ids = [p.parameter_id for p in draft.control_parameters]
        assert ids == [
            "reynolds_number",
            "domain_length",
            "wall_roughness",
            "cylinder_diameter",
        ]

    def test_known_parameter_source(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        re = draft.control_parameters[0]
        assert re.source == ParameterSource.USER_PROVIDED
        assert re.value == 3900
        assert re.source_reason == "Re=3900"
        assert re.category == "solver"

    def test_derived_parameter_source(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        domain = draft.control_parameters[1]
        assert domain.source == ParameterSource.DERIVED
        assert domain.value == 2.0
        assert domain.unit == "m"

    def test_assumed_parameter_maps_to_assumption(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        roughness = draft.control_parameters[2]
        assert roughness.source == ParameterSource.ASSUMPTION
        assert roughness.value == 0.0

    def test_unknown_required_parameter_source(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        diameter = draft.control_parameters[3]
        assert diameter.source == ParameterSource.UNKNOWN_REQUIRED
        assert diameter.value is None

    def test_parameter_fields_preserved(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        re = draft.control_parameters[0]
        assert isinstance(re, DraftParameter)
        assert re.display_name == "Re"
        assert re.unit is None

    def test_empty_parameter_lists(self) -> None:
        study = _make_study()
        study.known_parameters = []
        study.derived_parameters = []
        study.assumed_parameters = []
        study.unknown_required_parameters = []
        draft = DraftGenerator().generate(study)
        assert draft.control_parameters == []


class TestDraftGeneratorSections:
    def test_physics_models_copied(self) -> None:
        study = _make_study()
        draft = DraftGenerator().generate(study)
        assert draft.physics_models == study.physical_models

    def test_boundary_conditions_converted_to_dict_keyed_by_type(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        bc = draft.boundary_conditions
        assert isinstance(bc, dict)
        assert "inlet" in bc
        assert "outlet" in bc
        assert "wall" in bc
        assert bc["inlet"]["velocity"] == 1.0

    def test_initial_conditions_converted_to_dict(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        ic = draft.initial_conditions
        assert isinstance(ic, dict)
        assert "velocity" in ic
        assert "pressure" in ic

    def test_observables_become_requested_outputs(self) -> None:
        study = _make_study()
        draft = DraftGenerator().generate(study)
        assert len(draft.requested_outputs) == 1
        assert draft.requested_outputs[0]["observable_id"] == "drag"
        assert draft.requested_outputs[0]["category"] == "force"

    def test_analysis_goals_copied(self) -> None:
        study = _make_study()
        draft = DraftGenerator().generate(study)
        assert draft.analysis_goals == study.analysis_goals

    def test_assumptions_from_assumed_parameters(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        assert len(draft.assumptions) == 1
        assert draft.assumptions[0]["canonical_id"] == "wall_roughness"

    def test_blocking_issues_filter_only_blocking(self) -> None:
        study = _make_study()
        draft = DraftGenerator().generate(study)
        # Only the blocking_for_case_generation ambiguity is carried over.
        assert len(draft.blocking_issues) == 1
        assert draft.blocking_issues[0]["field"] == "cylinder_diameter"
        assert (
            draft.blocking_issues[0]["severity"]
            == "blocking_for_case_generation"
        )


class TestDraftGeneratorResearchState:
    def test_session_id_from_research_state(self) -> None:
        draft = DraftGenerator().generate(
            _make_study(), research_state={"session_id": "session_xyz"}
        )
        assert draft.session_id == "session_xyz"

    def test_session_id_blank_without_research_state(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        assert draft.session_id == ""

    def test_session_id_blank_when_research_state_missing_key(self) -> None:
        draft = DraftGenerator().generate(_make_study(), research_state={})
        assert draft.session_id == ""


class TestDraftGeneratorRoundTrip:
    def test_generated_draft_is_editable(self) -> None:
        draft = DraftGenerator().generate(_make_study())
        assert draft.is_read_only() is False

    def test_generated_draft_can_be_validated(self) -> None:
        """A generated draft with a blocking ambiguity should validate invalid."""
        from fluid_scientist.draft.validator import DraftValidator

        draft = DraftGenerator().generate(_make_study())
        result = DraftValidator().validate(draft)
        # The study carries a blocking ambiguity (D not specified), so the
        # draft must report it as a pre-existing blocking issue.
        assert result.valid is False
        assert any(
            issue["check"] == "pre_existing_blocking_issue"
            for issue in result.blocking_issues
        )
