import pytest
from pydantic import ValidationError

from fluid_scientist.experiment_planning.models import (
    CavityExperimentPlan,
    CustomExperimentPlan,
    CylinderExperimentPlan,
    ExperimentPlan,
    PipeExperimentPlan,
)


def common_fields(experiment_type: str) -> dict[str, object]:
    return {
        "experiment_name": "Reference CFD experiment",
        "experiment_type": experiment_type,
        "objective": "Quantify the resolved flow response.",
        "rationale": "This benchmark has a deterministic reference solution.",
        "assumptions": ("Newtonian incompressible fluid",),
        "limitations": ("Restricted to the stated parameter range",),
        "requested_outputs": ("residuals",),
        "convergence_targets": {
            "residual_tolerance": 1e-6,
            "mass_imbalance_percent": 0.1,
        },
    }


def pipe_plan() -> dict[str, object]:
    return {
        **common_fields("laminar_pipe"),
        "requested_outputs": ("pressure_drop", "mass_imbalance"),
        "case": {
            "diameter_m": 0.02,
            "length_m": 1.0,
            "mean_velocity_m_s": 0.05,
            "kinematic_viscosity_m2_s": 1e-6,
            "density_kg_m3": 998.2,
            "axial_cells": 80,
            "radial_cells": 10,
        },
        "parameter_sweeps": (
            {"parameter": "mean_velocity_m_s", "values": (0.02, 0.04, 0.06)},
        ),
    }


def cylinder_plan() -> dict[str, object]:
    return {
        **common_fields("cylinder_flow"),
        "requested_outputs": ("drag_coefficient", "lift_coefficient", "strouhal_number"),
        "case": {
            "diameter_m": 0.1,
            "reynolds_number": 100.0,
            "domain_upstream_diameters": 10.0,
            "domain_downstream_diameters": 20.0,
            "domain_transverse_diameters": 10.0,
            "cells_radial": 32,
            "cells_wake": 120,
            "end_time_s": 20.0,
            "time_step_s": 0.002,
            "density_kg_m3": 1.0,
            "kinematic_viscosity_m2_s": 0.001,
            "mean_velocity_m_s": 1.0,
        },
    }


def cavity_plan() -> dict[str, object]:
    return {
        **common_fields("lid_driven_cavity"),
        "requested_outputs": ("velocity_probes", "residuals"),
        "case": {
            "side_length_m": 1.0,
            "lid_velocity_m_s": 1.0,
            "kinematic_viscosity_m2_s": 0.001,
            "density_kg_m3": 1.0,
            "cells_per_side": 64,
            "end_time_s": 10.0,
        },
    }


def custom_plan() -> dict[str, object]:
    return {
        **common_fields("custom_openfoam"),
        "requested_outputs": ("area_averaged_outlet_velocity",),
        "case": {
            "geometry": "A three-dimensional diffuser with a circular inlet.",
            "boundary_conditions": ("fixed inlet velocity", "fixed outlet pressure"),
            "mesh_strategy": "Hex-dominant mesh with wall and outlet refinement.",
            "run_strategy": "Steady incompressible solve followed by metric extraction.",
        },
    }


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        (pipe_plan(), PipeExperimentPlan),
        (cylinder_plan(), CylinderExperimentPlan),
        (cavity_plan(), CavityExperimentPlan),
        (custom_plan(), CustomExperimentPlan),
    ],
)
def test_discriminated_union_routes_to_matching_plan(
    payload: dict[str, object], expected_type: type[object]
) -> None:
    plan = ExperimentPlan.model_validate(payload)

    assert isinstance(plan.root, expected_type)
    assert plan.model_dump(mode="json")["experiment_type"] == payload["experiment_type"]


def test_all_contracts_reject_extra_fields() -> None:
    payload = pipe_plan() | {"solver_backdoor": "shell command"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExperimentPlan.model_validate(payload)


def test_strict_contract_rejects_numeric_strings() -> None:
    payload = pipe_plan()
    payload["case"] = {**payload["case"], "axial_cells": "80"}  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="valid integer"):
        ExperimentPlan.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("diameter_m", 0.0),
        ("axial_cells", 9),
        ("radial_cells", 501),
        ("mean_velocity_m_s", 1.0),
    ],
)
def test_pipe_rejects_invalid_or_non_laminar_bounds(field: str, value: object) -> None:
    payload = pipe_plan()
    payload["case"] = {**payload["case"], field: value}  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(payload)


def test_cylinder_rejects_reynolds_numbers_above_first_release_limit() -> None:
    payload = cylinder_plan()
    payload["case"] = {**payload["case"], "reynolds_number": 301.0}  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(payload)


def test_cylinder_enforces_reynolds_consistency() -> None:
    payload = cylinder_plan()
    payload["case"] = {**payload["case"], "mean_velocity_m_s": 2.0}  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="Reynolds number"):
        ExperimentPlan.model_validate(payload)


def test_cylinder_cannot_accept_pipe_payload() -> None:
    payload = pipe_plan() | {"experiment_type": "cylinder_flow"}

    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(payload)


def test_custom_plan_cannot_accept_builtin_case_payload() -> None:
    payload = custom_plan()
    payload["case"] = {**payload["case"], "diameter_m": 0.1}  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExperimentPlan.model_validate(payload)


def test_common_nonempty_fields_and_bounded_sweeps_are_enforced() -> None:
    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(pipe_plan() | {"limitations": ()})

    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(
            pipe_plan()
            | {
                "parameter_sweeps": (
                    {"parameter": "mean_velocity_m_s", "values": tuple(range(21))},
                )
            }
        )


def test_pipe_sweep_cannot_leave_laminar_regime() -> None:
    payload = pipe_plan() | {
        "parameter_sweeps": (
            {"parameter": "mean_velocity_m_s", "values": (0.05, 0.2)},
        )
    }

    with pytest.raises(ValidationError, match="laminar regime"):
        ExperimentPlan.model_validate(payload)


def test_cylinder_reynolds_sweep_respects_case_bounds() -> None:
    payload = cylinder_plan() | {
        "parameter_sweeps": (
            {"parameter": "reynolds_number", "values": (100.0, 301.0)},
        )
    }

    with pytest.raises(ValidationError, match="case bounds"):
        ExperimentPlan.model_validate(payload)


def test_cavity_viscosity_sweep_respects_case_bounds() -> None:
    payload = cavity_plan() | {
        "parameter_sweeps": (
            {"parameter": "kinematic_viscosity_m2_s", "values": (0.001, 1.1)},
        )
    }

    with pytest.raises(ValidationError, match="case bounds"):
        ExperimentPlan.model_validate(payload)


def test_cavity_requires_positive_geometry_and_resolution() -> None:
    payload = cavity_plan()
    payload["case"] = {**payload["case"], "cells_per_side": 7}  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(payload)
