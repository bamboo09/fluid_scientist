"""End-to-end tests for the ConfigurableObstacleFlow2D experiment family.

Tests the full pipeline from spec creation through compilation and
static validation, covering the test matrix from Section 33 of the plan:

  Case A: Constant-velocity inlet cylinder flow
  Case B: Time-varying inlet cylinder flow
  Case C: Periodic pressure-driven bump-cylinder flow
  Case D: Open-top external flow
  Case E: Moving top wall combined driving
  Case F: Spatially non-uniform inlet
"""

from __future__ import annotations

import io
import tarfile
import pytest

from fluid_scientist.obstacle_flow import (
    BoundaryCombinationValidator,
    BoundarySpec,
    BoundaryTopologyError,
    BoundaryType,
    BumpProfileType,
    BumpSpec,
    CylinderBoundaryType,
    CylinderSpec,
    DomainSpec,
    FlowMode,
    FlowRegime,
    FluidSpec,
    ForcingSpec,
    GeometryFeasibilityError,
    GeometryFeasibilityValidator,
    InletProfileSpec,
    ObservableSpec,
    ObservableType,
    ObstacleFlowCompiler,
    ObstacleFlowExperimentSpecV1,
    ObstacleFlowMeshBackend,
    ObstacleFlowStaticValidator,
    PlotRequest,
    PressureGradientSpec,
    PressureGradientUnit,
    SimulationSpec,
    SpatialType,
    TemporalType,
    TimeMode,
    TurbulenceModel,
    WorkstationObstacleFlowPostprocessor,
)
from fluid_scientist.obstacle_flow.boundary_validator import BoundaryTopologyResolver
from fluid_scientist.obstacle_flow.compiler import CompilationError
from fluid_scientist.obstacle_flow.geometry import (
    BumpProfileGenerator,
    CylinderGeometryBuilder,
)
from fluid_scientist.obstacle_flow.models import (
    BoundaryConfig,
    BodyForceSpec,
    FlowDefinitionSpec,
    InitialVelocitySpec,
)


# ---------------------------------------------------------------------------
# Spec builders for test cases
# ---------------------------------------------------------------------------


def _water_fluid() -> FluidSpec:
    return FluidSpec(
        type="water",
        temperature_c=20,
        density_kg_m3=998.0,
        kinematic_viscosity_m2_s=1.004e-6,
    )


def _domain(length=300, height=25, thickness=1.0) -> DomainSpec:
    return DomainSpec(length_m=length, height_m=height, thickness_m=thickness)


# ---------------------------------------------------------------------------
# Case A: Constant-velocity inlet cylinder flow
# ---------------------------------------------------------------------------


def _case_a_spec() -> ObstacleFlowExperimentSpecV1:
    """Case A: uniform velocity inlet + pressure outlet + cylinder."""
    return ObstacleFlowExperimentSpecV1(
        domain=_domain(length=30, height=10),
        fluid=_water_fluid(),
        cylinders=[
            CylinderSpec(
                id="cylinder_1",
                center_x_m=10.0,
                center_y_m=5.0,
                diameter_m=2.0,
                boundary_type=CylinderBoundaryType.NO_SLIP_WALL,
            )
        ],
        flow_definition=FlowDefinitionSpec(
            mode=FlowMode.INLET_OUTLET,
            initial_velocity=InitialVelocitySpec(type="quiescent"),
        ),
        boundaries=BoundaryConfig(
            left=BoundarySpec(
                type=BoundaryType.VELOCITY_INLET,
                inlet_velocity=1.0,
            ),
            right=BoundarySpec(
                type=BoundaryType.PRESSURE_OUTLET,
                pressure_value=0.0,
            ),
            top=BoundarySpec(type=BoundaryType.SLIP_WALL),
            bottom_flat=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
        ),
        inlet_profile=InletProfileSpec(
            enabled=True,
            temporal_type=TemporalType.CONSTANT,
            spatial_type=SpatialType.UNIFORM,
            parameters={"velocity": 1.0},
        ),
        simulation=SimulationSpec(
            time_mode=TimeMode.TRANSIENT,
            end_time=10.0,
            max_courant_number=0.5,
        ),
        observables=[
            ObservableSpec(
                type=ObservableType.CYLINDER_DRAG,
                cylinder_id="cylinder_1",
            ),
            ObservableSpec(
                type=ObservableType.CYLINDER_LIFT,
                cylinder_id="cylinder_1",
            ),
        ],
        plot_requests=[
            PlotRequest.VELOCITY_MAGNITUDE,
            PlotRequest.STREAMLINES,
            PlotRequest.PRESSURE,
            PlotRequest.VORTICITY,
        ],
    )


