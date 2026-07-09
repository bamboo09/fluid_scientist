"""Tests for the :class:`DraftValidator`."""

from __future__ import annotations

from fluid_scientist.draft.models import (
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
)
from fluid_scientist.draft.validator import DraftValidator


def _valid_draft(**overrides) -> ExperimentDraft:
    """Return a draft that passes every validation check."""
    base = dict(
        draft_id="draft_001",
        session_id="session_001",
        status=DraftStatus.DRAFT,
        geometry={"type": "cylinder", "characteristic_length": 0.1},
        boundary_conditions={
            "inlet": {"type": "inlet", "velocity": 1.0},
            "outlet": {"type": "outlet", "pressure": 0.0},
            "wall": {"type": "wall"},
        },
        solver={"name": "simpleFoam"},
        mesh={"strategy": "structured"},
        control_parameters=[
            DraftParameter(
                parameter_id="reynolds_number",
                display_name="Reynolds Number",
                value=3900,
                source=ParameterSource.USER_PROVIDED,
            ),
        ],
        requested_outputs=[{"observable_id": "drag"}],
        measurement_plan={"sampling": "time_series"},
        analysis_goals=["Compute drag coefficient"],
        assumptions=[],
        blocking_issues=[],
    )
    base.update(overrides)
    return ExperimentDraft(**base)


class TestDraftValidatorValid:
    def test_valid_draft_has_no_blocking_issues(self) -> None:
        result = DraftValidator().validate(_valid_draft())
        assert result.valid is True
        assert result.blocking_issues == []

    def test_valid_draft_may_have_no_warnings(self) -> None:
        result = DraftValidator().validate(_valid_draft())
        assert result.warnings == []


# ---------------------------------------------------------------------------
# Blocking checks
# ---------------------------------------------------------------------------


class TestCriticalParameterMissingValue:
    def test_user_provided_without_value_is_blocking(self) -> None:
        draft = _valid_draft(
            control_parameters=[
                DraftParameter(
                    parameter_id="re",
                    display_name="Re",
                    value=None,
                    source=ParameterSource.USER_PROVIDED,
                ),
            ],
        )
        result = DraftValidator().validate(draft)
        assert result.valid is False
        assert any(
            issue["check"] == "critical_parameter_missing_value"
            for issue in result.blocking_issues
        )

    def test_derived_without_value_is_blocking(self) -> None:
        draft = _valid_draft(
            control_parameters=[
                DraftParameter(
                    parameter_id="domain_length",
                    display_name="Domain Length",
                    value=None,
                    source=ParameterSource.DERIVED,
                ),
            ],
        )
        result = DraftValidator().validate(draft)
        assert result.valid is False

    def test_assumption_without_value_is_blocking(self) -> None:
        draft = _valid_draft(
            control_parameters=[
                DraftParameter(
                    parameter_id="wall_roughness",
                    display_name="Wall Roughness",
                    value=None,
                    source=ParameterSource.ASSUMPTION,
                ),
            ],
        )
        result = DraftValidator().validate(draft)
        assert result.valid is False


class TestUnknownRequiredHasValue:
    def test_unknown_required_with_value_is_blocking(self) -> None:
        draft = _valid_draft(
            control_parameters=[
                DraftParameter(
                    parameter_id="cylinder_diameter",
                    display_name="D",
                    value=0.1,
                    source=ParameterSource.UNKNOWN_REQUIRED,
                ),
            ],
        )
        result = DraftValidator().validate(draft)
        assert result.valid is False
        assert any(
            issue["check"] == "unknown_required_has_value"
            for issue in result.blocking_issues
        )

    def test_unknown_required_without_value_is_ok(self) -> None:
        draft = _valid_draft(
            control_parameters=[
                DraftParameter(
                    parameter_id="cylinder_diameter",
                    display_name="D",
                    value=None,
                    source=ParameterSource.UNKNOWN_REQUIRED,
                ),
            ],
        )
        result = DraftValidator().validate(draft)
        assert all(
            issue["check"] != "unknown_required_has_value"
            for issue in result.blocking_issues
        )


class TestGeometryChecks:
    def test_empty_geometry_is_blocking(self) -> None:
        result = DraftValidator().validate(_valid_draft(geometry={}))
        assert result.valid is False
        assert any(
            issue["check"] == "geometry_empty" for issue in result.blocking_issues
        )

    def test_missing_type_is_blocking(self) -> None:
        result = DraftValidator().validate(
            _valid_draft(geometry={"characteristic_length": 0.1})
        )
        assert result.valid is False
        assert any(
            issue["check"] == "geometry_missing_type"
            for issue in result.blocking_issues
        )

    def test_missing_characteristic_dimension_is_blocking(self) -> None:
        result = DraftValidator().validate(
            _valid_draft(geometry={"type": "cylinder"})
        )
        assert result.valid is False
        assert any(
            issue["check"] == "geometry_missing_characteristic_dimension"
            for issue in result.blocking_issues
        )

    def test_diameter_alias_satisfies_dimension_check(self) -> None:
        result = DraftValidator().validate(
            _valid_draft(geometry={"type": "cylinder", "D": 0.1})
        )
        assert all(
            issue["check"] != "geometry_missing_characteristic_dimension"
            for issue in result.blocking_issues
        )


