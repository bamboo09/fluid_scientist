"""Native ExperimentSpec compilers — no ExperimentPlan intermediate.

These compilers read ExperimentSpec directly and generate OpenFOAM case
files without going through the old ExperimentPlan → compile_plan path.
"""

from __future__ import annotations

import contextlib
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


class MeasurementCompilationError(Exception):
    """Raised when MeasurementPlan compilation fails with blocking errors.

    This error blocks case compilation -- unlike warnings, which are
    non-blocking, error-severity issues or a ``success == False`` result
    from the measurement compiler must prevent the case from being built
    with an incomplete measurement configuration.
    """


# ---------------------------------------------------------------------------
# MeasurementPlan integration helpers
# ---------------------------------------------------------------------------

# Patches available per experiment type (extracted from blockMeshDict boundary).
_EXPERIMENT_PATCHES: dict[str, list[str]] = {
    "laminar_pipe": ["inlet", "outlet", "wall", "walls", "side1", "side2"],
    "cylinder_flow": ["inlet", "outlet", "cylinder", "mirrorPlane", "frontAndBack"],
    "lid_driven_cavity": ["movingLid", "fixedWalls", "frontAndBack"],
}


def _format_openfoam_value(value: Any, indent: int = 4) -> str:
    """Format a Python value as an OpenFOAM dict value string."""
    if isinstance(value, str):
        return value
    elif isinstance(value, bool):
        return "yes" if value else "no"
    elif isinstance(value, int):
        return str(value)
    elif isinstance(value, float):
        # Use same format as _number() in compilers.py
        return f"{value:.12g}"
    elif isinstance(value, list):
        return "(" + " ".join(_format_openfoam_value(v, indent) for v in value) + ")"
    elif isinstance(value, dict):
        inner_lines = []
        for k, v in value.items():
            inner_lines.append(
                " " * (indent + 4) + k + " " + _format_openfoam_value(v, indent + 4) + ";"
            )
        inner = "\n".join(inner_lines)
        return "{\n" + inner + "\n" + " " * indent + "}"
    return str(value)


def _render_fo_dict_to_openfoam(name: str, fo_dict: dict[str, Any], indent: int = 4) -> str:
    """Render a functionObject dict as OpenFOAM dict format string."""
    spaces = " " * indent
    lines = [spaces + name, spaces + "{"]
    for key, value in fo_dict.items():
        lines.append(
            spaces + "    " + key + " " + _format_openfoam_value(value, indent + 4) + ";"
        )
    lines.append(spaces + "}")
    return "\n".join(lines)


def _remove_existing_fo_block(control_dict_text: str, fo_name: str) -> str:
    """Remove a named functionObject block from the functions section.

    Scans for a line whose stripped content equals *fo_name* followed by
    a ``{`` line inside the functions block and removes everything up to
    the matching closing brace.  This ensures that MeasurementPlan
    functionObjects replace (not duplicate) existing ones.
    """
    lines = control_dict_text.split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == fo_name:
            # Check that the next non-empty line is "{"
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines) and lines[j].strip() == "{":
                # Found the block start.  Find the matching closing brace.
                depth = 1
                k = j + 1
                while k < len(lines) and depth > 0:
                    for ch in lines[k]:
                        if ch == "{":
                            depth += 1
                        elif ch == "}":
                            depth -= 1
                    k += 1
                # Remove lines[i:k] (the entire FO block including name line)
                del lines[i:k]
                return "\n".join(lines)
        i += 1
    return control_dict_text


def _merge_measurement_plan_into_control_dict(
    control_dict_text: str,
    additional_fos: dict[str, dict[str, Any]],
) -> str:
    """Merge MeasurementPlan functionObjects into the controlDict's functions block.

    If a functionObject with the same name already exists in the controlDict,
    the existing one is removed and replaced by the MeasurementPlan version.
    """
    if not additional_fos:
        return control_dict_text

    text = control_dict_text

    # Remove any existing FOs that share names with the new ones.
    for fo_name in additional_fos:
        text = _remove_existing_fo_block(text, fo_name)

    # Render the additional functionObjects as OpenFOAM dict text.
    fo_entries = []
    for name, fo_dict in additional_fos.items():
        fo_entries.append(_render_fo_dict_to_openfoam(name, fo_dict))
    fo_text = "\n".join(fo_entries)

    # Find the closing of the functions block (the last standalone "}")
    # and insert the new FOs before it.
    lines = text.rstrip().split("\n")
    insert_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "}":
            insert_idx = i
            break

    if insert_idx is not None:
        for j, fo_line in enumerate(fo_text.split("\n")):
            lines.insert(insert_idx + j, fo_line)
        return "\n".join(lines) + "\n"

    return control_dict_text