class TestCaseA_ConstantInletCylinder:
    """Case A: Constant-velocity inlet cylinder flow."""

    def test_spec_creation(self):
        spec = _case_a_spec()
        assert spec.flow_definition.mode == FlowMode.INLET_OUTLET
        assert spec.has_cylinder
        assert not spec.has_bump
        assert spec.is_transient

    def test_boundary_validation_passes(self):
        spec = _case_a_spec()
        validator = BoundaryCombinationValidator()
        validator.validate(spec)  # Should not raise

    def test_geometry_validation_passes(self):
        spec = _case_a_spec()
        validator = GeometryFeasibilityValidator()
        validator.validate(spec)  # Should not raise

    def test_compilation(self):
        spec = _case_a_spec()
        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(spec)

        assert "system/blockMeshDict" in compiled.files
        assert "system/snappyHexMeshDict" in compiled.files
        assert "constant/triSurface/cylinder.stl" in compiled.files
        assert "0/U" in compiled.files
        assert "0/p" in compiled.files
        assert "constant/physicalProperties" in compiled.files
        assert "constant/momentumTransport" in compiled.files
        assert "system/controlDict" in compiled.files
        assert "system/fvSchemes" in compiled.files
        assert "system/fvSolution" in compiled.files

        assert manifest.flow_mode == "inlet_outlet"
        assert manifest.has_cylinder
        assert not manifest.has_bump

    def test_control_dict_uses_incompressibleFluid(self):
        spec = _case_a_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)

        cd = compiled.files["system/controlDict"]
        assert "solver" in cd
        assert "incompressibleFluid" in cd
        assert "pimpleFoam" not in cd

    def test_velocity_field_has_cylinder_patch(self):
        spec = _case_a_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)

        u = compiled.files["0/U"]
        assert "cylinder" in u
        assert "noSlip" in u

    def test_static_validation_passes(self):
        spec = _case_a_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)

        validator = ObstacleFlowStaticValidator()
        result = validator.validate(spec, compiled.files)
        assert result.passed, f"Static validation failed: {result.errors}"

    def test_archive_is_valid_tar(self):
        spec = _case_a_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)

        assert len(compiled.archive) > 0
        buf = io.BytesIO(compiled.archive)
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            names = tar.getnames()
            assert "system/blockMeshDict" in names
            assert "0/U" in names

    def test_force_coefficients_in_control_dict(self):
        spec = _case_a_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)

        cd = compiled.files["system/controlDict"]
        assert "forceCoeffs" in cd
        assert "cylinder" in cd


# ---------------------------------------------------------------------------
# Case B: Time-varying inlet cylinder flow
# ---------------------------------------------------------------------------


def _case_b_spec() -> ObstacleFlowExperimentSpecV1:
    """Case B: sinusoidal velocity inlet + open outlet + cylinder."""
    return ObstacleFlowExperimentSpecV1(
        domain=_domain(length=30, height=10),
        fluid=_water_fluid(),
        cylinders=[
            CylinderSpec(
                id="cylinder_1",
                center_x_m=10.0,
                center_y_m=5.0,
                diameter_m=2.0,
            )
        ],
        flow_definition=FlowDefinitionSpec(
            mode=FlowMode.INLET_OUTLET,
        ),
        boundaries=BoundaryConfig(
            left=BoundarySpec(
                type=BoundaryType.VELOCITY_INLET,
                inlet_velocity=1.0,
            ),
            right=BoundarySpec(
                type=BoundaryType.OPEN_OUTLET,
            ),
            top=BoundarySpec(type=BoundaryType.SLIP_WALL),
            bottom_flat=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
        ),
        inlet_profile=InletProfileSpec(
            enabled=True,
            temporal_type=TemporalType.SINUSOIDAL,
            spatial_type=SpatialType.UNIFORM,
            parameters={
                "mean_velocity": 1.0,
                "amplitude": 0.3,
                "frequency": 0.5,
                "phase": 0.0,
            },
        ),
        simulation=SimulationSpec(
            time_mode=TimeMode.TRANSIENT,
            end_time=20.0,
        ),
        observables=[
            ObservableSpec(
                type=ObservableType.POINT_VELOCITY,
                point=[15.0, 5.0, 0.0],
                component="Ux",
            ),
        ],
    )