class TestBoundaryConditionChecks:
    def test_empty_boundary_conditions_is_blocking(self) -> None:
        result = DraftValidator().validate(_valid_draft(boundary_conditions={}))
        assert result.valid is False
        assert any(
            issue["check"] == "boundary_conditions_empty"
            for issue in result.blocking_issues
        )

    def test_missing_inlet_is_blocking(self) -> None:
        result = DraftValidator().validate(
            _valid_draft(
                boundary_conditions={
                    "outlet": {"type": "outlet"},
                    "wall": {"type": "wall"},
                }
            )
        )
        assert result.valid is False
        assert any(
            issue["check"] == "boundary_condition_missing_inlet"
            for issue in result.blocking_issues
        )

    def test_missing_outlet_is_blocking(self) -> None:
        result = DraftValidator().validate(
            _valid_draft(
                boundary_conditions={
                    "inlet": {"type": "inlet"},
                    "wall": {"type": "wall"},
                }
            )
        )
        assert result.valid is False
        assert any(
            issue["check"] == "boundary_condition_missing_outlet"
            for issue in result.blocking_issues
        )

    def test_nested_type_field_is_recognised(self) -> None:
        """The validator should detect inlet/outlet inside nested entries."""
        result = DraftValidator().validate(
            _valid_draft(
                boundary_conditions={
                    "bc_0": {"type": "inlet", "velocity": 1.0},
                    "bc_1": {"type": "outlet", "pressure": 0.0},
                }
            )
        )
        assert all(
            "boundary_condition_missing" not in issue["check"]
            for issue in result.blocking_issues
        )


class TestPreExistingBlockingIssues:
    def test_blocking_issues_list_is_blocking(self) -> None:
        draft = _valid_draft(
            blocking_issues=[
                {"field": "cylinder_diameter", "issue": "D not specified"}
            ]
        )
        result = DraftValidator().validate(draft)
        assert result.valid is False
        assert any(
            issue["check"] == "pre_existing_blocking_issue"
            for issue in result.blocking_issues
        )


class TestRejectedAssumptions:
    def test_rejected_assumption_status_is_blocking(self) -> None:
        draft = _valid_draft(
            assumptions=[
                {"field": "wall_roughness", "status": "rejected"},
            ]
        )
        result = DraftValidator().validate(draft)
        assert result.valid is False
        assert any(
            issue["check"] == "rejected_assumption_present"
            for issue in result.blocking_issues
        )

    def test_rejected_assumption_flag_is_blocking(self) -> None:
        draft = _valid_draft(
            assumptions=[
                {"name": "wall_roughness", "rejected": True},
            ]
        )
        result = DraftValidator().validate(draft)
        assert result.valid is False

    def test_accepted_assumption_is_ok(self) -> None:
        draft = _valid_draft(
            assumptions=[
                {"field": "wall_roughness", "status": "accepted"},
            ]
        )
        result = DraftValidator().validate(draft)
        assert all(
            issue["check"] != "rejected_assumption_present"
            for issue in result.blocking_issues
        )


# ---------------------------------------------------------------------------
# Warning checks
# ---------------------------------------------------------------------------


class TestWarningChecks:
    def test_missing_solver_is_warning(self) -> None:
        result = DraftValidator().validate(_valid_draft(solver={}))
        assert result.valid is True
        assert any("Solver is not specified" in w for w in result.warnings)

    def test_missing_mesh_is_warning(self) -> None:
        result = DraftValidator().validate(_valid_draft(mesh={}))
        assert result.valid is True
        assert any("Mesh strategy" in w for w in result.warnings)

    def test_outputs_without_measurement_plan_is_warning(self) -> None:
        result = DraftValidator().validate(_valid_draft(measurement_plan={}))
        assert result.valid is True
        assert any("measurement plan" in w for w in result.warnings)

    def test_no_outputs_no_measurement_plan_is_not_warning(self) -> None:
        result = DraftValidator().validate(
            _valid_draft(requested_outputs=[], measurement_plan={})
        )
        assert not any("measurement plan" in w for w in result.warnings)

    def test_empty_analysis_goals_is_warning(self) -> None:
        result = DraftValidator().validate(_valid_draft(analysis_goals=[]))
        assert result.valid is True
        assert any("Analysis goals" in w for w in result.warnings)

    def test_warnings_do_not_make_draft_invalid(self) -> None:
        draft = _valid_draft(
            solver={}, mesh={}, analysis_goals=[], measurement_plan={}
        )
        result = DraftValidator().validate(draft)
        # Many warnings but no blocking issues -> still valid.
        assert result.valid is True
        assert len(result.warnings) >= 3


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


class TestValidationAggregation:
    def test_multiple_blocking_issues_collected(self) -> None:
        draft = _valid_draft(
            geometry={},
            boundary_conditions={},
            control_parameters=[
                DraftParameter(
                    parameter_id="re",
                    display_name="Re",
                    value=None,
                    source=ParameterSource.USER_PROVIDED,
                ),
            ],
        )
        result = DraftValidator().validate(draft)
        assert result.valid is False
        # At least: geometry empty, bc empty, critical param missing.
        assert len(result.blocking_issues) >= 3
