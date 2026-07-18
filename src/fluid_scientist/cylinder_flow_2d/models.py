"""CylinderFlow2DExperimentSpecV1 — 二维可配置圆柱绕流实验规格.

This is the single source of truth for the CylinderFlow2D experiment family.
The LLM generates this structured Spec through a multi-pass pipeline,
never OpenFOAM files directly.

Key design principles:
- Every field carries provenance (value, source, status, confidence)
- USER_CONFIRMED > USER_EXPLICIT > FORMULA_DERIVED > SYSTEM_DERIVED > MODEL_RECOMMENDED > SYSTEM_DEFAULT
- Model recommendations NEVER override user-explicit values
- front/back are always 'empty' for 2D (SYSTEM_DERIVED, immutable)
- bottom_profile is optional — its absence does NOT block geometry
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Source priority — the single authoritative hierarchy
# ---------------------------------------------------------------------------


class FieldSource(str, Enum):
    """Source of a field value, with strict priority ordering."""

    USER_CONFIRMED = "USER_CONFIRMED"
    USER_EXPLICIT = "USER_EXPLICIT"
    FORMULA_DERIVED = "FORMULA_DERIVED"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    MODEL_RECOMMENDED = "MODEL_RECOMMENDED"
    SYSTEM_DEFAULT = "SYSTEM_DEFAULT"

    @classmethod
    def priority(cls, source: FieldSource) -> int:
        """Higher number = higher priority (wins over lower)."""
        order = {
            cls.USER_CONFIRMED: 100,
            cls.USER_EXPLICIT: 90,
            cls.FORMULA_DERIVED: 70,
            cls.SYSTEM_DERIVED: 50,
            cls.MODEL_RECOMMENDED: 30,
            cls.SYSTEM_DEFAULT: 10,
        }
        return order.get(source, 0)

    @classmethod
    def should_override(
        cls, existing_source: FieldSource, new_source: FieldSource
    ) -> bool:
        """Return True if new_source should override existing_source."""
        return cls.priority(new_source) > cls.priority(existing_source)


class FieldStatus(str, Enum):
    """Status of a field in the spec."""

    RESOLVED = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"


# ---------------------------------------------------------------------------
# Provenance-bearing field wrapper
# ---------------------------------------------------------------------------


class ProvenanceField(BaseModel):
    """A field value with full provenance tracking."""

    model_config = ConfigDict(extra="forbid")

    value: Any = None
    source: FieldSource = FieldSource.SYSTEM_DEFAULT
    status: FieldStatus = FieldStatus.UNRESOLVED
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str | None = None

    def is_resolved(self) -> bool:
        return self.value is not None and self.status in (
            FieldStatus.RESOLVED,
            FieldStatus.AWAITING_CONFIRMATION,
        )

    def is_user_provided(self) -> bool:
        return self.source in (FieldSource.USER_CONFIRMED, FieldSource.USER_EXPLICIT)


# ---------------------------------------------------------------------------
# Enums for the spec
# ---------------------------------------------------------------------------


class FlowMode(str, Enum):
    """Supported flow topology modes."""

    INLET_OUTLET = "inlet_outlet"
    PERIODIC_FORCED = "periodic_forced"
    PRESSURE_DIFFERENCE = "pressure_difference"
    OPEN_DOMAIN = "open_domain"
    WALL_DRIVEN = "wall_driven"
    COMBINED_DRIVING = "combined_driving"


class SemanticBoundaryType(str, Enum):
    """Semantic boundary types — NOT OpenFOAM dictionary types.

    These are the normalized scientific concepts that the user and model
    communicate in. The compiler maps these to OpenFOAM entries later.
    """

    # Inlet types
    UNIFORM_VELOCITY_INLET = "uniform_velocity_inlet"
    TIME_VARYING_VELOCITY_INLET = "time_varying_velocity_inlet"
    SPATIAL_NONUNIFORM_VELOCITY_INLET = "spatial_nonuniform_velocity_inlet"
    PRESSURE_INLET = "pressure_inlet"

    # Outlet types
    PRESSURE_OUTLET = "pressure_outlet"
    OPEN_OUTLET = "open_outlet"
    ADVECTIVE_OUTLET = "advective_outlet"

    # Wall types
    NO_SLIP_WALL = "no_slip_wall"
    SLIP_WALL = "slip_wall"
    MOVING_WALL = "moving_wall"
    SHEAR_STRESS = "shear_stress"

    # Other
    SYMMETRY = "symmetry"
    FREESTREAM = "freestream"
    OPEN_BOUNDARY = "open_boundary"
    PERIODIC = "periodic"
    EMPTY = "empty"
    PRESSURE_BOUNDARY = "pressure_boundary"


class BumpProfileType(str, Enum):
    """Bottom profile shape types."""

    FLAT = "flat"
    COSINE_BELL = "cosine_bell"
    HALF_SINE = "half_sine"
    GAUSSIAN = "gaussian"
    PIECEWISE_POINTS = "piecewise_points"


class CylinderWallType(str, Enum):
    """Cylinder surface boundary types."""

    NO_SLIP_WALL = "no_slip_wall"
    ROTATING_WALL = "rotating_wall"
    SLIP_WALL = "slip_wall"


class TemporalType(str, Enum):
    """Inlet temporal profile types."""

    CONSTANT = "constant"
    RAMP = "ramp"
    SINUSOIDAL = "sinusoidal"
    PIECEWISE_LINEAR = "piecewise_linear"
    TABULATED = "tabulated"


class SpatialType(str, Enum):
    """Inlet spatial profile types."""

    UNIFORM = "uniform"
    PARABOLIC = "parabolic"
    POWER_LAW = "power_law"
    LINEAR_SHEAR = "linear_shear"
    TABULATED_PROFILE = "tabulated_profile"


class TimeMode(str, Enum):
    """Simulation time mode."""

    STEADY = "steady"
    TRANSIENT = "transient"
    AUTO = "auto"


class FlowRegime(str, Enum):
    """Flow regime classification."""

    LAMINAR = "laminar"
    TURBULENT = "turbulent"
    AUTO = "auto"


class ObservableType(str, Enum):
    """Types of observables."""

    POINT_VELOCITY = "point_velocity"
    SECTION_MEAN_VELOCITY = "section_mean_velocity"
    SECTION_FLOW_RATE = "section_flow_rate"
    CYLINDER_DRAG = "cylinder_drag"
    CYLINDER_LIFT = "cylinder_lift"
    WALL_SHEAR_STRESS = "wall_shear_stress"
    RECIRCULATION_LENGTH = "recirculation_length"
    VELOCITY_MAGNITUDE_FIELD = "velocity_magnitude_field"
    PRESSURE_FIELD = "pressure_field"
    VORTICITY_FIELD = "vorticity_field"
    STREAMLINES = "streamlines"
    DRAG_LIFT_TIME_SERIES = "drag_lift_time_series"
    WAKE_SHEDDING_FREQUENCY = "wake_shedding_frequency"


class DraftStatus(str, Enum):
    """Draft status — the only states the state machine can be in."""

    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    READY_TO_CONFIRM = "READY_TO_CONFIRM"
    SPEC_CONFIRMED = "SPEC_CONFIRMED"


class PressureGradientUnit(str, Enum):
    """Pressure gradient unit."""

    PA_PER_M = "Pa/m"
    M_PER_S2 = "m/s²"


# ---------------------------------------------------------------------------
# Provenance-bearing boundary spec
# ---------------------------------------------------------------------------


class BoundarySpec(BaseModel):
    """A single boundary with provenance."""

    model_config = ConfigDict(extra="allow")

    semantic_type: SemanticBoundaryType | None = None
    source: FieldSource = FieldSource.SYSTEM_DEFAULT
    status: FieldStatus = FieldStatus.UNRESOLVED
    confidence: float = 0.0

    # Parameters (all optional, filled based on type)
    inlet_velocity: float | None = None
    pressure_value: float | None = None
    velocity_vector: list[float] | None = None
    shear_direction: list[float] | None = None
    shear_magnitude: float | None = None
    freestream_velocity: float | None = None
    pressure_gradient_magnitude: float | None = None


# ---------------------------------------------------------------------------
# Domain, Fluid, Cylinder, Bottom Profile
# ---------------------------------------------------------------------------


class DomainSpec(BaseModel):
    """Computational domain."""

    model_config = ConfigDict(extra="forbid")

    length_m: ProvenanceField = Field(default_factory=ProvenanceField)
    height_m: ProvenanceField = Field(default_factory=ProvenanceField)
    thickness_m: ProvenanceField = Field(
        default_factory=lambda: ProvenanceField(
            value=1.0,
            source=FieldSource.SYSTEM_DEFAULT,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
            reason="2D single-layer extrusion default",
        )
    )
    dimensionality: str = "2D"


class FluidSpec(BaseModel):
    """Fluid properties."""

    model_config = ConfigDict(extra="forbid")

    type: ProvenanceField = Field(default_factory=ProvenanceField)
    temperature_c: ProvenanceField = Field(default_factory=ProvenanceField)
    density_kg_m3: ProvenanceField = Field(default_factory=ProvenanceField)
    kinematic_viscosity_m2_s: ProvenanceField = Field(default_factory=ProvenanceField)


class CylinderSpec(BaseModel):
    """Cylinder obstacle configuration with provenance."""

    model_config = ConfigDict(extra="forbid")

    type: str = "cylinder"
    radius_m: ProvenanceField = Field(default_factory=ProvenanceField)
    diameter_m: ProvenanceField = Field(default_factory=ProvenanceField)
    characteristic_dimension_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_x_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_y_m: ProvenanceField = Field(default_factory=ProvenanceField)
    wall_type: CylinderWallType = CylinderWallType.NO_SLIP_WALL
    angular_velocity_rad_s: float = 0.0
    rotation_direction: Literal["cw", "ccw"] = "ccw"


class RectangleSpec(BaseModel):
    """Rectangle obstacle configuration with provenance."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    width_m: ProvenanceField = Field(default_factory=ProvenanceField)
    height_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_x_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_y_m: ProvenanceField = Field(default_factory=ProvenanceField)
    relation_to_cylinder: str | None = None


