"""Native ExperimentSpec compilers — no ExperimentPlan intermediate.

These compilers read ExperimentSpec directly and generate OpenFOAM case
files without going through the old ExperimentPlan → compile_plan path.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from typing import Any, Protocol
from uuid import uuid4

from fluid_scientist.experiment_planning.compilers import CompiledCase
from fluid_scientist.experiment_spec.compilation import (
    CompilationManifest,
    MissingRequiredParameterError,
    SpecNotConfirmedError,
    compute_case_hash,
    compute_spec_hash,
    validate_required_parameters,
)
from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ExperimentStatus,
)


class ExperimentCompiler(Protocol):
    """Protocol for native ExperimentSpec compilers."""

    compiler_id: str
    compiler_version: str

    def can_compile(self, spec: ExperimentSpec) -> bool:
        """Check if this compiler can handle the given spec."""
        ...

    def compile(self, spec: ExperimentSpec) -> CompiledCase:
        """Compile the spec into a CompiledCase."""
        ...


def _param_values(spec: ExperimentSpec) -> dict[str, Any]:
    """Extract parameter_id -> value dict from spec."""
    return {p.parameter_id: p.value for p in spec.parameters}


def _required_float(values: dict[str, Any], key: str) -> float:
    """Coerce a spec value to float. Raises MissingRequiredParameterError if missing."""
    v = values.get(key)
    if v is None:
        raise MissingRequiredParameterError(key, "value is None")
    return float(v)


def _required_int(values: dict[str, Any], key: str) -> int:
    """Coerce a spec value to int. Raises MissingRequiredParameterError if missing."""
    v = values.get(key)
    if v is None:
        raise MissingRequiredParameterError(key, "value is None")
    return int(v)


def _float(values: dict[str, Any], key: str, default: float) -> float:
    """Coerce a spec value to float with a fallback (for optional parameters)."""
    v = values.get(key)
    if v is None:
        return default
    return float(v)


def _int(values: dict[str, Any], key: str, default: int) -> int:
    """Coerce a spec value to int with a fallback (for optional parameters)."""
    v = values.get(key)
    if v is None:
        return default
    return int(v)


class PipeFlowCompiler:
    """Native compiler for laminar pipe flow experiments.

    Generates OpenFOAM case files directly from ExperimentSpec parameters
    without constructing PipeExperimentPlan or calling compile_pipe_plan.
    """

    compiler_id = "fluid_scientist.native.pipe_flow"
    compiler_version = "1.0.0"

    def can_compile(self, spec: ExperimentSpec) -> bool:
        ids = {p.parameter_id for p in spec.parameters}
        return "length" in ids and "axial_cells" in ids

    def compile(self, spec: ExperimentSpec) -> CompiledCase:
        from fluid_scientist.adapters.custom_openfoam import (
            validate_custom_case_archive,
        )
        from fluid_scientist.experiment_planning.compilers import (
            _deterministic_tar_gz,
            _momentum_transport,
            _normalize,
            _physical_properties,
            _pipe_block_mesh,
            _pipe_control_dict,
            _pipe_fv_solution,
            _pipe_pressure_field,
            _pipe_velocity_profile_field,
            _steady_fv_schemes,
        )
        from fluid_scientist.experiment_planning.registry import (
            get_experiment_capability,
        )

        v = _param_values(spec)
        diameter = _required_float(v, "diameter")
        length = _required_float(v, "length")
        mean_velocity = _required_float(v, "mean_velocity")
        kinematic_viscosity = _required_float(v, "kinematic_viscosity")
        # density is validated for completeness even though it is not
        # directly consumed by the OpenFOAM dictionary templates below.
        _required_float(v, "density")
        axial_cells = _required_int(v, "axial_cells")
        radial_cells = _required_int(v, "radial_cells")

        files = {
            "0/U": _pipe_velocity_profile_field(
                velocity=mean_velocity, radial_cells=radial_cells
            ),
            "0/p": _pipe_pressure_field(),
            "constant/momentumTransport": _momentum_transport(),
            "constant/physicalProperties": _physical_properties(
                kinematic_viscosity
            ),
            "system/blockMeshDict": _pipe_block_mesh(
                diameter=diameter,
                length=length,
                axial_cells=axial_cells,
                radial_cells=radial_cells,
            ),
            "system/controlDict": _pipe_control_dict(),
            "system/fvSchemes": _steady_fv_schemes(),
            "system/fvSolution": _pipe_fv_solution(1e-4),
        }

        metadata = {
            "schema_version": 2,
            "experiment_type": "laminar_pipe",
            "source": "native_compiler",
            "experiment_id": spec.experiment_id,
            "parameters": {p.parameter_id: p.value for p in spec.parameters},
            "requested_outputs": ["pressure_drop", "residuals"],
            "compilation": {"mode": "native", "compiler_id": self.compiler_id},
        }
        files["fluidScientist/spec.json"] = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        normalized = {name: _normalize(text) for name, text in files.items()}
        archive = _deterministic_tar_gz(normalized)
        manifest = validate_custom_case_archive(archive)
        capability = get_experiment_capability("laminar_pipe")
        return CompiledCase(
            archive=archive,
            archive_sha256="sha256:" + hashlib.sha256(archive).hexdigest(),
            manifest=manifest,
            experiment_type="laminar_pipe",
            preprocessing=capability.preprocessing,
            required_outputs=("pressure_drop", "residuals"),
        )


class CylinderFlowCompiler:
    """Native compiler for cylinder flow experiments."""

    compiler_id = "fluid_scientist.native.cylinder_flow"
    compiler_version = "1.0.0"

    def can_compile(self, spec: ExperimentSpec) -> bool:
        ids = {p.parameter_id for p in spec.parameters}
        return "cells_wake" in ids and "reynolds_number" in ids

    def compile(self, spec: ExperimentSpec) -> CompiledCase:
        from fluid_scientist.experiment_planning.compilers import compile_cylinder_plan
        from fluid_scientist.experiment_planning.models import (
            ConvergenceTargets,
            CylinderExperimentPlan,
            CylinderFlowCase,
        )

        v = _param_values(spec)
        time_step = v.get("time_step")
        max_courant = v.get("max_courant")

        if time_step is not None:
            ts = float(time_step)
            mc = None
        elif max_courant is not None:
            ts = None
            mc = float(max_courant)
        else:
            ts = None
            mc = 0.5

        diameter = _required_float(v, "diameter")
        reynolds = _required_float(v, "reynolds_number")
        kin_visc = _required_float(v, "kinematic_viscosity")
        derived_velocity = reynolds * kin_visc / diameter

        case = CylinderFlowCase(
            diameter_m=diameter,
            reynolds_number=reynolds,
            mean_velocity_m_s=derived_velocity,
            kinematic_viscosity_m2_s=kin_visc,
            density_kg_m3=_required_float(v, "density"),
            domain_upstream_diameters=_float(v, "domain_upstream", 10.0),
            domain_downstream_diameters=_float(v, "domain_downstream", 20.0),
            domain_transverse_diameters=_float(v, "domain_width", 10.0),
            cells_radial=_int(v, "cells_radial", 40),
            cells_wake=_int(v, "cells_wake", 120),
            end_time_s=_required_float(v, "end_time"),
            time_step_s=ts,
            max_courant=mc,
        )
        plan = CylinderExperimentPlan(
            experiment_type="cylinder_flow",
            experiment_name=spec.research.title,
            objective=spec.research.objective,
            rationale="Compiled natively from ExperimentSpec",
            assumptions=("incompressible flow", "2D approximation"),
            limitations=("low Reynolds number only",),
            requested_outputs=("drag_coefficient", "lift_coefficient", "residuals"),
            convergence_targets=ConvergenceTargets(
                residual_tolerance=1e-4,
                mass_imbalance_percent=1.0,
            ),
            case=case,
        )
        return compile_cylinder_plan(plan)


class CavityFlowCompiler:
    """Native compiler for lid-driven cavity experiments."""

    compiler_id = "fluid_scientist.native.cavity_flow"
    compiler_version = "1.0.0"

    def can_compile(self, spec: ExperimentSpec) -> bool:
        ids = {p.parameter_id for p in spec.parameters}
        return "side_length" in ids and "lid_velocity" in ids

    def compile(self, spec: ExperimentSpec) -> CompiledCase:
        from fluid_scientist.experiment_planning.compilers import compile_cavity_plan
        from fluid_scientist.experiment_planning.models import (
            CavityExperimentPlan,
            ConvergenceTargets,
            LidDrivenCavityCase,
        )

        v = _param_values(spec)
        case = LidDrivenCavityCase(
            side_length_m=_required_float(v, "side_length"),
            lid_velocity_m_s=_required_float(v, "lid_velocity"),
            kinematic_viscosity_m2_s=_required_float(v, "kinematic_viscosity"),
            density_kg_m3=_required_float(v, "density"),
            cells_per_side=_required_int(v, "cells_per_side"),
            end_time_s=_required_float(v, "end_time"),
        )
        plan = CavityExperimentPlan(
            experiment_type="lid_driven_cavity",
            experiment_name=spec.research.title,
            objective=spec.research.objective,
            rationale="Compiled natively from ExperimentSpec",
            assumptions=("incompressible flow", "2D approximation"),
            limitations=("laminar regime only",),
            requested_outputs=("velocity_probes", "pressure_probes", "residuals"),
            convergence_targets=ConvergenceTargets(
                residual_tolerance=1e-4,
                mass_imbalance_percent=1.0,
            ),
            case=case,
        )
        return compile_cavity_plan(plan)


class CompilerRegistry:
    """Registry of native ExperimentSpec compilers.

    Resolves the appropriate compiler based on the ExperimentSpec's
    parameters and physics configuration.
    """

    def __init__(self) -> None:
        self._compilers: list[ExperimentCompiler] = []
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(PipeFlowCompiler())
        self.register(CylinderFlowCompiler())
        self.register(CavityFlowCompiler())

    def register(self, compiler: ExperimentCompiler) -> None:
        self._compilers.append(compiler)

    def resolve(self, spec: ExperimentSpec) -> ExperimentCompiler | None:
        """Find a compiler that can handle the given spec.

        Returns None if no compiler can handle the spec.
        """
        for compiler in self._compilers:
            if compiler.can_compile(spec):
                return compiler
        return None

    def available_compilers(self) -> list[str]:
        """Return IDs of all registered compilers."""
        return [c.compiler_id for c in self._compilers]


# --- Native compile_spec ---


def compile_spec_native(
    spec: ExperimentSpec,
    registry: CompilerRegistry | None = None,
) -> tuple[CompiledCase, CompilationManifest]:
    """Compile an ExperimentSpec natively, without calling compile_plan.

    This is the new formal compilation path. It:
    1. Validates the spec is confirmed
    2. Resolves a compiler from the registry
    3. Compiles directly from ExperimentSpec
    4. Returns (CompiledCase, CompilationManifest)

    Does NOT call compile_plan() or compile_confirmed_spec().

    Raises:
        SpecNotConfirmedError: if spec is not confirmed
        ValueError: if no compiler can handle the spec
    """
    status_val = (
        spec.status.value if hasattr(spec.status, "value") else str(spec.status)
    )
    if status_val != ExperimentStatus.CONFIRMED.value:
        raise SpecNotConfirmedError(
            f"experiment spec must be 'confirmed' to compile, got '{status_val}'"
        )

    # Hard gate: validate required parameters BEFORE resolving the compiler.
    validate_required_parameters(spec)

    if registry is None:
        registry = CompilerRegistry()

    compiler = registry.resolve(spec)
    if compiler is None:
        raise ValueError(
            f"no native compiler registered for spec with parameters: "
            + ", ".join(sorted(p.parameter_id for p in spec.parameters))
        )

    compiled = compiler.compile(spec)

    spec_hash = compute_spec_hash(spec)
    case_hash = compute_case_hash(compiled)

    manifest = CompilationManifest(
        compilation_id=f"comp-{uuid4().hex[:16]}",
        experiment_id=spec.experiment_id,
        experiment_version=spec.experiment_version,
        spec_hash=spec_hash,
        case_hash=case_hash,
        generated_files=list(compiled.manifest.members),
        compiler_id=compiler.compiler_id,
        compiler_version=compiler.compiler_version,
        extension_versions={},
        environment={
            "python": sys.version.split(" ", 1)[0],
            "platform": platform.platform(),
        },
    )
    return compiled, manifest


__all__ = [
    "CavityFlowCompiler",
    "CompilerRegistry",
    "CylinderFlowCompiler",
    "ExperimentCompiler",
    "PipeFlowCompiler",
    "compile_spec_native",
]
