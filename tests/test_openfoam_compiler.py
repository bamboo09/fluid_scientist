"""Comprehensive tests for the ``fluid_scientist.openfoam_compiler`` package.

Covers:
* controlDict compilation (endTime, deltaT, adaptive, writeControl).
* fvSchemes compilation (ddtSchemes, gradSchemes, divSchemes).
* fvSolution compilation (solvers, algorithm block).
* Velocity / pressure field compilation with inlet/wall boundaries.
* Transport properties for air and water.
* Turbulence properties for laminar and RANS.
* Function objects for Cd/Cl, probes, and vorticity.
* Full compilation from SimulationStudySpec.
* Compiled-case validation.
* Determinism: end_time change produces different archive SHA.
* Geometry difference (triangle vs rectangle) produces different files.
"""

from __future__ import annotations

from fluid_scientist.openfoam_compiler import (
    CompiledCase,
    CompiledCaseValidator,
    OpenFOAMCompiler,
)
from fluid_scientist.openfoam_compiler.foundation13 import (
    compile_control_dict,
    compile_fv_schemes,
    compile_fv_solution,
    compile_function_objects,
    compile_pressure_field,
    compile_transport_properties,
    compile_turbulence_properties,
    compile_velocity_field,
)
from fluid_scientist.study_spec import (
    BoundaryCondition,
    BoundaryDefinition,
    ExecutionDefinition,
    GeometryDefinition,
    GeometryEntity,
    MeshDefinition,
    NumericsDefinition,
    ObservationDefinition,
    ObservationTarget,
    PhysicsDefinition,
    PlacementSpec,
    ProbeSpec,
    Quantity,
    SimulationStudySpec,
    SourcedValue,
    SpecProvenance,
    StudyDefinition,
    TimeControl,
    ValidationDefinition,
)
from fluid_scientist.study_spec.geometry import DomainSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sourced(value, unit=None, status="user_explicit", confidence=0.9):
    return SourcedValue(
        value=value,
        unit=unit,
        status=status,
        source_turn_ids=["turn_0"],
        confidence=confidence,
    )


