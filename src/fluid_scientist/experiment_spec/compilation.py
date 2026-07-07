"""Simulation Compiler integration — only reads confirmed ExperimentSpec.

Implements P0 requirement #7: the Simulation Compiler must only read
confirmed versions.  A confirmed ExperimentSpec is an immutable snapshot
whose parameter values are the single source of truth for case generation.

Usage::

    from fluid_scientist.experiment_spec.compilation import compile_spec

    compiled, manifest = compile_spec(spec)  # raises if not confirmed
"""

from __future__ import annotations

import hashlib
import platform
import sys
import warnings
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from fluid_scientist import __version__ as _PACKAGE_VERSION
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

#: Stable identifier of the Simulation Compiler implementation.
COMPILER_ID = "fluid_scientist.simulation_compiler"

#: Version of the Simulation Compiler implementation (tracks the package version).
COMPILER_VERSION = _PACKAGE_VERSION

#: Built-in OpenFOAM dictionary template versions used by the compilers.
TEMPLATE_VERSIONS: dict[str, str] = {"openfoam": "13"}


class SpecNotConfirmedError(ValueError):
    """Raised when attempting to compile a spec that is not in confirmed state."""


class MissingRequiredParameterError(ValueError):
    """Raised when a required parameter is None or 'unknown' during compilation."""

    def __init__(self, parameter_id: str, detail: str = ""):
        self.parameter_id = parameter_id
        super().__init__(
            f"Missing required parameter '{parameter_id}': {detail}"
            if detail
            else f"Missing required parameter '{parameter_id}'"
        )


@dataclass(frozen=True)
class CompilationManifest:
    """编译产物清单 — 追踪 spec 版本与编译结果的关联。

    The manifest ties a confirmed ``ExperimentSpec`` (via ``spec_hash``) to the
    generated OpenFOAM case archive (via ``case_hash``) and records the
    compiler/template/extension provenance required for reproducibility.
    """

    compilation_id: str
    experiment_id: str
    experiment_version: int
    spec_hash: str  # ExperimentSpec 的内容哈希
    case_hash: str  # 编译产物的内容哈希
    generated_files: list[str] = field(default_factory=list)
    compiler_id: str = COMPILER_ID
    compiler_version: str = COMPILER_VERSION
    template_versions: dict[str, str] = field(
        default_factory=lambda: dict(TEMPLATE_VERSIONS)
    )
    extension_versions: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)


def _param_values(spec: ExperimentSpec) -> dict[str, Any]:
    """Extract a flat parameter_id to value dict from the spec."""
    return {p.parameter_id: p.value for p in spec.parameters}


def _required_float(values: dict[str, Any], key: str) -> float:
    """Coerce a spec value to float. Raises if missing."""
    v = values.get(key)
    if v is None:
        raise MissingRequiredParameterError(key, "value is None")
    return float(v)


def _required_int(values: dict[str, Any], key: str) -> int:
    """Coerce a spec value to int. Raises if missing."""
    v = values.get(key)
    if v is None:
        raise MissingRequiredParameterError(key, "value is None")
    return int(v)


def _float(values: dict[str, Any], key: str, default: float) -> float:
    """[DEPRECATED] Coerce a spec value to float with a fallback.

    .. deprecated::
        New code must use :func:`_required_float` instead.  This function
        is retained only for the deprecated :func:`compile_confirmed_spec`
        path.
    """
    warnings.warn(
        "_float is deprecated; use _required_float instead",
        DeprecationWarning,
        stacklevel=2,
    )
    v = values.get(key)
    if v is None:
        return default
    return float(v)


def _int(values: dict[str, Any], key: str, default: int) -> int:
    """[DEPRECATED] Coerce a spec value to int with a fallback.

    .. deprecated::
        New code must use :func:`_required_int` instead.  This function
        is retained only for the deprecated :func:`compile_confirmed_spec`
        path.
    """
    warnings.warn(
        "_int is deprecated; use _required_int instead",
        DeprecationWarning,
        stacklevel=2,
    )
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


# Required parameters per experiment type
_REQUIRED_PARAMETERS: dict[str, list[str]] = {
    "laminar_pipe": [
        "diameter",
        "length",
        "mean_velocity",
        "kinematic_viscosity",
        "density",
        "axial_cells",
        "radial_cells",
    ],
    "cylinder_flow": [
        "diameter",
        "reynolds_number",
        "kinematic_viscosity",
        "density",
        "end_time",
    ],
    "lid_driven_cavity": [
        "side_length",
        "lid_velocity",
        "kinematic_viscosity",
        "density",
        "cells_per_side",
        "end_time",
    ],
}


def validate_required_parameters(spec: ExperimentSpec) -> None:
    """Validate that all required parameters have non-None, non-'unknown' values.

    Raises MissingRequiredParameterError if any required parameter is missing
    or unknown.
    """
    experiment_type = _detect_experiment_type(spec)
    required = _REQUIRED_PARAMETERS.get(experiment_type, [])
    param_values = _param_values(spec)

    for param_id in required:
        value = param_values.get(param_id)
        if value is None:
            raise MissingRequiredParameterError(param_id, "parameter value is None")
        if isinstance(value, str) and value.lower() in ("unknown", "none", "null", ""):
            raise MissingRequiredParameterError(
                param_id, f"parameter value is '{value}'"
            )


# --- Plan builders (reverse of migration) ---

# [DEPRECATED] — hardcoded convergence defaults; new code should not use this.
# Retained only for the deprecated compile_confirmed_spec() path.
# .. deprecated:: Use native compile_spec() which does not require ExperimentPlan.
_DEFAULT_CONVERGENCE = ConvergenceTargets(
    residual_tolerance=1e-4,
    mass_imbalance_percent=1.0,
)


