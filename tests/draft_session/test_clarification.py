"""Tests for ClarificationPlanner."""

from __future__ import annotations

import pytest

from fluid_scientist.draft_session.clarification import (
    ClarificationPlanner,
    _categorise_field,
)
from fluid_scientist.draft_session.models import ClarificationQuestion
from fluid_scientist.study_decomposition.ambiguity_detector import AmbiguityDetector
from fluid_scientist.study_decomposition.models import (
    AmbiguityItem,
    ExtractedParameter,
    ObservableSpec,
    StudyIntent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_study(**kwargs: object) -> StudyIntent:
    defaults: dict[str, object] = dict(
        study_id="test_001",
        title="Test study",
        raw_text="test",
        study_type="test",
        research_objective="test",
    )
    defaults.update(kwargs)
    return StudyIntent(**defaults)


def _amb(
    field: str,
    severity: str,
    issue: str = "test issue",
    reason: str = "test reason",
    suggested_question: str | None = None,
    recommended_default: object = None,
) -> AmbiguityItem:
    return AmbiguityItem(
        field=field,
        issue=issue,
        severity=severity,  # type: ignore[arg-type]
        reason=reason,
        suggested_question=suggested_question,
        recommended_default=recommended_default,
    )


# ---------------------------------------------------------------------------
# Basic filtering tests
# ---------------------------------------------------------------------------


class TestFiltering:
    """Only blocking and needs_confirmation items generate questions;
    non_blocking_assumption items are skipped."""

    def test_blocking_items_generate_questions(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("characteristic_length", "blocking_for_case_generation"),
        ]
        questions = planner.plan(ambiguities)
        assert len(questions) == 1
        assert questions[0].severity == "blocking_for_case_generation"

    def test_needs_confirmation_items_generate_questions(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("domain_size", "needs_confirmation"),
        ]
        questions = planner.plan(ambiguities)
        assert len(questions) == 1
        assert questions[0].severity == "needs_confirmation"

    def test_non_blocking_assumption_items_are_skipped(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("solver", "non_blocking_assumption"),
            _amb("time_step", "non_blocking_assumption"),
            _amb("numerics_schemes", "non_blocking_assumption"),
        ]
        questions = planner.plan(ambiguities)
        assert len(questions) == 0

    def test_mixed_severities_filters_non_blocking(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("solver", "non_blocking_assumption"),
            _amb("characteristic_length", "blocking_for_case_generation"),
            _amb("time_step", "non_blocking_assumption"),
            _amb("domain_size", "needs_confirmation"),
        ]
        questions = planner.plan(ambiguities)
        assert len(questions) == 2
        severities = {q.severity for q in questions}
        assert "non_blocking_assumption" not in severities
        assert "blocking_for_case_generation" in severities
        assert "needs_confirmation" in severities


# ---------------------------------------------------------------------------
# Max-questions-per-turn tests
# ---------------------------------------------------------------------------


class TestMaxQuestionsPerTurn:
    """At most MAX_QUESTIONS_PER_TURN (3) questions are returned."""

    def test_max_three_questions(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("characteristic_length", "blocking_for_case_generation"),
            _amb("heat_flux_role", "blocking_for_case_generation"),
            _amb("oscillation_parameters", "blocking_for_case_generation"),
            _amb("density_stratification_formula", "blocking_for_case_generation"),
            _amb("froude_number_definition", "blocking_for_case_generation"),
        ]
        questions = planner.plan(ambiguities)
        assert len(questions) == 3

    def test_exactly_three_returned_when_more_available(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("domain_size", "needs_confirmation"),
            _amb("mesh_resolution", "needs_confirmation"),
            _amb("turbulence_model", "needs_confirmation"),
            _amb("inlet_implementation", "needs_confirmation"),
        ]
        questions = planner.plan(ambiguities)
        assert len(questions) == 3

    def test_fewer_than_three_returns_all(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("characteristic_length", "blocking_for_case_generation"),
            _amb("domain_size", "needs_confirmation"),
        ]
        questions = planner.plan(ambiguities)
        assert len(questions) == 2

    def test_empty_input_returns_empty(self) -> None:
        planner = ClarificationPlanner()
        questions = planner.plan([])
        assert questions == []


# ---------------------------------------------------------------------------
# Priority ordering tests
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    """Blocking items come before needs_confirmation; within the same
    severity band, geometry > solver_physics > boundary_condition >
    postprocess_capability > numerical_setting > other."""

    def test_blocking_before_needs_confirmation(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("domain_size", "needs_confirmation"),
            _amb("characteristic_length", "blocking_for_case_generation"),
            _amb("turbulence_model", "needs_confirmation"),
            _amb("heat_flux_role", "blocking_for_case_generation"),
        ]
        questions = planner.plan(ambiguities)
        first_nc_idx = next(
            (
                i
                for i, q in enumerate(questions)
                if q.severity == "needs_confirmation"
            ),
            len(questions),
        )
        for i, q in enumerate(questions):
            if q.severity == "blocking_for_case_generation":
                assert i < first_nc_idx, (
                    f"Blocking question at index {i} should come before "
                    f"needs_confirmation at index {first_nc_idx}"
                )

    def test_geometry_before_solver_physics_within_blocking(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("turbulence_model", "blocking_for_case_generation"),
            _amb("characteristic_length", "blocking_for_case_generation"),
        ]
        questions = planner.plan(ambiguities)
        assert questions[0].field == "characteristic_length"
        assert questions[1].field == "turbulence_model"

    def test_boundary_condition_after_solver_physics(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("inlet_implementation", "blocking_for_case_generation"),
            _amb("turbulence_model", "blocking_for_case_generation"),
            _amb("characteristic_length", "blocking_for_case_generation"),
        ]
        questions = planner.plan(ambiguities)
        assert _categorise_field(questions[0].field) == "geometry"
        assert _categorise_field(questions[1].field) == "solver_physics"
        assert _categorise_field(questions[2].field) == "boundary_condition"

    def test_postprocess_comes_after_boundary_in_needs_conf(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("drag_coefficient", "needs_confirmation"),  # postprocess
            _amb("outlet_boundary_mapping", "needs_confirmation"),  # bc
            _amb("domain_size", "needs_confirmation"),  # geometry
            _amb("turbulence_model", "needs_confirmation"),  # solver_physics
        ]
        questions = planner.plan(ambiguities)
        categories = [_categorise_field(q.field) for q in questions]
        assert categories == [
            "geometry",
            "solver_physics",
            "boundary_condition",
        ]

    def test_categorisation_helpers(self) -> None:
        """Verify _categorise_field maps fields to expected categories."""
        assert _categorise_field("characteristic_length") == "geometry"
        assert _categorise_field("domain_size") == "geometry"
        assert _categorise_field("oscillation_parameters") == "geometry"
        assert _categorise_field("turbulence_model") == "solver_physics"
        assert _categorise_field("density_stratification_formula") == "solver_physics"
        assert _categorise_field("froude_number_definition") == "solver_physics"
        assert _categorise_field("inlet_implementation") == "boundary_condition"
        assert _categorise_field("outlet_boundary_mapping") == "boundary_condition"
        assert _categorise_field("heat_flux_role") == "boundary_condition"
        assert _categorise_field("drag_coefficient") == "postprocess_capability"
        assert _categorise_field("time_step") == "numerical_setting"
        assert _categorise_field("unknown_field_xyz") == "other"


# ---------------------------------------------------------------------------
# Question content tests
# ---------------------------------------------------------------------------


class TestQuestionContent:
    """Verify question text and reason are propagated correctly."""

    def test_suggested_question_used(self) -> None:
        planner = ClarificationPlanner()
        amb = _amb(
            "characteristic_length",
            "blocking_for_case_generation",
            issue="几何特征尺寸未知",
            reason="无法生成网格",
            suggested_question="请确认圆柱直径 D",
        )
        questions = planner.plan([amb])
        assert questions[0].question == "请确认圆柱直径 D"
        assert questions[0].reason == "无法生成网格"

    def test_fallback_question_when_no_suggested(self) -> None:
        planner = ClarificationPlanner()
        amb = _amb(
            "mystery_field",
            "needs_confirmation",
            issue="something is unclear",
            reason="some reason",
        )
        questions = planner.plan([amb])
        assert "something is unclear" in questions[0].question

    def test_recommended_answer_set(self) -> None:
        planner = ClarificationPlanner()
        amb = _amb(
            "domain_size",
            "needs_confirmation",
            recommended_default="20D x 10D",
        )
        questions = planner.plan([amb])
        assert questions[0].recommended_answer is not None
        assert "20D x 10D" in questions[0].recommended_answer["default"]


# ---------------------------------------------------------------------------
# Question ID assignment
# ---------------------------------------------------------------------------


class TestQuestionIds:
    """Each returned question gets a unique question_id, assigned in
    priority order."""

    def test_unique_ids(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("characteristic_length", "blocking_for_case_generation"),
            _amb("domain_size", "needs_confirmation"),
            _amb("turbulence_model", "needs_confirmation"),
        ]
        questions = planner.plan(ambiguities)
        ids = [q.question_id for q in questions]
        assert len(ids) == len(set(ids)), "question_ids must be unique"

    def test_ids_assigned_in_priority_order(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("domain_size", "needs_confirmation"),
            _amb("turbulence_model", "needs_confirmation"),
            _amb("characteristic_length", "blocking_for_case_generation"),
        ]
        questions = planner.plan(ambiguities)
        assert questions[0].field == "characteristic_length"
        assert questions[0].question_id == "q-001"

    def test_ids_increment_across_calls(self) -> None:
        """Counter persists across multiple plan() calls on same planner."""
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("characteristic_length", "blocking_for_case_generation"),
        ]
        q1 = planner.plan(ambiguities)
        q2 = planner.plan(ambiguities)
        assert q1[0].question_id == "q-001"
        assert q2[0].question_id == "q-002"