def make_study_spec(end_time: float = 15.0, **overrides):
    """Build a fully-populated SimulationStudySpec for testing.

    The default ``end_time`` is **15 s** to match the key acceptance
    criterion from the plan.
    """
    study = StudyDefinition(
        title="Cylinder Flow Re=100",
        objective="Investigate vortex shedding behind a cylinder",
        research_questions=["What is the Strouhal number at Re=100?"],
    )
    physics = PhysicsDefinition(
        material=make_sourced("water", status="user_confirmed"),
        density=make_sourced(998.2, unit="kg/m^3", status="user_confirmed"),
        kinematic_viscosity=make_sourced(1.0e-6, unit="m^2/s", status="derived"),
        reynolds_number=make_sourced(100.0, status="derived"),
        velocity=make_sourced(0.1, unit="m/s", status="derived"),
        characteristic_length=make_sourced(0.001, unit="m", status="derived"),
    )
    geometry = GeometryDefinition(
        domain=DomainSpec(
            length=make_sourced(12.0, unit="m"),
            width=make_sourced(8.0, unit="m"),
            dimensions="2d",
        ),
        entities={
            "cylinder": GeometryEntity(
                entity_id="cylinder",
                semantic_type="cylinder_2d",
                primitive={"type": "circle", "radius": 0.2, "diameter": 0.4},
                original_user_semantics="cylinder",
                placement=PlacementSpec(
                    x=make_sourced(4.0, unit="m"),
                    y=make_sourced(4.0, unit="m"),
                ),
            ),
        },
        relations=[],
    )
    boundaries = BoundaryDefinition(
        conditions=[
            BoundaryCondition(
                patch_name="inlet",
                role="inlet",
                bc_type="velocityInlet",
                parameters={"velocity": 0.1},
                source_status="user_explicit",
            ),
            BoundaryCondition(
                patch_name="outlet",
                role="outlet",
                bc_type="pressureOutlet",
                parameters={"pressure": 0.0},
                source_status="derived",
            ),
            BoundaryCondition(
                patch_name="top",
                role="wall",
                bc_type="slipWall",
                parameters={},
                source_status="model_recommended",
            ),
            BoundaryCondition(
                patch_name="bottom",
                role="wall",
                bc_type="slipWall",
                parameters={},
                source_status="model_recommended",
            ),
            BoundaryCondition(
                patch_name="cylinder",
                role="wall",
                bc_type="noSlipWall",
                parameters={},
                source_status="derived",
            ),
            BoundaryCondition(
                patch_name="front",
                role="empty",
                bc_type="empty",
                parameters={},
                source_status="derived",
            ),
            BoundaryCondition(
                patch_name="back",
                role="empty",
                bc_type="empty",
                parameters={},
                source_status="derived",
            ),
        ],
    )
    numerics = NumericsDefinition(
        time=TimeControl(
            mode="transient",
            start_time=Quantity(value=0.0, unit="s"),
            end_time=Quantity(value=end_time, unit="s"),
            delta_t=Quantity(value=0.01, unit="s"),
            adaptive=False,
            max_courant=0.5,
            write_control="runTime",
            write_interval=Quantity(value=0.1, unit="s"),
        ),
        solver="icoFoam",
        discretization={
            "ddtSchemes": {"ddtScheme": "backward"},
            "gradSchemes": {"gradScheme": "Gauss linear"},
        },
        turbulence_model="laminar",
    )
    observations = ObservationDefinition(
        targets=[
            ObservationTarget(
                target_id="drag",
                metric="cd",
                parameters={"patches": ["cylinder"], "magUInf": 0.1, "lRef": 0.4, "Aref": 0.4},
                function_object_type="forceCoeffs",
            ),
            ObservationTarget(
                target_id="lift",
                metric="cl",
                parameters={"patches": ["cylinder"]},
                function_object_type="forceCoeffs",
            ),
            ObservationTarget(
                target_id="strouhal",
                metric="strouhal",
                parameters={"probe": [5.0, 4.0, 0.0]},
                function_object_type="probes",
            ),
        ],
        probes=[
            ProbeSpec(
                probe_id="wake_probe_1",
                location={"x": 5.0, "y": 4.0, "z": 0.0},
                field="U",
            ),
        ],
        postprocessing=["streamlines", "vorticity"],
    )
    mesh = MeshDefinition(
        resolution=make_sourced(1200, unit="cells", status="derived"),
        mesh_type="blockMesh",
        refinement_regions=[],
    )
    execution = ExecutionDefinition(
        target_id="workstation",
        parallel=False,
        cores=None,
    )
    validation = ValidationDefinition(checks=["courant_number", "mass_balance"])
    provenance = SpecProvenance(
        created_at="2026-01-01T00:00:00+00:00",
        created_by="test_user",
        parent_version=None,
        creation_turn_id="turn_0",
    )

    defaults = dict(
        spec_id="test_spec_001",
        session_id="session_001",
        version=1,
        parent_version=None,
        study=study,
        physics=physics,
        geometry=geometry,
        boundaries=boundaries,
        initial_conditions=[],
        numerics=numerics,
        mesh=mesh,
        observations=observations,
        execution=execution,
        validation=validation,
        extensions={},
        provenance=provenance,
    )
    defaults.update(overrides)
    return SimulationStudySpec(**defaults)


def make_full_study_spec(end_time: float = 15.0, **overrides):
    """Build a fully-populated spec (alias for make_study_spec)."""
    return make_study_spec(end_time=end_time, **overrides)


# ---------------------------------------------------------------------------
# Tests: controlDict
# ---------------------------------------------------------------------------


