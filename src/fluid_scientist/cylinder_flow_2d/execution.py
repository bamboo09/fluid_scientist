"""execution.py — Complete execution chain for CylinderFlow2D experiments.

Provides the full pipeline from a confirmed CylinderFlow2DExperimentSpecV1
to OpenFOAM Foundation 13 simulation results and matplotlib plots.

Classes:
  SpecAdapter           — CylinderFlow2DExperimentSpecV1 -> ObstacleFlowExperimentSpecV1
  WorkstationExecutor   — SSH/SCP to remote OpenFOAM workstation
  Postprocessor         — matplotlib-based plot generation
  ExecutionOrchestrator — orchestrates the full chain

Architecture:
  Confirmed Spec
       |
       v
  SpecAdapter
       |
       v
  ObstacleFlowExperimentSpecV1
       |
       v
  ObstacleFlowCompiler  (tar.gz archive)
       |
       v
  WorkstationExecutor   (upload, mesh, smoke, full run)
       |
       v
  Postprocessor         (field contours, force time series)
       |
       v
  ExecutionResult       (plot paths + reports)
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
import numpy as np

from fluid_scientist.cylinder_flow_2d.models import (
    BumpProfileType as CylBumpProfileType,
    CylinderFlow2DExperimentSpecV1,
    CylinderWallType,
    FlowMode as CylFlowMode,
    FlowRegime as CylFlowRegime,
    ObservableType as CylObservableType,
    ProvenanceField,
    SemanticBoundaryType,
    SpatialType as CylSpatialType,
    TemporalType as CylTemporalType,
    TimeMode as CylTimeMode,
)
from fluid_scientist.obstacle_flow.compiler import (
    CompilationManifest,
    ObstacleFlowCompiledCase,
    ObstacleFlowCompiler,
)
from fluid_scientist.obstacle_flow.models import (
    BodyForceSpec as ObsBodyForceSpec,
    BoundaryConfig as ObsBoundaryConfig,
    BoundarySpec as ObsBoundarySpec,
    BoundaryType,
    BumpProfileType as ObsBumpProfileType,
    BumpSpec,
    CylinderBoundaryType,
    CylinderSpec as ObsCylinderSpec,
    DomainSpec as ObsDomainSpec,
    FieldProvenance,
    FlowDefinitionSpec,
    FlowMode as ObsFlowMode,
    FlowRegime as ObsFlowRegime,
    FluidSpec as ObsFluidSpec,
    ForcingSpec as ObsForcingSpec,
    InitialVelocitySpec,
    InletProfileSpec as ObsInletProfileSpec,
    ObservableSpec as ObsObservableSpec,
    ObservableType as ObsObservableType,
    ObstacleFlowExperimentSpecV1,
    PlotRequest,
    PressureGradientSpec as ObsPressureGradientSpec,
    PressureGradientUnit,
    RectangleSpec as ObsRectangleSpec,
    SimulationSpec as ObsSimulationSpec,
    SpatialType as ObsSpatialType,
    TemporalType as ObsTemporalType,
    TimeMode as ObsTimeMode,
    TriangleSpec as ObsTriangleSpec,
    TrapezoidSpec as ObsTrapezoidSpec,
    TurbulenceModel,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESULTS_ROOT = Path(r"d:\desktop\AI FOR SCIENCE\results")
OPENFOAM_BASHRC = "/opt/openfoam13/etc/bashrc"
SSH_KEY_DEFAULT = "~/.ssh/fluid_scientist_ed25519"

# Default fluid properties — water at 20 C
DEFAULT_RHO = 998.0
DEFAULT_NU = 1.004e-6
DEFAULT_TEMPERATURE = 20.0
DEFAULT_FLUID_TYPE = "water"

# Default domain
DEFAULT_DOMAIN_LENGTH = 30.0
DEFAULT_DOMAIN_HEIGHT = 10.0
DEFAULT_DOMAIN_THICKNESS = 1.0

# Default simulation
DEFAULT_END_TIME = 10.0
DEFAULT_MAX_COURANT = 0.5

# ---------------------------------------------------------------------------
# Mapping tables
# ---------------------------------------------------------------------------

_BOUNDARY_TYPE_MAP: dict[SemanticBoundaryType, BoundaryType] = {
    SemanticBoundaryType.UNIFORM_VELOCITY_INLET: BoundaryType.VELOCITY_INLET,
    SemanticBoundaryType.TIME_VARYING_VELOCITY_INLET: BoundaryType.VELOCITY_INLET,
    SemanticBoundaryType.SPATIAL_NONUNIFORM_VELOCITY_INLET: BoundaryType.VELOCITY_INLET,
    SemanticBoundaryType.PRESSURE_INLET: BoundaryType.PRESSURE_INLET,
    SemanticBoundaryType.PRESSURE_OUTLET: BoundaryType.PRESSURE_OUTLET,
    SemanticBoundaryType.OPEN_OUTLET: BoundaryType.OPEN_OUTLET,
    SemanticBoundaryType.ADVECTIVE_OUTLET: BoundaryType.ADVECTIVE_OUTLET,
    SemanticBoundaryType.NO_SLIP_WALL: BoundaryType.NO_SLIP_WALL,
    SemanticBoundaryType.SLIP_WALL: BoundaryType.SLIP_WALL,
    SemanticBoundaryType.MOVING_WALL: BoundaryType.MOVING_WALL,
    SemanticBoundaryType.SHEAR_STRESS: BoundaryType.SHEAR_STRESS,
    SemanticBoundaryType.SYMMETRY: BoundaryType.SYMMETRY,
    SemanticBoundaryType.FREESTREAM: BoundaryType.FREESTREAM,
    SemanticBoundaryType.OPEN_BOUNDARY: BoundaryType.OPEN_BOUNDARY,
    SemanticBoundaryType.PERIODIC: BoundaryType.PERIODIC,
    SemanticBoundaryType.EMPTY: BoundaryType.EMPTY,
    SemanticBoundaryType.PRESSURE_BOUNDARY: BoundaryType.PRESSURE_BOUNDARY,
}

_CYLINDER_WALL_MAP: dict[CylinderWallType, CylinderBoundaryType] = {
    CylinderWallType.NO_SLIP_WALL: CylinderBoundaryType.NO_SLIP_WALL,
    CylinderWallType.ROTATING_WALL: CylinderBoundaryType.ROTATING_WALL,
    CylinderWallType.SLIP_WALL: CylinderBoundaryType.SLIP_WALL,
}

_BUMP_PROFILE_MAP: dict[CylBumpProfileType, ObsBumpProfileType] = {
    CylBumpProfileType.COSINE_BELL: ObsBumpProfileType.COSINE_BELL,
    CylBumpProfileType.HALF_SINE: ObsBumpProfileType.HALF_SINE,
    CylBumpProfileType.GAUSSIAN: ObsBumpProfileType.GAUSSIAN,
    CylBumpProfileType.PIECEWISE_POINTS: ObsBumpProfileType.PIECEWISE_POINTS,
    # FLAT has no obstacle_flow equivalent — means bump disabled
}

# cylinder_flow_2d ObservableType values that map directly to obstacle_flow
_OBSERVABLE_MAP: dict[CylObservableType, ObsObservableType] = {
    CylObservableType.POINT_VELOCITY: ObsObservableType.POINT_VELOCITY,
    CylObservableType.SECTION_MEAN_VELOCITY: ObsObservableType.SECTION_MEAN_VELOCITY,
    CylObservableType.SECTION_FLOW_RATE: ObsObservableType.SECTION_FLOW_RATE,
    CylObservableType.CYLINDER_DRAG: ObsObservableType.CYLINDER_DRAG,
    CylObservableType.CYLINDER_LIFT: ObsObservableType.CYLINDER_LIFT,
    CylObservableType.WALL_SHEAR_STRESS: ObsObservableType.WALL_SHEAR_STRESS,
    CylObservableType.RECIRCULATION_LENGTH: ObsObservableType.RECIRCULATION_LENGTH,
}

# cylinder_flow_2d ObservableType values that become PlotRequest entries
_OBSERVABLE_TO_PLOT: dict[CylObservableType, PlotRequest] = {
    CylObservableType.VELOCITY_MAGNITUDE_FIELD: PlotRequest.VELOCITY_MAGNITUDE,
    CylObservableType.PRESSURE_FIELD: PlotRequest.PRESSURE,
    CylObservableType.VORTICITY_FIELD: PlotRequest.VORTICITY,
    CylObservableType.STREAMLINES: PlotRequest.STREAMLINES,
    CylObservableType.DRAG_LIFT_TIME_SERIES: PlotRequest.CD_CL_TIME_SERIES,
    CylObservableType.CYLINDER_DRAG: PlotRequest.CD_CL_TIME_SERIES,
    CylObservableType.CYLINDER_LIFT: PlotRequest.CD_CL_TIME_SERIES,
    CylObservableType.WAKE_SHEDDING_FREQUENCY: PlotRequest.CD_CL_TIME_SERIES,
}

_TEMPORAL_MAP: dict[CylTemporalType, ObsTemporalType] = {
    CylTemporalType.CONSTANT: ObsTemporalType.CONSTANT,
    CylTemporalType.RAMP: ObsTemporalType.RAMP,
    CylTemporalType.SINUSOIDAL: ObsTemporalType.SINUSOIDAL,
    CylTemporalType.PIECEWISE_LINEAR: ObsTemporalType.PIECEWISE_LINEAR,
    CylTemporalType.TABULATED: ObsTemporalType.TABULATED,
}

_SPATIAL_MAP: dict[CylSpatialType, ObsSpatialType] = {
    CylSpatialType.UNIFORM: ObsSpatialType.UNIFORM,
    CylSpatialType.PARABOLIC: ObsSpatialType.PARABOLIC,
    CylSpatialType.POWER_LAW: ObsSpatialType.POWER_LAW,
    CylSpatialType.LINEAR_SHEAR: ObsSpatialType.LINEAR_SHEAR,
    CylSpatialType.TABULATED_PROFILE: ObsSpatialType.TABULATED_PROFILE,
}

_TIME_MODE_MAP: dict[CylTimeMode, ObsTimeMode] = {
    CylTimeMode.STEADY: ObsTimeMode.STEADY,
    CylTimeMode.TRANSIENT: ObsTimeMode.TRANSIENT,
    CylTimeMode.AUTO: ObsTimeMode.AUTO,
}

_FLOW_REGIME_MAP: dict[CylFlowRegime, ObsFlowRegime] = {
    CylFlowRegime.LAMINAR: ObsFlowRegime.LAMINAR,
    CylFlowRegime.TURBULENT: ObsFlowRegime.TURBULENT,
    CylFlowRegime.AUTO: ObsFlowRegime.AUTO,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _unwrap(provenance_field: ProvenanceField | Any | None, default: Any = None) -> Any:
    """Unwrap a ProvenanceField to its plain value.

    If the argument is a ProvenanceField, return ``.value`` (or *default*
    when the value is ``None``).  If it is already a plain value, return it
    directly (or *default* when ``None``).
    """
    if provenance_field is None:
        return default
    if isinstance(provenance_field, ProvenanceField):
        val = provenance_field.value
        return val if val is not None else default
    return provenance_field if provenance_field is not None else default


def _expand_key_path(key_path: str) -> str:
    """Expand ``~`` in a key path."""
    return os.path.expanduser(key_path)


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------


@dataclass
class ExecutionResult:
    """Result of the full execution chain."""

    job_id: str
    status: str = "PENDING"  # SUCCESS | PARTIAL | FAILED | PENDING
    spec_version: int = 1
    remote_case_path: str = ""
    plot_paths: list[str] = field(default_factory=list)
    mesh_report: dict = field(default_factory=dict)
    smoke_test_report: dict = field(default_factory=dict)
    simulation_report: dict = field(default_factory=dict)
    adapted_spec: Any = None  # ObstacleFlowExperimentSpecV1
    compilation_manifest: CompilationManifest | None = None
    archive_sha256: str = ""
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0


# ---------------------------------------------------------------------------
# 1. SpecAdapter
# ---------------------------------------------------------------------------


class SpecAdapter:
    """Converts CylinderFlow2DExperimentSpecV1 -> ObstacleFlowExperimentSpecV1.

    The cylinder_flow_2d spec wraps every value in a ``ProvenanceField``
    (value, source, status, confidence, reason).  The obstacle_flow spec
    uses plain ``float`` / ``int`` values with a single ``FieldProvenance``
    block per sub-model.  This adapter unwraps, maps enums, and fills
    sensible defaults for anything that is unresolved.
    """

    def adapt(self, spec: CylinderFlow2DExperimentSpecV1) -> ObstacleFlowExperimentSpecV1:
        """Produce an ObstacleFlowExperimentSpecV1 from a cylinder flow spec."""
        # Validate that critical physical fields are resolved before
        # adaptation.  Silently filling in defaults for unresolved
        # density, viscosity, or domain dimensions would mask
        # incomplete specs and produce physically meaningless cases.
        self._validate_resolved_fields(spec)

        domain = self._adapt_domain(spec)
        fluid = self._adapt_fluid(spec)
        geometry_bump = self._adapt_bump(spec)
        cylinders = self._adapt_cylinders(spec)
        rectangles = self._adapt_rectangles(spec)
        triangles = self._adapt_triangles(spec)
        trapezoids = self._adapt_trapezoid(spec)
        flow_definition = self._adapt_flow_definition(spec)
        boundaries = self._adapt_boundaries(spec)
        inlet_profile = self._adapt_inlet_profile(spec)
        forcing = self._adapt_forcing(spec)
        simulation = self._adapt_simulation(spec)
        observables = self._adapt_observables(spec)
        plot_requests = self._adapt_plot_requests(spec)

        return ObstacleFlowExperimentSpecV1(
            schema_version="1.0",
            case_family="configurable_obstacle_flow_2d",
            spec_version=spec.spec_version,
            domain=domain,
            fluid=fluid,
            geometry_bump=geometry_bump,
            cylinders=cylinders,
            rectangles=rectangles,
            triangles=triangles,
            trapezoids=trapezoids,
            flow_definition=flow_definition,
            boundaries=boundaries,
            inlet_profile=inlet_profile,
            forcing=forcing,
            simulation=simulation,
            observables=observables,
            plot_requests=plot_requests,
            experiment_id=spec.experiment_id,
            title=spec.title,
            objective=spec.objective,
        )

    # -- sub-adapters -------------------------------------------------------

    def _validate_resolved_fields(self, spec: CylinderFlow2DExperimentSpecV1) -> None:
        """Validate that critical physical fields are resolved.

        Raises ``ValueError`` if any critical field (domain dimensions,
        fluid density, fluid viscosity) is unresolved (``None``).
        This prevents the adapter from silently substituting default
        values for physically essential parameters.
        """
        unresolved: list[str] = []
        if spec.domain.length_m.value is None:
            unresolved.append("domain.length_m")
        if spec.domain.height_m.value is None:
            unresolved.append("domain.height_m")
        if spec.fluid.density_kg_m3.value is None:
            unresolved.append("fluid.density_kg_m3")
        if spec.fluid.kinematic_viscosity_m2_s.value is None:
            unresolved.append("fluid.kinematic_viscosity_m2_s")
        if unresolved:
            raise ValueError(
                f"Cannot adapt spec with unresolved critical fields: "
                f"{', '.join(unresolved)}. All physical parameters must be "
                f"resolved before compilation — refusing to use silent defaults."
            )

    def _adapt_domain(self, spec: CylinderFlow2DExperimentSpecV1) -> ObsDomainSpec:
        return ObsDomainSpec(
            length_m=_unwrap(spec.domain.length_m, DEFAULT_DOMAIN_LENGTH),
            height_m=_unwrap(spec.domain.height_m, DEFAULT_DOMAIN_HEIGHT),
            thickness_m=_unwrap(spec.domain.thickness_m, DEFAULT_DOMAIN_THICKNESS),
        )

    def _adapt_fluid(self, spec: CylinderFlow2DExperimentSpecV1) -> ObsFluidSpec:
        return ObsFluidSpec(
            type=_unwrap(spec.fluid.type, DEFAULT_FLUID_TYPE),
            temperature_c=_unwrap(spec.fluid.temperature_c, DEFAULT_TEMPERATURE),
            density_kg_m3=_unwrap(spec.fluid.density_kg_m3, DEFAULT_RHO),
            kinematic_viscosity_m2_s=_unwrap(
                spec.fluid.kinematic_viscosity_m2_s, DEFAULT_NU
            ),
        )

    def _adapt_bump(self, spec: CylinderFlow2DExperimentSpecV1) -> BumpSpec:
        bp = spec.bottom_profile
        if not bp.enabled or bp.profile_type == CylBumpProfileType.FLAT:
            return BumpSpec(enabled=False)

        profile_type = _BUMP_PROFILE_MAP.get(bp.profile_type, ObsBumpProfileType.COSINE_BELL)
        custom_pts = [
            (float(p[0]), float(p[1])) for p in bp.custom_points
        ] if bp.custom_points else []

        center_x = _unwrap(bp.center_x_m)
        width = _unwrap(bp.width_m)
        height = _unwrap(bp.height_m)

        # --- Geometry auto-correction: clamp bump inside domain ---
        domain_length = _unwrap(spec.domain.length_m, DEFAULT_DOMAIN_LENGTH)
        domain_height = _unwrap(spec.domain.height_m, DEFAULT_DOMAIN_HEIGHT)

        if height is not None and height >= domain_height:
            new_height = domain_height * 0.5
            logger.warning(
                "Bump height %.3f m >= domain height %.3f m — shrinking to %.3f m",
                height, domain_height, new_height,
            )
            height = new_height

        if center_x is not None and width is not None:
            half_w = width / 2.0
            if center_x - half_w < 0:
                center_x = half_w
                logger.warning("Bump center_x clamped to %.3f to stay inside domain", center_x)
            elif center_x + half_w > domain_length:
                center_x = domain_length - half_w
                logger.warning("Bump center_x clamped to %.3f to stay inside domain", center_x)
        elif center_x is None:
            center_x = domain_length / 2.0

        return BumpSpec(
            enabled=True,
            profile_type=profile_type,
            center_x_m=center_x,
            width_m=width,
            height_m=height,
            custom_points=custom_pts,
        )

    def _adapt_cylinders(self, spec: CylinderFlow2DExperimentSpecV1) -> list[ObsCylinderSpec]:
        if not spec.has_cylinder:
            return []

        cyl = spec.cylinder
        diameter = spec.get_cylinder_diameter()
        center_x = _unwrap(cyl.center_x_m)
        center_y = _unwrap(cyl.center_y_m)

        # --- Geometry auto-correction: clamp cylinder inside domain ---
        domain_length = _unwrap(spec.domain.length_m, DEFAULT_DOMAIN_LENGTH)
        domain_height = _unwrap(spec.domain.height_m, DEFAULT_DOMAIN_HEIGHT)

        if diameter is not None and diameter > 0:
            radius = diameter / 2.0

            # Reject extreme oversize: if the requested cylinder diameter
            # exceeds the domain height, the spec is clearly invalid (e.g.
            # a patch overwrote the geometry).  Silently auto-shrinking a
            # cylinder that is larger than the entire domain would mask a
            # serious spec corruption.
            if diameter > domain_height:
                raise ValueError(
                    f"Cylinder diameter {diameter:.3f} m exceeds domain height "
                    f"{domain_height:.3f} m — cylinder cannot be larger than the "
                    f"domain. Refusing to auto-shrink an extreme geometry violation."
                )

            # Ensure diameter fits within domain at all
            # Also respect blockage ratio limit (diameter / domain_height <= 0.5)
            max_diameter_by_height = domain_height * 0.45  # 45% to leave margin
            max_diameter_by_length = domain_length * 0.45
            max_diameter = min(max_diameter_by_height, max_diameter_by_length)

            if diameter > max_diameter:
                logger.warning(
                    "Cylinder diameter %.3f m exceeds domain capacity "
                    "(height=%.3f, length=%.3f, max=%.3f) — auto-shrinking",
                    diameter, domain_height, domain_length, max_diameter,
                )
                diameter = max_diameter
                radius = diameter / 2.0

            # Clamp center_y so cylinder stays inside [min_gap + radius, height - min_gap - radius]
            # MIN_GAP_RATIO = 0.05 in obstacle_flow models
            min_gap = 0.05 * domain_height
            if center_y is not None:
                min_y = min_gap + radius
                max_y = domain_height - min_gap - radius
                if max_y < min_y:
                    # Domain too small for cylinder — place at center
                    center_y = domain_height / 2.0
                elif center_y < min_y:
                    logger.warning(
                        "Cylinder center_y=%.3f too low (radius=%.3f, min_gap=%.3f) — clamping to %.3f",
                        center_y, radius, min_gap, min_y,
                    )
                    center_y = min_y
                elif center_y > max_y:
                    logger.warning(
                        "Cylinder center_y=%.3f too high (radius=%.3f, height=%.3f, min_gap=%.3f) — clamping to %.3f",
                        center_y, radius, domain_height, min_gap, max_y,
                    )
                    center_y = max_y
            else:
                # Default: place at vertical center
                center_y = domain_height / 2.0

            # Clamp center_x so cylinder stays inside [min_gap + radius, length - min_gap - radius]
            min_gap_x = 0.05 * domain_length
            if center_x is not None:
                min_x = min_gap_x + radius
                max_x = domain_length - min_gap_x - radius
                if max_x < min_x:
                    center_x = domain_length / 2.0
                elif center_x < min_x:
                    logger.warning(
                        "Cylinder center_x=%.3f too low (radius=%.3f) — clamping to %.3f",
                        center_x, radius, min_x,
                    )
                    center_x = min_x
                elif center_x > max_x:
                    logger.warning(
                        "Cylinder center_x=%.3f too high (radius=%.3f, length=%.3f) — clamping to %.3f",
                        center_x, radius, domain_length, max_x,
                    )
                    center_x = max_x
            else:
                center_x = domain_length / 2.0

        boundary_type = _CYLINDER_WALL_MAP.get(cyl.wall_type, CylinderBoundaryType.NO_SLIP_WALL)

        return [
            ObsCylinderSpec(
                id="cylinder_1",
                center_x_m=center_x,
                center_y_m=center_y,
                diameter_m=diameter,
                boundary_type=boundary_type,
                angular_velocity_rad_s=cyl.angular_velocity_rad_s,
                rotation_direction="ccw",
            )
        ]

    def _adapt_rectangles(self, spec: CylinderFlow2DExperimentSpecV1) -> list[ObsRectangleSpec]:
        """Adapt rectangle obstacle from cylinder flow spec to obstacle flow spec."""
        if not spec.has_rectangle:
            return []

        rect = spec.rectangle
        width = _unwrap(rect.width_m)
        height = _unwrap(rect.height_m)
        center_x = _unwrap(rect.center_x_m)
        center_y = _unwrap(rect.center_y_m)

        # If rectangle center_x is not set, use cylinder center_x
        if center_x is None and spec.has_cylinder:
            center_x = _unwrap(spec.cylinder.center_x_m)

        # If rectangle center_y is not set, place it on the bottom wall
        if center_y is None:
            center_y = height / 2.0 if height else 0.025

        # If width or height is missing, cannot proceed
        if width is None or height is None:
            logger.warning(
                "Rectangle dimensions missing (width=%s, height=%s) — skipping",
                width, height,
            )
            return []

        domain_length = _unwrap(spec.domain.length_m, DEFAULT_DOMAIN_LENGTH)
        domain_height = _unwrap(spec.domain.height_m, DEFAULT_DOMAIN_HEIGHT)
        domain_thickness = _unwrap(spec.domain.thickness_m, 1.0)

        return [
            ObsRectangleSpec(
                rectangle_id="rectangle_1",
                center_x=center_x,
                center_y=center_y,
                width=width,
                height=height,
                thickness=domain_thickness,
            )
        ]

    def _adapt_triangles(self, spec: CylinderFlow2DExperimentSpecV1) -> list[ObsTriangleSpec]:
        """Adapt triangle obstacle from cylinder flow spec to obstacle flow spec."""
        if not spec.has_triangle:
            return []

        tri = spec.triangle

        # Validate semantic_type — must be 'triangle_2d'.  A mutated
        # semantic_type (e.g. 'cosine_bell') indicates the obstacle
        # identity was silently changed, which the compiler must reject.
        if tri.semantic_type != "triangle_2d":
            raise ValueError(
                f"TriangleSpec.semantic_type must be 'triangle_2d', got "
                f"'{tri.semantic_type}' — obstacle identity mismatch"
            )

        base_width = _unwrap(tri.base_width_m)
        height = _unwrap(tri.height_m)
        center_x = _unwrap(tri.center_x_m)

        # If triangle center_x is not set, use cylinder center_x
        if center_x is None and spec.has_cylinder:
            center_x = _unwrap(spec.cylinder.center_x_m)

        # Triangle is wall-attached: base at y=0, apex at y=height
        center_y = 0.0

        # If width or height is missing, cannot proceed
        if base_width is None or height is None:
            logger.warning(
                "Triangle dimensions missing (base_width=%s, height=%s) — skipping",
                base_width, height,
            )
            return []

        domain_thickness = _unwrap(spec.domain.thickness_m, 1.0)

        return [
            ObsTriangleSpec(
                triangle_id="triangle_1",
                center_x=center_x if center_x is not None else 0.0,
                center_y=center_y,
                base_width=base_width,
                height=height,
                apex_direction=tri.apex_direction,
                thickness=domain_thickness,
            )
        ]

    def _adapt_trapezoid(self, spec: CylinderFlow2DExperimentSpecV1) -> list[ObsTrapezoidSpec]:
        """Adapt trapezoid obstacle from cylinder flow spec to obstacle flow spec."""
        if not spec.trapezoid.enabled:
            return []

        trap = spec.trapezoid
        top_width = _unwrap(trap.top_width_m)
        bottom_width = _unwrap(trap.bottom_width_m)
        height = _unwrap(trap.height_m)
        center_x = _unwrap(trap.center_x_m)

        # If trapezoid center_x is not set, use cylinder center_x
        if center_x is None and spec.has_cylinder:
            center_x = _unwrap(spec.cylinder.center_x_m)

        # Trapezoid is wall-attached: wide base at y=0
        center_y = 0.0

        # If dimensions are missing, cannot proceed
        if top_width is None or bottom_width is None or height is None:
            logger.warning(
                "Trapezoid dimensions missing (top_width=%s, bottom_width=%s, height=%s) — skipping",
                top_width, bottom_width, height,
            )
            return []

        domain_thickness = _unwrap(spec.domain.thickness_m, 1.0)

        return [
            ObsTrapezoidSpec(
                trapezoid_id="trapezoid_1",
                center_x=center_x if center_x is not None else 0.0,
                center_y=center_y,
                top_width=top_width,
                bottom_width=bottom_width,
                height=height,
                thickness=domain_thickness,
            )
        ]

    def _adapt_flow_definition(self, spec: CylinderFlow2DExperimentSpecV1) -> FlowDefinitionSpec:
        # Resolve flow mode
        raw_mode = spec.flow_topology.get("mode")
        flow_mode = self._resolve_flow_mode(raw_mode)

        # Initial velocity
        ic = spec.initial_conditions.velocity
        init_velocity = InitialVelocitySpec(
            type=ic.type,
            vector_m_s=list(ic.vector_m_s),
        )

        return FlowDefinitionSpec(
            mode=flow_mode,
            initial_velocity=init_velocity,
        )

    def _resolve_flow_mode(self, raw_mode: Any) -> ObsFlowMode:
        """Resolve the flow mode from the flow_topology dict."""
        if raw_mode is None:
            return ObsFlowMode.INLET_OUTLET
        if isinstance(raw_mode, CylFlowMode):
            return ObsFlowMode(raw_mode.value)
        if isinstance(raw_mode, str):
            try:
                return ObsFlowMode(raw_mode)
            except ValueError:
                pass
        return ObsFlowMode.INLET_OUTLET

    def _adapt_boundaries(self, spec: CylinderFlow2DExperimentSpecV1) -> ObsBoundaryConfig:
        b = spec.boundaries
        return ObsBoundaryConfig(
            left=self._adapt_boundary(b.left),
            right=self._adapt_boundary(b.right),
            top=self._adapt_boundary(b.top),
            bottom_flat=self._adapt_boundary(b.bottom_flat),
            bump_surface=self._adapt_boundary(b.bottom_profile_surface),
            front=self._adapt_boundary(b.front),
            back=self._adapt_boundary(b.back),
        )

    def _adapt_boundary(self, cyl_b) -> ObsBoundarySpec:
        """Adapt a single cylinder_flow_2d BoundarySpec to obstacle_flow."""
        semantic_type = cyl_b.semantic_type

        if semantic_type is None:
            boundary_type = BoundaryType.NO_SLIP_WALL
        else:
            boundary_type = _BOUNDARY_TYPE_MAP.get(
                semantic_type, BoundaryType.NO_SLIP_WALL
            )

        # Gather parameters, providing defaults required by validators
        inlet_velocity = cyl_b.inlet_velocity
        pressure_value = cyl_b.pressure_value
        velocity_vector = cyl_b.velocity_vector
        shear_direction = cyl_b.shear_direction
        shear_magnitude = cyl_b.shear_magnitude
        freestream_velocity = cyl_b.freestream_velocity
        pressure_gradient_magnitude = cyl_b.pressure_gradient_magnitude

        # Ensure validator-required fields have values
        if boundary_type == BoundaryType.VELOCITY_INLET and inlet_velocity is None:
            inlet_velocity = 1.0
        if boundary_type == BoundaryType.MOVING_WALL and velocity_vector is None:
            velocity_vector = [0.0, 0.0, 0.0]
        if boundary_type == BoundaryType.SHEAR_STRESS:
            if shear_direction is None:
                shear_direction = [1.0, 0.0, 0.0]
            if shear_magnitude is None:
                shear_magnitude = 0.0
        if boundary_type == BoundaryType.FREESTREAM and freestream_velocity is None:
            freestream_velocity = 1.0
        if boundary_type == BoundaryType.PRESSURE_BOUNDARY and pressure_value is None:
            pressure_value = 0.0

        return ObsBoundarySpec(
            type=boundary_type,
            velocity_vector=velocity_vector,
            pressure_value=pressure_value,
            shear_direction=shear_direction,
            shear_magnitude=shear_magnitude,
            freestream_velocity=freestream_velocity,
            pressure_gradient_magnitude=pressure_gradient_magnitude,
            inlet_velocity=inlet_velocity,
        )

    def _adapt_inlet_profile(self, spec: CylinderFlow2DExperimentSpecV1) -> ObsInletProfileSpec:
        ip = spec.inlet_profile
        temporal = _TEMPORAL_MAP.get(ip.temporal_type, ObsTemporalType.CONSTANT)
        spatial = _SPATIAL_MAP.get(ip.spatial_type, ObsSpatialType.UNIFORM)
        return ObsInletProfileSpec(
            enabled=ip.enabled,
            temporal_type=temporal,
            spatial_type=spatial,
            parameters=dict(ip.parameters),
        )

    def _adapt_forcing(self, spec: CylinderFlow2DExperimentSpecV1) -> ObsForcingSpec:
        pf = spec.forcing

        # Pressure gradient
        pg = pf.pressure_gradient
        pg_unit_raw = _unwrap(pg.unit)
        pg_unit = None
        if pg_unit_raw is not None:
            try:
                pg_unit = PressureGradientUnit(pg_unit_raw)
            except (ValueError, TypeError):
                pg_unit = None

        obs_pg = ObsPressureGradientSpec(
            enabled=pg.enabled,
            direction=list(pg.direction),
            magnitude=_unwrap(pg.magnitude),
            unit=pg_unit,
        )

        # Body force
        bf = pf.body_force
        obs_bf = ObsBodyForceSpec(
            enabled=bf.enabled,
            vector_m_s2=list(bf.vector_m_s2),
        )

        return ObsForcingSpec(
            pressure_gradient=obs_pg,
            body_force=obs_bf,
        )

    def _adapt_simulation(self, spec: CylinderFlow2DExperimentSpecV1) -> ObsSimulationSpec:
        sim = spec.simulation
        time_mode = _TIME_MODE_MAP.get(sim.time_mode, ObsTimeMode.AUTO)
        flow_regime = _FLOW_REGIME_MAP.get(sim.flow_regime, ObsFlowRegime.AUTO)

        # Preserve the user-specified end_time (None when unset) so that
        # ObstacleFlowCompiler.compute_time_step can apply context-aware
        # defaults (100.0 for transient, 1000.0 for steady) instead of
        # receiving a blanket 10.0 that is wrong for steady-state iterations.
        end_time = sim.end_time
        max_courant = sim.max_courant_number if sim.max_courant_number else DEFAULT_MAX_COURANT

        # Force transient for cylinder flow (cylinders produce unsteady wakes)
        if time_mode == ObsTimeMode.AUTO:
            time_mode = ObsTimeMode.TRANSIENT

        return ObsSimulationSpec(
            time_mode=time_mode,
            flow_regime=flow_regime,
            turbulence_model=TurbulenceModel.AUTO,
            max_courant_number=max_courant,
            end_time=end_time,
            delta_t=sim.delta_t,
        )

    def _adapt_observables(self, spec: CylinderFlow2DExperimentSpecV1) -> list[ObsObservableSpec]:
        """Map observables that have a direct obstacle_flow equivalent."""
        result: list[ObsObservableSpec] = []
        # Get cylinder info for defaults
        cyl_id = "cylinder_1"
        cyl_x = _unwrap(spec.cylinder.center_x_m, 5.0)
        cyl_y = _unwrap(spec.cylinder.center_y_m, 2.0)
        cyl_d = spec.get_cylinder_diameter() or 0.2
        domain_len = _unwrap(spec.domain.length_m, 30.0)

        for obs in spec.observables:
            mapped = _OBSERVABLE_MAP.get(obs.type)
            if mapped is None:
                continue

            component = obs.component if obs.component in ("Ux", "Uy", "magnitude") else "Ux"

            # Provide defaults for required fields
            cylinder_id = obs.cylinder_id or cyl_id
            point = obs.point
            section_x = obs.section_x

            # Default point: 10 diameters downstream of cylinder center
            if point is None and mapped == ObsObservableType.POINT_VELOCITY:
                point = [cyl_x + cyl_d * 10, cyl_y, 0.0]

            # Default section_x: 10 diameters downstream of cylinder center
            if section_x is None and mapped == ObsObservableType.SECTION_MEAN_VELOCITY:
                section_x = min(cyl_x + cyl_d * 10, domain_len * 0.8)

            try:
                result.append(ObsObservableSpec(
                    type=mapped,
                    label=obs.label,
                    point=point,
                    section_x=section_x,
                    component=component,
                    averaging="time_average",
                    time_window=None,
                    cylinder_id=cylinder_id,
                    wall_name=obs.wall_name,
                ))
            except Exception as exc:
                logger.warning("Skipping observable %s: %s", obs.type, exc)
        return result

    def _adapt_plot_requests(self, spec: CylinderFlow2DExperimentSpecV1) -> list[PlotRequest]:
        """Build plot requests from observables and sensible defaults."""
        requests: list[PlotRequest] = []

        for obs in spec.observables:
            plot = _OBSERVABLE_TO_PLOT.get(obs.type)
            if plot is not None and plot not in requests:
                requests.append(plot)

        # Ensure minimum default set
        defaults = [
            PlotRequest.VELOCITY_MAGNITUDE,
            PlotRequest.UX,
            PlotRequest.PRESSURE,
            PlotRequest.VORTICITY,
            PlotRequest.STREAMLINES,
        ]
        for d in defaults:
            if d not in requests:
                requests.append(d)

        # Always include Cd/Cl time series when cylinder is present
        if spec.has_cylinder and PlotRequest.CD_CL_TIME_SERIES not in requests:
            requests.append(PlotRequest.CD_CL_TIME_SERIES)

        return requests


# ---------------------------------------------------------------------------
# 2. WorkstationExecutor
# ---------------------------------------------------------------------------


class WorkstationExecutor:
    """Execute OpenFOAM on the remote workstation via SSH.

    All SSH commands are prefixed with ``source /opt/openfoam13/etc/bashrc``
    so that the OpenFOAM environment is available.
    """

    def __init__(
        self,
        host: str = "10.129.177.241",
        user: str = "ls",
        key_path: str = SSH_KEY_DEFAULT,
        remote_root: str = "/home/ls/fluid_scientist/runs",
    ) -> None:
        self.host = host
        self.user = user
        self.key_path = _expand_key_path(key_path)
        self.remote_root = remote_root

    # -- low-level SSH / SCP ------------------------------------------------

    def _ssh(self, command: str, timeout: int = 600) -> subprocess.CompletedProcess:
        """Run *command* on the remote host via SSH.

        The command is automatically prefixed with the OpenFOAM bashrc
        source so that all OpenFOAM utilities are on PATH.
        """
        full = f"source {OPENFOAM_BASHRC} && {command}"
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-i", self.key_path,
            f"{self.user}@{self.host}",
            full,
        ]
        logger.debug("SSH: %s", full)
        return subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _ssh_raw(self, command: str, timeout: int = 600) -> subprocess.CompletedProcess:
        """Run *command* on the remote host WITHOUT the bashrc prefix."""
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-i", self.key_path,
            f"{self.user}@{self.host}",
            command,
        ]
        logger.debug("SSH(raw): %s", command)
        return subprocess.run(
            ssh_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _scp_download(self, remote_path: str, local_path: str, timeout: int = 300) -> bool:
        """Download a file from the remote host."""
        scp_cmd = [
            "scp",
            "-o", "StrictHostKeyChecking=accept-new",
            "-i", self.key_path,
            f"{self.user}@{self.host}:{remote_path}",
            local_path,
        ]
        logger.debug("SCP download: %s -> %s", remote_path, local_path)
        result = subprocess.run(
            scp_cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0

    def _scp_upload(self, local_path: str, remote_path: str, timeout: int = 300) -> bool:
        """Upload a file to the remote host."""
        scp_cmd = [
            "scp",
            "-o", "StrictHostKeyChecking=accept-new",
            "-i", self.key_path,
            local_path,
            f"{self.user}@{self.host}:{remote_path}",
        ]
        logger.debug("SCP upload: %s -> %s", local_path, remote_path)
        result = subprocess.run(
            scp_cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0

    # -- public API ---------------------------------------------------------

    def upload_case(self, job_id: str, archive: bytes) -> str:
        """Upload a tar.gz *archive* and extract it on the remote host.

        Returns the remote case directory path.
        """
        remote_dir = f"{self.remote_root}/{job_id}"
        remote_archive = f"{self.remote_root}/{job_id}.tar.gz"

        # Create remote root
        self._ssh_raw(f"mkdir -p {self.remote_root}")

        # Write archive to local temp file and upload
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
            f.write(archive)
            local_archive = f.name

        try:
            if not self._scp_upload(local_archive, remote_archive):
                raise RuntimeError(
                    f"Failed to upload archive to {remote_archive}"
                )

            # Extract on remote
            extract_cmd = (
                f"mkdir -p {remote_dir} && "
                f"tar -xzf {remote_archive} -C {remote_dir} && "
                f"rm -f {remote_archive}"
            )
            result = self._ssh_raw(extract_cmd)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to extract archive: {result.stderr}"
                )
        finally:
            os.unlink(local_archive)

        logger.info("Case uploaded to %s", remote_dir)
        return remote_dir

    def run_mesh(self, case_path: str) -> dict:
        """Run blockMesh, snappyHexMesh (if dict exists), and checkMesh.

        For 2D cases, snappyHexMesh cannot handle empty patches.
        We temporarily change front/back to wall, run snappyHexMesh,
        then change back to empty.

        If no snappyHexMeshDict is present (e.g. bump-only cases without
        STL surfaces), snappyHexMesh is skipped and only blockMesh +
        checkMesh are run.

        Returns a dict with parsed checkMesh statistics.
        """
        # Step 1: blockMesh
        result = self._ssh(
            f"cd {case_path} && blockMesh",
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"blockMesh failed: {result.stderr[-500:]}"
            )

        # Step 1b: Check if snappyHexMeshDict exists
        check_dict = self._ssh_raw(
            f"test -f {case_path}/system/snappyHexMeshDict && echo 'SNAPPY_EXISTS' || echo 'SNAPPY_MISSING'",
            timeout=30,
        )
        has_snappy_dict = "SNAPPY_EXISTS" in check_dict.stdout

        if has_snappy_dict:
            # Step 2: Change front/back from empty to wall for snappyHexMesh
            # Use sed (more reliable through SSH than awk)
            sed_result = self._ssh(
                f"cd {case_path} && "
                f"sed -i '/frontAndBack/,/}}/ s/empty/wall/g' constant/polyMesh/boundary",
                timeout=30,
            )
            if sed_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to change frontAndBack to wall: {sed_result.stderr}"
                )

            # Step 3: snappyHexMesh
            result = self._ssh(
                f"cd {case_path} && snappyHexMesh -overwrite",
                timeout=600,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"snappyHexMesh failed: {result.stderr[-500:]}"
                )

            # Step 4: Change front/back back to empty
            sed_result = self._ssh(
                f"cd {case_path} && "
                f"sed -i '/frontAndBack/,/}}/ s/wall/empty/g' constant/polyMesh/boundary",
                timeout=30,
            )
            if sed_result.returncode != 0:
                raise RuntimeError(
                    f"Failed to change frontAndBack back to empty: {sed_result.stderr}"
                )
        else:
            logger.info(
                "No snappyHexMeshDict found — skipping snappyHexMesh (blockMesh only)"
            )

        # Step 5: checkMesh
        result = self._ssh(
            f"cd {case_path} && checkMesh -allTopology -allGeometry",
            timeout=300,
        )

        report: dict[str, Any] = {
            "returncode": result.returncode,
            "stdout": result.stdout[-4000:] if len(result.stdout) > 4000 else result.stdout,
            "stderr": result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr,
            "mesh_ok": False,
            "stats": {},
        }

        output = result.stdout + "\n" + result.stderr

        # Parse checkMesh stats
        report["stats"] = self._parse_checkmesh(output)
        report["mesh_ok"] = "Mesh OK." in output or "mesh_ok" in output.lower()

        if result.returncode != 0:
            logger.error("Mesh generation failed: %s", result.stderr[-500:])
        else:
            logger.info("Mesh generation succeeded: %s", report["stats"])

        return report

    def _parse_checkmesh(self, output: str) -> dict:
        """Parse checkMesh output for mesh statistics."""
        stats: dict[str, Any] = {}
        patterns = {
            "points": r"points:\s+(\d+)",
            "cells": r"cells:\s+(\d+)",
            "faces": r"faces:\s+(\d+)",
            "internal_faces": r"internal faces:\s+(\d+)",
            "boundary_patches": r"boundary patches:\s+(\d+)",
            "max_aspect_ratio": r"Max aspect ratio:\s+([\d.]+)",
            "min_volume": r"Min volume:\s+([\d.eE+-]+)",
            "max_volume": r"Max volume:\s+([\d.eE+-]+)",
            "total_volume": r"Total volume:\s+([\d.eE+-]+)",
            "bounding_box": r"Overall domain bounding box\s*\(([^)]+)\)",
        }
        for key, pattern in patterns.items():
            m = re.search(pattern, output)
            if m:
                val = m.group(1).strip()
                try:
                    stats[key] = int(val)
                except ValueError:
                    try:
                        stats[key] = float(val)
                    except ValueError:
                        stats[key] = val
        return stats

    def run_smoke_test(self, case_path: str, timeout: int = 300) -> dict:
        """Run a 2-timestep smoke test.

        Temporarily modifies controlDict to run only 2 timesteps with
        fixed deltaT (no adaptive time stepping), then restores the
        original.  Parses the log for errors, NaN, and Courant number.
        """
        # Backup controlDict and modify for smoke test
        # Use fixed deltaT and disable adjustTimeStep for fast, predictable smoke test
        setup_cmd = (
            f"cd {case_path} && "
            f"cp system/controlDict system/controlDict.smoke.bak && "
            f"DELTA_T=$(grep -E '^\\s*deltaT\\s' system/controlDict | "
            f"awk '{{print $2}}' | tr -d ';') && "
            f"if [ -z \"$DELTA_T\" ]; then DELTA_T=0.01; fi && "
            f"SMOKE_END=$(python3 -c \"print(2 * float('$DELTA_T'))\") && "
            f"sed -i \"s/endTime\\s.*;/endTime         $SMOKE_END;/\" system/controlDict && "
            f"sed -i 's/writeInterval\\s.*;/writeInterval         1;/' system/controlDict && "
            f"sed -i 's/adjustTimeStep\\s.*;/adjustTimeStep  no;/' system/controlDict && "
            f"echo \"Smoke test endTime=$SMOKE_END deltaT=$DELTA_T (fixed, no adapt)\""
        )
        setup_result = self._ssh(setup_cmd, timeout=30)
        logger.info("Smoke setup: %s", setup_result.stdout.strip())

        # Run the solver
        run_cmd = f"cd {case_path} && foamRun -solver incompressibleFluid 2>&1 | tee log.smokeTest"
        result = self._ssh(run_cmd, timeout=timeout)

        # Restore controlDict
        restore_cmd = (
            f"cd {case_path} && "
            f"mv system/controlDict.smoke.bak system/controlDict"
        )
        self._ssh(restore_cmd, timeout=15)

        # Parse results
        output = result.stdout + "\n" + result.stderr
        report: dict[str, Any] = {
            "returncode": result.returncode,
            "has_nan": bool(re.search(r"\bNaN\b|\bnan\b", output)),
            "has_error": "FOAM FATAL ERROR" in output or "FOAM FATAL IO ERROR" in output,
            "courant_mean": None,
            "courant_max": None,
            "completed_timesteps": 0,
            "log_path": f"{case_path}/log.smokeTest",
        }

        # Parse Courant numbers (take the last occurrence)
        courant_matches = re.findall(
            r"Courant Number mean:\s+([\d.eE+-]+)\s+max:\s+([\d.eE+-]+)",
            output,
        )
        if courant_matches:
            last = courant_matches[-1]
            report["courant_mean"] = float(last[0])
            report["courant_max"] = float(last[1])

        # Count completed timesteps
        time_matches = re.findall(r"^Time\s*=\s*([\d.]+)", output, re.MULTILINE)
        report["completed_timesteps"] = len(time_matches)

        # Determine status
        if report["has_nan"] or report["has_error"]:
            report["status"] = "FAILED"
        elif report["completed_timesteps"] >= 1:
            report["status"] = "PASSED"
        else:
            report["status"] = "FAILED"

        # Truncate output for storage
        report["output_tail"] = output[-2000:]

        logger.info(
            "Smoke test: status=%s, Co_mean=%s, Co_max=%s, steps=%d",
            report["status"],
            report["courant_mean"],
            report["courant_max"],
            report["completed_timesteps"],
        )
        return report

    def run_full(
        self,
        case_path: str,
        parallel: bool = False,
        np: int = 4,
    ) -> dict:
        """Run the full simulation.

        If *parallel* is True, decompose the case and run with mpirun.
        Returns a dict with run status and log path.
        """
        if parallel:
            cmd = (
                f"cd {case_path} && "
                f"decomposePar -force && "
                f"mpirun -np {np} foamRun -solver incompressibleFluid -parallel "
                f"2>&1 | tee log.fullRun"
            )
        else:
            cmd = (
                f"cd {case_path} && "
                f"foamRun -solver incompressibleFluid 2>&1 | tee log.fullRun"
            )

        logger.info("Starting full simulation (parallel=%s, np=%d)", parallel, np)
        result = self._ssh(cmd, timeout=7200)  # 2-hour timeout

        output = result.stdout + "\n" + result.stderr
        report: dict[str, Any] = {
            "returncode": result.returncode,
            "parallel": parallel,
            "np": np if parallel else 1,
            "log_path": f"{case_path}/log.fullRun",
            "has_nan": bool(re.search(r"\bNaN\b|\bnan\b", output)),
            "has_error": "FOAM FATAL ERROR" in output or "FOAM FATAL IO ERROR" in output,
            "final_time": None,
            "courant_max": None,
        }

        # Parse final time
        time_matches = re.findall(r"^Time\s*=\s*([\d.]+)", output, re.MULTILINE)
        if time_matches:
            report["final_time"] = float(time_matches[-1])

        # Parse max Courant
        courant_matches = re.findall(
            r"Courant Number mean:\s+([\d.eE+-]+)\s+max:\s+([\d.eE+-]+)",
            output,
        )
        if courant_matches:
            report["courant_max"] = float(courant_matches[-1][1])

        if result.returncode == 0 and not report["has_nan"]:
            report["status"] = "SUCCESS"
        elif report["has_nan"]:
            report["status"] = "FAILED_NAN"
        else:
            report["status"] = "FAILED"

        report["output_tail"] = output[-3000:]
        logger.info(
            "Full simulation: status=%s, final_time=%s, Co_max=%s",
            report["status"],
            report["final_time"],
            report["courant_max"],
        )
        return report

    def collect_results(self, case_path: str) -> list[str]:
        """List result files on the remote host.

        Returns a list of remote file paths for logs, postProcessing data,
        and time directories.
        """
        cmd = (
            f"cd {case_path} && "
            f"find . -maxdepth 4 "
            f"\\( -name 'log.*' -o -name '*.dat' -o -name '*.csv' "
            f"-o -name 'forceCoeffs.dat' \\) "
            f"-type f 2>/dev/null | sort"
        )
        result = self._ssh_raw(cmd, timeout=60)
        files = [
            f"{case_path}/{line.strip()}"
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ]

        # Also list time directories
        cmd_times = (
            f"cd {case_path} && "
            f"ls -d [0-9]* 2>/dev/null | sort -n"
        )
        result_times = self._ssh_raw(cmd_times, timeout=30)
        time_dirs = [
            f"{case_path}/{line.strip()}"
            for line in result_times.stdout.strip().split("\n")
            if line.strip() and line.strip() != "0"
        ]

        all_results = files + time_dirs
        logger.info("Collected %d result entries from %s", len(all_results), case_path)
        return all_results

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download a single file from the remote host.

        Creates parent directories of *local_path* if needed.
        """
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        return self._scp_download(remote_path, local_path)

    def run_remote_command(self, command: str, timeout: int = 600) -> subprocess.CompletedProcess:
        """Run an arbitrary command on the remote host (with bashrc sourced).

        This is used by the Postprocessor for postProcess / sample commands.
        """
        return self._ssh(command, timeout=timeout)

    def list_remote_directory(self, remote_path: str) -> list[str]:
        """List the contents of a remote directory."""
        result = self._ssh_raw(f"ls -1 {remote_path} 2>/dev/null", timeout=30)
        return [
            line.strip()
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ]