# ---------------------------------------------------------------------------
# should_clarify tests
# ---------------------------------------------------------------------------


class TestShouldClarify:
    """should_clarify returns True when blocking questions exist."""

    def test_returns_true_for_blocking(self) -> None:
        planner = ClarificationPlanner()
        questions = [
            ClarificationQuestion(
                question_id="q-001",
                field="characteristic_length",
                question="?",
                reason="need dims",
                severity="blocking_for_case_generation",
            ),
        ]
        assert planner.should_clarify(questions) is True

    def test_returns_false_for_only_needs_confirmation(self) -> None:
        planner = ClarificationPlanner()
        questions = [
            ClarificationQuestion(
                question_id="q-001",
                field="domain_size",
                question="?",
                reason="domain",
                severity="needs_confirmation",
            ),
        ]
        assert planner.should_clarify(questions) is False

    def test_returns_false_for_empty_list(self) -> None:
        planner = ClarificationPlanner()
        assert planner.should_clarify([]) is False

    def test_returns_true_when_mixed(self) -> None:
        planner = ClarificationPlanner()
        questions = [
            ClarificationQuestion(
                question_id="q-001",
                field="domain_size",
                question="?",
                reason="domain",
                severity="needs_confirmation",
            ),
            ClarificationQuestion(
                question_id="q-002",
                field="characteristic_length",
                question="?",
                reason="dims",
                severity="blocking_for_case_generation",
            ),
        ]
        assert planner.should_clarify(questions) is True


