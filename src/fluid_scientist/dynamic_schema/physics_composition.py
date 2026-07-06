"""Physics module composition — maps physics requirements to OpenFOAM modules.

Determines the appropriate solver, turbulence model, boundary conditions,
discretization schemes, and linear solvers based on the physics specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


@dataclass(frozen=True)
class SolverCapability:
    """Capability description for an OpenFOAM solver.

    Attributes:
        solver_name: The solver executable name (e.g., "simpleFoam").
        supported_physics: Set of physics keywords this solver supports.
        supported_compressibility: "incompressible", "compressible", or "both".
        supported_temporal: "steady", "transient", or "both".
        supported_phases: Set of phase types ("single_phase", "multi_phase").
        requires_turbulence_model: Whether a turbulence model must be specified.
    """

    solver_name: str
    supported_physics: frozenset[str]
    supported_compressibility: str  # "incompressible", "compressible", "both"
    supported_temporal: str  # "steady", "transient", "both"
    supported_phases: frozenset[str]
    requires_turbulence_model: bool


@dataclass(frozen=True)
class TurbulenceModelSpec:
    """Specification of a turbulence model.

    Attributes:
        model_name: OpenFOAM turbulence model name.
        applicable_reynolds_min: Minimum Reynolds number for applicability.
        applicable_reynolds_max: Maximum Reynolds number (None = no upper limit).
        good_for: Set of flow features this model handles well.
        requires_wall_function: Whether wall functions are needed.
        y_plus_target: Target y+ range for near-wall mesh.
    """

    model_name: str
    applicable_reynolds_min: float
    applicable_reynolds_max: float | None
    good_for: frozenset[str]
    requires_wall_function: bool
    y_plus_target: tuple[float, float]


@dataclass(frozen=True)
class PhysicsModuleComposition:
    """Complete physics module configuration for a simulation.

    Attributes:
        solver: Selected OpenFOAM solver.
        turbulence_model: Selected turbulence model (None for laminar).
        boundary_conditions: Recommended boundary condition types.
        discretization_schemes: Recommended discretization schemes.
        linear_solvers: Recommended linear solver settings.
        warnings: Warnings about the composition.
        compatibility_issues: Issues that may affect results.
    """

    solver: str
    turbulence_model: str | None
    boundary_conditions: dict[str, str] = field(default_factory=dict)
    discretization_schemes: dict[str, str] = field(default_factory=dict)
    linear_solvers: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    compatibility_issues: list[str] = field(default_factory=list)


# --- Solver registry ---

_SOLVERS: dict[str, SolverCapability] = {
    "simpleFoam": SolverCapability(
        solver_name="simpleFoam",
        supported_physics=frozenset({"steady", "incompressible", "internal_flow", "external_flow"}),
        supported_compressibility="incompressible",
        supported_temporal="steady",
        supported_phases=frozenset({"single_phase"}),
        requires_turbulence_model=True,
    ),
    "pimpleFoam": SolverCapability(
        solver_name="pimpleFoam",
        supported_physics=frozenset({
            "transient", "incompressible",
            "internal_flow", "external_flow",
        }),
        supported_compressibility="incompressible",
        supported_temporal="transient",
        supported_phases=frozenset({"single_phase"}),
        requires_turbulence_model=True,
    ),
    "rhoSimpleFoam": SolverCapability(
        solver_name="rhoSimpleFoam",
        supported_physics=frozenset({"steady", "compressible"}),
        supported_compressibility="compressible",
        supported_temporal="steady",
        supported_phases=frozenset({"single_phase"}),
        requires_turbulence_model=True,
    ),
    "rhoPimpleFoam": SolverCapability(
        solver_name="rhoPimpleFoam",
        supported_physics=frozenset({"transient", "compressible"}),
        supported_compressibility="compressible",
        supported_temporal="transient",
        supported_phases=frozenset({"single_phase"}),
        requires_turbulence_model=True,
    ),
    "interFoam": SolverCapability(
        solver_name="interFoam",
        supported_physics=frozenset({"transient", "incompressible", "multi_phase", "free_surface"}),
        supported_compressibility="incompressible",
        supported_temporal="transient",
        supported_phases=frozenset({"multi_phase"}),
        requires_turbulence_model=True,
    ),
}

# --- Turbulence model registry ---

_TURBULENCE_MODELS: dict[str, TurbulenceModelSpec] = {
    "kOmegaSST": TurbulenceModelSpec(
        model_name="kOmegaSST",
        applicable_reynolds_min=2300,
        applicable_reynolds_max=None,
        good_for=frozenset({"wall_bounded", "adverse_pressure_gradient", "separation"}),
        requires_wall_function=False,
        y_plus_target=(30.0, 300.0),
    ),
    "kEpsilon": TurbulenceModelSpec(
        model_name="kEpsilon",
        applicable_reynolds_min=4000,
        applicable_reynolds_max=None,
        good_for=frozenset({"free_shear", "jet", "wake"}),
        requires_wall_function=True,
        y_plus_target=(30.0, 300.0),
    ),
    "SpalartAllmaras": TurbulenceModelSpec(
        model_name="SpalartAllmaras",
        applicable_reynolds_min=2300,
        applicable_reynolds_max=None,
        good_for=frozenset({"aerospace", "external_aerodynamics"}),
        requires_wall_function=False,
        y_plus_target=(1.0, 5.0),
    ),
    "laminar": TurbulenceModelSpec(
        model_name="laminar",
        applicable_reynolds_min=0,
        applicable_reynolds_max=2300,
        good_for=frozenset({"low_reynolds", "creeping_flow"}),
        requires_wall_function=False,
        y_plus_target=(0.0, 1.0),
    ),
}


def get_solver_capability(solver_name: str) -> SolverCapability | None:
    """Look up a solver's capability description."""
    return _SOLVERS.get(solver_name)