class TriangleSpec(BaseModel):
    """Triangle obstacle configuration with provenance.

    Semantic type: triangle_2d
    Solver representation: polygon (3-vertex polygon via snappyHexMesh STL)
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_width_m: ProvenanceField = Field(default_factory=ProvenanceField)
    height_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_x_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_y_m: ProvenanceField = Field(default_factory=ProvenanceField)
    apex_direction: str = "up"
    relation_to_cylinder: str | None = None
    attached_boundary: str | None = None
    semantic_type: str = "triangle_2d"
    solver_representation: str = "polygon"
    source_text: str | None = None


class TrapezoidSpec(BaseModel):
    """Trapezoid obstacle configuration with provenance.

    Semantic type: trapezoid_2d
    Solver representation: polygon (4-vertex polygon via snappyHexMesh STL)

    Uses generic parametric_polygon representation:
    top_width + bottom_width + height → 4 vertices.
    No dedicated TrapezoidCompiler — compiled via PolygonGeometryCompiler.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    top_width_m: ProvenanceField = Field(default_factory=ProvenanceField)
    bottom_width_m: ProvenanceField = Field(default_factory=ProvenanceField)
    height_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_x_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_y_m: ProvenanceField = Field(default_factory=ProvenanceField)
    apex_direction: str = "up"  # wide base at bottom, narrow top
    relation_to_cylinder: str | None = None
    attached_boundary: str | None = None
    semantic_type: str = "trapezoid_2d"
    solver_representation: str = "parametric_polygon"
    source_text: str | None = None


