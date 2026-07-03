import json

import pytest
from pydantic import ValidationError

import fluid_scientist.experiment_planning.models as planning_models
from fluid_scientist.experiment_planning.models import (
    CavityExperimentPlan,
    ConvergenceTargets,
    CustomExperimentPlan,
    CylinderExperimentPlan,
    CylinderFlowCase,
    ExperimentPlan,
    PipeExperimentPlan,
    PipeOutput,
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


def cylinder_case() -> dict[str, object]:
    return {
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


@pytest.mark.parametrize("payload", [pipe_plan(), cylinder_plan(), cavity_plan(), custom_plan()])
def test_provider_style_json_list_payloads_validate(payload: dict[str, object]) -> None:
    provider_payload = json.loads(json.dumps(payload))

    plan = ExperimentPlan.model_validate(provider_payload)

    assert isinstance(plan.root.assumptions, tuple)
    assert isinstance(plan.root.limitations, tuple)
    assert isinstance(plan.root.requested_outputs, tuple)
    if plan.root.experiment_type == "custom_openfoam":
        assert isinstance(plan.root.case.boundary_conditions, tuple)
    else:
        assert isinstance(plan.root.parameter_sweeps, tuple)


@pytest.mark.parametrize("payload", [pipe_plan(), cylinder_plan(), cavity_plan(), custom_plan()])
def test_json_mode_dump_round_trips_through_model_validate(
    payload: dict[str, object],
) -> None:
    original = ExperimentPlan.model_validate(payload)

    round_tripped = ExperimentPlan.model_validate(original.model_dump(mode="json"))

    assert round_tripped == original


def test_all_contracts_reject_extra_fields() -> None:
    payload = pipe_plan() | {"solver_backdoor": "shell command"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExperimentPlan.model_validate(payload)


def test_strict_contract_rejects_numeric_strings() -> None:
    payload = pipe_plan()
    payload["case"] = {**payload["case"], "axial_cells": "80"}  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="valid integer"):
        ExperimentPlan.model_validate(payload)


def test_narrative_fields_are_stripped_and_enum_outputs_are_preserved() -> None:
    payload = pipe_plan() | {
        "experiment_name": "  Pipe benchmark  ",
        "objective": "  Quantify pressure losses accurately.  ",
        "rationale": "  This benchmark has an analytical reference.  ",
        "assumptions": ("  Newtonian fluid  ",),
        "limitations": ("  Laminar regime only  ",),
        "requested_outputs": ("  pressure_drop  ", "  mass_imbalance  "),
    }

    plan = ExperimentPlan.model_validate(payload).root

    assert plan.experiment_name == "Pipe benchmark"
    assert plan.objective == "Quantify pressure losses accurately."
    assert plan.rationale == "This benchmark has an analytical reference."
    assert plan.assumptions == ("Newtonian fluid",)
    assert plan.limitations == ("Laminar regime only",)
    assert plan.requested_outputs[0] is PipeOutput.PRESSURE_DROP


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("experiment_name", "   "),
        ("objective", "          "),
        ("rationale", "          "),
        ("assumptions", ("   ",)),
        ("limitations", ("   ",)),
        ("requested_outputs", ("   ",)),
    ],
)
def test_common_narrative_fields_reject_whitespace_only(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(pipe_plan() | {field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("geometry", "          "),
        ("boundary_conditions", ("   ", "fixed outlet pressure")),
        ("mesh_strategy", "          "),
        ("run_strategy", "          "),
    ],
)
def test_custom_narrative_fields_reject_whitespace_only(
    field: str, value: object
) -> None:
    payload = custom_plan()
    payload["case"] = {**payload["case"], field: value}  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(payload)


def test_custom_narrative_fields_are_stripped() -> None:
    payload = custom_plan()
    payload["case"] = {
        "geometry": "  A three-dimensional diffuser with a circular inlet.  ",
        "boundary_conditions": ("  fixed inlet velocity  ", "  fixed outlet pressure  "),
        "mesh_strategy": "  Hex-dominant mesh with wall refinement.  ",
        "run_strategy": "  Steady solve followed by metric extraction.  ",
    }

    case = ExperimentPlan.model_validate(payload).root.case

    assert case.geometry == "A three-dimensional diffuser with a circular inlet."
    assert case.boundary_conditions == ("fixed inlet velocity", "fixed outlet pressure")
    assert case.mesh_strategy == "Hex-dominant mesh with wall refinement."
    assert case.run_strategy == "Steady solve followed by metric extraction."


@pytest.mark.parametrize("assumptions", ["not a sequence", {"not", "json"}])
def test_tuple_compatibility_does_not_coerce_non_list_inputs(assumptions: object) -> None:
    with pytest.raises(ValidationError, match="valid tuple"):
        ExperimentPlan.model_validate(pipe_plan() | {"assumptions": assumptions})


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


@pytest.mark.parametrize(
    "updates",
    [
        {"time_step_s": None, "max_courant": None},
        {"time_step_s": 0.002, "max_courant": 0.5},
    ],
)
def test_cylinder_requires_exactly_one_time_control(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        CylinderFlowCase.model_validate(cylinder_case() | updates)


def test_cylinder_rejects_time_step_larger_than_end_time() -> None:
    with pytest.raises(ValidationError, match="end_time_s"):
        CylinderFlowCase.model_validate(
            cylinder_case() | {"end_time_s": 1.0, "time_step_s": 1.1}
        )


def test_cylinder_accepts_time_step_equal_to_end_time() -> None:
    case = CylinderFlowCase.model_validate(
        cylinder_case() | {"end_time_s": 1.0, "time_step_s": 1.0}
    )

    assert case.time_step_s == case.end_time_s


def test_cylinder_rejects_calculated_reynolds_above_release_limit() -> None:
    with pytest.raises(ValidationError, match="calculated Reynolds"):
        CylinderFlowCase.model_validate(
            cylinder_case()
            | {
                "reynolds_number": 300.0,
                "mean_velocity_m_s": 3.015,
            }
        )


def test_cylinder_accepts_mathematically_exact_reynolds_limit() -> None:
    velocity = 300.0 * 0.0001 / 0.03
    calculated_reynolds = velocity * 0.03 / 0.0001
    assert calculated_reynolds == 300.00000000000006

    case = CylinderFlowCase.model_validate(
        cylinder_case()
        | {
            "diameter_m": 0.03,
            "reynolds_number": 300.0,
            "kinematic_viscosity_m2_s": 0.0001,
            "mean_velocity_m_s": velocity,
        }
    )

    assert case.reynolds_number == 300.0


def test_cylinder_cannot_accept_pipe_payload() -> None:
    payload = pipe_plan() | {"experiment_type": "cylinder_flow"}

    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(payload)


@pytest.mark.parametrize(
    ("payload", "unsupported"),
    [
        (cylinder_plan(), "mass_imbalance"),
        (cavity_plan(), "mass_imbalance"),
    ],
)
def test_builtin_plans_reject_outputs_without_compiler_objects(
    payload: dict[str, object], unsupported: str
) -> None:
    payload["requested_outputs"] = (unsupported,)

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


def test_cavity_grid_independence_accepts_cells_per_side_sweep() -> None:
    payload = cavity_plan() | {
        "parameter_sweeps": (
            {"parameter": "cells_per_side", "values": (32.0, 64.0, 128.0)},
        )
    }

    plan = ExperimentPlan.model_validate(payload).root

    assert plan.parameter_sweeps[0].parameter == "cells_per_side"
    assert plan.parameter_sweeps[0].values == (32.0, 64.0, 128.0)


def test_cavity_requires_positive_geometry_and_resolution() -> None:
    payload = cavity_plan()
    payload["case"] = {**payload["case"], "cells_per_side": 7}  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"residual_tolerance": 0.0, "mass_imbalance_percent": 0.1},
        {"residual_tolerance": 0.011, "mass_imbalance_percent": 0.1},
        {"residual_tolerance": 1e-6, "mass_imbalance_percent": -0.1},
        {"residual_tolerance": 1e-6, "mass_imbalance_percent": 5.1},
    ],
)
def test_convergence_targets_enforce_direct_bounds(payload: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        ConvergenceTargets.model_validate(payload)


@pytest.mark.parametrize("mass_imbalance_percent", [0.0, 5.0])
def test_convergence_targets_accept_inclusive_boundaries(
    mass_imbalance_percent: float,
) -> None:
    targets = ConvergenceTargets(
        residual_tolerance=1e-2,
        mass_imbalance_percent=mass_imbalance_percent,
    )

    assert targets.mass_imbalance_percent == mass_imbalance_percent


def test_models_module_does_not_publish_misleading_plan_aliases() -> None:
    assert not hasattr(planning_models, "LaminarPipePlan")
    assert not hasattr(planning_models, "CylinderFlowPlan")
    assert not hasattr(planning_models, "LidDrivenCavityPlan")
    assert not hasattr(planning_models, "CustomOpenFOAMPlan")
