"""ObstacleFlowExperimentSpecV1 — 二维可配置障碍物流动实验规格.

This module defines the complete structured experiment specification for
the ConfigurableObstacleFlow2D scenario family, covering domain, fluid,
geometry (bump + cylinder), flow topology, boundaries, inlet profiles,
forcing, simulation settings, observables, and plot requests.

The spec is the single source of truth — LLM generates this structured
Spec, never OpenFOAM files directly.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FlowMode(str, Enum):
    """Supported flow topology modes."""

    INLET_OUTLET = "inlet_outlet"
    PERIODIC_FORCED = "periodic_forced"
    PRESSURE_DIFFERENCE = "pressure_difference"
    OPEN_DOMAIN = "open_domain"
    WALL_DRIVEN = "wall_driven"
    COMBINED_DRIVING = "combined_driving"


class SpecSource(str, Enum):
    """Source of a parameter value."""

    USER_EXPLICIT = "user_explicit"
    USER_CONFIRMED = "user_confirmed"
    MODEL_RECOMMENDED = "model_recommended"
    FORMULA_DERIVED = "formula_derived"
    SYSTEM_DEFAULT = "system_default"


class SpecStatus(str, Enum):
    """Status of a field in the spec."""

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class BoundaryType(str, Enum):
    """Boundary condition types."""

    # Inlet types
    VELOCITY_INLET = "velocity_inlet"
    PRESSURE_INLET = "pressure_inlet"
    MASS_FLOW_INLET = "mass_flow_inlet"

    # Outlet types
    PRESSURE_OUTLET = "pressure_outlet"
    OPEN_OUTLET = "open_outlet"
    ADVECTIVE_OUTLET = "advective_outlet"
    NON_REFLECTING_OUTLET = "non_reflecting_outlet"

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
    """Bump profile shape types."""

    COSINE_BELL = "cosine_bell"
    HALF_SINE = "half_sine"
    GAUSSIAN = "gaussian"
    PIECEWISE_POINTS = "piecewise_points"


class CylinderBoundaryType(str, Enum):
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


class TurbulenceModel(str, Enum):
    """Turbulence model selection."""

    NONE = "none"
    KOMEGA_SST = "kOmegaSST"
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


class PlotRequest(str, Enum):
    """Default plot request types."""

    VELOCITY_MAGNITUDE = "velocity_magnitude"
    UX = "ux"
    PRESSURE = "pressure"
    VORTICITY = "vorticity"
    STREAMLINES = "streamlines"
    CD_CL_TIME_SERIES = "cd_cl_time_series"
    INLET_RESPONSE = "inlet_response"


class PressureGradientUnit(str, Enum):
    """Pressure gradient unit."""

    PA_PER_M = "Pa/m"
    M_PER_S2 = "m/s²"


# ---------------------------------------------------------------------------
# Field provenance mixin
# ---------------------------------------------------------------------------


class FieldProvenance(BaseModel):
    """Provenance metadata for a spec field."""

    model_config = ConfigDict(extra="forbid")

    source: SpecSource = SpecSource.SYSTEM_DEFAULT
    status: SpecStatus = SpecStatus.UNRESOLVED
    confidence: Literal["high", "medium", "low"] = "low"
    unit: str | None = None
    reason: str | None = None


# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------


class DomainSpec(BaseModel):
    """Rectangular computational domain."""

    model_config = ConfigDict(extra="forbid")

    length_m: float = Field(..., gt=0, description="Domain length in x-direction")
    height_m: float = Field(..., gt=0, description="Domain height in y-direction")
    thickness_m: float = Field(1.0, gt=0, description="Single-layer extrusion thickness for 2D")

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)


# ---------------------------------------------------------------------------
# Fluid
# ---------------------------------------------------------------------------


class FluidSpec(BaseModel):
    """Fluid properties — single-phase, incompressible, Newtonian."""

    model_config = ConfigDict(extra="forbid")

    type: str = "water"
    temperature_c: float | None = None
    density_kg_m3: float = Field(..., gt=0)
    kinematic_viscosity_m2_s: float = Field(..., gt=0)

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)


# ---------------------------------------------------------------------------
# Geometry: Bump
# ---------------------------------------------------------------------------


class BumpSpec(BaseModel):
    """Bottom bump configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    profile_type: BumpProfileType = BumpProfileType.COSINE_BELL
    center_x_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    standard_deviation: float | None = None
    cutoff_width: float | None = None
    custom_points: list[tuple[float, float]] = Field(default_factory=list)

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)

    @model_validator(mode="after")
    def validate_bump(self) -> BumpSpec:
        if not self.enabled:
            return self
        if self.profile_type != BumpProfileType.PIECEWISE_POINTS:
            if self.center_x_m is None or self.width_m is None or self.height_m is None:
                raise ValueError(
                    "bump with non-piecewise profile requires center_x_m, width_m, height_m"
                )
        if self.profile_type == BumpProfileType.GAUSSIAN:
            if self.standard_deviation is None:
                raise ValueError("gaussian bump requires standard_deviation")
        if self.profile_type == BumpProfileType.PIECEWISE_POINTS:
            if len(self.custom_points) < 2:
                raise ValueError("piecewise_points bump requires at least 2 points")
        return self