def get_turbulence_model(name: str) -> TurbulenceModelSpec | None:
    """Look up a turbulence model specification."""
    return _TURBULENCE_MODELS.get(name)


def list_solvers() -> tuple[str, ...]:
    """Return all registered solver names."""
    return tuple(sorted(_SOLVERS.keys()))


def list_turbulence_models() -> tuple[str, ...]:
    """Return all registered turbulence model names."""
    return tuple(sorted(_TURBULENCE_MODELS.keys()))


def check_solver_capability(
    solver_name: str,
    compressibility: str = "incompressible",
    temporal: str = "steady",
    phase: str = "single_phase",
) -> tuple[bool, list[str]]:
    """Check if a solver supports the required physics.

    Returns (is_capable, list_of_issues).
    """
    solver = _SOLVERS.get(solver_name)
    if solver is None:
        return False, [f"unknown solver: {solver_name}"]

    issues: list[str] = []

    if (
        solver.supported_compressibility != "both"
        and solver.supported_compressibility != compressibility
    ):
            issues.append(
                f"{solver_name} does not support {compressibility} flow "
                f"(supports {solver.supported_compressibility})"
            )

    if solver.supported_temporal != "both" and solver.supported_temporal != temporal:
        issues.append(
            f"{solver_name} does not support {temporal} simulation "
            f"(supports {solver.supported_temporal})"
        )

    if phase not in solver.supported_phases:
        issues.append(
            f"{solver_name} does not support {phase} flow"
        )

    return len(issues) == 0, issues


def recommend_turbulence_model(
    reynolds: float,
    flow_features: frozenset[str] | None = None,
) -> str:
    """Recommend a turbulence model based on Reynolds number and flow features.

    Returns the model name, or "laminar" if Re < 2300.
    """
    if reynolds < 2300:
        return "laminar"

    if flow_features is None:
        flow_features = frozenset()

    # Score each turbulent model
    best_model = "kOmegaSST"  # Default
    best_score = 0

    for name, spec in _TURBULENCE_MODELS.items():
        if name == "laminar":
            continue
        if spec.applicable_reynolds_min > reynolds:
            continue
        if spec.applicable_reynolds_max is not None and reynolds > spec.applicable_reynolds_max:
            continue

        # Score by overlap with desired features
        score = len(spec.good_for & flow_features)
        if score > best_score:
            best_score = score
            best_model = name

    return best_model