class PolygonSpec(BaseModel):
    """Custom polygon obstacle configuration with provenance.

    Semantic type: custom_polygon_2d
    Solver representation: polygon_stl (arbitrary polygon via snappyHexMesh STL)

    Stores an ordered list of vertices ``[[x0, y0], [x1, y1], ...]`` that
    define the polygon boundary in counter-clockwise order.  The polygon is
    extruded in z by the domain thickness to create a 2D prism for
    snappyHexMesh.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    vertices: list[list[float]] = Field(default_factory=list)
    center_x_m: ProvenanceField = Field(default_factory=ProvenanceField)
    center_y_m: ProvenanceField = Field(default_factory=ProvenanceField)
    semantic_type: str = "custom_polygon_2d"
    solver_representation: str = "polygon_stl"
    source_text: str | None = None


class BottomProfileSpec(BaseModel):
    """Bottom profile configuration — optional."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    profile_type: BumpProfileType = BumpProfileType.FLAT
    center_x_m: ProvenanceField = Field(default_factory=ProvenanceField)
    width_m: ProvenanceField = Field(default_factory=ProvenanceField)
    height_m: ProvenanceField = Field(default_factory=ProvenanceField)
    custom_points: list[list[float]] = Field(default_factory=list)
    aligned_below_cylinder: bool = False