class TestCaseB_SinusoidalInletCylinder:
    """Case B: Time-varying inlet cylinder flow."""

    def test_spec_creation(self):
        spec = _case_b_spec()
        assert spec.flow_definition.mode == FlowMode.INLET_OUTLET
        assert spec.inlet_profile.temporal_type == TemporalType.SINUSOIDAL
        assert spec.is_transient

    def test_compilation(self):
        spec = _case_b_spec()
        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(spec)
        assert manifest.flow_mode == "inlet_outlet"

    def test_probes_in_control_dict(self):
        spec = _case_b_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        cd = compiled.files["system/controlDict"]
        assert "probes" in cd

    def test_static_validation_passes(self):
        spec = _case_b_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        validator = ObstacleFlowStaticValidator()
        result = validator.validate(spec, compiled.files)
        assert result.passed, f"Errors: {result.errors}"


# ---------------------------------------------------------------------------
# Case C: Periodic pressure-driven bump-cylinder flow
# ---------------------------------------------------------------------------


def _case_c_spec() -> ObstacleFlowExperimentSpecV1:
    """Case C: periodic + pressure gradient + shear stress top + bump + cylinder."""
    return ObstacleFlowExperimentSpecV1(
        domain=_domain(length=300, height=25),
        fluid=_water_fluid(),
        geometry_bump=BumpSpec(
            enabled=True,
            profile_type=BumpProfileType.COSINE_BELL,
            center_x_m=150,
            width_m=20,
            height_m=5,
        ),
        cylinders=[
            CylinderSpec(
                id="cylinder_1",
                center_x_m=200,
                center_y_m=15,
                diameter_m=4,
            )
        ],
        flow_definition=FlowDefinitionSpec(
            mode=FlowMode.PERIODIC_FORCED,
            initial_velocity=InitialVelocitySpec(type="quiescent"),
        ),
        boundaries=BoundaryConfig(
            left=BoundarySpec(type=BoundaryType.PERIODIC),
            right=BoundarySpec(type=BoundaryType.PERIODIC),
            top=BoundarySpec(
                type=BoundaryType.SHEAR_STRESS,
                shear_direction=[1.0, 0.0, 0.0],
                shear_magnitude=0.1,
            ),
            bottom_flat=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
        ),
        forcing=ForcingSpec(
            pressure_gradient=PressureGradientSpec(
                enabled=True,
                direction=[1.0, 0.0, 0.0],
                magnitude=4e-4,
                unit=PressureGradientUnit.PA_PER_M,
            ),
        ),
        simulation=SimulationSpec(
            time_mode=TimeMode.TRANSIENT,
            end_time=200.0,
            max_courant_number=0.5,
        ),
        observables=[
            ObservableSpec(
                type=ObservableType.SECTION_MEAN_VELOCITY,
                section_x=180,
                component="Ux",
            ),
        ],
    )