def compose_physics_modules(
    solver_name: str,
    reynolds: float | None,
    flow_features: frozenset[str] | None = None,
    geometry_type: str = "internal",
) -> PhysicsModuleComposition:
    """Compose a complete physics module configuration.

    Args:
        solver_name: The OpenFOAM solver to use.
        reynolds: Reynolds number (None if unknown).
        flow_features: Set of flow feature keywords (e.g., "wall_bounded").
        geometry_type: "internal" or "external".

    Returns:
        PhysicsModuleComposition with all recommendations.
    """
    warnings: list[str] = []
    issues: list[str] = []

    # Validate solver exists
    solver = _SOLVERS.get(solver_name)
    if solver is None:
        return PhysicsModuleComposition(
            solver=solver_name,
            turbulence_model=None,
            warnings=[],
            compatibility_issues=[f"unknown solver: {solver_name}"],
        )

    # Determine turbulence model
    if reynolds is None:
        turb_model = "kOmegaSST"
        warnings.append("Reynolds number unknown; defaulting to kOmegaSST")
    elif reynolds < 2300:
        turb_model = None  # Laminar
    else:
        turb_model = recommend_turbulence_model(reynolds, flow_features)

    # Check turbulence model applicability
    if turb_model is not None:
        turb_spec = _TURBULENCE_MODELS.get(turb_model)
        if (
            turb_spec is not None
            and reynolds is not None
            and turb_spec.applicable_reynolds_min > reynolds
        ):
                warnings.append(
                    f"{turb_model} requires Re >= {turb_spec.applicable_reynolds_min}, "
                    f"but Re = {reynolds:.0f}"
                )

    # Recommend boundary conditions based on geometry type
    bc: dict[str, str] = {}
    if geometry_type == "internal":
        bc = {
            "inlet": "fixedValue (velocity)",
            "outlet": "inletOutlet (pressure)",
            "walls": "noSlip",
        }
    elif geometry_type == "external":
        bc = {
            "inlet": "freestreamVelocity",
            "outlet": "freestreamPressure",
            "walls": "noSlip",
            "farfield": "slip",
        }
    else:
        bc = {"inlet": "fixedValue", "outlet": "zeroGradient", "walls": "noSlip"}

    # Recommend discretization schemes
    if solver.supported_temporal == "steady":
        schemes = {
            "ddtSchemes": "steadyState",
            "gradSchemes": "Gauss linear",
            "divSchemes": "Gauss linearUpwind",
            "laplacianSchemes": "Gauss linear corrected",
        }
    else:
        schemes = {
            "ddtSchemes": "backward",
            "gradSchemes": "Gauss linear",
            "divSchemes": "Gauss linearUpwind",
            "laplacianSchemes": "Gauss linear corrected",
        }

    # Recommend linear solvers
    linear: dict[str, str] = {
        "U": "PBiCGStab",
        "p": "GAMG",
        "k": "PBiCGStab",
        "omega": "PBiCGStab",
        "epsilon": "PBiCGStab",
    }

    # Check solver-turbulence compatibility
    if turb_model is not None and solver.requires_turbulence_model:
        turb_spec = _TURBULENCE_MODELS.get(turb_model)
        if (
            turb_spec is not None
            and turb_spec.requires_wall_function
            and geometry_type == "external"
        ):
                warnings.append(
                    f"{turb_model} requires wall functions; "
                    "ensure y+ is in target range"
                )

    return PhysicsModuleComposition(
        solver=solver_name,
        turbulence_model=turb_model,
        boundary_conditions=bc,
        discretization_schemes=schemes,
        linear_solvers=linear,
        warnings=warnings,
        compatibility_issues=issues,
    )


def handle_unknown_scenario(
    research_question: str,
    detected_type: str = "unknown",
) -> dict[str, Any]:
    """Graceful fallback for scenarios that don't match known templates.

    Returns a recommendation dict with:
    - closest_match: The closest known experiment type
    - recommendations: Suggested actions
    - required_custom_parameters: Parameters that need manual specification
    - risk_level: "low", "medium", or "high"
    """
    recommendations: list[str] = []
    custom_params: list[str] = []
    risk_level = "medium"

    question_lower = research_question.lower() if research_question else ""

    # Try to find closest match by keyword
    closest = "unknown"
    if any(kw in question_lower for kw in ("圆柱", "cylinder", "bluff body")):
        closest = "cylinder_flow"
    elif any(kw in question_lower for kw in ("管", "pipe", "tube", "channel")):
        closest = "laminar_pipe"
    elif any(kw in question_lower for kw in ("腔", "cavity", "lid")):
        closest = "lid_driven_cavity"

    if closest != "unknown":
        recommendations.append(
            f"Closest known template: '{closest}'. "
            "Consider using this template as a starting point."
        )
        risk_level = "low"
    else:
        recommendations.append(
            "No close template match found. Manual configuration required."
        )
        custom_params.extend(["geometry", "boundary_conditions", "solver"])
        risk_level = "high"

    recommendations.append(
        "Validate physics assumptions before running simulation."
    )

    if risk_level == "high":
        recommendations.append(
            "High-risk scenario: perform a pilot run with coarse mesh first."
        )

    return {
        "closest_match": closest,
        "recommendations": recommendations,
        "required_custom_parameters": custom_params,
        "risk_level": risk_level,
        "detected_type": detected_type,
    }


__all__ = [
    "PhysicsModuleComposition",
    "SolverCapability",
    "TurbulenceModelSpec",
    "check_solver_capability",
    "compose_physics_modules",
    "get_solver_capability",
    "get_turbulence_model",
    "handle_unknown_scenario",
    "list_solvers",
    "list_turbulence_models",
    "recommend_turbulence_model",
]