def _build_pipe_plan(spec: ExperimentSpec) -> PipeExperimentPlan:
    """[DEPRECATED] Build a PipeExperimentPlan from confirmed spec parameters.

    .. deprecated::
        This function is retained only for the deprecated
        :func:`compile_confirmed_spec` path.  New code must use the
        native compiler via :func:`compile_spec`.
    """
    warnings.warn(
        "_build_pipe_plan is deprecated; use native compile_spec path",
        DeprecationWarning,
        stacklevel=2,
    )
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
    """[DEPRECATED] Build a CylinderExperimentPlan from confirmed spec parameters.

    .. deprecated::
        This function is retained only for the deprecated
        :func:`compile_confirmed_spec` path.  New code must use the
        native compiler via :func:`compile_spec`.
    """
    warnings.warn(
        "_build_cylinder_plan is deprecated; use native compile_spec path",
        DeprecationWarning,
        stacklevel=2,
    )
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
    """[DEPRECATED] Build a CavityExperimentPlan from confirmed spec parameters.

    .. deprecated::
        This function is retained only for the deprecated
        :func:`compile_confirmed_spec` path.  New code must use the
        native compiler via :func:`compile_spec`.
    """
    warnings.warn(
        "_build_cavity_plan is deprecated; use native compile_spec path",
        DeprecationWarning,
        stacklevel=2,
    )
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


# [DEPRECATED] — legacy plan builders; retained only for compile_confirmed_spec().
# New code must use the native compiler via compile_spec().
_BUILDERS = {
    "laminar_pipe": _build_pipe_plan,
    "cylinder_flow": _build_cylinder_plan,
    "lid_driven_cavity": _build_cavity_plan,
}


def compute_spec_hash(spec: ExperimentSpec) -> str:
    """计算 ExperimentSpec 的内容哈希。

    The hash is a stable, short content digest of the canonical JSON
    serialization of the spec.  It lets callers detect whether two specs
    carry identical content regardless of their identity fields.
    """
    return hashlib.sha256(spec.model_dump_json().encode()).hexdigest()[:16]


def compute_case_hash(compiled: CompiledCase) -> str:
    """计算编译产物的内容哈希。

    The hash is a stable, short content digest of the generated OpenFOAM
    case archive bytes.
    """
    return hashlib.sha256(compiled.archive).hexdigest()[:16]


def _build_environment() -> dict[str, str]:
    """Capture the runtime environment relevant to compilation reproducibility."""
    return {
        "python": sys.version.split(" ", 1)[0],
        "platform": platform.platform(),
    }


def _build_extension_versions(spec: ExperimentSpec) -> dict[str, str]:
    """Extract extension id -> version pairs from the spec, if any."""
    versions: dict[str, str] = {}
    for extension in spec.code_extensions:
        ext_id = extension.get("extension_id") or extension.get("id")
        ext_version = extension.get("version")
        if isinstance(ext_id, str) and isinstance(ext_version, str):
            versions[ext_id] = ext_version
    return versions


def compile_confirmed_spec(spec: ExperimentSpec) -> CompiledCase:
    """[DEPRECATED] 从已确认的 ExperimentSpec 编译 OpenFOAM 算例。

    .. deprecated::
        使用 :func:`compile_spec` 或 :func:`compile_spec_native` 代替。
        新代码应使用原生编译路径，不经过 ExperimentPlan 中间层。

    Compile a confirmed ExperimentSpec into a runnable OpenFOAM case.

    Raises:
        SpecNotConfirmedError: if the spec is not in confirmed state.
        ValueError: if the experiment type cannot be detected or the
            reconstructed plan fails validation.
    """
    warnings.warn(
        "compile_confirmed_spec is deprecated; use compile_spec or compile_spec_native",
        DeprecationWarning,
        stacklevel=2,
    )
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


def compile_spec(spec: ExperimentSpec) -> tuple[CompiledCase, CompilationManifest]:
    """直接从 ExperimentSpec 编译 OpenFOAM 算例。

    Uses the native compilation path (CompilerRegistry) exclusively.
    The legacy ``compile_confirmed_spec`` fallback has been removed —
    all three native compilers (Pipe, Cylinder, Cavity) are now working.

    Args:
        spec: 必须处于 ``confirmed`` 状态的 ExperimentSpec。

    Returns:
        ``(CompiledCase, CompilationManifest)`` 元组。

    Raises:
        SpecNotConfirmedError: 如果 spec 不是 confirmed 状态。
        ValueError: 如果没有可用的原生编译器。
    """
    from fluid_scientist.experiment_spec.native_compiler import (
        CompilerRegistry,
        compile_spec_native,
    )

    # Hard gate: validate required parameters BEFORE attempting compilation.
    validate_required_parameters(spec)

    registry = CompilerRegistry()
    compiler = registry.resolve(spec)
    if compiler is None:
        experiment_type = _detect_experiment_type(spec)
        raise ValueError(
            f"No native compiler available for experiment type '{experiment_type}'. "
            "Legacy compile_confirmed_spec fallback has been removed. "
            "Ensure the spec has parameters that match a supported experiment type."
        )
    return compile_spec_native(spec, registry)


__all__ = [
    "COMPILER_ID",
    "COMPILER_VERSION",
    "TEMPLATE_VERSIONS",
    "CompilationManifest",
    "MissingRequiredParameterError",
    "SpecNotConfirmedError",
    "compile_confirmed_spec",
    "compile_spec",
    "compute_case_hash",
    "compute_spec_hash",
    "validate_required_parameters",
]
