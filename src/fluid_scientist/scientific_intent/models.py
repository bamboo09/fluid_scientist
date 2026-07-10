"""Generic structured scientific intent models.

These models form the universal semantic layer between natural-language
research descriptions and the deterministic downstream pipeline
(experiment design, closure, metric compilation, capability resolution,
case generation, validation).

Every model carries ``source_evidence`` -- a list of text spans (or
references) that justify the extracted value -- so provenance is never
lost.  LLM output must conform to these schemas; the backend validates
strictly.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    """A piece of source evidence supporting an extracted value."""

    text: str
    span_start: int | None = None
    span_end: int | None = None
    confidence: float = 0.8


class DimensionalValue(BaseModel):
    """A value with explicit unit and provenance."""

    value: float | str | dict[str, Any] | list[Any] | None = None
    unit: str | None = None
    source: Literal[
        "USER_SPECIFIED",
        "SYSTEM_DERIVED",
        "SYSTEM_SELECTED",
        "TEMPLATE_DEFAULT",
        "ASSUMED_BASELINE",
    ] = "SYSTEM_SELECTED"
    reason: str = ""
    confidence: float = 0.8
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. StudyIntent (top-level)
# ---------------------------------------------------------------------------


class StudyIntent(BaseModel):
    """Top-level scientific intent for a single CFD study."""

    study_id: str
    title: str = ""
    raw_text: str
    research_objective: str
    research_hypothesis: str = ""
    physics_family: str = ""  # e.g. external_aerodynamics, internal_flow, ...
    flow_regime: str = ""  # laminar / transitional / turbulent
    temporal_mode: Literal["steady", "transient", "periodic", "statistical_steady"] = "transient"
    multiphase: bool = False
    heat_transfer: bool = False
    combustion: bool = False
    fsi: bool = False  # fluid-structure interaction
    entities: list[PhysicsEntity] = Field(default_factory=list)
    geometry: GeometryIntent | None = None
    motions: list[MotionIntent] = Field(default_factory=list)
    materials: list[MaterialIntent] = Field(default_factory=list)
    boundaries: list[BoundaryIntent] = Field(default_factory=list)
    initial_conditions: list[InitialConditionIntent] = Field(default_factory=list)
    physical_models: PhysicalModelIntent | None = None
    numerical: NumericalIntent | None = None
    analysis_goals: list[AnalysisGoal] = Field(default_factory=list)
    measurements: list[MeasurementIntent] = Field(default_factory=list)
    comparisons: list[ComparisonIntent] = Field(default_factory=list)
    credibility: CredibilityRequirement | None = None
    user_constraints: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 2. PhysicsEntity
# ---------------------------------------------------------------------------


class PhysicsEntity(BaseModel):
    """A physical body/region in the simulation."""

    entity_id: str
    name: str
    entity_role: Literal[
        "fluid_domain",
        "solid_body",
        "porous_medium",
        "immersed_boundary",
        "overset_region",
        "secondary_phase",
    ] = "fluid_domain"
    material_id: str | None = None
    motion_id: str | None = None
    geometry_ref: str | None = None
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 3. GeometryIntent  (generic -- no pipe/cylinder/channel hard-coding)
# ---------------------------------------------------------------------------


class GeometryIntent(BaseModel):
    """Generic description of computational geometry and domain."""

    geometry_family: str = ""
    primitive_or_imported: Literal["primitive", "imported_mesh", "imported_cad", "procedural"] = "primitive"
    dimensions: dict[str, DimensionalValue] = Field(default_factory=dict)
    orientation: dict[str, float] = Field(default_factory=dict)
    relative_positions: dict[str, Any] = Field(default_factory=dict)
    wall_proximity: dict[str, DimensionalValue] = Field(default_factory=dict)
    moving_parts: list[str] = Field(default_factory=list)
    symmetry: dict[str, Any] = Field(default_factory=dict)
    periodicity: dict[str, Any] = Field(default_factory=dict)
    domain_extents: dict[str, DimensionalValue] = Field(default_factory=dict)
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. MotionIntent  (rotating, pitching, translating, 6-DoF, etc.)
# ---------------------------------------------------------------------------


class MotionIntent(BaseModel):
    """Generic motion description for moving bodies or dynamic meshes."""

    motion_id: str
    motion_type: Literal[
        "static",
        "constant_velocity",
        "constant_rotation",
        "oscillatory_translation",
        "oscillatory_rotation",
        "prescribed_rigid_body",
        "six_dof",
        "overset",
        "morphing",
        "dynamic_mesh",
        "slider_interface",
        "none",
    ] = "static"
    target_body: str = ""
    axis: list[float] = Field(default_factory=list)
    center: list[float] = Field(default_factory=list)
    law: str = ""  # analytical law name if prescribed
    mean_value: DimensionalValue | None = None
    amplitude: DimensionalValue | None = None
    frequency: DimensionalValue | None = None
    phase: DimensionalValue | None = None
    angular_velocity: DimensionalValue | None = None
    linear_velocity: DimensionalValue | None = None
    dimensionless_parameters: dict[str, float] = Field(default_factory=dict)
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. MaterialIntent
# ---------------------------------------------------------------------------


class MaterialIntent(BaseModel):
    """Fluid/solid material property description."""

    material_id: str
    name: str = ""
    phase: Literal["fluid", "solid", "gas", "liquid", "particle"] = "fluid"
    density: DimensionalValue | None = None
    dynamic_viscosity: DimensionalValue | None = None
    kinematic_viscosity: DimensionalValue | None = None
    specific_heat: DimensionalValue | None = None
    thermal_conductivity: DimensionalValue | None = None
    prandtl_number: float | None = None
    speed_of_sound: DimensionalValue | None = None
    newtonian: bool = True
    dimensionless_groups: dict[str, float] = Field(default_factory=dict)
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 6. BoundaryIntent
# ---------------------------------------------------------------------------


class BoundaryIntent(BaseModel):
    """Generic boundary condition intent."""

    boundary_id: str
    physical_role: Literal[
        "wall",
        "inlet",
        "outlet",
        "farfield",
        "symmetry",
        "periodic",
        "interface",
        "cyclic_ami",
        "overset_interface",
        "conjugate_interface",
        "empty",
        "wedge",
    ] = "wall"
    wall_type: Literal[
        "no_slip_static",
        "no_slip_moving",
        "free_slip",
        "rough",
        "suction_blowing",
        "porous_wall",
        "not_applicable",
    ] = "not_applicable"
    inlet_type: Literal[
        "fixed_velocity",
        "mass_flow",
        "fully_developed",
        "turbulent_inflow",
        "perturbed",
        "not_applicable",
    ] = "not_applicable"
    outlet_type: Literal[
        "fixed_pressure",
        "convective",
        "zero_gradient",
        "advective",
        "non_reflecting",
        "not_applicable",
    ] = "not_applicable"
    patch_selector: dict[str, Any] = Field(default_factory=dict)
    field_conditions: dict[str, DimensionalValue] = Field(default_factory=dict)
    motion_coupling: dict[str, Any] = Field(default_factory=dict)
    turbulence_specification: dict[str, Any] = Field(default_factory=dict)
    thermal_condition: dict[str, Any] = Field(default_factory=dict)
    verification_metrics: list[str] = Field(default_factory=list)
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 7. InitialConditionIntent
# ---------------------------------------------------------------------------


class InitialConditionIntent(BaseModel):
    """Initial condition specification."""

    field: str  # U, p, T, k, omega, etc.
    value_type: Literal["uniform", "field", "perturbed", "restart", "mapping"] = "uniform"
    value: DimensionalValue | None = None
    perturbation: dict[str, Any] = Field(default_factory=dict)
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 8. PhysicalModelIntent
# ---------------------------------------------------------------------------


class PhysicalModelIntent(BaseModel):
    """Selection of physical closure models."""

    turbulence_model: str = ""  # laminar / Smagorinsky / WALE / kOmegaSST / ...
    turbulence_model_family: Literal["laminar", "RANS", "LES", "DES", "hybrid"] = "laminar"
    wall_treatment: Literal["wall_resolved", "wall_function", "hybrid"] = "wall_resolved"
    multiphase_model: str = ""  # VOF / Euler-Euler / ...
    combustion_model: str = ""
    radiation_model: str = ""
    heat_transfer: bool = False
    compressibility: Literal["incompressible", "weakly_compressible", "compressible"] = "incompressible"
    gravity: list[float] = Field(default_factory=list)
    source_terms: list[str] = Field(default_factory=list)
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 9. NumericalIntent
# ---------------------------------------------------------------------------


class NumericalIntent(BaseModel):
    """Numerical method and solver intent."""

    solver_family: str = ""  # pimpleFoam / rhoSimpleFoam / ...
    time_integration: str = ""
    spatial_discretization: dict[str, str] = Field(default_factory=dict)
    pressure_velocity_coupling: str = ""
    max_courant: float | None = None
    target_y_plus: float | None = None
    convergence_criteria: dict[str, float] = Field(default_factory=dict)
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 10. AnalysisGoal
# ---------------------------------------------------------------------------


class AnalysisGoal(BaseModel):
    """A scientific analysis goal that drives metric compilation."""

    goal_id: str
    phenomenon: str  # vortex_shedding, wake_recovery, heat_transfer, mixing, ...
    target_quantity: str  # drag_coefficient, strouhal_number, nusselt_number, ...
    spatial_region: str = "full_domain"
    temporal_mode: Literal[
        "instantaneous",
        "time_averaged",
        "phase_averaged",
        "statistical",
        "spectral",
        "steady",
    ] = "time_averaged"
    statistic: str = ""  # mean, rms, psd, pdf, ...
    comparison_dimension: str = ""  # Re, angle, frequency, ...
    evidence_text: str = ""
    priority: Literal["primary", "secondary", "exploratory"] = "primary"
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 11. MeasurementIntent
# ---------------------------------------------------------------------------


class MeasurementIntent(BaseModel):
    """Where and how to sample/measure data."""

    measurement_id: str
    measurement_type: Literal[
        "point_probe",
        "line_sample",
        "surface_sample",
        "volume_field",
        "force_coefficient",
        "surface_integral",
        "volume_integral",
        "particle_tracking",
    ] = "point_probe"
    target_regions: list[str] = Field(default_factory=list)
    target_patches: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    sampling_frequency: DimensionalValue | None = None
    sampling_start_time: DimensionalValue | None = None
    averaging_duration: DimensionalValue | None = None
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 12. ComparisonIntent
# ---------------------------------------------------------------------------


class ComparisonIntent(BaseModel):
    """Parametric or cross-validation comparison targets."""

    comparison_id: str
    comparison_type: Literal["parametric_sweep", "validation_against_data", "grid_convergence", "model_comparison"]
    varying_parameter: str = ""
    reference_data_source: str = ""
    target_quantities: list[str] = Field(default_factory=list)
    source_evidence: list[Evidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 13. CredibilityRequirement
# ---------------------------------------------------------------------------


class CredibilityRequirement(BaseModel):
    """Numerical credibility requirements (GCI, conservation, etc.)."""

    gci_target: float | None = None
    mass_conservation_tolerance: float | None = None
    momentum_conservation_tolerance: float | None = None
    residual_target: dict[str, float] = Field(default_factory=dict)
    statistical_convergence: dict[str, Any] = Field(default_factory=dict)
    required_validation_metrics: list[str] = Field(default_factory=list)
    source_evidence: list[Evidence] = Field(default_factory=list)


__all__ = [
    "AnalysisGoal",
    "BoundaryIntent",
    "ComparisonIntent",
    "CredibilityRequirement",
    "DimensionalValue",
    "Evidence",
    "GeometryIntent",
    "InitialConditionIntent",
    "MaterialIntent",
    "MeasurementIntent",
    "MotionIntent",
    "NumericalIntent",
    "PhysicalModelIntent",
    "PhysicsEntity",
    "StudyIntent",
]