def _extract_spec_parameters(spec: ExperimentSpec) -> dict[str, float]:
    """Extract physical parameters from the ExperimentSpec for measurement compilation.

    Returns a dict mapping parameter names to float values.  Keys include:
    density, inlet_velocity, mean_velocity, diameter, length, extrusion_span.
    Missing parameters are simply omitted from the dict.
    """
    values = {p.parameter_id: p.value for p in spec.parameters}
    spec_params: dict[str, float] = {}
    for key in ("density", "inlet_velocity", "mean_velocity",
                "diameter", "length", "extrusion_span",
                "side_length", "domain_width", "domain_height"):
        raw = values.get(key)
        if raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                spec_params[key] = float(raw)
    return spec_params


def _integrate_measurement_plan(
    spec: ExperimentSpec,
    experiment_type: str,
    files: dict[str, str],
) -> None:
    """If *spec* carries a MeasurementPlan in ``spec.metrics``, compile it
    and merge the resulting functionObjects into the ``system/controlDict`` file.

    This mutates *files* in-place by updating ``files["system/controlDict"]``.

    **Blocking behaviour**: if ``compile_measurement_plan()`` returns
    ``success == False`` or produces error-severity issues, a
    :class:`MeasurementCompilationError` is raised to block case compilation.
    Only non-blocking exceptions (e.g. MeasurementPlan parsing failures for
    malformed data) are caught and silently skipped.

    Warning-severity issues are non-blocking and are logged but do not
    prevent compilation.
    """
    if not spec.metrics:
        return

    from fluid_scientist.measurement.compiler import compile_measurement_plan
    from fluid_scientist.measurement.models import MeasurementPlan

    plan_data = spec.metrics[0]
    if not isinstance(plan_data, dict):
        return

    # Parse the MeasurementPlan — if the data is malformed, this is a
    # non-blocking issue (the plan simply can't be used).
    try:
        measurement_plan = MeasurementPlan.model_validate(plan_data)
    except Exception:
        return

    patches = _EXPERIMENT_PATCHES.get(experiment_type, [])
    spec_parameters = _extract_spec_parameters(spec)

    result = compile_measurement_plan(
        measurement_plan,
        available_patches=patches,
        solver_output_fields=["U", "p"],
        spec_parameters=spec_parameters,
    )

    # Blocking: compilation failure or error-severity issues
    error_issues = [i for i in result.issues if i.severity == "error"]
    if not result.success or error_issues:
        messages = (
            "; ".join(i.message for i in error_issues)
            if error_issues
            else "compilation failed"
        )
        raise MeasurementCompilationError(
            f"MeasurementPlan compilation failed: {messages}"
        )

    # Success — merge functionObjects into controlDict
    additional_fos = result.control_dict_additions.get("functions", {})
    if additional_fos:
        files["system/controlDict"] = _merge_measurement_plan_into_control_dict(
            files["system/controlDict"],
            additional_fos,
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

        # Integrate MeasurementPlan functionObjects if present in spec.metrics.
        _integrate_measurement_plan(spec, "laminar_pipe", files)

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
        from fluid_scientist.adapters.custom_openfoam import (
            validate_custom_case_archive,
        )
        from fluid_scientist.experiment_planning.compilers import (
            CompilationError,
            _cylinder_block_mesh,
            _cylinder_control_dict,
            _cylinder_minimum_cell_size,
            _cylinder_pressure_field,
            _cylinder_velocity_field,
            _deterministic_tar_gz,
            _mirror_mesh_dict,
            _momentum_transport,
            _normalize,
            _physical_properties,
            _transient_fv_schemes,
            _transient_fv_solution,
        )
        from fluid_scientist.experiment_planning.registry import (
            get_experiment_capability,
        )

        v = _param_values(spec)
        diameter = _required_float(v, "diameter")
        reynolds = _required_float(v, "reynolds_number")
        kinematic_viscosity = _required_float(v, "kinematic_viscosity")
        density = _required_float(v, "density")
        end_time = _required_float(v, "end_time")

        domain_upstream = _float(v, "domain_upstream", 10.0)
        domain_downstream = _float(v, "domain_downstream", 20.0)
        domain_width = _float(v, "domain_width", 10.0)
        cells_radial = _int(v, "cells_radial", 40)
        cells_wake = _int(v, "cells_wake", 120)

        time_step_raw = v.get("time_step")
        time_step = float(time_step_raw) if time_step_raw is not None else None
        max_courant_raw = v.get("max_courant")
        max_courant = (
            float(max_courant_raw) if max_courant_raw is not None else None
        )

        mean_velocity = reynolds * kinematic_viscosity / diameter

        radius = diameter / 2.0
        upstream = domain_upstream * diameter
        downstream = domain_downstream * diameter
        transverse = domain_width * diameter
        extrusion_span = diameter * 0.1

        estimated_cell_size = _cylinder_minimum_cell_size(
            radius=radius,
            upstream=upstream,
            downstream=downstream,
            transverse=transverse,
            thickness=extrusion_span,
            radial_cells=cells_radial,
            wake_cells=cells_wake,
        )
        effective_max_courant = max_courant if max_courant is not None else 1.0
        stable_delta_t = (
            effective_max_courant * estimated_cell_size / mean_velocity
        )
        if stable_delta_t < 1e-12:
            raise CompilationError(
                "required cylinder time step is below the safe "
                "representable limit"
            )
        delta_t = time_step
        if delta_t is not None and delta_t > stable_delta_t:
            raise CompilationError(
                "initial cylinder time step exceeds the conservative "
                "Courant limit"
            )
        if delta_t is None:
            delta_t = 0.5 * stable_delta_t

        files = {
            "0/U": _cylinder_velocity_field(mean_velocity),
            "0/p": _cylinder_pressure_field(),
            "constant/momentumTransport": _momentum_transport(),
            "constant/physicalProperties": _physical_properties(
                kinematic_viscosity
            ),
            "system/blockMeshDict": _cylinder_block_mesh(
                radius=radius,
                upstream=upstream,
                downstream=downstream,
                transverse=transverse,
                thickness=extrusion_span,
                circumferential_cells=cells_radial,
                wake_cells=cells_wake,
            ),
            "system/mirrorMeshDict": _mirror_mesh_dict(diameter),
            "system/controlDict": _cylinder_control_dict(
                end_time=end_time,
                delta_t=delta_t,
                adjust_time_step=True,
                max_courant=effective_max_courant,
                density=density,
                velocity=mean_velocity,
                diameter=diameter,
                extrusion_span=extrusion_span,
            ),
            "system/fvSchemes": _transient_fv_schemes(),
            "system/fvSolution": _transient_fv_solution(1e-4),
        }

        metadata = {
            "schema_version": 2,
            "experiment_type": "cylinder_flow",
            "source": "native_compiler",
            "experiment_id": spec.experiment_id,
            "parameters": {p.parameter_id: p.value for p in spec.parameters},
            "requested_outputs": [
                "drag_coefficient",
                "lift_coefficient",
                "residuals",
            ],
            "compilation": {"mode": "native", "compiler_id": self.compiler_id},
        }
        files["fluidScientist/spec.json"] = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        # Integrate MeasurementPlan functionObjects if present in spec.metrics.
        _integrate_measurement_plan(spec, "cylinder_flow", files)

        normalized = {name: _normalize(text) for name, text in files.items()}
        archive = _deterministic_tar_gz(normalized)
        manifest = validate_custom_case_archive(archive)
        capability = get_experiment_capability("cylinder_flow")
        return CompiledCase(
            archive=archive,
            archive_sha256="sha256:" + hashlib.sha256(archive).hexdigest(),
            manifest=manifest,
            experiment_type="cylinder_flow",
            preprocessing=capability.preprocessing,
            required_outputs=(
                "drag_coefficient",
                "lift_coefficient",
                "residuals",
            ),
        )


class CavityFlowCompiler:
    """Native compiler for lid-driven cavity experiments."""

    compiler_id = "fluid_scientist.native.cavity_flow"
    compiler_version = "1.0.0"

    def can_compile(self, spec: ExperimentSpec) -> bool:
        ids = {p.parameter_id for p in spec.parameters}
        return "side_length" in ids and "lid_velocity" in ids

    def compile(self, spec: ExperimentSpec) -> CompiledCase:
        from fluid_scientist.adapters.custom_openfoam import (
            validate_custom_case_archive,
        )
        from fluid_scientist.experiment_planning.compilers import (
            CompilationError,
            _cavity_block_mesh,
            _cavity_control_dict,
            _cavity_pressure_field,
            _cavity_velocity_field,
            _deterministic_tar_gz,
            _momentum_transport,
            _normalize,
            _physical_properties,
            _transient_fv_schemes,
            _transient_fv_solution,
        )
        from fluid_scientist.experiment_planning.registry import (
            get_experiment_capability,
        )

        v = _param_values(spec)
        side_length = _required_float(v, "side_length")
        lid_velocity = _required_float(v, "lid_velocity")
        kinematic_viscosity = _required_float(v, "kinematic_viscosity")
        # density is validated for completeness even though it is not
        # directly consumed by the OpenFOAM dictionary templates below.
        _required_float(v, "density")
        cells_per_side = _required_int(v, "cells_per_side")
        end_time = _required_float(v, "end_time")

        thickness = side_length / cells_per_side
        stable_delta_t = 0.5 * thickness / lid_velocity
        if stable_delta_t < 1e-12:
            raise CompilationError(
                "required cavity time step is below the safe "
                "representable limit"
            )
        delta_t = min(end_time / 1000.0, stable_delta_t)

        files = {
            "0/U": _cavity_velocity_field(lid_velocity),
            "0/p": _cavity_pressure_field(),
            "constant/momentumTransport": _momentum_transport(),
            "constant/physicalProperties": _physical_properties(
                kinematic_viscosity
            ),
            "system/blockMeshDict": _cavity_block_mesh(
                side=side_length, cells=cells_per_side
            ),
            "system/controlDict": _cavity_control_dict(
                end_time=end_time,
                side=side_length,
                thickness=thickness,
                delta_t=delta_t,
            ),
            "system/fvSchemes": _transient_fv_schemes(),
            "system/fvSolution": _transient_fv_solution(
                1e-4, pressure_reference=True
            ),
        }

        metadata = {
            "schema_version": 2,
            "experiment_type": "lid_driven_cavity",
            "source": "native_compiler",
            "experiment_id": spec.experiment_id,
            "parameters": {p.parameter_id: p.value for p in spec.parameters},
            "requested_outputs": [
                "velocity_probes",
                "pressure_probes",
                "residuals",
            ],
            "compilation": {"mode": "native", "compiler_id": self.compiler_id},
        }
        files["fluidScientist/spec.json"] = json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        # Integrate MeasurementPlan functionObjects if present in spec.metrics.
        _integrate_measurement_plan(spec, "lid_driven_cavity", files)

        normalized = {name: _normalize(text) for name, text in files.items()}
        archive = _deterministic_tar_gz(normalized)
        manifest = validate_custom_case_archive(archive)
        capability = get_experiment_capability("lid_driven_cavity")
        return CompiledCase(
            archive=archive,
            archive_sha256="sha256:" + hashlib.sha256(archive).hexdigest(),
            manifest=manifest,
            experiment_type="lid_driven_cavity",
            preprocessing=capability.preprocessing,
            required_outputs=(
                "velocity_probes",
                "pressure_probes",
                "residuals",
            ),
        )


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
            "no native compiler registered for spec with parameters: "
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
    "MeasurementCompilationError",
    "PipeFlowCompiler",
    "compile_spec_native",
]