class TestCaseC_PeriodicPressureDriven:
    """Case C: Periodic pressure-driven bump-cylinder flow."""

    def test_spec_creation(self):
        spec = _case_c_spec()
        assert spec.flow_definition.mode == FlowMode.PERIODIC_FORCED
        assert spec.is_periodic
        assert spec.has_bump
        assert spec.has_cylinder

    def test_boundary_validation_passes(self):
        spec = _case_c_spec()
        validator = BoundaryCombinationValidator()
        validator.validate(spec)

    def test_geometry_validation_passes(self):
        spec = _case_c_spec()
        validator = GeometryFeasibilityValidator()
        validator.validate(spec)

    def test_compilation(self):
        spec = _case_c_spec()
        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(spec)

        assert manifest.flow_mode == "periodic_forced"
        assert manifest.has_cylinder
        assert manifest.has_bump

        # Check cyclic patches in blockMeshDict
        bm = compiled.files["system/blockMeshDict"]
        assert "cyclic" in bm
        assert "neighbourPatch" in bm

        # Check fvModels for pressure gradient
        assert "system/fvModels" in compiled.files
        fvm = compiled.files["system/fvModels"]
        assert "bodyForce" in fvm

    def test_block_mesh_has_bump_spline(self):
        spec = _case_c_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        bm = compiled.files["system/blockMeshDict"]
        assert "spline" in bm

    def test_static_validation_passes(self):
        spec = _case_c_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        validator = ObstacleFlowStaticValidator()
        result = validator.validate(spec, compiled.files)
        assert result.passed, f"Errors: {result.errors}"

    def test_equivalent_body_force(self):
        spec = _case_c_spec()
        bf = spec.equivalent_body_force()
        assert bf is not None
        assert bf[0] > 0  # Acceleration in +x direction

    def test_section_sampling_in_control_dict(self):
        spec = _case_c_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        cd = compiled.files["system/controlDict"]
        assert "surfaces" in cd


# ---------------------------------------------------------------------------
# Case D: Open-top external flow
# ---------------------------------------------------------------------------


def _case_d_spec() -> ObstacleFlowExperimentSpecV1:
    """Case D: velocity inlet + open outlet + freestream top + cylinder."""
    return ObstacleFlowExperimentSpecV1(
        domain=_domain(length=40, height=15),
        fluid=_water_fluid(),
        cylinders=[
            CylinderSpec(
                id="cylinder_1",
                center_x_m=15.0,
                center_y_m=5.0,
                diameter_m=2.0,
            )
        ],
        flow_definition=FlowDefinitionSpec(
            mode=FlowMode.OPEN_DOMAIN,
        ),
        boundaries=BoundaryConfig(
            left=BoundarySpec(
                type=BoundaryType.VELOCITY_INLET,
                inlet_velocity=1.0,
            ),
            right=BoundarySpec(
                type=BoundaryType.ADVECTIVE_OUTLET,
            ),
            top=BoundarySpec(
                type=BoundaryType.FREESTREAM,
                freestream_velocity=1.0,
            ),
            bottom_flat=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
        ),
        simulation=SimulationSpec(
            time_mode=TimeMode.TRANSIENT,
            end_time=15.0,
        ),
    )


class TestCaseD_OpenDomain:
    """Case D: Open-top external flow."""

    def test_spec_creation(self):
        spec = _case_d_spec()
        assert spec.flow_definition.mode == FlowMode.OPEN_DOMAIN

    def test_compilation(self):
        spec = _case_d_spec()
        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(spec)
        assert manifest.flow_mode == "open_domain"

    def test_freestream_in_velocity_field(self):
        spec = _case_d_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        u = compiled.files["0/U"]
        assert "freestream" in u.lower()

    def test_static_validation_passes(self):
        spec = _case_d_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        validator = ObstacleFlowStaticValidator()
        result = validator.validate(spec, compiled.files)
        assert result.passed, f"Errors: {result.errors}"


# ---------------------------------------------------------------------------
# Case E: Moving top wall combined driving
# ---------------------------------------------------------------------------


def _case_e_spec() -> ObstacleFlowExperimentSpecV1:
    """Case E: periodic + moving wall top + bump + cylinder."""
    return ObstacleFlowExperimentSpecV1(
        domain=_domain(length=50, height=10),
        fluid=_water_fluid(),
        geometry_bump=BumpSpec(
            enabled=True,
            profile_type=BumpProfileType.HALF_SINE,
            center_x_m=25,
            width_m=10,
            height_m=2,
        ),
        cylinders=[
            CylinderSpec(
                id="cylinder_1",
                center_x_m=35,
                center_y_m=6,
                diameter_m=2,
            )
        ],
        flow_definition=FlowDefinitionSpec(
            mode=FlowMode.COMBINED_DRIVING,
        ),
        boundaries=BoundaryConfig(
            left=BoundarySpec(type=BoundaryType.PERIODIC),
            right=BoundarySpec(type=BoundaryType.PERIODIC),
            top=BoundarySpec(
                type=BoundaryType.MOVING_WALL,
                velocity_vector=[1.0, 0.0, 0.0],
            ),
            bottom_flat=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
        ),
        forcing=ForcingSpec(
            pressure_gradient=PressureGradientSpec(
                enabled=True,
                direction=[1.0, 0.0, 0.0],
                magnitude=1e-4,
                unit=PressureGradientUnit.PA_PER_M,
            ),
        ),
        simulation=SimulationSpec(
            time_mode=TimeMode.TRANSIENT,
            end_time=50.0,
        ),
    )


