"""Dynamic Schema Engine — generates parameter schemas from physics requirements.

Given a PhysicsSpec (flow regime, compressibility, phase, etc.), the engine
determines which parameters are needed, selects appropriate defaults, and
produces a complete parameter schema for the experiment.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import StrEnum
from fluid_scientist.dynamic_schema.ontology import (
    OntologyEntry,
    ParameterCategory,
    ParameterOntology,
    default_ontology,
)
from fluid_scientist.experiment_spec.models import (
    ParameterConstraints,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    PhysicsSpec,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class FlowPhase(StrEnum):
    SINGLE_PHASE = "single_phase"
    MULTI_PHASE = "multi_phase"


class SchemaGenerationResult(StrictModel):
    """Result of dynamic schema generation.

    Attributes:
        parameters: Generated parameter specifications.
        experiment_type: Detected experiment type.
        solver_recommendation: Recommended OpenFOAM solver.
        turbulence_model: Recommended turbulence model (or None for laminar).
        warnings: Warnings about the generated schema.
        unsupported_features: Physics features not supported in current stage.
    """

    parameters: tuple[ParameterSpec, ...] = Field(min_length=1, max_length=100)
    experiment_type: str = Field(min_length=1, max_length=128)
    solver_recommendation: str = Field(min_length=1, max_length=128)
    turbulence_model: str | None = None
    warnings: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    unsupported_features: tuple[str, ...] = Field(default_factory=tuple, max_length=50)


def _is_turbulent(reynolds: float | None) -> bool:
    """Determine if flow is turbulent based on Reynolds number."""
    if reynolds is None:
        return False
    # Internal flow: Re > 2300 is transitional, > 4000 is turbulent
    return reynolds > 2300


def _recommend_solver(
    compressibility: str,
    temporal: str,
    phase: str,
) -> tuple[str, list[str]]:
    """Recommend an OpenFOAM solver based on physics.

    Returns (solver_name, unsupported_features).
    """
    unsupported: list[str] = []

    if phase != FlowPhase.SINGLE_PHASE.value:
        unsupported.append("multi_phase_flow")

    if compressibility == "incompressible":
        if temporal == "steady":
            return "simpleFoam", unsupported
        if temporal == "transient":
            return "pimpleFoam", unsupported
    elif compressibility == "compressible":
        if temporal == "steady":
            return "rhoSimpleFoam", unsupported
        if temporal == "transient":
            return "rhoPimpleFoam", unsupported

    # Default fallback
    return "simpleFoam", unsupported


def _recommend_turbulence_model(
    reynolds: float | None,
    geometry_type: str,
) -> str | None:
    """Recommend a turbulence model or None for laminar flow."""
    if reynolds is None:
        return None
    if reynolds < 2300:
        return None  # Laminar
    # For turbulent flow, recommend kOmegaSST as default
    # (good for wall-bounded flows and adverse pressure gradients)
    return "kOmegaSST"


def _select_parameters(
    ontology: ParameterOntology,
    physics: PhysicsSpec,
    reynolds: float | None,
) -> list[ParameterSpec]:
    """Select relevant parameters based on physics specification."""
    params: list[ParameterSpec] = []

    # Always include geometry parameters
    geometry_entries = ontology.by_category(ParameterCategory.GEOMETRY)
    for entry in geometry_entries:
        params.append(_entry_to_param(entry))

    # Include boundary conditions
    bc_entries = ontology.by_category(ParameterCategory.BOUNDARY_CONDITION)
    for entry in bc_entries:
        params.append(_entry_to_param(entry))

    # Include material properties
    mat_entries = ontology.by_category(ParameterCategory.MATERIAL_PROPERTY)
    for entry in mat_entries:
        params.append(_entry_to_param(entry))

    # Include Reynolds number (dimensionless)
    dim_entries = ontology.by_category(ParameterCategory.DIMENSIONLESS)
    for entry in dim_entries:
        if entry.parameter_id == "reynolds_number" and reynolds is not None:
            params.append(_entry_to_param(entry, value=reynolds))
        else:
            params.append(_entry_to_param(entry))

    # Include mesh parameters
    mesh_entries = ontology.by_category(ParameterCategory.MESH)
    for entry in mesh_entries:
        params.append(_entry_to_param(entry))

    # Include time parameters
    time_entries = ontology.by_category(ParameterCategory.TIME)
    for entry in time_entries:
        params.append(_entry_to_param(entry))

    return params


def _entry_to_param(
    entry: OntologyEntry,
    value: Any = None,
) -> ParameterSpec:
    """Convert an OntologyEntry to a ParameterSpec."""
    constraints = None
    if entry.typical_range_min is not None and entry.typical_range_max is not None:
        constraints = ParameterConstraints(
            min=entry.typical_range_min,
            max=entry.typical_range_max,
        )

    return ParameterSpec(
        parameter_id=entry.parameter_id,
        display_name=entry.display_name,
        category=entry.category.value,
        value=value,
        unit=entry.unit,
        data_type=entry.data_type,
        source=ParameterSourceInfo(
            type=ParameterSource.SYSTEM_RECOMMENDED,
            reference=f"ontology:{entry.parameter_id}",
        ),
        constraints=constraints,
    )


def detect_experiment_type(
    physics: PhysicsSpec,
    geometry_params: dict[str, Any] | None = None,
) -> str:
    """Detect the experiment type from physics spec and geometry parameters.

    Returns one of: "cylinder_flow", "laminar_pipe", "lid_driven_cavity",
    or "unknown".
    """
    if geometry_params is None:
        geometry_params = {}

    # Check for cylinder flow indicators
    if "diameter" in geometry_params and "cells_wake" in geometry_params:
        return "cylinder_flow"

    # Check for pipe flow indicators
    if "length" in geometry_params and "axial_cells" in geometry_params:
        return "laminar_pipe"

    # Check for cavity flow indicators
    if "side_length" in geometry_params and "lid_velocity" in geometry_params:
        return "lid_driven_cavity"

    # Try to infer from physics spec
    flow_regime = getattr(physics, "flow_regime", None)
    if flow_regime == "internal_pipe":
        return "laminar_pipe"
    if flow_regime == "external_flow":
        return "cylinder_flow"

    return "unknown"


def generate_schema(
    physics: PhysicsSpec,
    ontology: ParameterOntology | None = None,
    existing_params: dict[str, Any] | None = None,
) -> SchemaGenerationResult:
    """Generate a parameter schema from a PhysicsSpec.

    Args:
        physics: Physics specification (compressibility, temporal, phase, etc.)
        ontology: Parameter ontology. If None, uses default_ontology().
        existing_params: Optional dict of existing parameter values to
            incorporate (e.g., from a research question).

    Returns:
        SchemaGenerationResult with generated parameters and recommendations.
    """
    if ontology is None:
        ontology = default_ontology()

    if existing_params is None:
        existing_params = {}

    warnings: list[str] = []
    unsupported: list[str] = []

    # Extract Reynolds number if available
    reynolds = existing_params.get("reynolds_number")
    if reynolds is not None:
        reynolds = float(reynolds)

    # Detect experiment type
    experiment_type = detect_experiment_type(physics, existing_params)

    # Recommend solver — extract enum values from PhysicsSpec
    compressibility = getattr(physics, "compressibility", None)
    if compressibility is not None:
        compressibility = (
            compressibility.value
            if hasattr(compressibility, "value")
            else str(compressibility)
        )
    else:
        compressibility = "incompressible"

    temporal = getattr(physics, "temporal_type", None)
    if temporal is not None:
        temporal = (
            temporal.value
            if hasattr(temporal, "value")
            else str(temporal)
        )
    else:
        temporal = "steady"

    phase = getattr(physics, "phases", None)
    if phase is not None:
        phase = (
            phase.value
            if hasattr(phase, "value")
            else str(phase)
        )
    else:
        phase = FlowPhase.SINGLE_PHASE.value

    solver, solver_unsupported = _recommend_solver(compressibility, temporal, phase)
    unsupported.extend(solver_unsupported)

    # Recommend turbulence model
    turb_model = _recommend_turbulence_model(reynolds, experiment_type)

    # Select parameters
    params = _select_parameters(ontology, physics, reynolds)

    # Update with existing values
    if existing_params:
        updated: list[ParameterSpec] = []
        for p in params:
            if p.parameter_id in existing_params:
                updated.append(
                    p.model_copy(update={"value": existing_params[p.parameter_id]})
                )
            else:
                updated.append(p)
        params = updated

    # Add warnings for unsupported features
    if unsupported:
        warnings.extend(
            f"Feature '{f}' is not supported in the current stage" for f in unsupported
        )

    if experiment_type == "unknown":
        warnings.append(
            "Could not detect experiment type; using generic parameter schema"
        )

    if reynolds is not None and reynolds > 1e6:
        warnings.append(
            f"Reynolds number {reynolds:.0f} is very high; "
            "results may require additional validation"
        )

    return SchemaGenerationResult(
        parameters=tuple(params),
        experiment_type=experiment_type,
        solver_recommendation=solver,
        turbulence_model=turb_model,
        warnings=tuple(warnings),
        unsupported_features=tuple(unsupported),
    )


__all__ = [
    "FlowPhase",
    "SchemaGenerationResult",
    "detect_experiment_type",
    "generate_schema",
]
