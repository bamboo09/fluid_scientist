"""MeasurementPlan compiler — writes functionObjects into OpenFOAM case files.

This module bridges the abstract MeasurementPlan (which functionObjects to use)
and the concrete OpenFOAM case files (system/controlDict, system/sampleDict,
system/surfaceSamplingDict).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.measurement.models import (
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
)


@dataclass
class CompilationIssue:
    """An issue found during measurement plan compilation."""
    severity: str  # "error" or "warning"
    metric_id: str | None
    message: str


@dataclass
class MeasurementCompilationResult:
    """Result of compiling a MeasurementPlan into case files."""
    success: bool
    control_dict_additions: dict[str, Any] = field(default_factory=dict)
    sample_dict: dict[str, Any] | None = None
    surface_sampling_dict: dict[str, Any] | None = None
    issues: list[CompilationIssue] = field(default_factory=list)
    generated_function_objects: list[dict[str, Any]] = field(default_factory=list)


def compile_measurement_plan(
    measurement_plan: MeasurementPlan,
    available_patches: list[str] | None = None,
    solver_output_fields: list[str] | None = None,
    simulation_end_time: float | None = None,
    core_metric_ids: list[str] | None = None,
    spec_parameters: dict[str, float] | None = None,
) -> MeasurementCompilationResult:
    """Compile a MeasurementPlan into OpenFOAM case file additions.

    Args:
        measurement_plan: The measurement plan to compile.
        available_patches: List of patch names in the mesh (e.g., ["inlet", "outlet", "wall"]).
        solver_output_fields: Fields the solver outputs (e.g., ["U", "p", "k", "omega"]).
        simulation_end_time: End time of the simulation.
        core_metric_ids: IDs of core metrics that must have data sources.

    Returns:
        MeasurementCompilationResult with case file additions and validation issues.

    Rules:
        - Each core metric must have at least one MetricBinding
        - Each MetricBinding's function_object must exist in function_objects
        - Each functionObject referencing a patch must reference an available patch
        - Each functionObject's field must be in solver_output_fields
        - Sampling time must be within simulation time range
        - If any core metric lacks data -> compilation fails (blocking)
    """
    available_patches = available_patches or []
    solver_output_fields = solver_output_fields or ["U", "p"]
    simulation_end_time = simulation_end_time or 100.0
    core_metric_ids = core_metric_ids or []

    issues: list[CompilationIssue] = []
    generated_fos: list[dict[str, Any]] = []

    # 1. Validate metric bindings
    fo_names = {fo.name for fo in measurement_plan.function_objects if fo.name}
    binding_metric_ids = {b.metric_id for b in measurement_plan.metric_bindings}

    for binding in measurement_plan.metric_bindings:
        if binding.function_object and binding.function_object not in fo_names:
            issues.append(CompilationIssue(
                severity="error",
                metric_id=binding.metric_id,
                message=f"MetricBinding for '{binding.metric_id}' references "
                        f"non-existent functionObject '{binding.function_object}'",
            ))

    # 2. Check core metrics have bindings
    for core_id in core_metric_ids:
        if core_id not in binding_metric_ids:
            issues.append(CompilationIssue(
                severity="error",
                metric_id=core_id,
                message=f"Core metric '{core_id}' has no MetricBinding — "
                        f"cannot obtain required data",
            ))

    # 3. Validate patches
    for fo in measurement_plan.function_objects:
        if fo.target_patch and fo.target_patch not in available_patches:
            issues.append(CompilationIssue(
                severity="error",
                metric_id=None,
                message=f"functionObject '{fo.name}' references "
                        f"patch '{fo.target_patch}' which is not in available patches",
            ))

    # 4. Validate fields
    for fo in measurement_plan.function_objects:
        if fo.field and fo.field not in solver_output_fields:
            # Check if it's a compound field like "mag(U)"
            base_field = fo.field.replace("mag(", "").replace(")", "")
            if base_field not in solver_output_fields:
                issues.append(CompilationIssue(
                    severity="warning",
                    metric_id=None,
                    message=f"functionObject '{fo.name}' references "
                            f"field '{fo.field}' which may not be output by solver",
                ))

    # 5. Validate time sampling
    ts = measurement_plan.time_sampling
    if ts.end_time > simulation_end_time:
        issues.append(CompilationIssue(
            severity="warning",
            metric_id=None,
            message=f"MeasurementPlan end_time ({ts.end_time}) exceeds "
                    f"simulation end_time ({simulation_end_time})",
        ))
    if ts.start_time >= ts.end_time:
        issues.append(CompilationIssue(
            severity="error",
            metric_id=None,
            message=f"Invalid time range: start ({ts.start_time}) >= end ({ts.end_time})",
        ))

    # 6. Check for errors — if any, compilation fails
    has_errors = any(i.severity == "error" for i in issues)
    if has_errors:
        return MeasurementCompilationResult(
            success=False,
            issues=issues,
        )

    # 7. Generate controlDict functionObjects
    functions_dict: dict[str, dict[str, Any]] = {}

    for fo in measurement_plan.function_objects:
        fo_dict = _render_function_object(fo, ts, measurement_plan, spec_parameters)
        if fo_dict:
            functions_dict[fo.name] = fo_dict
            generated_fos.append(fo_dict)

    # 8. Generate sampleDict if there are probes or sets
    sample_dict = None
    if measurement_plan.probes:
        sample_dict = _render_sample_dict(measurement_plan)

    # 9. Generate surfaceSamplingDict if there are surface samplings
    surface_sampling_dict = None
    surface_samplings = [s for s in measurement_plan.spatial_sampling
                         if s.type.value == "surface"]
    if surface_samplings:
        surface_sampling_dict = _render_surface_sampling_dict(surface_samplings)

    control_dict_additions = {
        "functions": functions_dict,
    }

    return MeasurementCompilationResult(
        success=True,
        control_dict_additions=control_dict_additions,
        sample_dict=sample_dict,
        surface_sampling_dict=surface_sampling_dict,
        issues=issues,
        generated_function_objects=generated_fos,
    )


def _render_function_object(
    fo: FunctionObjectSpec,
    time_sampling: Any,
    plan: MeasurementPlan | None = None,
    spec_parameters: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Render a single functionObject into OpenFOAM dict format.

    Args:
        fo: The functionObject specification.
        time_sampling: TimeSamplingSpec with start/end times.
        plan: The full MeasurementPlan (used to look up probe locations
            and surface sampling geometry for PROBES / SURFACE types).
        spec_parameters: Physical parameters extracted from ExperimentSpec
            (density, inlet_velocity, mean_velocity, diameter, extrusion_span).
            Used to populate forceCoeffs reference quantities.  Missing
            parameters fall back to reasonable defaults with a warning.
    """
    import logging

    _logger = logging.getLogger(__name__)
    write_interval = fo.write_interval
    start_time = time_sampling.start_time
    end_time = time_sampling.end_time
    spec_parameters = spec_parameters or {}

    if fo.type == FunctionObjectType.FORCE_COEFFS:
        # Extract reference quantities from spec_parameters with defaults
        rho_inf = spec_parameters.get("density", 998.2)
        mag_u_inf = (
            spec_parameters.get("inlet_velocity")
            or spec_parameters.get("mean_velocity")
            or 1.0
        )
        l_ref = spec_parameters.get("diameter", 1.0)
        # Aref = diameter * extrusion_span for 3D, or just diameter for 2D
        diameter = spec_parameters.get("diameter")
        extrusion_span = spec_parameters.get("extrusion_span")
        if diameter is not None and extrusion_span is not None:
            a_ref = diameter * extrusion_span
        elif diameter is not None:
            a_ref = diameter
        else:
            a_ref = 1.0

        # Log warnings for any missing parameters
        missing = []
        if "density" not in spec_parameters:
            missing.append("density")
        if "inlet_velocity" not in spec_parameters and "mean_velocity" not in spec_parameters:
            missing.append("inlet_velocity/mean_velocity")
        if "diameter" not in spec_parameters:
            missing.append("diameter")
        if missing:
            _logger.warning(
                "forceCoeffs using default values for missing spec parameters: %s",
                ", ".join(missing),
            )

        return {
            "type": "forceCoeffs",
            "libs": ['"libforces.so"'],
            "patches": [f'"{fo.target_patch}"'] if fo.target_patch else [],
            "rho": "rhoInf",
            "rhoInf": rho_inf,
            "liftDir": "(0 1 0)",
            "dragDir": "(1 0 0)",
            "CofR": "(0 0 0)",
            "pitchAxis": "(0 0 1)",
            "magUInf": mag_u_inf,
            "lRef": l_ref,
            "Aref": a_ref,
            "writeControl": "timeStep",
            "writeInterval": write_interval,
            "startTime": start_time,
            "endTime": end_time,
        }

    elif fo.type == FunctionObjectType.FORCES:
        rho_inf = spec_parameters.get("density", 998.2)
        return {
            "type": "forces",
            "libs": ['"libforces.so"'],
            "patches": [f'"{fo.target_patch}"'] if fo.target_patch else [],
            "rho": "rhoInf",
            "rhoInf": rho_inf,
            "writeControl": "timeStep",
            "writeInterval": write_interval,
        }

    elif fo.type == FunctionObjectType.SURFACE_FIELD_VALUE:
        # Look up surface geometry from the plan's spatial_sampling
        surface_info = {}
        if plan and fo.surface:
            for s in plan.spatial_sampling:
                if s.id == fo.surface:
                    surface_info = s.location
                    break
        result: dict[str, Any] = {
            "type": "surfaceFieldValue",
            "libs": ['"libfieldFunctionObjects.so"'],
            "surface": fo.surface or "default",
            "fields": [fo.field or "p"],
            "operation": fo.operation or "areaAverage",
            "writeControl": "timeStep",
            "writeInterval": write_interval,
        }
        if surface_info:
            if "basePoint" in surface_info:
                result["basePoint"] = surface_info["basePoint"]
            if "normal" in surface_info:
                result["normal"] = surface_info["normal"]
        return result

    elif fo.type == FunctionObjectType.PROBES:
        # Gather probe locations from the plan's probes list
        probe_locations: list[list[float]] = []
        probe_fields: list[str] = []
        if plan:
            for probe in plan.probes:
                if probe.field:
                    probe_fields.append(probe.field)
                for pos in probe.positions:
                    probe_locations.append(list(pos.values()))
        return {
            "type": "probes",
            "libs": ['"libsampling.so"'],
            "fields": probe_fields or [fo.field or "U"],
            "probeLocations": probe_locations,
            "writeControl": "timeStep",
            "writeInterval": write_interval,
        }

    elif fo.type == FunctionObjectType.FIELD_AVERAGE:
        return {
            "type": "fieldAverage",
            "libs": ['"libfieldFunctionObjects.so"'],
            "fields": [fo.field or "U"],
            "window": 10.0,
            "windowType": "approximate",
            "writeControl": "timeStep",
            "writeInterval": write_interval,
        }

    elif fo.type == FunctionObjectType.SAMPLED_SURFACES:
        # Look up surface geometry from the plan
        surface_info = {}
        if plan and fo.surface:
            for s in plan.spatial_sampling:
                if s.id == fo.surface:
                    surface_info = s.location
                    break
        result = {
            "type": "sampledSurfaces",
            "libs": ['"libsampling.so"'],
            "surfaceFormat": surface_info.get("surfaceFormat", "raw") if surface_info else "raw",
            "fields": [fo.field or "U"],
            "writeControl": "timeStep",
            "writeInterval": write_interval,
        }
        if surface_info:
            if "basePoint" in surface_info:
                result["basePoint"] = surface_info["basePoint"]
            if "normal" in surface_info:
                result["normal"] = surface_info["normal"]
        return result

    elif fo.type == FunctionObjectType.SETS:
        return {
            "type": "sets",
            "libs": ['"libsampling.so"'],
            "fields": [fo.field or "U"],
            "writeControl": "timeStep",
            "writeInterval": write_interval,
        }

    elif fo.type == FunctionObjectType.RESIDUALS:
        return {
            "type": "residuals",
            "libs": ['"libutilityFunctionObjects.so"'],
            "fields": ["U", "p"],
            "writeControl": "timeStep",
            "writeInterval": write_interval,
        }

    return {}