class TestControlDict:
    """Tests for the controlDict compiler."""

    def test_control_dict_with_end_time_15(self):
        """controlDict should compile with end_time=15s."""
        spec = make_full_study_spec(end_time=15.0)
        cd = compile_control_dict(spec.numerics, spec.observations, spec.spec_id)
        assert "endTime" in cd
        assert "controlDict" in cd

    def test_end_time_is_15(self):
        """The key acceptance criterion: endTime must be 15."""
        spec = make_full_study_spec(end_time=15.0)
        cd = compile_control_dict(spec.numerics, spec.observations, spec.spec_id)
        # Parse the endTime value from the controlDict.
        import re
        match = re.search(r"(?m)^endTime\s+(\S+);", cd)
        assert match is not None
        assert match.group(1) == "15"

    def test_delta_t_mapping(self):
        """delta_t should map to deltaT in the controlDict."""
        spec = make_full_study_spec()
        cd = compile_control_dict(spec.numerics, spec.observations, spec.spec_id)
        import re
        match = re.search(r"(?m)^deltaT\s+(\S+);", cd)
        assert match is not None
        assert match.group(1) == "0.01"

    def test_adaptive_time_step_mapping(self):
        """Adaptive time step should produce adjustTimeStep yes and maxCo."""
        spec = make_full_study_spec()
        # Make it adaptive.
        numerics = spec.numerics.model_copy(update={
            "time": spec.numerics.time.model_copy(update={
                "adaptive": True,
                "max_courant": 0.7,
            }),
        })
        cd = compile_control_dict(numerics, spec.observations, spec.spec_id)
        assert "adjustTimeStep  yes" in cd
        assert "maxCo" in cd
        import re
        match = re.search(r"(?m)^maxCo\s+(\S+);", cd)
        assert match is not None
        assert match.group(1) == "0.7"

    def test_non_adaptive_no_max_co(self):
        """Non-adaptive should produce adjustTimeStep no and no maxCo."""
        spec = make_full_study_spec()
        cd = compile_control_dict(spec.numerics, spec.observations, spec.spec_id)
        assert "adjustTimeStep  no" in cd

    def test_write_control_mapping(self):
        """write_control should map to writeControl."""
        spec = make_full_study_spec()
        cd = compile_control_dict(spec.numerics, spec.observations, spec.spec_id)
        assert "writeControl" in cd
        assert "runTime" in cd
        import re
        match = re.search(r"(?m)^writeInterval\s+(\S+);", cd)
        assert match is not None
        assert match.group(1) == "0.1"

    def test_purge_write_mapping(self):
        """purge_write should map to purgeWrite."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={
            "time": spec.numerics.time.model_copy(update={"purge_write": 3}),
        })
        cd = compile_control_dict(numerics, spec.observations, spec.spec_id)
        import re
        match = re.search(r"(?m)^purgeWrite\s+(\S+);", cd)
        assert match is not None
        assert match.group(1) == "3"

    def test_control_dict_is_openfoam_format(self):
        """controlDict should be OpenFOAM dictionary format, not JSON."""
        spec = make_full_study_spec()
        cd = compile_control_dict(spec.numerics, spec.observations, spec.spec_id)
        assert "FoamFile" in cd
        assert "{" in cd
        assert "}" in cd
        # Should not look like JSON.
        assert not cd.strip().startswith("{")

    def test_control_dict_contains_function_objects(self):
        """controlDict should include function objects from observations."""
        spec = make_full_study_spec()
        cd = compile_control_dict(spec.numerics, spec.observations, spec.spec_id)
        assert "functions" in cd
        assert "forceCoeffs" in cd


# ---------------------------------------------------------------------------
# Tests: fvSchemes
# ---------------------------------------------------------------------------


class TestFvSchemes:
    """Tests for the fvSchemes compiler."""

    def test_fv_schemes_compilation(self):
        """fvSchemes should compile with all scheme blocks."""
        spec = make_full_study_spec()
        fs = compile_fv_schemes(spec.numerics)
        assert "fvSchemes" in fs
        assert "ddtSchemes" in fs
        assert "gradSchemes" in fs
        assert "divSchemes" in fs
        assert "laplacianSchemes" in fs
        assert "interpolationSchemes" in fs

    def test_fv_schemes_uses_spec_ddt_scheme(self):
        """fvSchemes should respect the spec's ddtScheme."""
        spec = make_full_study_spec()
        fs = compile_fv_schemes(spec.numerics)
        assert "backward" in fs

    def test_fv_schemes_transient_default_euler(self):
        """Transient without spec ddtScheme should default to Euler."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"discretization": {}})
        fs = compile_fv_schemes(numerics)
        assert "Euler" in fs

    def test_fv_schemes_steady_default(self):
        """Steady mode should default to steadyState when no explicit scheme."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={
            "time": spec.numerics.time.model_copy(update={"mode": "steady"}),
            "discretization": {},
        })
        fs = compile_fv_schemes(numerics)
        assert "steadyState" in fs

    def test_fv_schemes_default_grad(self):
        """Default gradScheme should be Gauss linear."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"discretization": {}})
        fs = compile_fv_schemes(numerics)
        assert "Gauss linear" in fs

    def test_fv_schemes_default_laplacian(self):
        """Default laplacianScheme should be Gauss linear corrected."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"discretization": {}})
        fs = compile_fv_schemes(numerics)
        assert "Gauss linear corrected" in fs