# ---------------------------------------------------------------------------
# Blocking issues (external dict) integration
# ---------------------------------------------------------------------------


class TestBlockingIssues:
    """blocking_issues dicts are converted to blocking questions."""

    def test_blocking_issues_included(self) -> None:
        planner = ClarificationPlanner()
        issues = [
            {
                "issue": "Missing capability: LES",
                "field": "solver_capability",
                "reason": "LES not available",
            },
        ]
        questions = planner.plan([], blocking_issues=issues)
        assert len(questions) == 1
        assert questions[0].severity == "blocking_for_case_generation"
        assert "Missing capability" in questions[0].question

    def test_blocking_issues_prioritised_with_ambiguities(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("domain_size", "needs_confirmation"),
        ]
        issues = [
            {
                "issue": "Capability gap",
                "field": "turbulence_model",
                "reason": "no turb model",
            },
        ]
        questions = planner.plan(ambiguities, blocking_issues=issues)
        assert len(questions) == 2
        assert questions[0].severity == "blocking_for_case_generation"
        assert questions[1].severity == "needs_confirmation"

    def test_blocking_issues_count_toward_max(self) -> None:
        planner = ClarificationPlanner()
        ambiguities = [
            _amb("characteristic_length", "blocking_for_case_generation"),
            _amb("heat_flux_role", "blocking_for_case_generation"),
        ]
        issues = [
            {"issue": "A", "field": "field_a", "reason": "a"},
            {"issue": "B", "field": "field_b", "reason": "b"},
        ]
        questions = planner.plan(ambiguities, blocking_issues=issues)
        assert len(questions) == 3


