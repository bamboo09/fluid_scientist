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

# Mapping of experiment types to applicable parameter IDs.
# Only parameters in this list will be included for each experiment type.
# "unknown" includes all parameters as a fallback.
EXPERIMENT_TYPE_PARAMETERS: dict[str, frozenset[str]] = {
    "cylinder_flow": frozenset({
        "diameter",
        "domain_width",
        "domain_height",
        "inlet_velocity",
        "density",
        "kinematic_viscosity",
        "reynolds_number",
        "strouhal_number",
        "end_time",
        "time_step",
        "max_courant",
        "cells_radial",
        "cells_wake",
    }),
    "laminar_pipe": frozenset({
        "diameter",
        "length",
        "mean_velocity",
        "mass_flow_rate",
        "outlet_pressure",
        "density",
        "kinematic_viscosity",
        "reynolds_number",
        "end_time",
        "axial_cells",
        "radial_cells",
    }),
    "lid_driven_cavity": frozenset({
        "side_length",
        "lid_velocity",
        "density",
        "kinematic_viscosity",
        "reynolds_number",
        "end_time",
        "cells_per_side",
    }),
}


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
    compressibility: str | None,
    temporal: str | None,
    phase: str | None,
) -> tuple[str | None, list[str]]:
    """Recommend an OpenFOAM solver based on physics.

    Returns (solver_name, unsupported_features).
    Returns (None, unsupported) when critical physics fields are unknown,
    so the caller can decide on a fallback with appropriate warnings.
    """
    unsupported: list[str] = []

    if compressibility is None or temporal is None or phase is None:
        return None, unsupported

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

    # Default fallback for known but unmatched combinations
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
    experiment_type: str = "unknown",
) -> list[ParameterSpec]:
    """Select relevant parameters based on physics specification and experiment type.

    When experiment_type is known (cylinder_flow, laminar_pipe, lid_driven_cavity),
    only parameters applicable to that experiment type are included.
    When experiment_type is "unknown", all parameters are included as a fallback.
    """
    params: list[ParameterSpec] = []

    # Determine which parameter IDs are applicable
    applicable_ids = EXPERIMENT_TYPE_PARAMETERS.get(experiment_type)
    if applicable_ids is None:
        # Unknown experiment type: include all parameters as fallback
        all_entries: list[OntologyEntry] = []
        for category in ParameterCategory:
            all_entries.extend(ontology.by_category(category))
        for entry in all_entries:
            if entry.parameter_id == "reynolds_number" and reynolds is not None:
                params.append(_entry_to_param(entry, value=reynolds))
            else:
                params.append(_entry_to_param(entry))
        return params

    # Known experiment type: only include applicable parameters
    for param_id in sorted(applicable_ids):
        entry = ontology.get(param_id)
        if entry is None:
            continue
        if entry.parameter_id == "reynolds_number" and reynolds is not None:
            params.append(_entry_to_param(entry, value=reynolds))
        else:
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
    if "diameter" in geometry_params and (
        "cells_wake" in geometry_params
        or "domain_width" in geometry_params
        or "domain_height" in geometry_params
    ):
        return "cylinder_flow"

    # Check for pipe flow indicators
    if "length" in geometry_params and (
        "axial_cells" in geometry_params
        or "mass_flow_rate" in geometry_params
        or "mean_velocity" in geometry_params
    ):
        return "laminar_pipe"

    # Check for cavity flow indicators
    if "side_length" in geometry_params and "lid_velocity" in geometry_params:
        return "lid_driven_cavity"

    # Try to infer from physics spec
    flow_regime = getattr(physics, "flow_regime", None)
    if flow_regime is not None:
        flow_regime_str = (
            flow_regime.value if hasattr(flow_regime, "value") else str(flow_regime)
        )
        if flow_regime_str in ("internal_pipe", "internal_flow"):
            return "laminar_pipe"
        if flow_regime_str in ("external_flow", "external"):
            return "cylinder_flow"
        if flow_regime_str in ("cavity_flow", "cavity"):
            return "lid_driven_cavity"

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

    # Recommend solver — extract enum values from PhysicsSpec.
    # High-risk fields are NOT silently defaulted; None triggers warnings.
    compressibility = getattr(physics, "compressibility", None)
    if compressibility is not None:
        compressibility = (
            compressibility.value if hasattr(compressibility, "value") else str(compressibility)
        )
    else:
        compressibility = None  # 不再静默默认 "incompressible"
        warnings.append("compressibility is unknown - system recommendation will be used")

    temporal = getattr(physics, "temporal_type", None)
    if temporal is not None:
        temporal = temporal.value if hasattr(temporal, "value") else str(temporal)
    else:
        temporal = None  # 不再静默默认 "steady"
        warnings.append("temporal_type is unknown - system recommendation will be used")

    phase = getattr(physics, "phases", None)
    if phase is not None:
        phase = phase.value if hasattr(phase, "value") else str(phase)
    else:
        phase = None  # 不再静默默认 "single_phase"
        warnings.append("phases is unknown - system recommendation will be used")

    solver, solver_unsupported = _recommend_solver(compressibility, temporal, phase)
    unsupported.extend(solver_unsupported)
    if solver is None:
        warnings.append(
            "solver could not be determined from unknown physics "
            "- using simpleFoam as system fallback"
        )
        solver = "simpleFoam"

    # Recommend turbulence model
    turb_model = _recommend_turbulence_model(reynolds, experiment_type)

    # Select parameters
    params = _select_parameters(ontology, physics, reynolds, experiment_type)

    # Update with existing values
    if existing_params:
        updated: list[ParameterSpec] = []
        for p in params:
            if p.parameter_id in existing_params:
                updated.append(p.model_copy(update={"value": existing_params[p.parameter_id]}))
            else:
                updated.append(p)
        params = updated

    # Add warnings for unsupported features
    if unsupported:
        warnings.extend(f"Feature '{f}' is not supported in the current stage" for f in unsupported)

    if experiment_type == "unknown":
        warnings.append("Could not detect experiment type; using generic parameter schema")

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