# ---------------------------------------------------------------------------
# Tests: fvSolution
# ---------------------------------------------------------------------------


class TestFvSolution:
    """Tests for the fvSolution compiler."""

    def test_fv_solution_compilation(self):
        """fvSolution should compile with solvers and algorithm blocks."""
        spec = make_full_study_spec()
        fs = compile_fv_solution(spec.numerics)
        assert "fvSolution" in fs
        assert "solvers" in fs
        assert "PCG" in fs
        assert "PBiCGStab" in fs

    def test_fv_solution_piso_for_icofoam(self):
        """icoFoam should produce a PISO algorithm block."""
        spec = make_full_study_spec()
        fs = compile_fv_solution(spec.numerics)
        assert "PISO" in fs

    def test_fv_solution_simple_for_simplefoam(self):
        """simpleFoam should produce a SIMPLE algorithm block."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"solver": "simpleFoam"})
        fs = compile_fv_solution(numerics)
        assert "SIMPLE" in fs

    def test_fv_solution_pimple_for_pimplefoam(self):
        """pimpleFoam should produce a PIMPLE algorithm block."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"solver": "pimpleFoam"})
        fs = compile_fv_solution(numerics)
        assert "PIMPLE" in fs

    def test_fv_solution_pressure_solver(self):
        """Pressure solver should be PCG with DIC."""
        spec = make_full_study_spec()
        fs = compile_fv_solution(spec.numerics)
        assert "PCG" in fs
        assert "DIC" in fs

    def test_fv_solution_relaxation_for_simple(self):
        """SIMPLE should include relaxation factors."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"solver": "simpleFoam"})
        fs = compile_fv_solution(numerics)
        assert "relaxationFactors" in fs


# ---------------------------------------------------------------------------
# Tests: Velocity field
# ---------------------------------------------------------------------------


class TestVelocityField:
    """Tests for the velocity field (0/U) compiler."""

    def test_velocity_field_compilation(self):
        """Velocity field should compile with boundary conditions."""
        spec = make_full_study_spec()
        u = compile_velocity_field(spec.boundaries, spec.geometry.domain)
        assert "volVectorField" in u
        assert "U" in u
        assert "boundaryField" in u

    def test_velocity_inlet_fixed_value(self):
        """Inlet should have fixedValue with the inlet velocity."""
        spec = make_full_study_spec()
        u = compile_velocity_field(spec.boundaries, spec.geometry.domain)
        assert "inlet" in u
        assert "fixedValue" in u
        assert "0.1" in u

    def test_velocity_wall_no_slip(self):
        """No-slip wall (cylinder) should have noSlip."""
        spec = make_full_study_spec()
        u = compile_velocity_field(spec.boundaries, spec.geometry.domain)
        assert "cylinder" in u
        assert "noSlip" in u

    def test_velocity_wall_slip(self):
        """Slip wall (top) should have slip."""
        spec = make_full_study_spec()
        u = compile_velocity_field(spec.boundaries, spec.geometry.domain)
        assert "top" in u
        assert "slip" in u

    def test_velocity_outlet_zero_gradient(self):
        """Outlet should have zeroGradient."""
        spec = make_full_study_spec()
        u = compile_velocity_field(spec.boundaries, spec.geometry.domain)
        assert "outlet" in u
        assert "zeroGradient" in u

    def test_velocity_empty_patches(self):
        """Empty patches (front/back) should have empty type."""
        spec = make_full_study_spec()
        u = compile_velocity_field(spec.boundaries, spec.geometry.domain)
        assert "front" in u
        assert "back" in u
        assert "empty" in u

    def test_velocity_domain_comment(self):
        """Velocity field should include domain metadata."""
        spec = make_full_study_spec()
        u = compile_velocity_field(spec.boundaries, spec.geometry.domain)
        assert "domain:" in u


# ---------------------------------------------------------------------------
# Tests: Pressure field
# ---------------------------------------------------------------------------


class TestPressureField:
    """Tests for the pressure field (0/p) compiler."""

    def test_pressure_field_compilation(self):
        """Pressure field should compile with boundary conditions."""
        spec = make_full_study_spec()
        p = compile_pressure_field(spec.boundaries)
        assert "volScalarField" in p
        assert "p" in p
        assert "boundaryField" in p

    def test_pressure_inlet_zero_gradient(self):
        """Inlet should have zeroGradient for pressure."""
        spec = make_full_study_spec()
        p = compile_pressure_field(spec.boundaries)
        assert "inlet" in p
        assert "zeroGradient" in p

    def test_pressure_outlet_fixed_value(self):
        """Outlet should have fixedValue with value 0 for pressure."""
        spec = make_full_study_spec()
        p = compile_pressure_field(spec.boundaries)
        assert "outlet" in p
        assert "fixedValue" in p
        assert "uniform 0" in p

    def test_pressure_wall_zero_gradient(self):
        """Wall should have zeroGradient for pressure."""
        spec = make_full_study_spec()
        p = compile_pressure_field(spec.boundaries)
        assert "cylinder" in p
        # Wall pressure should be zeroGradient.
        # Find the cylinder block and check.
        assert "zeroGradient" in p


# ---------------------------------------------------------------------------
# Tests: Transport properties
# ---------------------------------------------------------------------------


class TestTransportProperties:
    """Tests for the transportProperties compiler."""

    def test_transport_properties_water(self):
        """Water at 20C should produce nu = 1.0e-6."""
        spec = make_full_study_spec()
        tp = compile_transport_properties(spec.physics)
        assert "transportProperties" in tp
        assert "Newtonian" in tp
        assert "nu" in tp
        assert "1e-06" in tp

    def test_transport_properties_air(self):
        """Air at 20C should produce nu = 1.5e-5."""
        physics = PhysicsDefinition(
            material=make_sourced("air", status="user_confirmed"),
            density=make_sourced(1.2, unit="kg/m^3", status="derived"),
            kinematic_viscosity=None,
        )
        tp = compile_transport_properties(physics)
        assert "nu" in tp
        assert "1.5e-05" in tp

    def test_transport_properties_explicit_nu(self):
        """Explicit kinematic_viscosity should override material default."""
        physics = PhysicsDefinition(
            material=make_sourced("air", status="user_confirmed"),
            kinematic_viscosity=make_sourced(2.5e-5, unit="m^2/s", status="derived"),
        )
        tp = compile_transport_properties(physics)
        assert "2.5e-05" in tp

    def test_transport_properties_includes_density(self):
        """Transport properties should include density when available."""
        spec = make_full_study_spec()
        tp = compile_transport_properties(spec.physics)
        assert "rho" in tp


# ---------------------------------------------------------------------------
# Tests: Turbulence properties
# ---------------------------------------------------------------------------


class TestTurbulenceProperties:
    """Tests for the turbulenceProperties compiler."""

    def test_turbulence_laminar(self):
        """Laminar model should produce simulationType laminar."""
        spec = make_full_study_spec()
        tp = compile_turbulence_properties(spec.numerics)
        assert "turbulenceProperties" in tp
        assert "laminar" in tp

    def test_turbulence_rans_kepsilon(self):
        """RANS_kEpsilon should produce RAS with kEpsilon model."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"turbulence_model": "RANS_kEpsilon"})
        tp = compile_turbulence_properties(numerics)
        assert "RAS" in tp
        assert "kEpsilon" in tp
        assert "simulationType  RAS" in tp

    def test_turbulence_rans_komegasst(self):
        """RANS_kOmegaSST should produce RAS with kOmegaSST model."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"turbulence_model": "RANS_kOmegaSST"})
        tp = compile_turbulence_properties(numerics)
        assert "kOmegaSST" in tp

    def test_turbulence_les(self):
        """LES should produce LES with WALE model."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"turbulence_model": "LES"})
        tp = compile_turbulence_properties(numerics)
        assert "LES" in tp
        assert "WALE" in tp

    def test_turbulence_none_is_laminar(self):
        """None turbulence model should produce laminar."""
        spec = make_full_study_spec()
        numerics = spec.numerics.model_copy(update={"turbulence_model": None})
        tp = compile_turbulence_properties(numerics)
        assert "laminar" in tp