class ForcingSpec(BaseModel):
    """Forcing specification."""

    model_config = ConfigDict(extra="forbid")

    class PressureGradientSpec(BaseModel):
        model_config = ConfigDict(extra="forbid")
        enabled: bool = False
        direction: list[float] = Field(default_factory=lambda: [1.0, 0.0, 0.0])
        magnitude: ProvenanceField = Field(default_factory=ProvenanceField)
        unit: ProvenanceField = Field(default_factory=ProvenanceField)

    class BodyForceSpec(BaseModel):
        model_config = ConfigDict(extra="forbid")
        enabled: bool = False
        vector_m_s2: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])

    pressure_gradient: PressureGradientSpec = Field(default_factory=PressureGradientSpec)
    body_force: BodyForceSpec = Field(default_factory=BodyForceSpec)


class InletProfileSpec(BaseModel):
    """Inlet velocity profile."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    temporal_type: TemporalType = TemporalType.CONSTANT
    spatial_type: SpatialType = SpatialType.UNIFORM
    parameters: dict[str, Any] = Field(default_factory=dict)


class InitialConditionsSpec(BaseModel):
    """Initial conditions."""

    model_config = ConfigDict(extra="forbid")

    class VelocityIC(BaseModel):
        model_config = ConfigDict(extra="forbid")
        type: str = "quiescent"
        vector_m_s: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])

    velocity: VelocityIC = Field(default_factory=VelocityIC)


class SimulationSpec(BaseModel):
    """Simulation control."""

    model_config = ConfigDict(extra="forbid")

    time_mode: TimeMode = TimeMode.AUTO
    flow_regime: FlowRegime = FlowRegime.AUTO
    max_courant_number: float = 0.5
    end_time: float | None = None
    delta_t: float | None = None


# ---------------------------------------------------------------------------
# Observables and Analysis Goals
# ---------------------------------------------------------------------------


class ObservableSpec(BaseModel):
    """An observation target with provenance."""

    model_config = ConfigDict(extra="forbid")

    type: ObservableType
    label: str | None = None
    component: str = "Ux"
    spatial_operation: str | None = None
    temporal_operation: str | None = None
    point: list[float] | None = None
    section_x: float | None = None
    cylinder_id: str | None = None
    wall_name: str | None = None
    source: FieldSource = FieldSource.MODEL_RECOMMENDED
    status: FieldStatus = FieldStatus.AWAITING_CONFIRMATION
    missing_fields: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class AnalysisGoalSpec(BaseModel):
    """An analysis goal with provenance."""

    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    related_observables: list[str] = Field(default_factory=list)
    source: FieldSource = FieldSource.MODEL_RECOMMENDED
    status: FieldStatus = FieldStatus.AWAITING_CONFIRMATION
    confidence: float = 0.5


# ---------------------------------------------------------------------------
# Boundary configuration
# ---------------------------------------------------------------------------


class BoundaryConfig(BaseModel):
    """Complete boundary configuration for all domain sides."""

    model_config = ConfigDict(extra="forbid")

    left: BoundarySpec = Field(default_factory=BoundarySpec)
    right: BoundarySpec = Field(default_factory=BoundarySpec)
    top: BoundarySpec = Field(default_factory=BoundarySpec)
    bottom_flat: BoundarySpec = Field(default_factory=BoundarySpec)
    bottom_profile_surface: BoundarySpec = Field(default_factory=BoundarySpec)
    front: BoundarySpec = Field(
        default_factory=lambda: BoundarySpec(
            semantic_type=SemanticBoundaryType.EMPTY,
            source=FieldSource.SYSTEM_DERIVED,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
            reason="2D simulation: front must be empty",
        )
    )
    back: BoundarySpec = Field(
        default_factory=lambda: BoundarySpec(
            semantic_type=SemanticBoundaryType.EMPTY,
            source=FieldSource.SYSTEM_DERIVED,
            status=FieldStatus.RESOLVED,
            confidence=1.0,
            reason="2D simulation: back must be empty",
        )
    )


# ---------------------------------------------------------------------------
# Decision summary (for audit, not full chain-of-thought)
# ---------------------------------------------------------------------------


class DecisionSummary(BaseModel):
    """Structured decision summary — replaces raw chain-of-thought."""

    model_config = ConfigDict(extra="forbid")

    facts: list[str] = Field(default_factory=list)
    derived_values: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    rejected_interpretations: list[str] = Field(default_factory=list)
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Top-level Experiment Spec
# ---------------------------------------------------------------------------


class CylinderFlow2DExperimentSpecV1(BaseModel):
    """Complete experiment specification for CylinderFlow2D.

    This is the single source of truth. The multi-pass pipeline produces
    this structured spec. The compiler reads this spec and deterministically
    produces OpenFOAM Foundation 13 case files.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    spec_version: int = 1
    case_family: str = "cylinder_flow_2d"
    pipeline_id: str = "cylinder-flow-2d-v1"
    pipeline_stage: str = "DRAFT_NORMALIZED"

    domain: DomainSpec = Field(default_factory=DomainSpec)
    fluid: FluidSpec = Field(default_factory=FluidSpec)
    cylinder: CylinderSpec = Field(default_factory=CylinderSpec)
    rectangle: RectangleSpec = Field(default_factory=RectangleSpec)
    triangle: TriangleSpec = Field(default_factory=TriangleSpec)
    trapezoid: TrapezoidSpec = Field(default_factory=TrapezoidSpec)
    polygon: PolygonSpec = Field(default_factory=PolygonSpec)
    bottom_profile: BottomProfileSpec = Field(default_factory=BottomProfileSpec)
    flow_topology: dict[str, Any] = Field(default_factory=lambda: {"mode": None})
    boundaries: BoundaryConfig = Field(default_factory=BoundaryConfig)
    forcing: ForcingSpec = Field(default_factory=ForcingSpec)
    inlet_profile: InletProfileSpec = Field(default_factory=InletProfileSpec)
    initial_conditions: InitialConditionsSpec = Field(default_factory=InitialConditionsSpec)
    simulation: SimulationSpec = Field(default_factory=SimulationSpec)

    observables: list[ObservableSpec] = Field(default_factory=list)
    analysis_goals: list[AnalysisGoalSpec] = Field(default_factory=list)

    # Metadata
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)
    blocking_issues: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    decision_summary: DecisionSummary = Field(default_factory=DecisionSummary)

    draft_status: DraftStatus = DraftStatus.NEEDS_CLARIFICATION
    experiment_id: str | None = None
    title: str | None = None
    objective: str | None = None
    user_input_text: str | None = None

    @model_validator(mode="after")
    def enforce_2d_boundary(self) -> CylinderFlow2DExperimentSpecV1:
        """Enforce that front/back are always 'empty' for 2D."""
        # front/back are SYSTEM_DERIVED and cannot be changed
        self.boundaries.front.semantic_type = SemanticBoundaryType.EMPTY
        self.boundaries.front.source = FieldSource.SYSTEM_DERIVED
        self.boundaries.front.status = FieldStatus.RESOLVED
        self.boundaries.back.semantic_type = SemanticBoundaryType.EMPTY
        self.boundaries.back.source = FieldSource.SYSTEM_DERIVED
        self.boundaries.back.status = FieldStatus.RESOLVED
        return self

    @property
    def has_cylinder(self) -> bool:
        return self.cylinder.radius_m.is_resolved() or self.cylinder.diameter_m.is_resolved()

    @property
    def has_rectangle(self) -> bool:
        return self.rectangle.enabled and self.rectangle.width_m.is_resolved()

    @property
    def has_triangle(self) -> bool:
        return self.triangle.enabled and self.triangle.base_width_m.is_resolved()

    @property
    def has_trapezoid(self) -> bool:
        return self.trapezoid.enabled and self.trapezoid.bottom_width_m.is_resolved()

    @property
    def has_polygon(self) -> bool:
        return self.polygon.enabled and len(self.polygon.vertices) >= 3

    @property
    def has_bottom_profile(self) -> bool:
        return self.bottom_profile.enabled and self.bottom_profile.profile_type != BumpProfileType.FLAT

    @property
    def is_periodic(self) -> bool:
        return (
            self.boundaries.left.semantic_type == SemanticBoundaryType.PERIODIC
            and self.boundaries.right.semantic_type == SemanticBoundaryType.PERIODIC
        )

    @property
    def is_transient(self) -> bool:
        if self.simulation.time_mode == TimeMode.TRANSIENT:
            return True
        if self.simulation.time_mode == TimeMode.AUTO:
            if self.has_cylinder:
                return True
            if self.inlet_profile.enabled and self.inlet_profile.temporal_type != TemporalType.CONSTANT:
                return True
        return False

    def get_cylinder_diameter(self) -> float | None:
        """Get the cylinder diameter, deriving from radius if needed."""
        if self.cylinder.diameter_m.is_resolved():
            return self.cylinder.diameter_m.value
        if self.cylinder.radius_m.is_resolved():
            return self.cylinder.radius_m.value * 2.0
        return None

    def get_cylinder_radius(self) -> float | None:
        """Get the cylinder radius, deriving from diameter if needed."""
        if self.cylinder.radius_m.is_resolved():
            return self.cylinder.radius_m.value
        if self.cylinder.diameter_m.is_resolved():
            return self.cylinder.diameter_m.value / 2.0
        return None

    def get_characteristic_dimension(self) -> float | None:
        """Get the characteristic dimension (cylinder diameter)."""
        d = self.get_cylinder_diameter()
        if d is not None:
            return d
        return None

    def estimate_reynolds(self) -> float | None:
        """Estimate Reynolds number based on available parameters."""
        nu = self.fluid.kinematic_viscosity_m2_s.value
        if nu is None or nu <= 0:
            return None

        char_length = self.get_characteristic_dimension()
        if char_length is None:
            char_length = self.domain.height_m.value
        if char_length is None or char_length <= 0:
            return None

        char_velocity = None
        if self.boundaries.left.semantic_type in (
            SemanticBoundaryType.UNIFORM_VELOCITY_INLET,
            SemanticBoundaryType.TIME_VARYING_VELOCITY_INLET,
            SemanticBoundaryType.SPATIAL_NONUNIFORM_VELOCITY_INLET,
        ):
            char_velocity = self.boundaries.left.inlet_velocity
        elif self.inlet_profile.enabled:
            p = self.inlet_profile.parameters
            if self.inlet_profile.temporal_type == TemporalType.CONSTANT:
                char_velocity = p.get("velocity") or p.get("max_velocity")
            elif self.inlet_profile.temporal_type == TemporalType.SINUSOIDAL:
                char_velocity = p.get("mean_velocity")
            elif self.inlet_profile.temporal_type == TemporalType.RAMP:
                char_velocity = p.get("end_velocity")

        if char_velocity is None or char_velocity <= 0:
            return None
        return char_velocity * char_length / nu

    def to_semantic_display(self) -> dict[str, Any]:
        """Generate a human-readable semantic display dict for the UI panel.

        This is what the right-side research plan panel should show —
        scientific semantics, NOT raw OpenFOAM JSON.
        """
        display: dict[str, Any] = {}

        # Domain
        display["计算域"] = {
            "dimensionality": self.domain.dimensionality,
            "length_m": self.domain.length_m.value,
            "height_m": self.domain.height_m.value,
            "source_length": self.domain.length_m.source.value,
            "source_height": self.domain.height_m.source.value,
        }

        # Cylinder
        radius = self.get_cylinder_radius()
        diameter = self.get_cylinder_diameter()
        display["圆柱"] = {
            "type": "圆柱" if self.has_cylinder else "未指定",
            "radius_m": radius,
            "diameter_m": diameter,
            "characteristic_dimension_m": self.get_characteristic_dimension(),
            "center_x_m": self.cylinder.center_x_m.value,
            "center_y_m": self.cylinder.center_y_m.value,
            "center_status": self.cylinder.center_x_m.status.value
            if self.cylinder.center_x_m.value is None
            else "RESOLVED",
            "source_radius": self.cylinder.radius_m.source.value,
            "source_diameter": self.cylinder.diameter_m.source.value,
        }

        # Triangle obstacle
        if self.triangle.enabled:
            display["三角障碍物"] = {
                "type": "三角形",
                "base_width_m": self.triangle.base_width_m.value,
                "height_m": self.triangle.height_m.value,
                "center_x_m": self.triangle.center_x_m.value,
                "source_base_width": self.triangle.base_width_m.source.value,
                "source_height": self.triangle.height_m.source.value,
                "relation_to_cylinder": self.triangle.relation_to_cylinder,
            }

        # Rectangle obstacle
        if self.rectangle.enabled:
            display["矩形障碍物"] = {
                "type": "矩形",
                "width_m": self.rectangle.width_m.value,
                "height_m": self.rectangle.height_m.value,
                "center_x_m": self.rectangle.center_x_m.value,
                "source_width": self.rectangle.width_m.source.value,
                "source_height": self.rectangle.height_m.source.value,
                "relation_to_cylinder": self.rectangle.relation_to_cylinder,
            }

        # Trapezoid obstacle
        if self.trapezoid.enabled:
            display["梯形障碍物"] = {
                "type": "梯形",
                "top_width_m": self.trapezoid.top_width_m.value,
                "bottom_width_m": self.trapezoid.bottom_width_m.value,
                "height_m": self.trapezoid.height_m.value,
                "center_x_m": self.trapezoid.center_x_m.value,
                "source_top_width": self.trapezoid.top_width_m.source.value,
                "source_bottom_width": self.trapezoid.bottom_width_m.source.value,
                "source_height": self.trapezoid.height_m.source.value,
                "relation_to_cylinder": self.trapezoid.relation_to_cylinder,
                "solver_representation": self.trapezoid.solver_representation,
            }

        # Custom polygon obstacle
        if self.polygon.enabled:
            display["多边形障碍物"] = {
                "type": "自定义多边形",
                "vertices": self.polygon.vertices,
                "vertex_count": len(self.polygon.vertices),
                "center_x_m": self.polygon.center_x_m.value,
                "center_y_m": self.polygon.center_y_m.value,
                "semantic_type": self.polygon.semantic_type,
                "solver_representation": self.polygon.solver_representation,
            }

        # Bottom profile
        if self.has_bottom_profile:
            display["底部轮廓"] = {
                "type": self.bottom_profile.profile_type.value,
                "center_x_m": self.bottom_profile.center_x_m.value,
                "width_m": self.bottom_profile.width_m.value,
                "height_m": self.bottom_profile.height_m.value,
            }
        else:
            display["底部轮廓"] = {"type": "平直"}

        # Boundaries
        boundary_labels = {
            "left": "左侧边界",
            "right": "右侧边界",
            "top": "顶部边界",
            "bottom_flat": "底部边界",
            "front": "前侧边界",
            "back": "后侧边界",
        }

        semantic_labels = {
            SemanticBoundaryType.UNIFORM_VELOCITY_INLET: "恒定速度入口",
            SemanticBoundaryType.TIME_VARYING_VELOCITY_INLET: "时变速度入口",
            SemanticBoundaryType.SPATIAL_NONUNIFORM_VELOCITY_INLET: "空间非均匀入口",
            SemanticBoundaryType.PRESSURE_INLET: "压力入口",
            SemanticBoundaryType.PRESSURE_OUTLET: "压力出口",
            SemanticBoundaryType.OPEN_OUTLET: "开放出口",
            SemanticBoundaryType.ADVECTIVE_OUTLET: "对流出口",
            SemanticBoundaryType.NO_SLIP_WALL: "无滑移壁面",
            SemanticBoundaryType.SLIP_WALL: "滑移壁面",
            SemanticBoundaryType.MOVING_WALL: "运动壁面",
            SemanticBoundaryType.SHEAR_STRESS: "剪切应力",
            SemanticBoundaryType.SYMMETRY: "对称边界",
            SemanticBoundaryType.FREESTREAM: "自由流",
            SemanticBoundaryType.OPEN_BOUNDARY: "开放边界",
            SemanticBoundaryType.PERIODIC: "周期边界",
            SemanticBoundaryType.EMPTY: "二维empty",
            SemanticBoundaryType.PRESSURE_BOUNDARY: "压力边界",
        }

        source_labels = {
            FieldSource.USER_CONFIRMED: "用户确认",
            FieldSource.USER_EXPLICIT: "用户输入",
            FieldSource.FORMULA_DERIVED: "公式派生",
            FieldSource.SYSTEM_DERIVED: "系统派生",
            FieldSource.MODEL_RECOMMENDED: "模型推荐",
            FieldSource.SYSTEM_DEFAULT: "系统默认",
        }

        for side, label in boundary_labels.items():
            b = getattr(self.boundaries, side)
            st = b.semantic_type
            display[label] = {
                "type": semantic_labels.get(st, st.value if st else "未指定"),
                "source": source_labels.get(b.source, b.source.value),
                "status": b.status.value,
            }

        # Observables
        display["观测量"] = [
            {
                "type": obs.type.value,
                "label": obs.label,
                "status": obs.status.value,
                "source": source_labels.get(obs.source, obs.source.value),
                "missing_fields": obs.missing_fields,
            }
            for obs in self.observables
        ]

        # Analysis goals
        display["分析目标"] = [
            {
                "description": goal.description,
                "source": source_labels.get(goal.source, goal.source.value),
                "status": goal.status.value,
            }
            for goal in self.analysis_goals
        ]

        # Status
        display["状态"] = {
            "draft_status": self.draft_status.value,
            "blocking_issues": len(self.blocking_issues),
            "unresolved_fields": self.unresolved_fields,
        }

        return display