class TestCaseE_CombinedDriving:
    """Case E: Moving top wall combined driving."""

    def test_spec_creation(self):
        spec = _case_e_spec()
        assert spec.flow_definition.mode == FlowMode.COMBINED_DRIVING

    def test_compilation(self):
        spec = _case_e_spec()
        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(spec)
        assert manifest.flow_mode == "combined_driving"

    def test_moving_wall_in_velocity_field(self):
        spec = _case_e_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        u = compiled.files["0/U"]
        assert "fixedValue" in u  # Moving wall uses fixedValue
        assert "1" in u  # velocity vector value

    def test_static_validation_passes(self):
        spec = _case_e_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        validator = ObstacleFlowStaticValidator()
        result = validator.validate(spec, compiled.files)
        assert result.passed, f"Errors: {result.errors}"


# ---------------------------------------------------------------------------
# Case F: Spatially non-uniform inlet
# ---------------------------------------------------------------------------


def _case_f_spec() -> ObstacleFlowExperimentSpecV1:
    """Case F: parabolic inlet + pressure outlet + bump."""
    return ObstacleFlowExperimentSpecV1(
        domain=_domain(length=30, height=10),
        fluid=_water_fluid(),
        geometry_bump=BumpSpec(
            enabled=True,
            profile_type=BumpProfileType.COSINE_BELL,
            center_x_m=15,
            width_m=8,
            height_m=2,
        ),
        flow_definition=FlowDefinitionSpec(
            mode=FlowMode.INLET_OUTLET,
        ),
        boundaries=BoundaryConfig(
            left=BoundarySpec(
                type=BoundaryType.VELOCITY_INLET,
                inlet_velocity=1.5,
            ),
            right=BoundarySpec(
                type=BoundaryType.PRESSURE_OUTLET,
                pressure_value=0.0,
            ),
            top=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
            bottom_flat=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
        ),
        inlet_profile=InletProfileSpec(
            enabled=True,
            temporal_type=TemporalType.CONSTANT,
            spatial_type=SpatialType.PARABOLIC,
            parameters={"velocity": 1.5, "max_velocity": 1.5},
        ),
        simulation=SimulationSpec(
            time_mode=TimeMode.STEADY,
            end_time=1000,
        ),
    )


class TestCaseF_NonUniformInlet:
    """Case F: Spatially non-uniform inlet."""

    def test_spec_creation(self):
        spec = _case_f_spec()
        assert spec.inlet_profile.spatial_type == SpatialType.PARABOLIC
        assert not spec.is_transient  # steady mode

    def test_compilation(self):
        spec = _case_f_spec()
        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(spec)
        assert manifest.has_bump
        assert not manifest.has_cylinder

    def test_steady_state_fv_schemes(self):
        spec = _case_f_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        schemes = compiled.files["system/fvSchemes"]
        assert "steadyState" in schemes

    def test_static_validation_passes(self):
        spec = _case_f_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        validator = ObstacleFlowStaticValidator()
        result = validator.validate(spec, compiled.files)
        assert result.passed, f"Errors: {result.errors}"


# ---------------------------------------------------------------------------
# Flat bottom, no cylinder (simplest case)
# ---------------------------------------------------------------------------