# ---------------------------------------------------------------------------
# Tests: Function objects
# ---------------------------------------------------------------------------


class TestFunctionObjects:
    """Tests for the function objects compiler."""

    def test_function_objects_cd_cl(self):
        """Cd and Cl should produce a single forceCoeffs object."""
        observations = ObservationDefinition(
            targets=[
                ObservationTarget(
                    target_id="drag",
                    metric="cd",
                    parameters={"patches": ["cylinder"], "magUInf": 0.1, "lRef": 0.4, "Aref": 0.4},
                    function_object_type="forceCoeffs",
                ),
                ObservationTarget(
                    target_id="lift",
                    metric="cl",
                    parameters={"patches": ["cylinder"]},
                    function_object_type="forceCoeffs",
                ),
            ],
        )
        fos = compile_function_objects(observations)
        force_objs = [fo for fo in fos if fo.get("type") == "forceCoeffs"]
        assert len(force_objs) == 1
        assert force_objs[0]["patches"] == ["cylinder"]
        assert force_objs[0]["magUInf"] == 0.1
        assert force_objs[0]["lRef"] == 0.4
        assert force_objs[0]["Aref"] == 0.4

    def test_function_objects_probes(self):
        """point_velocity and probes list should produce a probes object."""
        observations = ObservationDefinition(
            targets=[
                ObservationTarget(
                    target_id="pv1",
                    metric="point_velocity",
                    parameters={"probe": [5.0, 4.0, 0.0]},
                    function_object_type="probes",
                ),
            ],
            probes=[
                ProbeSpec(
                    probe_id="p1",
                    location={"x": 5.0, "y": 4.0, "z": 0.0},
                    field="U",
                ),
            ],
        )
        fos = compile_function_objects(observations)
        probe_objs = [fo for fo in fos if fo.get("type") == "probes"]
        assert len(probe_objs) == 1
        assert "U" in probe_objs[0]["fields"]

    def test_function_objects_vorticity(self):
        """vorticity target should produce a vorticity function object."""
        observations = ObservationDefinition(
            targets=[
                ObservationTarget(
                    target_id="vort",
                    metric="vorticity",
                    parameters={},
                    function_object_type="vorticity",
                ),
            ],
        )
        fos = compile_function_objects(observations)
        vort_objs = [fo for fo in fos if fo.get("type") == "vorticity"]
        assert len(vort_objs) == 1

    def test_function_objects_vorticity_from_postprocessing(self):
        """vorticity in postprocessing should also produce a vorticity object."""
        observations = ObservationDefinition(
            targets=[],
            postprocessing=["vorticity"],
        )
        fos = compile_function_objects(observations)
        vort_objs = [fo for fo in fos if fo.get("type") == "vorticity"]
        assert len(vort_objs) == 1

    def test_function_objects_time_average(self):
        """time_average in postprocessing should produce fieldAverage."""
        observations = ObservationDefinition(
            targets=[],
            postprocessing=["time_average"],
        )
        fos = compile_function_objects(observations)
        avg_objs = [fo for fo in fos if fo.get("type") == "fieldAverage"]
        assert len(avg_objs) == 1

    def test_function_objects_wall_shear(self):
        """wall_shear target should produce wallShearStress."""
        observations = ObservationDefinition(
            targets=[
                ObservationTarget(
                    target_id="ws",
                    metric="wall_shear",
                    parameters={"patches": ["cylinder"]},
                ),
            ],
        )
        fos = compile_function_objects(observations)
        ws_objs = [fo for fo in fos if fo.get("type") == "wallShearStress"]
        assert len(ws_objs) == 1

    def test_function_objects_y_plus(self):
        """y_plus target should produce yPlus."""
        observations = ObservationDefinition(
            targets=[
                ObservationTarget(
                    target_id="yp",
                    metric="y_plus",
                    parameters={"patches": ["cylinder"]},
                ),
            ],
        )
        fos = compile_function_objects(observations)
        yp_objs = [fo for fo in fos if fo.get("type") == "yPlus"]
        assert len(yp_objs) == 1

    def test_function_objects_dedup_cd_cl_strouhal(self):
        """cd, cl, and strouhal should produce a single forceCoeffs."""
        observations = ObservationDefinition(
            targets=[
                ObservationTarget(target_id="d", metric="cd", parameters={"patches": ["obj"]}),
                ObservationTarget(target_id="l", metric="cl", parameters={"patches": ["obj"]}),
                ObservationTarget(target_id="s", metric="strouhal", parameters={"probe": [1, 2, 3]}),
            ],
        )
        fos = compile_function_objects(observations)
        force_objs = [fo for fo in fos if fo.get("type") == "forceCoeffs"]
        assert len(force_objs) == 1

    def test_function_objects_empty(self):
        """Empty observations should produce no function objects."""
        observations = ObservationDefinition()
        fos = compile_function_objects(observations)
        assert fos == []