# ---------------------------------------------------------------------------
# 5 canonical study cases
# ---------------------------------------------------------------------------


def _cylinder_study() -> StudyIntent:
    """Flow past a circular cylinder at Re=3900 — diameter missing."""
    return _make_study(
        study_id="cylinder_001",
        title="Flow past a circular cylinder",
        study_type="cylinder",
        geometry={"type": "cylinder"},
        physical_models={"turbulent": True},
        observables=[
            ObservableSpec(
                observable_id="drag_coefficient",
                display_name="Drag Coefficient",
                category="force",
            ),
        ],
        known_parameters=[
            ExtractedParameter(
                canonical_id="reynolds_number",
                display_name="Re",
                value=3900,
                dimensionless=True,
                source_text="Re=3900",
                source="user_provided",
            ),
        ],
    )


def _backward_step_study() -> StudyIntent:
    """Backward-facing step — well-specified (no blocking items)."""
    return _make_study(
        study_id="bfs_001",
        title="Backward-facing step flow",
        study_type="backward_facing_step",
        geometry={"type": "backward_facing_step"},
        physical_models={"turbulent": True},
        known_parameters=[
            ExtractedParameter(
                canonical_id="step_height",
                display_name="H",
                value=0.05,
                unit="m",
                source_text="H=0.05",
                source="user_provided",
            ),
            ExtractedParameter(
                canonical_id="reynolds_number",
                display_name="Re",
                value=5000,
                dimensionless=True,
                source_text="Re=5000",
                source="user_provided",
            ),
            ExtractedParameter(
                canonical_id="turbulence_model",
                display_name="Turbulence Model",
                value="LES",
                source_text="LES",
                source="user_provided",
            ),
        ],
    )


def _pipe_study() -> StudyIntent:
    """Pipe flow — domain/turbulence specified; inlet/outlet BC need confirmation."""
    return _make_study(
        study_id="pipe_001",
        title="Turbulent pipe flow",
        study_type="pipe",
        geometry={"type": "pipe"},
        physical_models={"turbulent": True},
        initial_conditions=[{"type": "fully_developed"}],
        boundary_conditions=[
            {"type": "advective", "patch": "outlet"},
        ],
        known_parameters=[
            ExtractedParameter(
                canonical_id="pipe_diameter",
                display_name="D",
                value=0.1,
                unit="m",
                source_text="D=0.1m",
                source="user_provided",
            ),
            ExtractedParameter(
                canonical_id="domain_length",
                display_name="L",
                value=5.0,
                unit="m",
                source_text="L=5m",
                source="user_provided",
            ),
            ExtractedParameter(
                canonical_id="turbulence_model",
                display_name="Turbulence Model",
                value="k-omega SST",
                source_text="k-omega SST",
                source="user_provided",
            ),
        ],
    )


def _cavity_study() -> StudyIntent:
    """Lid-driven cavity — minimal description (needs_confirmation items)."""
    return _make_study(
        study_id="cavity_001",
        title="Lid-driven cavity flow",
        study_type="cavity",
        geometry={"type": "cavity"},
        known_parameters=[],
    )


def _airfoil_study() -> StudyIntent:
    """Airfoil with heat flux observable — heat_flux_role is blocking."""
    return _make_study(
        study_id="airfoil_001",
        title="Airfoil heat transfer study",
        study_type="airfoil",
        geometry={"type": "airfoil"},
        physical_models={"thermal": True},
        observables=[
            ObservableSpec(
                observable_id="heat_flux",
                display_name="Heat Flux",
                category="heat_flux",
            ),
        ],
        known_parameters=[],
    )


CANONICAL_CASES = [
    ("cylinder", _cylinder_study),
    ("backward_facing_step", _backward_step_study),
    ("pipe", _pipe_study),
    ("cavity", _cavity_study),
    ("airfoil", _airfoil_study),
]