# ---------------------------------------------------------------------------
# Model Policy for multi-pass pipeline
# ---------------------------------------------------------------------------


class ModelPolicy(BaseModel):
    """Model routing policy for the multi-pass pipeline."""

    model_config = ConfigDict(extra="forbid")

    classifier_model: str = "fast"
    reasoning_model: str = "reasoning"
    critic_model: str = "reasoning"
    temperature: float = 0.1
    structured_output: bool = True
    enable_reasoning: bool = True


__all__ = [
    "AnalysisGoalSpec",
    "BottomProfileSpec",
    "BoundaryConfig",
    "BoundarySpec",
    "BumpProfileType",
    "CylinderFlow2DExperimentSpecV1",
    "CylinderSpec",
    "CylinderWallType",
    "DecisionSummary",
    "DomainSpec",
    "DraftStatus",
    "FieldSource",
    "FieldStatus",
    "FlowMode",
    "FlowRegime",
    "FluidSpec",
    "ForcingSpec",
    "InletProfileSpec",
    "InitialConditionsSpec",
    "ModelPolicy",
    "ObservableSpec",
    "ObservableType",
    "PolygonSpec",
    "PressureGradientUnit",
    "ProvenanceField",
    "SemanticBoundaryType",
    "SimulationSpec",
    "SpatialType",
    "TemporalType",
    "TimeMode",
    "TrapezoidSpec",
    "TriangleSpec",
]