def _render_sample_dict(plan: MeasurementPlan) -> dict[str, Any]:
    """Render sampleDict from probes and line sampling.

    Includes real probe coordinate locations and fields extracted from the
    plan's ProbeSpec objects.
    """
    fields_set: set[str] = set()
    probe_locations: list[list[float]] = []
    write_intervals: list[int] = []
    for p in plan.probes:
        if p.field:
            fields_set.add(p.field)
        for pos in p.positions:
            probe_locations.append(list(pos.values()))
        write_intervals.append(p.write_interval)

    return {
        "probes": {
            "fields": list(fields_set),
            "probeLocations": probe_locations,
            "writeControl": "timeStep",
            "writeInterval": min(write_intervals) if write_intervals else 1,
        },
    }


def _render_surface_sampling_dict(surfaces: list) -> dict[str, Any]:
    """Render surfaceSamplingDict from surface sampling specs.

    Includes basePoint, normal, fields, and surfaceFormat extracted from the
    SpatialSamplingSpec location dict when available.
    """
    rendered_surfaces: list[dict[str, Any]] = []
    for s in surfaces:
        loc = s.location or {}
        entry: dict[str, Any] = {
            "name": s.id,
            "type": "sampledSurface",
            "surfaceType": "plane",
            "description": s.description,
        }
        if "basePoint" in loc:
            entry["basePoint"] = loc["basePoint"]
        if "normal" in loc:
            entry["normal"] = loc["normal"]
        if "fields" in loc:
            entry["fields"] = loc["fields"]
        else:
            entry["fields"] = ["p", "U"]
        if "surfaceFormat" in loc:
            entry["surfaceFormat"] = loc["surfaceFormat"]
        else:
            entry["surfaceFormat"] = "raw"
        if "writeInterval" in loc:
            entry["writeInterval"] = loc["writeInterval"]
        rendered_surfaces.append(entry)

    return {"surfaces": rendered_surfaces}


__all__ = [
    "CompilationIssue",
    "MeasurementCompilationResult",
    "compile_measurement_plan",
]