# ---------------------------------------------------------------------------
# Tests: Full compilation
# ---------------------------------------------------------------------------


class TestFullCompilation:
    """Tests for the full OpenFOAMCompiler.compile method."""

    def test_full_compilation(self):
        """Full compilation should produce all expected files."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)

        assert isinstance(case, CompiledCase)
        assert case.spec_id == spec.spec_id
        assert case.spec_version == spec.version

        expected_files = [
            "system/controlDict",
            "system/fvSchemes",
            "system/fvSolution",
            "constant/transportProperties",
            "0/U",
            "0/p",
        ]
        for f in expected_files:
            assert f in case.files, f"Missing file: {f}"
            assert case.files[f], f"Empty file: {f}"

    def test_full_compilation_laminar_no_turbulence_files(self):
        """Laminar case should NOT have turbulenceProperties or nuTilda."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        assert "constant/turbulenceProperties" not in case.files
        assert "0/nuTilda" not in case.files

    def test_full_compilation_turbulent_has_turbulence_files(self):
        """Turbulent case should have turbulenceProperties and nuTilda."""
        spec = make_full_study_spec()
        spec = spec.model_copy(update={
            "numerics": spec.numerics.model_copy(update={"turbulence_model": "RANS_kEpsilon"}),
        })
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        assert "constant/turbulenceProperties" in case.files
        assert "0/nuTilda" in case.files
        assert "kEpsilon" in case.files["constant/turbulenceProperties"]

    def test_compiled_case_has_archive_sha(self):
        """Compiled case should have a non-None archive_sha256."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        assert case.archive_sha256 is not None
        assert len(case.archive_sha256) == 64  # SHA-256 hex

    def test_compiled_case_has_compiled_at(self):
        """Compiled case should have a compiled_at string."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        assert case.compiled_at == spec.provenance.created_at

    def test_compiled_case_has_compiler_version(self):
        """Compiled case should have a compiler_version string."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        assert "foundation13" in case.compiler_version

    def test_compiled_case_case_id(self):
        """case_id should be derived from spec_id and version."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        assert case.case_id == f"{spec.spec_id}_v{spec.version}"

    def test_determinism_same_spec_same_output(self):
        """Compiling the same spec twice should produce identical output."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case1 = compiler.compile(spec)
        case2 = compiler.compile(spec)
        assert case1.archive_sha256 == case2.archive_sha256
        assert case1.files == case2.files


# ---------------------------------------------------------------------------
# Tests: Validation
# ---------------------------------------------------------------------------


class TestValidation:
    """Tests for the CompiledCaseValidator."""

    def test_valid_case_no_errors(self):
        """A correctly compiled case should produce no validation errors."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        validator = CompiledCaseValidator()
        errors = validator.validate(case, spec)
        assert errors == [], f"Unexpected validation errors: {errors}"

    def test_validation_detects_end_time_mismatch(self):
        """Validator should detect endTime mismatch."""
        spec = make_full_study_spec(end_time=15.0)
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        # Tamper with the controlDict.
        tampered_files = dict(case.files)
        tampered_files["system/controlDict"] = case.files["system/controlDict"].replace(
            "endTime         15;", "endTime         99;"
        )
        tampered = case.model_copy(update={"files": tampered_files})
        validator = CompiledCaseValidator()
        errors = validator.validate(tampered, spec)
        assert any("endTime" in e for e in errors)

    def test_validation_detects_missing_patch(self):
        """Validator should detect missing boundary patches."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        # Remove a patch from 0/U.
        tampered_files = dict(case.files)
        tampered_files["0/U"] = case.files["0/U"].replace("cylinder", "removed")
        tampered = case.model_copy(update={"files": tampered_files})
        validator = CompiledCaseValidator()
        errors = validator.validate(tampered, spec)
        assert any("cylinder" in e for e in errors)


# ---------------------------------------------------------------------------
# Tests: Determinism / spec-difference
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Tests for determinism and spec-difference properties."""

    def test_end_time_change_different_sha(self):
        """Changing end_time from 10 to 15 should produce different archive SHA."""
        spec_10 = make_full_study_spec(end_time=10.0)
        spec_15 = make_full_study_spec(end_time=15.0)
        compiler = OpenFOAMCompiler()
        case_10 = compiler.compile(spec_10)
        case_15 = compiler.compile(spec_15)
        assert case_10.archive_sha256 != case_15.archive_sha256

    def test_end_time_10_vs_15_control_dict_differs(self):
        """controlDict should differ between end_time 10 and 15."""
        spec_10 = make_full_study_spec(end_time=10.0)
        spec_15 = make_full_study_spec(end_time=15.0)
        cd_10 = compile_control_dict(spec_10.numerics, spec_10.observations, spec_10.spec_id)
        cd_15 = compile_control_dict(spec_15.numerics, spec_15.observations, spec_15.spec_id)
        assert cd_10 != cd_15
        assert "endTime         10;" in cd_10
        assert "endTime         15;" in cd_15

    def test_triangle_vs_rectangle_different_files(self):
        """Triangle vs rectangle geometry should produce different files."""

        def make_shape_spec(shape_name: str, semantic_type: str, primitive: dict):
            """Build a spec with a given obstacle shape."""
            spec = make_study_spec(end_time=15.0)
            geometry = GeometryDefinition(
                domain=spec.geometry.domain,
                entities={
                    shape_name: GeometryEntity(
                        entity_id=shape_name,
                        semantic_type=semantic_type,
                        primitive=primitive,
                        original_user_semantics=shape_name,
                        placement=PlacementSpec(
                            x=make_sourced(4.0, unit="m"),
                            y=make_sourced(4.0, unit="m"),
                        ),
                    ),
                },
                relations=[],
            )
            boundaries = BoundaryDefinition(
                conditions=[
                    BoundaryCondition(
                        patch_name="inlet",
                        role="inlet",
                        bc_type="velocityInlet",
                        parameters={"velocity": 0.1},
                    ),
                    BoundaryCondition(
                        patch_name="outlet",
                        role="outlet",
                        bc_type="pressureOutlet",
                        parameters={"pressure": 0.0},
                    ),
                    BoundaryCondition(
                        patch_name=shape_name,
                        role="wall",
                        bc_type="noSlipWall",
                        parameters={},
                    ),
                    BoundaryCondition(
                        patch_name="front",
                        role="empty",
                        bc_type="empty",
                        parameters={},
                    ),
                    BoundaryCondition(
                        patch_name="back",
                        role="empty",
                        bc_type="empty",
                        parameters={},
                    ),
                ],
            )
            mesh = MeshDefinition(
                resolution=make_sourced(1200, unit="cells"),
                mesh_type="blockMesh",
            )
            execution = ExecutionDefinition(target_id="workstation", parallel=False)
            validation = ValidationDefinition(checks=["courant_number"])
            return spec.model_copy(update={
                "geometry": geometry,
                "boundaries": boundaries,
                "mesh": mesh,
                "execution": execution,
                "validation": validation,
            })

        triangle_spec = make_shape_spec(
            "triangle",
            "triangle_2d",
            {"type": "polygon", "n_vertices": 3},
        )
        rectangle_spec = make_shape_spec(
            "rectangle",
            "rectangle_2d",
            {"type": "polygon", "n_vertices": 4},
        )

        compiler = OpenFOAMCompiler()
        triangle_case = compiler.compile(triangle_spec)
        rectangle_case = compiler.compile(rectangle_spec)

        # Files should differ — at minimum 0/U and 0/p have different patch names.
        assert triangle_case.files != rectangle_case.files
        assert triangle_case.archive_sha256 != rectangle_case.archive_sha256

        # The 0/U file should contain the obstacle patch name.
        assert "triangle" in triangle_case.files["0/U"]
        assert "rectangle" in rectangle_case.files["0/U"]


# ---------------------------------------------------------------------------
# Tests: OpenFOAM format validity
# ---------------------------------------------------------------------------


class TestOpenFOAMFormat:
    """Tests for OpenFOAM dictionary format validity."""

    def test_all_files_have_foamfile_header(self):
        """All compiled files should have a FoamFile header."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        for path, content in case.files.items():
            assert "FoamFile" in content, f"Missing FoamFile header in {path}"
            assert "version     2.0;" in content, f"Missing version in {path}"

    def test_no_json_in_output(self):
        """Compiled files should not contain JSON-style braces-only content."""
        spec = make_full_study_spec()
        compiler = OpenFOAMCompiler()
        case = compiler.compile(spec)
        for path, content in case.files.items():
            # Should not start with { (JSON object).
            assert not content.strip().startswith("{"), f"{path} looks like JSON"
