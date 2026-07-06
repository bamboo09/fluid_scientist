"""Simulation Compiler integration — only reads confirmed ExperimentSpec.

Implements P0 requirement #7: the Simulation Compiler must only read
confirmed versions.  A confirmed ExperimentSpec is an immutable snapshot
whose parameter values are the single source of truth for case generation.

Usage::

    from fluid_scientist.experiment_spec.compilation import compile_confirmed_spec

    compiled = compile_confirmed_spec(spec)  # raises if not confirmed
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.experiment_planning.compilers import CompiledCase, compile_plan
from fluid_scientist.experiment_planning.models import (
    CavityExperimentPlan,
    ConvergenceTargets,
    CylinderExperimentPlan,
    CylinderFlowCase,
    LaminarPipeCase,
    LidDrivenCavityCase,
    PipeExperimentPlan,
)
from fluid_scientist.experiment_spec.models import ExperimentSpec, ExperimentStatus


class SpecNotConfirmedError(ValueError):
    """Raised when attempting to compile a spec that is not in confirmed state."""


def _param_values(spec: ExperimentSpec) -> dict[str, Any]:
    """Extract a flat parameter_id to value dict from the spec."""
    return {p.parameter_id: p.value for p in spec.parameters}


def _float(values: dict[str, Any], key: str, default: float) -> float:
    """Coerce a spec value to float with a fallback."""
    v = values.get(key)
    if v is None:
        return default
    return float(v)


def _int(values: dict[str, Any], key: str, default: int) -> int:
    """Coerce a spec value to int with a fallback."""
    v = values.get(key)
    if v is None:
        return default
    return int(v)


def _detect_experiment_type(spec: ExperimentSpec) -> str:
    """Infer the experiment type from the spec parameter IDs."""
    ids = {p.parameter_id for p in spec.parameters}
    if "length" in ids and "axial_cells" in ids:
        return "laminar_pipe"
    if "cells_wake" in ids and "reynolds_number" in ids:
        return "cylinder_flow"
    if "side_length" in ids and "lid_velocity" in ids:
        return "lid_driven_cavity"
    raise ValueError(
        "cannot detect experiment type from spec parameters: "
        + ", ".join(sorted(ids))
    )


# --- Plan builders (reverse of migration) ---

_DEFAULT_CONVERGENCE = ConvergenceTargets(
    residual_tolerance=1e-4,
    mass_imbalance_percent=1.0,
)


def _build_pipe_plan(spec: ExperimentSpec) -> PipeExperimentPlan:
    """Build a PipeExperimentPlan from confirmed spec parameters."""
    v = _param_values(spec)
    case = LaminarPipeCase(
        diameter_m=_float(v, "diameter", 0.05),
        length_m=_float(v, "length", 1.0),
        mean_velocity_m_s=_float(v, "mean_velocity", 0.1),
        kinematic_viscosity_m2_s=_float(v, "kinematic_viscosity", 1e-6),
        density_kg_m3=_float(v, "density", 998.2),
        axial_cells=_int(v, "axial_cells", 80),
        radial_cells=_int(v, "radial_cells", 10),
    )
    return PipeExperimentPlan(
        experiment_type="laminar_pipe",
        experiment_name=spec.research.title,
        objective=spec.research.objective,
        rationale="Compiled from confirmed ExperimentSpec",
        assumptions=("incompressible flow", "fully developed inlet"),
        limitations=("laminar regime only",),
        requested_outputs=("pressure_drop", "residuals"),
        convergence_targets=_DEFAULT_CONVERGENCE,
        case=case,
    )


def _build_cylinder_plan(spec: ExperimentSpec) -> CylinderExperimentPlan:
    """Build a CylinderExperimentPlan from confirmed spec parameters."""
    v = _param_values(spec)
    time_step = v.get("time_step")
    max_courant = v.get("max_courant")

    # CylinderFlowCase requires exactly one of time_step_s or max_courant
    if time_step is not None:
        ts: float | None = float(time_step)
        mc: float | None = None
    elif max_courant is not None:
        ts = None
        mc = float(max_courant)
    else:
        ts = None
        mc = 0.5

    # Derive mean_velocity from reynolds_number for physical consistency
    diameter = _float(v, "diameter", 0.1)
    reynolds = _float(v, "reynolds_number", 100.0)
    kin_visc = _float(v, "kinematic_viscosity", 1e-6)
    derived_velocity = reynolds * kin_visc / diameter

    case = CylinderFlowCase(
        diameter_m=diameter,
        reynolds_number=reynolds,
        mean_velocity_m_s=derived_velocity,
        kinematic_viscosity_m2_s=kin_visc,
        density_kg_m3=_float(v, "density", 998.2),
        domain_upstream_diameters=_float(v, "domain_upstream", 10.0),
        domain_downstream_diameters=_float(v, "domain_downstream", 20.0),
        domain_transverse_diameters=_float(v, "domain_width", 10.0),
        cells_radial=_int(v, "cells_radial", 40),
        cells_wake=_int(v, "cells_wake", 120),
        end_time_s=_float(v, "end_time", 10.0),
        time_step_s=ts,
        max_courant=mc,
    )
    return CylinderExperimentPlan(
        experiment_type="cylinder_flow",
        experiment_name=spec.research.title,
        objective=spec.research.objective,
        rationale="Compiled from confirmed ExperimentSpec",
        assumptions=("incompressible flow", "2D approximation"),
        limitations=("low Reynolds number only",),
        requested_outputs=("drag_coefficient", "lift_coefficient", "residuals"),
        convergence_targets=_DEFAULT_CONVERGENCE,
        case=case,
    )


def _build_cavity_plan(spec: ExperimentSpec) -> CavityExperimentPlan:
    """Build a CavityExperimentPlan from confirmed spec parameters."""
    v = _param_values(spec)
    case = LidDrivenCavityCase(
        side_length_m=_float(v, "side_length", 0.1),
        lid_velocity_m_s=_float(v, "lid_velocity", 1.0),
        kinematic_viscosity_m2_s=_float(v, "kinematic_viscosity", 1e-6),
        density_kg_m3=_float(v, "density", 998.2),
        cells_per_side=_int(v, "cells_per_side", 64),
        end_time_s=_float(v, "end_time", 10.0),
    )
    return CavityExperimentPlan(
        experiment_type="lid_driven_cavity",
        experiment_name=spec.research.title,
        objective=spec.research.objective,
        rationale="Compiled from confirmed ExperimentSpec",
        assumptions=("incompressible flow", "2D approximation"),
        limitations=("laminar regime only",),
        requested_outputs=("velocity_probes", "pressure_probes", "residuals"),
        convergence_targets=_DEFAULT_CONVERGENCE,
        case=case,
    )


_BUILDERS = {
    "laminar_pipe": _build_pipe_plan,
    "cylinder_flow": _build_cylinder_plan,
    "lid_driven_cavity": _build_cavity_plan,
}


def compile_confirmed_spec(spec: ExperimentSpec) -> CompiledCase:
    """Compile a confirmed ExperimentSpec into a runnable OpenFOAM case.

    Raises:
        SpecNotConfirmedError: if the spec is not in confirmed state.
        ValueError: if the experiment type cannot be detected or the
            reconstructed plan fails validation.
    """
    status_val = spec.status.value if hasattr(spec.status, "value") else str(spec.status)
    if status_val != ExperimentStatus.CONFIRMED.value:
        raise SpecNotConfirmedError(
            f"experiment spec must be 'confirmed' to compile, got '{status_val}'"
        )

    experiment_type = _detect_experiment_type(spec)
    builder = _BUILDERS.get(experiment_type)
    if builder is None:
        raise ValueError(f"no builder for experiment type '{experiment_type}'")

    plan = builder(spec)
    return compile_plan(plan)


__all__ = [
    "SpecNotConfirmedError",
    "compile_confirmed_spec",
]