# ---------------------------------------------------------------------------
# 3. Postprocessor
# ---------------------------------------------------------------------------


class Postprocessor:
    """Generate matplotlib plots from OpenFOAM simulation results.

    Connects to the workstation via SSH to run ``postProcess`` and
    ``sample`` utilities, downloads the extracted data, and produces
    PNG plots locally using matplotlib.

    Only matplotlib and numpy are required locally — pyvista / gmsh are
    not used.
    """

    def __init__(
        self,
        executor: WorkstationExecutor | None = None,
        results_root: Path = RESULTS_ROOT,
    ) -> None:
        self.executor = executor or WorkstationExecutor()
        self.results_root = Path(results_root)

    def generate_plots(
        self,
        case_path: str,
        job_id: str,
        spec: Any,
    ) -> list[str]:
        """Generate all plots for a completed simulation.

        Parameters
        ----------
        case_path : str
            Remote case directory on the workstation.
        job_id : str
            Unique run identifier (used for output directory naming).
        spec : Any
            Either a ``CylinderFlow2DExperimentSpecV1`` or an
            ``ObstacleFlowExperimentSpecV1`` — used for domain/cylinder
            geometry metadata in plot annotations.

        Returns
        -------
        list[str]
            Absolute paths to generated PNG files.
        """
        results_dir = self.results_root / job_id
        results_dir.mkdir(parents=True, exist_ok=True)

        # Extract geometry metadata from the spec
        geom = self._extract_geometry(spec)

        plot_paths: list[str] = []

        # --- Field data plots ---
        field_data = self._fetch_field_data(case_path, geom)

        if field_data is not None:
            sim_time = field_data.get("time", 0.0)
            x = field_data.get("x")
            y = field_data.get("y")
            ux = field_data.get("ux")
            uy = field_data.get("uy")
            p = field_data.get("p")
            vort = field_data.get("vorticity")

            # Velocity magnitude
            if ux is not None and uy is not None:
                vmag = np.sqrt(ux ** 2 + uy ** 2)
                path = self._plot_contour(
                    x, y, vmag,
                    title="Velocity Magnitude",
                    zlabel="|U| [m/s]",
                    job_id=job_id,
                    spec_version=getattr(spec, "spec_version", 1),
                    sim_time=sim_time,
                    geom=geom,
                    output_path=results_dir / "velocity_magnitude.png",
                )
                if path:
                    plot_paths.append(path)

            # Ux contour
            if ux is not None:
                path = self._plot_contour(
                    x, y, ux,
                    title="Ux — Streamwise Velocity",
                    zlabel="Ux [m/s]",
                    job_id=job_id,
                    spec_version=getattr(spec, "spec_version", 1),
                    sim_time=sim_time,
                    geom=geom,
                    output_path=results_dir / "ux.png",
                    cmap="RdBu_r",
                    symmetric=True,
                )
                if path:
                    plot_paths.append(path)

            # Pressure contour
            if p is not None:
                path = self._plot_contour(
                    x, y, p,
                    title="Pressure Field",
                    zlabel="p [Pa]",
                    job_id=job_id,
                    spec_version=getattr(spec, "spec_version", 1),
                    sim_time=sim_time,
                    geom=geom,
                    output_path=results_dir / "pressure.png",
                    cmap="coolwarm",
                )
                if path:
                    plot_paths.append(path)

            # Vorticity contour
            if vort is not None:
                path = self._plot_contour(
                    x, y, vort,
                    title="Vorticity Field",
                    zlabel=r"ωz [1/s]",
                    job_id=job_id,
                    spec_version=getattr(spec, "spec_version", 1),
                    sim_time=sim_time,
                    geom=geom,
                    output_path=results_dir / "vorticity.png",
                    cmap="RdBu_r",
                    symmetric=True,
                )
                if path:
                    plot_paths.append(path)

            # Streamlines
            if ux is not None and uy is not None:
                path = self._plot_streamlines(
                    x, y, ux, uy,
                    job_id=job_id,
                    spec_version=getattr(spec, "spec_version", 1),
                    sim_time=sim_time,
                    geom=geom,
                    output_path=results_dir / "streamlines.png",
                )
                if path:
                    plot_paths.append(path)
        else:
            logger.warning("No field data available — skipping field plots")

        # --- Force coefficient time series ---
        force_path = self._plot_force_coefficients(
            case_path, job_id, spec, results_dir
        )
        if force_path:
            plot_paths.append(force_path)

        # --- Animations ---
        try:
            anim_paths = self._generate_animations(
                case_path, job_id, spec, results_dir, geom
            )
            plot_paths.extend(anim_paths)
        except Exception as exc:
            logger.warning("Animation generation failed: %s", exc)

        logger.info("Generated %d outputs (plots + animations) in %s", len(plot_paths), results_dir)
        return plot_paths

    # -- geometry extraction ------------------------------------------------

    def _extract_geometry(self, spec: Any) -> dict:
        """Extract domain and cylinder geometry from either spec type."""
        geom: dict[str, Any] = {
            "domain_length": DEFAULT_DOMAIN_LENGTH,
            "domain_height": DEFAULT_DOMAIN_HEIGHT,
            "domain_thickness": DEFAULT_DOMAIN_THICKNESS,
            "has_cylinder": False,
            "cylinder_center_x": None,
            "cylinder_center_y": None,
            "cylinder_radius": None,
        }

        if isinstance(spec, ObstacleFlowExperimentSpecV1):
            geom["domain_length"] = spec.domain.length_m
            geom["domain_height"] = spec.domain.height_m
            geom["domain_thickness"] = spec.domain.thickness_m
            if spec.has_cylinder:
                cyl = spec.cylinders[0]
                geom["has_cylinder"] = True
                geom["cylinder_center_x"] = cyl.center_x_m
                geom["cylinder_center_y"] = cyl.center_y_m
                geom["cylinder_radius"] = cyl.radius_m
        elif isinstance(spec, CylinderFlow2DExperimentSpecV1):
            geom["domain_length"] = _unwrap(spec.domain.length_m, DEFAULT_DOMAIN_LENGTH)
            geom["domain_height"] = _unwrap(spec.domain.height_m, DEFAULT_DOMAIN_HEIGHT)
            geom["domain_thickness"] = _unwrap(spec.domain.thickness_m, DEFAULT_DOMAIN_THICKNESS)
            if spec.has_cylinder:
                geom["has_cylinder"] = True
                geom["cylinder_center_x"] = _unwrap(spec.cylinder.center_x_m)
                geom["cylinder_center_y"] = _unwrap(spec.cylinder.center_y_m)
                geom["cylinder_radius"] = spec.get_cylinder_radius()
        else:
            # Try duck-typing
            try:
                geom["domain_length"] = getattr(spec.domain, "length_m", DEFAULT_DOMAIN_LENGTH)
                if isinstance(geom["domain_length"], ProvenanceField):
                    geom["domain_length"] = _unwrap(geom["domain_length"], DEFAULT_DOMAIN_LENGTH)
                geom["domain_height"] = getattr(spec.domain, "height_m", DEFAULT_DOMAIN_HEIGHT)
                if isinstance(geom["domain_height"], ProvenanceField):
                    geom["domain_height"] = _unwrap(geom["domain_height"], DEFAULT_DOMAIN_HEIGHT)
                geom["domain_thickness"] = getattr(spec.domain, "thickness_m", DEFAULT_DOMAIN_THICKNESS)
                if isinstance(geom["domain_thickness"], ProvenanceField):
                    geom["domain_thickness"] = _unwrap(geom["domain_thickness"], DEFAULT_DOMAIN_THICKNESS)
            except Exception:
                pass

        return geom

    # -- field data fetching ------------------------------------------------

    def _fetch_field_data(self, case_path: str, geom: dict) -> dict | None:
        """Run postProcess and foamToVTK on the remote workstation, then download VTK data.

        The ``sample`` utility is unavailable in OpenFOAM Foundation 13, so
        field data is exported with ``foamToVTK`` (legacy ASCII VTK) and
        parsed locally by :meth:`_parse_vtk_ascii`.

        Returns a dict with keys: x, y, ux, uy, p, vorticity, time.
        Returns None if no data could be extracted.
        """
        # Step 1: Get the latest time directory
        result = self.executor.run_remote_command(
            f"cd {case_path} && foamListTimes -latestTime 2>/dev/null",
            timeout=30,
        )
        latest_time = result.stdout.strip()
        if not latest_time or latest_time == "0":
            # Try listing directories manually
            result = self.executor._ssh_raw(
                f"cd {case_path} && ls -d [0-9]* 2>/dev/null | sort -n | tail -1",
                timeout=30,
            )
            latest_time = result.stdout.strip()

        if not latest_time or latest_time == "0":
            logger.warning("No simulation time directories found")
            return None

        logger.info("Latest time directory: %s", latest_time)

        try:
            sim_time = float(latest_time)
        except ValueError:
            sim_time = 0.0

        # Step 2: Create the vorticity derived field
        pp_result = self.executor.run_remote_command(
            f"cd {case_path} && postProcess -func 'vorticity' -latestTime 2>&1 | tail -5",
            timeout=180,
        )
        if pp_result.returncode != 0:
            logger.warning(
                "postProcess vorticity failed: %s",
                (pp_result.stderr or "")[-300:],
            )

        # Step 3: Export selected fields to legacy ASCII VTK
        vtk_result = self.executor.run_remote_command(
            f"cd {case_path} && foamToVTK -ascii -latestTime -fields '(U p vorticity)' 2>&1 | tail -20",
            timeout=300,
        )
        if vtk_result.returncode != 0:
            logger.warning(
                "foamToVTK failed: %s",
                (vtk_result.stderr or "")[-500:],
            )
            return self._fetch_field_data_fallback(case_path, latest_time, geom)

        # Step 4: Locate the VTK file produced by foamToVTK
        # Foundation 13 foamToVTK names the internal mesh file as
        # <caseName>_<time>.vtk (not internalMesh_<time>.vtk)
        vtk_dir = f"{case_path}/VTK"
        file_list = self.executor.list_remote_directory(vtk_dir)
        if not file_list:
            logger.warning("No VTK directory contents at %s", vtk_dir)
            return None

        # Filter to top-level .vtk files (not in patch subdirectories)
        vtk_files = [f for f in file_list if f.endswith(".vtk")]

        vtk_filename = None
        # Prefer a file whose name contains the latest time
        for fname in vtk_files:
            if latest_time in fname:
                vtk_filename = fname
                break
        # Fallback to any .vtk file
        if vtk_filename is None and vtk_files:
            vtk_filename = vtk_files[0]

        if vtk_filename is None:
            logger.warning("No VTK file found in %s (files: %s)", vtk_dir, file_list)
            return None

        # Step 5: Download the VTK file
        remote_vtk = f"{vtk_dir}/{vtk_filename}"
        temp_dir = tempfile.mkdtemp(prefix="foam_vtk_")
        local_vtk = os.path.join(temp_dir, vtk_filename)

        if not self.executor._scp_download(remote_vtk, local_vtk):
            logger.warning("Failed to download VTK file: %s", remote_vtk)
            try:
                import shutil
                shutil.rmtree(temp_dir)
            except Exception:
                pass
            return None

        # Step 6: Parse the VTK file
        field_data = self._parse_vtk_ascii(local_vtk, sim_time)

        # Clean up temp directory
        try:
            import shutil
            shutil.rmtree(temp_dir)
        except Exception:
            pass

        if field_data is None:
            logger.warning("Could not parse VTK file: %s", local_vtk)
            return None

        return field_data

    def _fetch_field_data_multi_time(
        self, case_path: str, geom: dict, max_steps: int = 12
    ) -> list[dict]:
        """Fetch field data at multiple time steps for animation.

        Exports VTK files for all time steps, selects up to ``max_steps``
        evenly spaced time steps, downloads and parses each one.

        Returns a list of field_data dicts (each with keys: x, y, ux, uy, p,
        vorticity, time). Returns an empty list if no data could be extracted.
        """
        # Step 1: Get all time directories (excluding 0 — initial condition)
        result = self.executor.run_remote_command(
            f"cd {case_path} && foamListTimes 2>/dev/null",
            timeout=30,
        )
        raw_output = result.stdout.strip()
        all_times_raw = raw_output.replace(",", " ").split()
        all_times = []
        for t_str in all_times_raw:
            t_str = t_str.strip()
            if not t_str:
                continue
            try:
                t_val = float(t_str)
                if t_val > 0.001:  # skip t=0 (initial condition)
                    all_times.append((t_str, t_val))
            except ValueError:
                continue

        # Fallback: list directories manually
        if not all_times:
            logger.info("foamListTimes returned no times — trying ls fallback")
            ls_result = self.executor._ssh_raw(
                f"cd {case_path} && ls -d [0-9]* 2>/dev/null | sort -g",
                timeout=30,
            )
            for line in ls_result.stdout.strip().split("\n"):
                t_str = line.strip()
                if not t_str:
                    continue
                try:
                    t_val = float(t_str)
                    if t_val > 0.001:
                        all_times.append((t_str, t_val))
                except ValueError:
                    continue

        if not all_times:
            logger.warning("No non-zero time directories found for animation")
            return []

        # Sort by time value
        all_times.sort(key=lambda item: item[1])
        logger.info(
            "Found %d time steps for animation (range: %s..%s)",
            len(all_times), all_times[0][0], all_times[-1][0],
        )

        # Select up to max_steps evenly spaced time steps
        if len(all_times) <= max_steps:
            selected = all_times
        else:
            indices = np.linspace(0, len(all_times) - 1, max_steps, dtype=int)
            selected = [all_times[i] for i in indices]

        logger.info("Selected %d time steps for animation", len(selected))

        # Step 2: Run postProcess for vorticity at all times
        pp_result = self.executor.run_remote_command(
            f"cd {case_path} && postProcess -func 'vorticity' 2>&1 | tail -5",
            timeout=600,
        )
        if pp_result.returncode != 0:
            logger.warning(
                "postProcess vorticity (all times) failed: %s",
                (pp_result.stderr or "")[-300:],
            )

        # Step 3: Export ALL VTK files at once, then match by sorted order
        # foamToVTK names files as {caseName}_{timestepIndex}.vtk
        # We export all at once and match by order to time directories
        vtk_dir = f"{case_path}/VTK"
        export_cmd = (
            f"cd {case_path} && rm -rf VTK && foamToVTK -ascii "
            f"-fields '(U p vorticity)' 2>&1 | tail -5"
        )
        export_result = self.executor.run_remote_command(export_cmd, timeout=600)
        if export_result.returncode != 0:
            logger.warning(
                "foamToVTK export all failed: %s",
                (export_result.stderr or "")[-300:],
            )

        # List all .vtk files and sort by numeric suffix (timestep index)
        file_list = self.executor.list_remote_directory(vtk_dir)
        vtk_files = []
        for f in file_list:
            if f.endswith(".vtk"):
                # Extract numeric suffix: job_xxx_1800.vtk -> 1800
                base = f.rsplit(".vtk", 1)[0]
                parts = base.rsplit("_", 1)
                if len(parts) == 2:
                    try:
                        idx = int(parts[1])
                        vtk_files.append((idx, f))
                    except ValueError:
                        pass
        vtk_files.sort(key=lambda x: x[0])
        logger.info("Found %d VTK files (sorted by timestep index)", len(vtk_files))

        if not vtk_files:
            logger.warning("No VTK files found after export")
            return []

        # Match VTK files to time values by order
        # VTK files are sorted by index, times are sorted by value
        # They should correspond 1:1 (excluding t=0 which has no VTK in most cases)
        # Build a mapping from timestep index to time value
        # Get all times including t=0 for index calculation
        all_times_with_zero = [(t_str, t_val) for t_str, t_val in all_times]
        # Read deltaT from controlDict to compute time from index
        deltaT_result = self.executor.run_remote_command(
            f"cd {case_path} && grep deltaT system/controlDict | head -1",
            timeout=15,
        )
        delta_t = None
        for line in deltaT_result.stdout.strip().split("\n"):
            parts = line.split()
            for p in parts:
                p = p.strip().rstrip(";")
                try:
                    delta_t = float(p)
                    break
                except ValueError:
                    continue
            if delta_t is not None:
                break
        if delta_t is None or delta_t <= 0:
            logger.warning("Could not determine deltaT for VTK time mapping")
            delta_t = 0.001  # fallback assumption

        logger.info("deltaT=%.6g, using to map VTK indices to time values", delta_t)

        # Step 4: Select evenly spaced VTK files and download
        temp_dir = tempfile.mkdtemp(prefix="foam_anim_")
        multi_data: list[dict] = []

        if len(vtk_files) <= max_steps:
            selected_vtk = vtk_files
        else:
            indices = np.linspace(0, len(vtk_files) - 1, max_steps, dtype=int)
            selected_vtk = [vtk_files[i] for i in indices]

        logger.info("Selected %d VTK files for download", len(selected_vtk))

        for idx, vtk_file in selected_vtk:
            # Compute time from timestep index
            t_val = idx * delta_t
            logger.info("Processing VTK %s (index=%d, t=%.4f)", vtk_file, idx, t_val)

            remote_vtk = f"{vtk_dir}/{vtk_file}"
            local_vtk = os.path.join(temp_dir, f"field_{idx}.vtk")

            if not self.executor._scp_download(remote_vtk, local_vtk):
                logger.warning("Failed to download VTK: %s", remote_vtk)
                continue

            field_data = self._parse_vtk_ascii(local_vtk, t_val)
            if field_data is not None:
                multi_data.append(field_data)
                logger.info("Parsed VTK for t=%.4f (%d points)", t_val, len(field_data.get("x", [])))

        # Clean up
        try:
            import shutil
            shutil.rmtree(temp_dir)
        except Exception:
            pass

        logger.info("Successfully parsed %d time steps for animation", len(multi_data))
        return multi_data

    def _generate_animations(
        self,
        case_path: str,
        job_id: str,
        spec: Any,
        results_dir: Path,
        geom: dict,
    ) -> list[str]:
        """Generate field evolution animations from multi-time-step data.

        Produces GIF animations for velocity magnitude and vorticity fields.
        Returns paths to generated animation files.
        """
        multi_data = self._fetch_field_data_multi_time(case_path, geom)

        if len(multi_data) < 2:
            logger.warning("Insufficient time steps (%d) for animation — need ≥2", len(multi_data))
            return []

        logger.info("Generating animations from %d time steps", len(multi_data))
        spec_version = getattr(spec, "spec_version", 1)
        anim_paths: list[str] = []

        # Velocity magnitude animation
        vmag_path = self._animate_field(
            multi_data, geom, job_id, spec_version,
            field_extractor=lambda d: (
                np.sqrt(d["ux"] ** 2 + d["uy"] ** 2)
                if d.get("ux") is not None and d.get("uy") is not None
                else None
            ),
            title="Velocity Magnitude Evolution",
            zlabel="|U| [m/s]",
            output_path=results_dir / "velocity_magnitude_animation.gif",
            cmap="viridis",
            symmetric=False,
        )
        if vmag_path:
            anim_paths.append(vmag_path)

        # Vorticity animation
        vort_path = self._animate_field(
            multi_data, geom, job_id, spec_version,
            field_extractor=lambda d: d.get("vorticity"),
            title="Vorticity Field Evolution",
            zlabel=r"ωz [1/s]",
            output_path=results_dir / "vorticity_animation.gif",
            cmap="RdBu_r",
            symmetric=True,
        )
        if vort_path:
            anim_paths.append(vort_path)

        # Streamlines animation (velocity field evolution)
        stream_path = self._animate_streamlines(
            multi_data, geom, job_id, spec_version,
            output_path=results_dir / "streamlines_animation.gif",
        )
        if stream_path:
            anim_paths.append(stream_path)

        logger.info("Generated %d animations", len(anim_paths))
        return anim_paths

    def _animate_field(
        self,
        multi_data: list[dict],
        geom: dict,
        job_id: str,
        spec_version: int,
        field_extractor,
        title: str,
        zlabel: str,
        output_path: Path,
        cmap: str = "viridis",
        symmetric: bool = False,
    ) -> str | None:
        """Create a GIF animation of a scalar field evolving over time.

        Uses matplotlib ``FuncAnimation`` with ``PillowWriter`` (no ffmpeg
        required). Each frame is a filled tricontour plot at one time step.
        """
        try:
            # Extract field values for all time steps
            frames: list[tuple[float, np.ndarray, np.ndarray, np.ndarray]] = []
            all_values: list[np.ndarray] = []

            for fd in multi_data:
                values = field_extractor(fd)
                if values is None:
                    continue
                x = fd.get("x")
                y = fd.get("y")
                t = fd.get("time", 0.0)
                if x is None or y is None:
                    continue
                frames.append((t, x, y, values))
                all_values.append(values)

            if len(frames) < 2:
                logger.warning("Not enough valid frames for %s animation", title)
                return None

            # Determine color scale across all frames for consistency
            if symmetric:
                vmax = max(np.nanmax(np.abs(v)) for v in all_values)
                vmin = -vmax
                levels = np.linspace(vmin, vmax, 41)
            else:
                vmin = min(np.nanmin(v) for v in all_values)
                vmax = max(np.nanmax(v) for v in all_values)
                levels = np.linspace(vmin, vmax, 41)

            # Set up the figure
            fig, ax = plt.subplots(figsize=(12, 5))

            def update(frame_idx: int):
                ax.clear()
                t, x, y, values = frames[frame_idx]

                if symmetric:
                    tcf = ax.tricontourf(
                        x, y, values, levels=levels, cmap=cmap, extend="both"
                    )
                else:
                    tcf = ax.tricontourf(
                        x, y, values, levels=levels, cmap=cmap
                    )

                # Colorbar (only redraw if first frame or color scale changes)
                if frame_idx == 0:
                    cbar = fig.colorbar(tcf, ax=ax, pad=0.02)
                    cbar.set_label(zlabel, fontsize=11)

                # Cylinder outline
                self._draw_cylinder(ax, geom)

                ax.set_xlabel("x [m]", fontsize=11)
                ax.set_ylabel("y [m]", fontsize=11)
                ax.set_title(f"{title}  (t = {t:.3f} s)", fontsize=13)
                ax.set_aspect("equal", adjustable="box")

                # Metadata
                ax.text(
                    0.98, 0.02,
                    f"Run ID: {job_id}\nSpec v{spec_version}\nFrame {frame_idx + 1}/{len(frames)}",
                    transform=ax.transAxes,
                    fontsize=8,
                    horizontalalignment="right",
                    verticalalignment="bottom",
                    bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
                    zorder=20,
                )

            anim = FuncAnimation(
                fig, update,
                frames=len(frames),
                interval=300,  # 300ms between frames
                blit=False,
            )

            writer = PillowWriter(fps=4)
            anim.save(str(output_path), writer=writer)
            plt.close(fig)
            logger.info("Saved animation: %s", output_path)
            return str(output_path)

        except Exception as exc:
            logger.error("Failed to create animation %s: %s", title, exc)
            plt.close("all")
            return None

    def _animate_streamlines(
        self,
        multi_data: list[dict],
        geom: dict,
        job_id: str,
        spec_version: int,
        output_path: Path,
    ) -> str | None:
        """Create a GIF animation of streamlines evolving over time."""
        try:
            frames: list[tuple[float, np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []

            for fd in multi_data:
                ux = fd.get("ux")
                uy = fd.get("uy")
                x = fd.get("x")
                y = fd.get("y")
                t = fd.get("time", 0.0)
                if any(v is None for v in (ux, uy, x, y)):
                    continue
                frames.append((t, x, y, ux, uy))

            if len(frames) < 2:
                logger.warning("Not enough valid frames for streamline animation")
                return None

            # Grid for streamplot (compute once from first frame)
            x0, y0 = frames[0][1], frames[0][2]
            nx, ny = 120, 60
            xi = np.linspace(x0.min(), x0.max(), nx)
            yi = np.linspace(y0.min(), y0.max(), ny)
            xi_grid, yi_grid = np.meshgrid(xi, yi)

            # Precompute gridded data for all frames
            gridded: list[tuple[float, np.ndarray, np.ndarray]] = []
            for t, x, y, ux, uy in frames:
                ux_grid = self._griddata(x, y, ux, xi_grid, yi_grid)
                uy_grid = self._griddata(x, y, uy, xi_grid, yi_grid)
                gridded.append((t, ux_grid, uy_grid))

            # Determine global speed range for consistent coloring
            max_speed = 0.0
            for _, ux_g, uy_g in gridded:
                speed = np.sqrt(ux_g ** 2 + uy_g ** 2)
                max_speed = max(max_speed, np.nanmax(speed))

            fig, ax = plt.subplots(figsize=(12, 5))

            def update(frame_idx: int):
                ax.clear()
                t, ux_grid, uy_grid = gridded[frame_idx]
                speed = np.sqrt(ux_grid ** 2 + uy_grid ** 2)

                ax.contourf(xi, yi, speed, levels=20, cmap="YlGnBu", alpha=0.5,
                            vmin=0, vmax=max_speed)

                lw = 2 * speed / (max_speed + 1e-30)
                ax.streamplot(
                    xi, yi, ux_grid, uy_grid,
                    color=speed,
                    linewidth=lw,
                    cmap="cool",
                    density=1.5,
                    arrowstyle="->",
                    arrowsize=1.0,
                )

                self._draw_cylinder(ax, geom)

                ax.set_xlabel("x [m]", fontsize=11)
                ax.set_ylabel("y [m]", fontsize=11)
                ax.set_title(f"Streamlines Evolution  (t = {t:.3f} s)", fontsize=13)
                ax.set_aspect("equal", adjustable="box")
                ax.set_xlim(x0.min(), x0.max())
                ax.set_ylim(y0.min(), y0.max())

                ax.text(
                    0.98, 0.02,
                    f"Run ID: {job_id}\nSpec v{spec_version}\nFrame {frame_idx + 1}/{len(gridded)}",
                    transform=ax.transAxes,
                    fontsize=8,
                    horizontalalignment="right",
                    verticalalignment="bottom",
                    bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
                    zorder=20,
                )

            anim = FuncAnimation(
                fig, update,
                frames=len(gridded),
                interval=300,
                blit=False,
            )

            writer = PillowWriter(fps=4)
            anim.save(str(output_path), writer=writer)
            plt.close(fig)
            logger.info("Saved animation: %s", output_path)
            return str(output_path)

        except Exception as exc:
            logger.error("Failed to create streamline animation: %s", exc)
            plt.close("all")
            return None

    def _parse_vtk_ascii(self, filepath: str, sim_time: float) -> dict | None:
        """Parse a legacy VTK ASCII file produced by ``foamToVTK``.

        Extracts POINTS (x, y; z is ignored) and CELL_DATA fields for U
        (vector), p (scalar) and vorticity (scalar). Because foamToVTK writes
        cell-centred data, cell centroids are computed from the cell vertex
        connectivity and used as the (x, y) coordinates.

        Returns a dict with keys: x, y, ux, uy, p, vorticity, time (numpy
        arrays for the fields; ``time`` is a float). Returns None on failure.
        """
        try:
            with open(filepath, "r", errors="replace") as f:
                tokens = f.read().split()
        except Exception as exc:
            logger.warning("Failed to read VTK file %s: %s", filepath, exc)
            return None

        if not tokens:
            logger.warning("Empty VTK file: %s", filepath)
            return None

        n = len(tokens)
        i = 0
        points: np.ndarray | None = None
        cells: list[list[int]] | None = None
        cell_data: dict[str, np.ndarray] = {}
        point_data: dict[str, np.ndarray] = {}

        def _is_int(s: str) -> bool:
            try:
                int(s)
                return True
            except (ValueError, TypeError):
                return False

        def _parse_block(
            start_i: int, count: int
        ) -> tuple[int, dict[str, np.ndarray]]:
            """Parse a FIELD / SCALARS / VECTORS block.

            Returns (new_index, {name: ndarray}).
            """
            j = start_i
            out: dict[str, np.ndarray] = {}
            t = tokens[j]
            if t == "FIELD":
                j += 1
                # Skip the field name (e.g., "FieldData", "attributes", etc.)
                if j < n and not _is_int(tokens[j]):
                    j += 1
                nfields = int(tokens[j]); j += 1
                for _ in range(nfields):
                    name = tokens[j]; j += 1
                    ncomp = int(tokens[j]); j += 1
                    ntuples = int(tokens[j]); j += 1
                    j += 1  # datatype token (e.g. 'double', 'float', 'int')
                    total = ncomp * ntuples
                    vals = np.array(tokens[j:j + total], dtype=float)
                    j += total
                    if ncomp > 1:
                        out[name] = vals.reshape(ntuples, ncomp)
                    else:
                        out[name] = vals
            elif t in ("SCALARS", "VECTORS"):
                is_vec = (t == "VECTORS")
                j += 1
                name = tokens[j]; j += 1
                j += 1  # datatype
                ncomp = 3 if is_vec else 1
                if not is_vec and j < n and _is_int(tokens[j]):
                    ncomp = int(tokens[j]); j += 1
                if j < n and tokens[j] == "LOOKUP_TABLE":
                    j += 1
                    if j < n:
                        j += 1  # 'default' or table name
                total = ncomp * count
                vals = np.array(tokens[j:j + total], dtype=float)
                j += total
                if ncomp > 1:
                    out[name] = vals.reshape(count, ncomp)
                else:
                    out[name] = vals
            else:
                j += 1
            return j, out

        while i < n:
            tok = tokens[i]

            if tok == "POINTS":
                npoints = int(tokens[i + 1])
                i += 3  # skip count and datatype
                total = npoints * 3
                points = np.array(tokens[i:i + total], dtype=float).reshape(
                    npoints, 3
                )
                i += total

            elif tok == "CELLS":
                ncells = int(tokens[i + 1])
                i += 3  # skip count and total size
                cells = []
                for _ in range(ncells):
                    nverts = int(tokens[i]); i += 1
                    verts = [int(x) for x in tokens[i:i + nverts]]
                    i += nverts
                    cells.append(verts)

            elif tok == "CELL_TYPES":
                nct = int(tokens[i + 1])
                i += 2
                i += nct  # skip cell type values

            elif tok == "CELL_DATA":
                count = int(tokens[i + 1])
                i += 2
                while i < n and tokens[i] in ("FIELD", "SCALARS", "VECTORS"):
                    i, fld = _parse_block(i, count)
                    cell_data.update(fld)

            elif tok == "POINT_DATA":
                count = int(tokens[i + 1])
                i += 2
                while i < n and tokens[i] in ("FIELD", "SCALARS", "VECTORS"):
                    i, fld = _parse_block(i, count)
                    point_data.update(fld)

            elif tok == "FIELD":
                # Top-level FIELD metadata (nCells / nFaces / nPoints) — discard.
                i, _ = _parse_block(i, 0)

            else:
                i += 1

        if points is None:
            logger.warning("No POINTS section found in VTK file")
            return None

        # Choose coordinates & data source: prefer cell-centred data.
        if cells is not None and len(cells) > 0 and cell_data:
            ncells = len(cells)
            nverts0 = len(cells[0]) if cells else 0
            if nverts0 > 0 and all(len(c) == nverts0 for c in cells):
                idx = np.array(cells, dtype=int)
                coords = points[idx].mean(axis=1)
            else:
                coords = np.zeros((ncells, 3), dtype=float)
                for c, verts in enumerate(cells):
                    coords[c] = points[verts].mean(axis=0)
            data_source = cell_data
        else:
            coords = points
            data_source = point_data

        x = np.asarray(coords[:, 0])
        y = np.asarray(coords[:, 1])

        result: dict[str, Any] = {
            "x": x,
            "y": y,
            "time": sim_time,
        }

        # Velocity vector -> ux, uy (ignore z component).
        u_arr = data_source.get("U")
        if u_arr is not None:
            u_arr = np.asarray(u_arr)
            if u_arr.ndim == 2 and u_arr.shape[1] >= 2:
                result["ux"] = u_arr[:, 0]
                result["uy"] = u_arr[:, 1]
        # Component-wise fallback (e.g. components(U) export).
        if "ux" not in result and "Ux" in data_source:
            result["ux"] = np.asarray(data_source["Ux"]).reshape(-1)
        if "uy" not in result and "Uy" in data_source:
            result["uy"] = np.asarray(data_source["Uy"]).reshape(-1)

        if "p" in data_source:
            result["p"] = np.asarray(data_source["p"]).reshape(-1)
        if "vorticity" in data_source:
            vort = np.asarray(data_source["vorticity"])
            if vort.ndim == 2 and vort.shape[1] >= 3:
                # Vector vorticity (vx, vy, vz) — extract z-component for 2D
                result["vorticity"] = vort[:, 2]
            elif vort.ndim == 2 and vort.shape[1] == 1:
                result["vorticity"] = vort[:, 0]
            else:
                result["vorticity"] = vort.reshape(-1)

        if not any(k in result for k in ("ux", "uy", "p", "vorticity")):
            logger.warning("VTK parse: no field data extracted from %s", filepath)
            return None

        return result

    def _fetch_field_data_fallback(
        self, case_path: str, latest_time: str, geom: dict
    ) -> dict | None:
        """Fallback: use postProcess with surfaces function object."""
        # Try using postProcess -func 'surfaces' with a pre-configured dict
        # If that also fails, return None
        logger.warning("Field data fallback also unavailable")
        return None

    def _build_sample_dict(self, z_plane: float) -> str:
        """Build an OpenFOAM sampleDict for cutting-plane sampling."""
        return f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  13                                    |
|   \\\\  /    A nd           | Web:      www.openfoam.org                      |
|    \\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      sampleDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

interpolationScheme cellPoint;

surfaceFormat raw;

surfaces
(
    zPlane
    {{
        type        cuttingPlane;
        planeType   pointNormal;
        point       (0 0 {z_plane:.6g});
        normal      (0 0 1);
        interpolate true;
        triangulate false;
    }}
);

fields (Ux Uy p vorticity);

// ************************************************************************* //
"""

    def _parse_raw_surface(self, filepath: str) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Parse an OpenFOAM raw surface file.

        Returns (x, y, values) arrays, or None if parsing fails.
        Handles both scalar and vector fields — for vectors, returns
        the magnitude.
        """
        try:
            data = []
            with open(filepath, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Remove parentheses (vector format)
                    line = line.replace("(", "").replace(")", "")
                    parts = line.split()
                    if len(parts) < 4:
                        continue
                    x = float(parts[0])
                    y = float(parts[1])
                    # z = float(parts[2])  # ignored for 2D
                    vals = [float(v) for v in parts[3:]]
                    if len(vals) == 1:
                        val = vals[0]
                    else:
                        # Vector: take magnitude
                        val = np.sqrt(sum(v ** 2 for v in vals))
                    data.append((x, y, val))

            if not data:
                return None

            arr = np.array(data)
            return arr[:, 0], arr[:, 1], arr[:, 2]
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", filepath, exc)
            return None

    # -- plot helpers -------------------------------------------------------

    def _plot_contour(
        self,
        x: np.ndarray,
        y: np.ndarray,
        values: np.ndarray,
        title: str,
        zlabel: str,
        job_id: str,
        spec_version: int,
        sim_time: float,
        geom: dict,
        output_path: Path,
        cmap: str = "viridis",
        symmetric: bool = False,
    ) -> str | None:
        """Create a filled contour plot from scattered data."""
        try:
            fig, ax = plt.subplots(figsize=(12, 5))

            # Use tricontourf for scattered data
            if symmetric:
                vmax = np.nanmax(np.abs(values))
                vmin = -vmax
                levels = np.linspace(vmin, vmax, 41)
                tcf = ax.tricontourf(x, y, values, levels=levels, cmap=cmap, extend="both")
            else:
                tcf = ax.tricontourf(x, y, values, levels=41, cmap=cmap)

            # Colorbar
            cbar = fig.colorbar(tcf, ax=ax, pad=0.02)
            cbar.set_label(zlabel, fontsize=11)

            # Cylinder outline
            self._draw_cylinder(ax, geom)

            # Axes
            ax.set_xlabel("x [m]", fontsize=11)
            ax.set_ylabel("y [m]", fontsize=11)
            ax.set_title(title, fontsize=13)
            ax.set_aspect("equal", adjustable="box")

            # Metadata annotation
            self._add_metadata(ax, job_id, spec_version, sim_time)

            fig.tight_layout()
            fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
            plt.close(fig)
            logger.info("Saved: %s", output_path)
            return str(output_path)
        except Exception as exc:
            logger.error("Failed to create contour plot %s: %s", title, exc)
            plt.close("all")
            return None

    def _plot_streamlines(
        self,
        x: np.ndarray,
        y: np.ndarray,
        ux: np.ndarray,
        uy: np.ndarray,
        job_id: str,
        spec_version: int,
        sim_time: float,
        geom: dict,
        output_path: Path,
    ) -> str | None:
        """Create a streamline plot.

        Interpolates scattered velocity data onto a regular grid and
        uses matplotlib's ``streamplot``.
        """
        try:
            # Grid the data
            nx = 120
            ny = 60
            xi = np.linspace(x.min(), x.max(), nx)
            yi = np.linspace(y.min(), y.max(), ny)
            xi_grid, yi_grid = np.meshgrid(xi, yi)

            ux_grid = self._griddata(x, y, ux, xi_grid, yi_grid)
            uy_grid = self._griddata(x, y, uy, xi_grid, yi_grid)
            speed = np.sqrt(ux_grid ** 2 + uy_grid ** 2)

            fig, ax = plt.subplots(figsize=(12, 5))

            # Background speed contour (faint)
            ax.contourf(xi, yi, speed, levels=20, cmap="YlGnBu", alpha=0.5)

            # Streamlines
            lw = 2 * speed / (speed.max() + 1e-30)
            ax.streamplot(
                xi, yi, ux_grid, uy_grid,
                color=speed,
                linewidth=lw,
                cmap="cool",
                density=1.5,
                arrowstyle="->",
                arrowsize=1.0,
            )

            # Cylinder outline
            self._draw_cylinder(ax, geom)

            ax.set_xlabel("x [m]", fontsize=11)
            ax.set_ylabel("y [m]", fontsize=11)
            ax.set_title("Streamlines", fontsize=13)
            ax.set_aspect("equal", adjustable="box")
            ax.set_xlim(x.min(), x.max())
            ax.set_ylim(y.min(), y.max())

            self._add_metadata(ax, job_id, spec_version, sim_time)

            fig.tight_layout()
            fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
            plt.close(fig)
            logger.info("Saved: %s", output_path)
            return str(output_path)
        except Exception as exc:
            logger.error("Failed to create streamline plot: %s", exc)
            plt.close("all")
            return None

    def _griddata(
        self,
        x: np.ndarray,
        y: np.ndarray,
        values: np.ndarray,
        xi_grid: np.ndarray,
        yi_grid: np.ndarray,
    ) -> np.ndarray:
        """Interpolate scattered data onto a regular grid.

        Tries scipy.interpolate.griddata first; falls back to a simple
        nearest-neighbour implementation using numpy only.
        """
        try:
            from scipy.interpolate import griddata as scipy_griddata
            zi = scipy_griddata(
                (x, y), values, (xi_grid, yi_grid), method="linear"
            )
            # Fill NaNs with nearest
            if np.any(np.isnan(zi)):
                zi_nn = scipy_griddata(
                    (x, y), values, (xi_grid, yi_grid), method="nearest"
                )
                mask = np.isnan(zi)
                zi[mask] = zi_nn[mask]
            return zi
        except ImportError:
            logger.warning("scipy not available — using numpy nearest-neighbour gridding")
            return self._griddata_numpy(x, y, values, xi_grid, yi_grid)

    def _griddata_numpy(
        self,
        x: np.ndarray,
        y: np.ndarray,
        values: np.ndarray,
        xi_grid: np.ndarray,
        yi_grid: np.ndarray,
    ) -> np.ndarray:
        """Simple nearest-neighbour interpolation using numpy only."""
        ny, nx = xi_grid.shape
        result = np.zeros_like(xi_grid)
        points = np.column_stack([x, y])

        for i in range(ny):
            for j in range(nx):
                dx = points[:, 0] - xi_grid[i, j]
                dy = points[:, 1] - yi_grid[i, j]
                dist2 = dx ** 2 + dy ** 2
                idx = np.argmin(dist2)
                result[i, j] = values[idx]

        return result

    def _plot_force_coefficients(
        self,
        case_path: str,
        job_id: str,
        spec: Any,
        results_dir: Path,
    ) -> str | None:
        """Download and plot force coefficient time series.

        Looks for ``postProcessing/forceCoeffs*/0/forceCoeffs.dat`` on
        the remote host.
        """
        # Try common forceCoeffs directory names
        fc_dirs = ["forceCoeffs1", "forceCoeffs", "forceCoeffs0"]
        remote_fc_file = None

        for fc_dir in fc_dirs:
            candidate = f"{case_path}/postProcessing/{fc_dir}/0/forceCoeffs.dat"
            check = self.executor._ssh_raw(
                f"test -f {candidate} && echo EXISTS || echo MISSING",
                timeout=15,
            )
            if "EXISTS" in check.stdout:
                remote_fc_file = candidate
                break

        if remote_fc_file is None:
            # Broad search
            search = self.executor._ssh_raw(
                f"find {case_path}/postProcessing -name 'forceCoeffs.dat' -type f 2>/dev/null | head -1",
                timeout=30,
            )
            found = search.stdout.strip()
            if found:
                remote_fc_file = found

        if remote_fc_file is None:
            logger.info("No forceCoeffs.dat found — skipping force plot")
            return None

        # Download
        local_fc = results_dir / "forceCoeffs.dat"
        if not self.executor.download_file(remote_fc_file, str(local_fc)):
            logger.warning("Failed to download forceCoeffs.dat")
            return None

        # Parse
        try:
            data = np.loadtxt(str(local_fc), comments="#")
        except Exception as exc:
            logger.warning("Failed to parse forceCoeffs.dat: %s", exc)
            return None

        if data.ndim == 1:
            data = data.reshape(1, -1)

        time_arr = data[:, 0]
        cd = data[:, 1] if data.shape[1] > 1 else np.zeros_like(time_arr)
        cl = data[:, 2] if data.shape[1] > 2 else np.zeros_like(time_arr)

        spec_version = getattr(spec, "spec_version", 1)

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(time_arr, cd, label="Cd (drag)", color="steelblue", linewidth=1.2)
        ax.plot(time_arr, cl, label="Cl (lift)", color="coral", linewidth=1.2)
        ax.set_xlabel("Time [s]", fontsize=11)
        ax.set_ylabel("Coefficient [-]", fontsize=11)
        ax.set_title("Drag & Lift Coefficient Time Series", fontsize=13)
        ax.legend(fontsize=10)
        ax.grid(True, alpha=0.3)

        # Metadata
        ax.text(
            0.02, 0.98,
            f"Run ID: {job_id}\nSpec v{spec_version}",
            transform=ax.transAxes,
            fontsize=8,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )

        output_path = results_dir / "cd_cl_time_series.png"
        fig.tight_layout()
        fig.savefig(str(output_path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("Saved: %s", output_path)
        return str(output_path)

    def _draw_cylinder(self, ax, geom: dict) -> None:
        """Draw a cylinder outline on the axes if present."""
        if not geom.get("has_cylinder"):
            return
        cx = geom.get("cylinder_center_x")
        cy = geom.get("cylinder_center_y")
        r = geom.get("cylinder_radius")
        if cx is None or cy is None or r is None or r <= 0:
            return
        circle = plt.Circle(
            (cx, cy), r,
            fill=False,
            edgecolor="black",
            linewidth=2,
            zorder=10,
        )
        ax.add_patch(circle)

    def _add_metadata(
        self,
        ax,
        job_id: str,
        spec_version: int,
        sim_time: float,
    ) -> None:
        """Add a metadata annotation to the lower-right of the axes."""
        ax.text(
            0.98, 0.02,
            f"Run ID: {job_id}\n"
            f"Spec v{spec_version}\n"
            f"t = {sim_time:.4f} s",
            transform=ax.transAxes,
            fontsize=8,
            horizontalalignment="right",
            verticalalignment="bottom",
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.85),
            zorder=20,
        )


# ---------------------------------------------------------------------------
# 4. ExecutionOrchestrator
# ---------------------------------------------------------------------------


class ExecutionOrchestrator:
    """Orchestrates the full execution chain.

    Steps:
      1. Adapt spec (SpecAdapter)
      2. Compile (ObstacleFlowCompiler)
      3. Upload to workstation
      4. Run mesh generation + checkMesh
      5. Run smoke test
      6. Run full simulation
      7. Generate plots
      8. Return ExecutionResult
    """

    def __init__(
        self,
        adapter: SpecAdapter | None = None,
        compiler: ObstacleFlowCompiler | None = None,
        executor: WorkstationExecutor | None = None,
        postprocessor: Postprocessor | None = None,
    ) -> None:
        self.adapter = adapter or SpecAdapter()
        self.compiler = compiler or ObstacleFlowCompiler()
        self.executor = executor or WorkstationExecutor()
        self.postprocessor = postprocessor or Postprocessor(executor=self.executor)

    def run(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        job_id: str,
        parallel: bool = False,
        np: int = 4,
        skip_smoke: bool = False,
        stop_after_smoke: bool = False,
    ) -> ExecutionResult:
        """Execute the full chain.

        Parameters
        ----------
        spec : CylinderFlow2DExperimentSpecV1
            A confirmed experiment specification.
        job_id : str
            Unique identifier for this run.
        parallel : bool
            Whether to run the full simulation in parallel.
        np : int
            Number of MPI processes for parallel runs.
        skip_smoke : bool
            If True, skip the smoke test step.
        stop_after_smoke : bool
            If True, stop after smoke test passes and return with
            status="SMOKE_PASSED". Caller must invoke resume_run() to
            start the full simulation.
        """
        t_start = time.time()
        result = ExecutionResult(
            job_id=job_id,
            status="PENDING",
            spec_version=spec.spec_version,
        )

        try:
            # --- Step 1: Adapt spec ---
            logger.info("[%s] Step 1: Adapting spec", job_id)
            adapted = self.adapter.adapt(spec)
            result.adapted_spec = adapted
            logger.info("[%s] Adapted to ObstacleFlowExperimentSpecV1", job_id)

            # --- Step 2: Compile ---
            logger.info("[%s] Step 2: Compiling OpenFOAM case", job_id)
            compiled, manifest = self.compiler.compile(adapted)
            result.compilation_manifest = manifest
            result.archive_sha256 = compiled.archive_sha256
            logger.info(
                "[%s] Compiled: %d files, sha256=%s",
                job_id,
                len(compiled.files),
                compiled.archive_sha256[:20],
            )

            # --- Step 3: Upload ---
            logger.info("[%s] Step 3: Uploading case to workstation", job_id)
            case_path = self.executor.upload_case(job_id, compiled.archive)
            result.remote_case_path = case_path
            logger.info("[%s] Uploaded to %s", job_id, case_path)

            # --- Step 4: Mesh ---
            logger.info("[%s] Step 4: Running mesh generation", job_id)
            mesh_report = self.executor.run_mesh(case_path)
            result.mesh_report = mesh_report

            if mesh_report["returncode"] != 0:
                result.status = "FAILED"
                result.error = "Mesh generation failed"
                result.elapsed_seconds = time.time() - t_start
                logger.error("[%s] Mesh generation failed", job_id)
                return result

            # --- Step 5: Smoke test ---
            if not skip_smoke:
                logger.info("[%s] Step 5: Running smoke test", job_id)
                smoke_report = self.executor.run_smoke_test(case_path)
                result.smoke_test_report = smoke_report

                if smoke_report["status"] == "FAILED":
                    # BLOCK: Do NOT proceed with full run when smoke test fails
                    result.status = "FAILED"
                    result.error = "Smoke test failed — full run blocked to prevent wasted computation"
                    result.warnings.append("Smoke test FAILED — full run BLOCKED (P3 fix)")
                    logger.error("[%s] Smoke test failed — blocking full run", job_id)

                    # Classify the error for potential repair
                    try:
                        from fluid_scientist.repair.error_classifier import OpenFOAMErrorClassifier
                        classifier = OpenFOAMErrorClassifier()
                        smoke_log = smoke_report.get("output_tail", "")
                        errors = classifier.classify(smoke_log, stage="smoke")
                        if errors:
                            primary = classifier.get_primary_error(errors)
                            result.warnings.append(
                                f"Error classified: {primary.category.value} — {primary.error_message}"
                            )
                            result.smoke_test_report["classified_errors"] = [e.to_dict() for e in errors]
                    except Exception as cls_err:
                        logger.warning("[%s] Error classification failed: %s", job_id, cls_err)

                    result.elapsed_seconds = time.time() - t_start
                    return result
                else:
                    logger.info("[%s] Smoke test passed", job_id)
            else:
                logger.info("[%s] Skipping smoke test", job_id)

            # --- Pause point: stop after smoke test if requested ---
            if stop_after_smoke:
                result.status = "SMOKE_PASSED"
                result.elapsed_seconds = time.time() - t_start
                result.remote_case_path = case_path
                logger.info(
                    "[%s] Stopped after smoke test — awaiting user confirmation to run",
                    job_id,
                )
                return result

            # --- Step 6: Full simulation ---
            logger.info("[%s] Step 6: Running full simulation", job_id)
            sim_report = self.executor.run_full(
                case_path, parallel=parallel, np=np
            )
            result.simulation_report = sim_report

            if sim_report["status"] not in ("SUCCESS",):
                result.status = "PARTIAL" if sim_report["final_time"] else "FAILED"
                result.error = f"Simulation status: {sim_report['status']}"
                logger.warning(
                    "[%s] Simulation ended with status: %s",
                    job_id,
                    sim_report["status"],
                )
            else:
                logger.info(
                    "[%s] Simulation completed: final_time=%s",
                    job_id,
                    sim_report["final_time"],
                )

            # --- Step 7: Generate plots ---
            logger.info("[%s] Step 7: Generating plots", job_id)
            try:
                plot_paths = self.postprocessor.generate_plots(
                    case_path, job_id, adapted
                )
                result.plot_paths = plot_paths
                logger.info("[%s] Generated %d plots", job_id, len(plot_paths))
            except Exception as exc:
                logger.error("[%s] Plot generation failed: %s", job_id, exc)
                result.warnings.append(f"Plot generation failed: {exc}")

            # --- Step 8: Finalize ---
            if result.status == "PENDING":
                if result.plot_paths:
                    result.status = "SUCCESS"
                else:
                    result.status = "PARTIAL"

        except Exception as exc:
            result.status = "FAILED"
            result.error = str(exc)
            logger.exception("[%s] Execution failed", job_id)

        result.elapsed_seconds = time.time() - t_start
        logger.info(
            "[%s] Execution complete: status=%s, elapsed=%.1fs, plots=%d",
            job_id,
            result.status,
            result.elapsed_seconds,
            len(result.plot_paths),
        )
        return result

    def resume_run(
        self,
        job_id: str,
        case_path: str,
        spec: CylinderFlow2DExperimentSpecV1,
        parallel: bool = False,
        np: int = 4,
    ) -> ExecutionResult:
        """Resume execution after smoke test — run full simulation + postprocess.

        Called after stop_after_smoke=True returned successfully.
        """
        t_start = time.time()
        result = ExecutionResult(
            job_id=job_id,
            status="PENDING",
            spec_version=spec.spec_version,
            remote_case_path=case_path,
        )

        try:
            adapted = self.adapter.adapt(spec)
            result.adapted_spec = adapted

            # --- Step 6: Full simulation ---
            logger.info("[%s] Resume: Running full simulation", job_id)
            sim_report = self.executor.run_full(
                case_path, parallel=parallel, np=np
            )
            result.simulation_report = sim_report

            if sim_report["status"] not in ("SUCCESS",):
                result.status = "PARTIAL" if sim_report.get("final_time") else "FAILED"
                result.error = f"Simulation status: {sim_report['status']}"
                logger.warning(
                    "[%s] Simulation ended with status: %s",
                    job_id,
                    sim_report["status"],
                )
            else:
                logger.info(
                    "[%s] Simulation completed: final_time=%s",
                    job_id,
                    sim_report.get("final_time"),
                )

            # --- Step 7: Generate plots ---
            logger.info("[%s] Resume: Generating plots", job_id)
            try:
                plot_paths = self.postprocessor.generate_plots(
                    case_path, job_id, adapted
                )
                result.plot_paths = plot_paths
                logger.info("[%s] Generated %d plots", job_id, len(plot_paths))
            except Exception as exc:
                logger.error("[%s] Plot generation failed: %s", job_id, exc)
                result.warnings.append(f"Plot generation failed: {exc}")

            # --- Step 8: Finalize ---
            if result.status == "PENDING":
                if result.plot_paths:
                    result.status = "SUCCESS"
                else:
                    result.status = "PARTIAL"

        except Exception as exc:
            result.status = "FAILED"
            result.error = str(exc)
            logger.exception("[%s] Resume run failed", job_id)

        result.elapsed_seconds = time.time() - t_start
        logger.info(
            "[%s] Resume complete: status=%s, elapsed=%.1fs, plots=%d",
            job_id,
            result.status,
            result.elapsed_seconds,
            len(result.plot_paths),
        )
        return result

__all__ = [
    "ExecutionOrchestrator",
    "ExecutionResult",
    "Postprocessor",
    "SpecAdapter",
    "WorkstationExecutor",
]