def _flat_no_cylinder_spec() -> ObstacleFlowExperimentSpecV1:
    """Simplest case: flat bottom, no cylinder, inlet-outlet."""
    return ObstacleFlowExperimentSpecV1(
        domain=_domain(length=20, height=5),
        fluid=_water_fluid(),
        flow_definition=FlowDefinitionSpec(
            mode=FlowMode.INLET_OUTLET,
        ),
        boundaries=BoundaryConfig(
            left=BoundarySpec(
                type=BoundaryType.VELOCITY_INLET,
                inlet_velocity=0.1,
            ),
            right=BoundarySpec(
                type=BoundaryType.PRESSURE_OUTLET,
                pressure_value=0.0,
            ),
            top=BoundarySpec(type=BoundaryType.SLIP_WALL),
            bottom_flat=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
        ),
        simulation=SimulationSpec(
            time_mode=TimeMode.STEADY,
        ),
    )


class TestFlatNoCylinder:
    """Simplest case: flat channel, no obstacles."""

    def test_compilation(self):
        spec = _flat_no_cylinder_spec()
        compiler = ObstacleFlowCompiler()
        compiled, manifest = compiler.compile(spec)
        assert not manifest.has_cylinder
        assert not manifest.has_bump
        # No snappyHexMeshDict for cases without cylinder
        assert "system/snappyHexMeshDict" not in compiled.files
        assert "constant/triSurface/cylinder.stl" not in compiled.files

    def test_static_validation_passes(self):
        spec = _flat_no_cylinder_spec()
        compiler = ObstacleFlowCompiler()
        compiled, _ = compiler.compile(spec)
        validator = ObstacleFlowStaticValidator()
        result = validator.validate(spec, compiled.files)
        assert result.passed, f"Errors: {result.errors}"


# ---------------------------------------------------------------------------
# Boundary validation tests
# ---------------------------------------------------------------------------


class TestBoundaryValidation:
    """Tests for boundary combination validation."""

    def test_periodic_left_non_periodic_right_rejected(self):
        spec = _case_a_spec()
        spec.boundaries.left.type = BoundaryType.PERIODIC
        spec.boundaries.right.type = BoundaryType.PRESSURE_OUTLET
        validator = BoundaryCombinationValidator()
        with pytest.raises(BoundaryTopologyError):
            validator.validate(spec)

    def test_all_static_walls_no_forcing_rejected(self):
        spec = _case_a_spec()
        spec.boundaries.left.type = BoundaryType.NO_SLIP_WALL
        spec.boundaries.right.type = BoundaryType.NO_SLIP_WALL
        spec.boundaries.top.type = BoundaryType.NO_SLIP_WALL
        validator = BoundaryCombinationValidator()
        with pytest.raises(BoundaryTopologyError):
            validator.validate(spec)

    def test_periodic_with_velocity_inlet_rejected(self):
        spec = _case_c_spec()
        spec.boundaries.left.type = BoundaryType.VELOCITY_INLET
        spec.boundaries.right.type = BoundaryType.PERIODIC
        validator = BoundaryCombinationValidator()
        with pytest.raises(BoundaryTopologyError):
            validator.validate(spec)

    def test_topology_resolver_detects_inlet_outlet(self):
        spec = _case_a_spec()
        resolver = BoundaryTopologyResolver()
        mode = resolver.resolve(spec.boundaries, spec.forcing)
        assert mode == FlowMode.INLET_OUTLET

    def test_topology_resolver_detects_periodic(self):
        """Test with a pure periodic+pressure-gradient case (no shear stress)."""
        spec = ObstacleFlowExperimentSpecV1(
            domain=_domain(length=50, height=10),
            fluid=_water_fluid(),
            flow_definition=FlowDefinitionSpec(mode=FlowMode.PERIODIC_FORCED),
            boundaries=BoundaryConfig(
                left=BoundarySpec(type=BoundaryType.PERIODIC),
                right=BoundarySpec(type=BoundaryType.PERIODIC),
                top=BoundarySpec(type=BoundaryType.SLIP_WALL),
                bottom_flat=BoundarySpec(type=BoundaryType.NO_SLIP_WALL),
            ),
            forcing=ForcingSpec(
                pressure_gradient=PressureGradientSpec(
                    enabled=True,
                    direction=[1.0, 0.0, 0.0],
                    magnitude=1e-4,
                    unit=PressureGradientUnit.PA_PER_M,
                ),
            ),
        )
        resolver = BoundaryTopologyResolver()
        mode = resolver.resolve(spec.boundaries, spec.forcing)
        assert mode == FlowMode.PERIODIC_FORCED