# ---------------------------------------------------------------------------
# Geometry: Cylinder
# ---------------------------------------------------------------------------


class CylinderSpec(BaseModel):
    """Cylinder obstacle configuration."""

    model_config = ConfigDict(extra="forbid")

    id: str = "cylinder_1"
    center_x_m: float | None = None
    center_y_m: float | None = None
    diameter_m: float | None = None
    boundary_type: CylinderBoundaryType = CylinderBoundaryType.NO_SLIP_WALL
    angular_velocity_rad_s: float = 0.0
    rotation_direction: Literal["cw", "ccw"] = "ccw"

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)

    @property
    def radius_m(self) -> float | None:
        if self.diameter_m is None:
            return None
        return self.diameter_m / 2.0


# ---------------------------------------------------------------------------
# Geometry: Rectangle
# ---------------------------------------------------------------------------


class RectangleSpec(BaseModel):
    """Rectangle obstacle configuration (snappyHexMesh + STL route)."""

    model_config = ConfigDict(extra="forbid")

    rectangle_id: str = "rectangle_1"
    center_x: float = 0.0
    center_y: float = 0.0
    width: float = 0.1
    height: float = 0.05
    thickness: float = 1.0

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)


# ---------------------------------------------------------------------------
# Geometry: Triangle
# ---------------------------------------------------------------------------


class TriangleSpec(BaseModel):
    """Triangle obstacle configuration (snappyHexMesh + STL route)."""

    model_config = ConfigDict(extra="forbid")

    triangle_id: str = "triangle_1"
    center_x: float = 0.0  # center of base
    center_y: float = 0.0  # center of base (usually 0 for wall-attached)
    base_width: float = 0.1
    height: float = 0.05
    apex_direction: str = "up"  # "up" = apex points away from wall
    thickness: float = 1.0

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


class BoundarySpec(BaseModel):
    """A single boundary specification."""

    model_config = ConfigDict(extra="forbid")

    type: BoundaryType
    velocity_vector: list[float] | None = None
    pressure_value: float | None = None
    shear_direction: list[float] | None = None
    shear_magnitude: float | None = None
    shear_unit: Literal["Pa"] = "Pa"
    freestream_velocity: float | None = None
    pressure_gradient_magnitude: float | None = None
    inlet_velocity: float | None = None

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)

    @model_validator(mode="after")
    def validate_boundary(self) -> BoundarySpec:
        if self.type == BoundaryType.VELOCITY_INLET and self.inlet_velocity is None:
            raise ValueError("velocity_inlet requires inlet_velocity")
        if self.type == BoundaryType.MOVING_WALL and self.velocity_vector is None:
            raise ValueError("moving_wall requires velocity_vector")
        if self.type == BoundaryType.SHEAR_STRESS:
            if self.shear_direction is None or self.shear_magnitude is None:
                raise ValueError("shear_stress requires shear_direction and shear_magnitude")
        if self.type == BoundaryType.FREESTREAM and self.freestream_velocity is None:
            raise ValueError("freestream requires freestream_velocity")
        if self.type == BoundaryType.PRESSURE_BOUNDARY and self.pressure_value is None:
            raise ValueError("pressure_boundary requires pressure_value")
        return self