class TestCanonicalCases:
    """Validate the planner against real ambiguity reports from the
    AmbiguityDetector for all five canonical CFD study cases."""

    @pytest.mark.parametrize("case_name,study_factory", CANONICAL_CASES)
    def test_max_three_questions_per_case(
        self, case_name: str, study_factory: object
    ) -> None:
        study = study_factory()  # type: ignore[operator]
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        assert len(questions) <= 3, (
            f"{case_name}: expected <= 3 questions, got {len(questions)}"
        )

    @pytest.mark.parametrize("case_name,study_factory", CANONICAL_CASES)
    def test_no_non_blocking_in_output(
        self, case_name: str, study_factory: object
    ) -> None:
        study = study_factory()  # type: ignore[operator]
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        for q in questions:
            assert q.severity in (
                "blocking_for_case_generation",
                "needs_confirmation",
            ), f"{case_name}: unexpected severity {q.severity}"

    @pytest.mark.parametrize("case_name,study_factory", CANONICAL_CASES)
    def test_blocking_precedes_needs_confirmation(
        self, case_name: str, study_factory: object
    ) -> None:
        study = study_factory()  # type: ignore[operator]
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        first_nc = next(
            (
                i
                for i, q in enumerate(questions)
                if q.severity == "needs_confirmation"
            ),
            len(questions),
        )
        for i, q in enumerate(questions):
            if q.severity == "blocking_for_case_generation":
                assert i < first_nc, (
                    f"{case_name}: blocking question at {i} should come before "
                    f"needs_confirmation at {first_nc}"
                )

    @pytest.mark.parametrize("case_name,study_factory", CANONICAL_CASES)
    def test_questions_have_unique_ids(
        self, case_name: str, study_factory: object
    ) -> None:
        study = study_factory()  # type: ignore[operator]
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        ids = [q.question_id for q in questions]
        assert len(ids) == len(set(ids)), (
            f"{case_name}: duplicate question_ids found"
        )

    @pytest.mark.parametrize("case_name,study_factory", CANONICAL_CASES)
    def test_all_questions_have_required_fields(
        self, case_name: str, study_factory: object
    ) -> None:
        study = study_factory()  # type: ignore[operator]
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        for q in questions:
            assert q.question_id, f"{case_name}: empty question_id"
            assert q.field, f"{case_name}: empty field"
            assert q.question, f"{case_name}: empty question text"
            assert q.reason, f"{case_name}: empty reason"

    @pytest.mark.parametrize("case_name,study_factory", CANONICAL_CASES)
    def test_should_clarify_consistency(
        self, case_name: str, study_factory: object
    ) -> None:
        study = study_factory()  # type: ignore[operator]
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        has_blocking = any(
            q.severity == "blocking_for_case_generation" for q in questions
        )
        assert planner.should_clarify(questions) is has_blocking, (
            f"{case_name}: should_clarify mismatch"
        )

    # ---- Individual case expectations ----

    def test_cylinder_has_geometry_blocking_first(self) -> None:
        """Cylinder at Re=3900 with no diameter => characteristic_length
        is blocking and should be asked first."""
        study = _cylinder_study()
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        assert len(questions) >= 1
        assert questions[0].field == "characteristic_length"
        assert questions[0].severity == "blocking_for_case_generation"

    def test_airfoil_heat_flux_is_blocking(self) -> None:
        """Airfoil with heat_flux observable => heat_flux_role is blocking."""
        study = _airfoil_study()
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        blocking_fields = {
            q.field
            for q in questions
            if q.severity == "blocking_for_case_generation"
        }
        assert "heat_flux_role" in blocking_fields

    def test_cavity_returns_needs_confirmation_questions(self) -> None:
        """Cavity with no parameters => needs_confirmation items (domain, mesh)."""
        study = _cavity_study()
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        assert len(questions) >= 1
        assert all(q.severity == "needs_confirmation" for q in questions)
        fields = {q.field for q in questions}
        assert "domain_size" in fields

    def test_backward_step_well_specified_no_blocking(self) -> None:
        """Well-specified BFS should have no blocking questions."""
        study = _backward_step_study()
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        blocking = [
            q
            for q in questions
            if q.severity == "blocking_for_case_generation"
        ]
        assert len(blocking) == 0

    def test_pipe_has_inlet_bc_question(self) -> None:
        """Pipe with fully_developed IC => inlet_implementation present."""
        study = _pipe_study()
        detector = AmbiguityDetector()
        ambiguities = detector.detect(study)
        planner = ClarificationPlanner()
        questions = planner.plan(ambiguities)
        fields = {q.field for q in questions}
        assert "inlet_implementation" in fields