# ---------------------------------------------------------------------------
# Geometry validation tests
# ---------------------------------------------------------------------------


class TestGeometryValidation:
    """Tests for geometry feasibility validation."""

    def test_cylinder_outside_domain_rejected(self):
        spec = _case_a_spec()
        spec.cylinders[0].center_x_m = 100.0  # Outside domain
        validator = GeometryFeasibilityValidator()
        with pytest.raises(GeometryFeasibilityError):
            validator.validate(spec)

    def test_cylinder_intersecting_bottom_rejected(self):
        spec = _case_a_spec()
        spec.cylinders[0].center_y_m = 0.5  # Too close to bottom
        spec.cylinders[0].diameter_m = 2.0
        validator = GeometryFeasibilityValidator()
        with pytest.raises(GeometryFeasibilityError):
            validator.validate(spec)

    def test_cylinder_intersecting_bump_rejected(self):
        spec = _case_c_spec()
        # Place cylinder directly above bump center, low enough to overlap
        # Bump: center_x=150, width=20, height=5 -> bump top at y=5
        # Cylinder: center_x=150, center_y=4, diameter=4 -> bottom at y=2
        # This gives both x-overlap and y-overlap (cylinder bottom 2 < bump top 5)
        # But cylinder bottom (2) > min_gap (1.25), so no bottom intersection
        spec.cylinders[0].center_x_m = 150
        spec.cylinders[0].center_y_m = 4
        spec.cylinders[0].diameter_m = 4
        validator = GeometryFeasibilityValidator()
        with pytest.raises(GeometryFeasibilityError, match="CYLINDER_INTERSECTS_BUMP"):
            validator.validate(spec)

    def test_observation_inside_cylinder_rejected(self):
        spec = _case_a_spec()
        spec.observables = [
            ObservableSpec(
                type=ObservableType.POINT_VELOCITY,
                point=[10.0, 5.0, 0.0],  # Same as cylinder center
            )
        ]
        validator = GeometryFeasibilityValidator()
        with pytest.raises(GeometryFeasibilityError, match="OBSERVATION_INSIDE_SOLID"):
            validator.validate(spec)

    def test_cylinder_blockage_exceeded_rejected(self):
        spec = _case_a_spec()
        spec.cylinders[0].diameter_m = 8.0  # 80% blockage
        spec.cylinders[0].center_y_m = 5.0
        validator = GeometryFeasibilityValidator()
        with pytest.raises(GeometryFeasibilityError, match="BLOCKAGE"):
            validator.validate(spec)


# ---------------------------------------------------------------------------
# Mesh generation tests
# ---------------------------------------------------------------------------


class TestMeshGeneration:
    """Tests for mesh generation."""

    def test_flat_mesh_has_single_block(self):
        spec = _flat_no_cylinder_spec()
        backend = ObstacleFlowMeshBackend()
        manifest = backend.generate(spec)
        assert manifest.n_blocks == 1
        assert not manifest.has_cylinder

    def test_bump_mesh_has_three_blocks(self):
        spec = _case_f_spec()
        backend = ObstacleFlowMeshBackend()
        manifest = backend.generate(spec)
        assert manifest.n_blocks == 3
        assert manifest.has_bump

    def test_cylinder_mesh_has_stl(self):
        spec = _case_a_spec()
        backend = ObstacleFlowMeshBackend()
        manifest = backend.generate(spec)
        assert manifest.has_cylinder
        assert manifest.cylinder_stl is not None
        assert "vertex" in manifest.cylinder_stl  # STL format

    def test_periodic_mesh_has_cyclic(self):
        spec = _case_c_spec()
        backend = ObstacleFlowMeshBackend()
        manifest = backend.generate(spec)
        assert "cyclic" in manifest.block_mesh_dict
        assert "neighbourPatch" in manifest.block_mesh_dict

    def test_cylinder_stl_is_valid(self):
        spec = _case_a_spec()
        backend = ObstacleFlowMeshBackend()
        manifest = backend.generate(spec)
        stl = manifest.cylinder_stl
        assert stl.startswith("solid cylinder")
        assert stl.endswith("endsolid cylinder")
        assert "facet normal" in stl
        assert "vertex" in stl