class BoundaryConfig(BaseModel):
    """Complete boundary configuration for all domain sides."""

    model_config = ConfigDict(extra="forbid")

    left: BoundarySpec
    right: BoundarySpec
    top: BoundarySpec
    bottom_flat: BoundarySpec = Field(
        default_factory=lambda: BoundarySpec(type=BoundaryType.NO_SLIP_WALL)
    )
    bump_surface: BoundarySpec = Field(
        default_factory=lambda: BoundarySpec(type=BoundaryType.NO_SLIP_WALL)
    )
    front: BoundarySpec = Field(
        default_factory=lambda: BoundarySpec(type=BoundaryType.EMPTY)
    )
    back: BoundarySpec = Field(
        default_factory=lambda: BoundarySpec(type=BoundaryType.EMPTY)
    )


# ---------------------------------------------------------------------------
# Inlet Profile
# ---------------------------------------------------------------------------


class InletProfileSpec(BaseModel):
    """Inlet velocity profile — temporal x spatial."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    temporal_type: TemporalType = TemporalType.CONSTANT
    spatial_type: SpatialType = SpatialType.UNIFORM
    parameters: dict[str, Any] = Field(default_factory=dict)

    # constant: {"velocity": float}
    # ramp: {"start_velocity", "end_velocity", "start_time", "end_time"}
    # sinusoidal: {"mean_velocity", "amplitude", "frequency", "phase"}
    # piecewise_linear: {"points": [[t, v], ...]}
    # tabulated: {"data": [[t, v], ...]}
    # parabolic: {"max_velocity": float}
    # power_law: {"max_velocity", "exponent"}
    # linear_shear: {"u0", "slope"}
    # tabulated_profile: {"profile": [[y, u], ...]}

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)

    @model_validator(mode="after")
    def validate_inlet(self) -> InletProfileSpec:
        if not self.enabled:
            return self
        p = self.parameters
        if self.temporal_type == TemporalType.CONSTANT and "velocity" not in p:
            raise ValueError("constant temporal requires 'velocity' parameter")
        if self.temporal_type == TemporalType.RAMP:
            for k in ("start_velocity", "end_velocity", "start_time", "end_time"):
                if k not in p:
                    raise ValueError(f"ramp temporal requires '{k}' parameter")
        if self.temporal_type == TemporalType.SINUSOIDAL:
            for k in ("mean_velocity", "amplitude", "frequency"):
                if k not in p:
                    raise ValueError(f"sinusoidal temporal requires '{k}' parameter")
        return self


# ---------------------------------------------------------------------------
# Forcing
# ---------------------------------------------------------------------------


class PressureGradientSpec(BaseModel):
    """Pressure gradient forcing for periodic or combined driving."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    direction: list[float] = Field(default_factory=lambda: [1.0, 0.0, 0.0])
    magnitude: float | None = None
    unit: PressureGradientUnit | None = None

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)

    @model_validator(mode="after")
    def validate_pg(self) -> PressureGradientSpec:
        if self.enabled and self.magnitude is None:
            raise ValueError("enabled pressure_gradient requires magnitude")
        return self


class BodyForceSpec(BaseModel):
    """Body force vector."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    vector_m_s2: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)


class ForcingSpec(BaseModel):
    """Complete forcing specification."""

    model_config = ConfigDict(extra="forbid")

    pressure_gradient: PressureGradientSpec = Field(default_factory=PressureGradientSpec)
    body_force: BodyForceSpec = Field(default_factory=BodyForceSpec)


# ---------------------------------------------------------------------------
# Flow Definition
# ---------------------------------------------------------------------------


class InitialVelocitySpec(BaseModel):
    """Initial velocity field."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["quiescent", "uniform", "specified"] = "quiescent"
    vector_m_s: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class FlowDefinitionSpec(BaseModel):
    """Flow definition — mode and initial condition."""

    model_config = ConfigDict(extra="forbid")

    mode: FlowMode
    initial_velocity: InitialVelocitySpec = Field(default_factory=InitialVelocitySpec)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


class SimulationSpec(BaseModel):
    """Simulation control parameters."""

    model_config = ConfigDict(extra="forbid")

    time_mode: TimeMode = TimeMode.AUTO
    flow_regime: FlowRegime = FlowRegime.AUTO
    turbulence_model: TurbulenceModel = TurbulenceModel.AUTO
    max_courant_number: float = Field(0.5, gt=0, le=10.0)
    end_time: float | None = None
    delta_t: float | None = None
    write_interval: int | None = None

    provenance: FieldProvenance = Field(default_factory=FieldProvenance)


# ---------------------------------------------------------------------------
# Observables
# ---------------------------------------------------------------------------


class ObservableSpec(BaseModel):
    """An observation target."""

    model_config = ConfigDict(extra="forbid")

    type: ObservableType
    label: str | None = None
    point: list[float] | None = None
    section_x: float | None = None
    component: Literal["Ux", "Uy", "magnitude"] = "Ux"
    averaging: Literal["instantaneous", "time_average"] = "time_average"
    time_window: list[float] | None = None
    cylinder_id: str | None = None
    wall_name: str | None = None

    @model_validator(mode="after")
    def validate_observable(self) -> ObservableSpec:
        if self.type == ObservableType.POINT_VELOCITY and self.point is None:
            raise ValueError("point_velocity requires point")
        if self.type == ObservableType.SECTION_MEAN_VELOCITY and self.section_x is None:
            raise ValueError("section_mean_velocity requires section_x")
        if self.type == ObservableType.SECTION_FLOW_RATE and self.section_x is None:
            raise ValueError("section_flow_rate requires section_x")
        if self.type in (ObservableType.CYLINDER_DRAG, ObservableType.CYLINDER_LIFT):
            if self.cylinder_id is None:
                raise ValueError(f"{self.type.value} requires cylinder_id")
        if self.type == ObservableType.WALL_SHEAR_STRESS and self.wall_name is None:
            raise ValueError("wall_shear_stress requires wall_name")
        return self


# ---------------------------------------------------------------------------
# Top-level Experiment Spec
# ---------------------------------------------------------------------------