# ---------------------------------------------------------------------------
# Postprocessing tests
# ---------------------------------------------------------------------------


class TestPostprocessing:
    """Tests for postprocessing PlotSpec and result manifest."""

    def test_plot_spec_from_case_a(self):
        spec = _case_a_spec()
        pp = WorkstationObstacleFlowPostprocessor()
        plot_spec = pp.create_plot_spec(spec, "run_001", "/case/path")
        assert len(plot_spec.plots) >= 4
        assert any(p.plot_type == "scalar_contour" for p in plot_spec.plots)
        assert any(p.plot_type == "streamlines" for p in plot_spec.plots)

    def test_plot_spec_from_case_c(self):
        spec = _case_c_spec()
        pp = WorkstationObstacleFlowPostprocessor()
        plot_spec = pp.create_plot_spec(spec, "run_002", "/case/path")
        assert any(m.metric_type == "section_mean_velocity" for m in plot_spec.metrics)

    def test_plot_spec_to_json(self):
        spec = _case_a_spec()
        pp = WorkstationObstacleFlowPostprocessor()
        plot_spec = pp.create_plot_spec(spec, "run_003", "/case/path")
        j = plot_spec.to_json()
        assert "run_id" in j
        assert "plots" in j

    def test_result_manifest(self):
        spec = _case_a_spec()
        pp = WorkstationObstacleFlowPostprocessor()
        plot_spec = pp.create_plot_spec(spec, "run_004", "/case/path")
        manifest = pp.create_result_manifest(plot_spec, simulation_time=10.0)
        assert manifest.status == "SUCCESS"
        assert len(manifest.artifacts) > 0

    def test_postprocess_script_generation(self):
        spec = _case_c_spec()
        pp = WorkstationObstacleFlowPostprocessor()
        plot_spec = pp.create_plot_spec(spec, "run_005", "/case/path")
        script = pp.generate_postprocess_script(plot_spec)
        assert "import" in script
        assert "matplotlib" in script
        assert "def main" in script
        assert plot_spec.run_id in script


# ---------------------------------------------------------------------------
# Reynolds number estimation tests
# ---------------------------------------------------------------------------


class TestReynoldsEstimation:
    """Tests for Reynolds number estimation."""

    def test_inlet_outlet_reynolds(self):
        spec = _case_a_spec()
        re = spec.estimate_reynolds()
        assert re is not None
        assert re > 0

    def test_periodic_reynolds(self):
        spec = _case_c_spec()
        re = spec.estimate_reynolds()
        assert re is not None
        assert re > 0

    def test_auto_transient_with_cylinder(self):
        spec = _case_a_spec()
        spec.simulation.time_mode = TimeMode.AUTO
        assert spec.is_transient  # Cylinder -> transient

    def test_auto_turbulent_high_re(self):
        spec = _case_a_spec()
        spec.simulation.flow_regime = FlowRegime.AUTO
        # With water (nu=1e-6), U=1, D=2, Re ~ 2e6 -> turbulent
        re = spec.estimate_reynolds()
        if re and re > 2000:
            assert spec.is_turbulent


# ---------------------------------------------------------------------------
# Deterministic compilation tests
# ---------------------------------------------------------------------------


class TestDeterministicCompilation:
    """Tests for compilation determinism."""

    def test_same_spec_produces_same_archive(self):
        spec = _case_a_spec()
        compiler = ObstacleFlowCompiler()
        compiled1, _ = compiler.compile(spec)
        compiled2, _ = compiler.compile(spec)
        assert compiled1.archive == compiled2.archive
        assert compiled1.archive_sha256 == compiled2.archive_sha256

    def test_different_spec_produces_different_archive(self):
        spec1 = _case_a_spec()
        spec2 = _case_a_spec()
        spec2.cylinders[0].diameter_m = 3.0  # Different
        compiler = ObstacleFlowCompiler()
        compiled1, _ = compiler.compile(spec1)
        compiled2, _ = compiler.compile(spec2)
        assert compiled1.archive != compiled2.archive