class ObstacleFlowExperimentSpecV1(BaseModel):
    """Complete experiment specification for ConfigurableObstacleFlow2D.

    This is the single source of truth — the LLM generates this structured
    Spec, never OpenFOAM files directly.  The compiler reads this spec and
    deterministically produces OpenFOAM Foundation 13 case files.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    case_family: str = "configurable_obstacle_flow_2d"
    spec_version: int = Field(1, ge=1)

    domain: DomainSpec
    fluid: FluidSpec
    geometry_bump: BumpSpec = Field(default_factory=BumpSpec)
    cylinders: list[CylinderSpec] = Field(default_factory=list)
    rectangles: list[RectangleSpec] = Field(default_factory=list)
    triangles: list[TriangleSpec] = Field(default_factory=list)
    flow_definition: FlowDefinitionSpec
    boundaries: BoundaryConfig
    inlet_profile: InletProfileSpec = Field(default_factory=InletProfileSpec)
    forcing: ForcingSpec = Field(default_factory=ForcingSpec)
    operating_stages: list[dict[str, Any]] = Field(default_factory=list)
    simulation: SimulationSpec = Field(default_factory=SimulationSpec)
    observables: list[ObservableSpec] = Field(default_factory=list)
    plot_requests: list[PlotRequest] = Field(
        default_factory=lambda: [
            PlotRequest.VELOCITY_MAGNITUDE,
            PlotRequest.STREAMLINES,
            PlotRequest.PRESSURE,
            PlotRequest.VORTICITY,
        ]
    )
    unresolved_fields: list[str] = Field(default_factory=list)

    # Metadata
    experiment_id: str | None = None
    title: str | None = None
    objective: str | None = None

    @model_validator(mode="after")
    def validate_spec(self) -> ObstacleFlowExperimentSpecV1:
        # V1 limit: at most one cylinder
        if len(self.cylinders) > 1:
            raise ValueError("V1 supports at most one cylinder")

        # front/back must be empty for 2D
        if self.boundaries.front.type != BoundaryType.EMPTY:
            raise ValueError("front boundary must be 'empty' for 2D")
        if self.boundaries.back.type != BoundaryType.EMPTY:
            raise ValueError("back boundary must be 'empty' for 2D")

        # Cylinder must be inside domain
        for cyl in self.cylinders:
            if cyl.center_x_m is not None and cyl.diameter_m is not None:
                r = cyl.diameter_m / 2.0
                if cyl.center_x_m - r < 0 or cyl.center_x_m + r > self.domain.length_m:
                    raise ValueError(
                        f"cylinder {cyl.id} extends beyond domain x-bounds"
                    )
            if cyl.center_y_m is not None and cyl.diameter_m is not None:
                r = cyl.diameter_m / 2.0
                if cyl.center_y_m - r < 0 or cyl.center_y_m + r > self.domain.height_m:
                    raise ValueError(
                        f"cylinder {cyl.id} extends beyond domain y-bounds"
                    )

        return self

    @property
    def has_cylinder(self) -> bool:
        return len(self.cylinders) > 0

    @property
    def has_rectangle(self) -> bool:
        return len(self.rectangles) > 0

    @property
    def has_triangle(self) -> bool:
        return len(self.triangles) > 0

    @property
    def has_bump(self) -> bool:
        return self.geometry_bump.enabled

    @property
    def is_periodic(self) -> bool:
        return (
            self.boundaries.left.type == BoundaryType.PERIODIC
            and self.boundaries.right.type == BoundaryType.PERIODIC
        )

    @property
    def is_transient(self) -> bool:
        if self.simulation.time_mode == TimeMode.TRANSIENT:
            return True
        if self.simulation.time_mode == TimeMode.AUTO:
            # Auto-detect: cylinder or time-varying inlet -> transient
            if self.has_cylinder:
                return True
            if self.inlet_profile.enabled and self.inlet_profile.temporal_type != TemporalType.CONSTANT:
                return True
        return False

    @property
    def is_turbulent(self) -> bool:
        if self.simulation.flow_regime == FlowRegime.TURBULENT:
            return True
        if self.simulation.flow_regime == FlowRegime.AUTO:
            re = self.estimate_reynolds()
            if re is not None and re > 2000:
                return True
        return False

    def estimate_reynolds(self) -> float | None:
        """Estimate Reynolds number based on available parameters."""
        nu = self.fluid.kinematic_viscosity_m2_s
        # Use cylinder diameter as characteristic length when present
        # (most physically meaningful for obstacle flows)
        char_length = None
        for cyl in self.cylinders:
            if cyl.diameter_m is not None:
                char_length = cyl.diameter_m
                break
        if char_length is None:
            char_length = self.domain.height_m
        char_velocity = None

        # Try inlet velocity
        if self.boundaries.left.type == BoundaryType.VELOCITY_INLET:
            char_velocity = self.boundaries.left.inlet_velocity
        elif self.inlet_profile.enabled:
            p = self.inlet_profile.parameters
            if self.inlet_profile.temporal_type == TemporalType.CONSTANT:
                char_velocity = p.get("velocity")
            elif self.inlet_profile.temporal_type == TemporalType.SINUSOIDAL:
                char_velocity = p.get("mean_velocity")
            elif self.inlet_profile.temporal_type == TemporalType.RAMP:
                char_velocity = p.get("end_velocity")
        elif self.forcing.pressure_gradient.enabled:
            # Derive velocity from pressure gradient (very rough estimate)
            pg = self.forcing.pressure_gradient.magnitude
            if pg is not None and self.fluid.density_kg_m3 > 0:
                # u ~ sqrt(dp/dx * H / rho) for channel flow
                char_velocity = math.sqrt(
                    abs(pg) * self.domain.height_m / self.fluid.density_kg_m3
                )

        # Use cylinder diameter if present
        if self.has_cylinder and self.cylinders[0].diameter_m is not None:
            char_length = self.cylinders[0].diameter_m

        if char_velocity is None or char_velocity <= 0:
            return None
        return char_velocity * char_length / nu

    def equivalent_body_force(self) -> list[float] | None:
        """Convert pressure gradient to equivalent body force acceleration.

        a = -(1/rho) * dp/dx
        """
        pg = self.forcing.pressure_gradient
        if not pg.enabled or pg.magnitude is None:
            return None

        direction = pg.direction if len(pg.direction) == 3 else [1.0, 0.0, 0.0]
        magnitude = pg.magnitude

        # Convert Pa/m to m/s² if needed
        if pg.unit == PressureGradientUnit.PA_PER_M:
            rho = self.fluid.density_kg_m3
            acceleration = magnitude / rho
        else:
            acceleration = magnitude

        # Direction: pressure decreases along +x means acceleration to +x
        # dp/dx < 0 -> a = -(1/rho)*dp/dx > 0 in +x
        return [acceleration * d for d in direction]


# ---------------------------------------------------------------------------
# Geometry Feasibility Validation
# ---------------------------------------------------------------------------


class GeometryFeasibilityError(ValueError):
    """Raised when geometry configuration is infeasible."""

    def __init__(self, code: str, message: str, suggested_changes: list[str] | None = None):
        self.code = code
        self.message = message
        self.suggested_changes = suggested_changes or []
        super().__init__(f"[{code}] {message}")


class GeometryFeasibilityValidator:
    """Validates geometric feasibility of the obstacle flow configuration.

    Checks:
    - Cylinder does not overlap with bump
    - Cylinder does not intersect top boundary
    - Cylinder does not cross left/right boundaries
    - Minimum gap between cylinder and walls
    - Cylinder does not cover observation points
    - Cylinder blockage ratio within limits
    - Cylinder distance from open boundaries sufficient
    """

    MIN_GAP_RATIO = 0.05  # Minimum gap as fraction of domain height
    MAX_BLOCKAGE_RATIO = 0.5  # Maximum cylinder diameter / domain height

    def validate(self, spec: ObstacleFlowExperimentSpecV1) -> None:
        """Validate geometry, raising GeometryFeasibilityError on failure."""
        domain = spec.domain

        for cyl in spec.cylinders:
            if cyl.center_x_m is None or cyl.center_y_m is None or cyl.diameter_m is None:
                continue  # Unresolved — skip

            r = cyl.diameter_m / 2.0
            min_gap = self.MIN_GAP_RATIO * domain.height_m

            # Check blockage ratio
            blockage = cyl.diameter_m / domain.height_m
            if blockage > self.MAX_BLOCKAGE_RATIO:
                raise GeometryFeasibilityError(
                    "CYLINDER_BLOCKAGE_EXCEEDED",
                    f"Cylinder blockage ratio {blockage:.2f} exceeds maximum {self.MAX_BLOCKAGE_RATIO}",
                    ["Reduce cylinder diameter", "Increase domain height"],
                )

            # Check cylinder does not cross left/right boundaries
            if cyl.center_x_m - r < 0:
                raise GeometryFeasibilityError(
                    "CYLINDER_OUTSIDE_DOMAIN",
                    f"Cylinder {cyl.id} crosses left boundary",
                    ["Move cylinder center to the right", "Reduce cylinder diameter"],
                )
            if cyl.center_x_m + r > domain.length_m:
                raise GeometryFeasibilityError(
                    "CYLINDER_OUTSIDE_DOMAIN",
                    f"Cylinder {cyl.id} crosses right boundary",
                    ["Move cylinder center to the left", "Reduce cylinder diameter"],
                )

            # Check cylinder does not intersect top boundary
            if cyl.center_y_m + r > domain.height_m - min_gap:
                raise GeometryFeasibilityError(
                    "CYLINDER_INTERSECTS_TOP",
                    f"Cylinder {cyl.id} too close to top boundary",
                    ["Lower cylinder center", "Reduce cylinder diameter", "Increase domain height"],
                )

            # Check cylinder does not intersect bottom
            if cyl.center_y_m - r < min_gap:
                raise GeometryFeasibilityError(
                    "CYLINDER_INTERSECTS_BOTTOM",
                    f"Cylinder {cyl.id} too close to bottom boundary",
                    ["Raise cylinder center", "Reduce cylinder diameter"],
                )

            # Check cylinder does not overlap with bump
            if spec.has_bump and spec.geometry_bump.center_x_m is not None:
                bump = spec.geometry_bump
                if bump.width_m is not None and bump.height_m is not None:
                    bump_left = bump.center_x_m - bump.width_m / 2.0
                    bump_right = bump.center_x_m + bump.width_m / 2.0
                    bump_top = bump.height_m

                    cyl_left = cyl.center_x_m - r
                    cyl_right = cyl.center_x_m + r
                    cyl_bottom = cyl.center_y_m - r

                    # Check x-overlap
                    x_overlap = cyl_left < bump_right and cyl_right > bump_left
                    # Check y-overlap
                    y_overlap = cyl_bottom < bump_top

                    if x_overlap and y_overlap:
                        raise GeometryFeasibilityError(
                            "CYLINDER_INTERSECTS_BUMP",
                            f"Cylinder {cyl.id} overlaps with bump geometrically",
                            [
                                "Raise cylinder center height",
                                "Reduce cylinder diameter",
                                "Adjust bump position",
                            ],
                        )

            # Check observation points are not inside cylinder
            for obs in spec.observables:
                if obs.point is not None and len(obs.point) >= 2:
                    px, py = obs.point[0], obs.point[1]
                    dist = math.sqrt((px - cyl.center_x_m) ** 2 + (py - cyl.center_y_m) ** 2)
                    if dist < r:
                        raise GeometryFeasibilityError(
                            "OBSERVATION_INSIDE_SOLID",
                            f"Observation point ({px}, {py}) is inside cylinder {cyl.id}",
                            ["Move observation point outside cylinder", "Adjust cylinder position"],
                        )

        # Validate bump is inside domain
        if spec.has_bump and spec.geometry_bump.center_x_m is not None:
            bump = spec.geometry_bump
            if bump.width_m is not None:
                bump_left = bump.center_x_m - bump.width_m / 2.0
                bump_right = bump.center_x_m + bump.width_m / 2.0
                if bump_left < 0 or bump_right > domain.length_m:
                    raise GeometryFeasibilityError(
                        "BUMP_OUTSIDE_DOMAIN",
                        "Bump extends beyond domain x-bounds",
                        ["Adjust bump center_x", "Reduce bump width"],
                    )
            if bump.height_m is not None and bump.height_m >= domain.height_m:
                raise GeometryFeasibilityError(
                    "BUMP_TOO_TALL",
                    "Bump height exceeds domain height",
                    ["Reduce bump height", "Increase domain height"],
                )

        # Validate piecewise_points bump
        if spec.has_bump and spec.geometry_bump.profile_type == BumpProfileType.PIECEWISE_POINTS:
            pts = spec.geometry_bump.custom_points
            xs = [p[0] for p in pts]
            if xs != sorted(xs):
                raise GeometryFeasibilityError(
                    "BUMP_GEOMETRY_INVALID",
                    "Piecewise points x-coordinates must be monotonically increasing",
                    ["Sort points by x-coordinate"],
                )
            for x in xs:
                if x < 0 or x > domain.length_m:
                    raise GeometryFeasibilityError(
                        "BUMP_OUTSIDE_DOMAIN",
                        f"Piecewise point x={x} is outside domain",
                        ["Adjust points to be within [0, domain.length_m]"],
                    )


__all__ = [
    "BodyForceSpec",
    "BoundaryConfig",
    "BoundarySpec",
    "BoundaryType",
    "BumpProfileType",
    "BumpSpec",
    "CylinderBoundaryType",
    "CylinderSpec",
    "DomainSpec",
    "FieldProvenance",
    "FlowDefinitionSpec",
    "FlowMode",
    "FlowRegime",
    "FluidSpec",
    "ForcingSpec",
    "GeometryFeasibilityError",
    "GeometryFeasibilityValidator",
    "InitialVelocitySpec",
    "InletProfileSpec",
    "ObservableSpec",
    "ObservableType",
    "ObstacleFlowExperimentSpecV1",
    "PlotRequest",
    "PressureGradientSpec",
    "PressureGradientUnit",
    "RectangleSpec",
    "SimulationSpec",
    "SpatialType",
    "SpecSource",
    "SpecStatus",
    "TemporalType",
    "TimeMode",
    "TriangleSpec",
    "TurbulenceModel",
]
