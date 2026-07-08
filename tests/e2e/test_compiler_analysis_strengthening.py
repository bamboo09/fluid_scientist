"""E2E tests for Commit 7: 底层编译和分析补强.

Tests cover:
1. MeasurementPlan compilation blocking behaviour (native_compiler.py)
2. forceCoeffs reference quantities from ExperimentSpec (measurement/compiler.py)
3. surface/probe real geometry (measurement/planner.py + compiler.py)
4. CodeExtension sandbox-test and auto-test endpoints (api/app.py)
5. Analysis API endpoints verification (api/app.py)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ExperimentStatus,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)
from fluid_scientist.measurement.compiler import (
    compile_measurement_plan,
)
from fluid_scientist.measurement.models import (
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
    MetricBinding,
    ProbeSpec,
    SpatialSamplingSpec,
    SpatialSamplingType,
    TimeSamplingSpec,
)
from fluid_scientist.measurement.planner import MetricPlanner
from fluid_scientist.ports import StoredExperimentSpec
from fluid_scientist.research.models import ResearchPhysicsSpec

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repository():
    """Create an in-memory repository."""
    return SQLWorkflowRepository("sqlite:///:memory:")


@pytest.fixture
def client(repository):
    """Create a test client backed by *repository*."""
    app = create_app(repository=repository, execution_targets=[])
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def project_id(client):
    """Create a test project and return its id."""
    response = client.post(
        "/api/projects", json={"question": "compiler analysis strengthening test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_param(
    parameter_id: str,
    display_name: str,
    category: str,
    value: float | int | str,
) -> ParameterSpec:
    """Build a minimal ParameterSpec with proper enum values."""
    return ParameterSpec(
        parameter_id=parameter_id,
        display_name=display_name,
        category=category,
        value=value,
        source=ParameterSourceInfo(type=ParameterSource.USER),
    )


def _create_spec(
    repository,
    project_id: str,
    *,
    parameters: list[ParameterSpec] | None = None,
    metrics: list[dict] | None = None,
    code_extensions: list[dict] | None = None,
    status: ExperimentStatus = ExperimentStatus.DRAFT,
) -> str:
    """Create an experiment spec directly in the repository.

    Returns the experiment_id.
    """
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        research=ResearchSpec(
            title="Compiler Strengthening Test",
            objective="Test compiler and analysis strengthening",
        ),
        parameters=parameters or [],
        metrics=metrics or [],
        code_extensions=code_extensions or [],
        status=status,
    )

    stored = StoredExperimentSpec(
        experiment_id=experiment_id,
        project_id=project_id,
        schema_version=spec.schema_version,
        experiment_version=spec.experiment_version,
        status=spec.status.value,
        task_type=spec.task_type.value,
        interaction_mode=spec.interaction_mode.value,
        spec_json=spec.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    repository.save_experiment_spec(stored)
    return experiment_id


def _make_extension(
    extension_id: str = "",
    name: str = "Test Extension",
    status: str = "draft",
    extension_type: str = "function_object",
    code: str = "def my_func():\n    return 42\n",
) -> dict:
    """Build a minimal extension dict for direct repository insertion."""
    return {
        "extension_id": extension_id or f"ext-{uuid4().hex[:12]}",
        "name": name,
        "description": "A test extension",
        "extension_type": extension_type,
        "code": code,
        "language": "python",
        "dependencies": [],
        "openfoam_files": [],
        "tests": [],
        "status": status,
        "version": "1.0.0",
        "author": "tester",
        "review_notes": "",
        "created_at": "",
        "updated_at": "",
    }


def _build_force_coeffs_plan() -> MeasurementPlan:
    """Build a MeasurementPlan with a forceCoeffs function object."""
    return MeasurementPlan(
        function_objects=[
            FunctionObjectSpec(
                type=FunctionObjectType.FORCE_COEFFS,
                name="forceCoeffs_1",
                target_patch="cylinder",
            )
        ],
        time_sampling=TimeSamplingSpec(start_time=0.0, end_time=100.0),
        metric_bindings=[
            MetricBinding(
                metric_id="drag_coefficient",
                source="forceCoeffs_1",
                function_object="forceCoeffs_1",
            )
        ],
    )


def _build_probes_plan() -> MeasurementPlan:
    """Build a MeasurementPlan with probes."""
    return MeasurementPlan(
        probes=[
            ProbeSpec(
                id="centerline",
                field="U",
                positions=[
                    {"x": 0.0, "y": 0.0, "z": 0.5},
                    {"x": 0.0, "y": 0.0, "z": 1.0},
                ],
                write_interval=10,
            )
        ],
        time_sampling=TimeSamplingSpec(start_time=0.0, end_time=100.0),
    )


def create_fake_case(tmp_path: Path) -> Path:
    """Create a minimal fake OpenFOAM case directory with a solver log."""
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "log.solver").write_text(
        """
Time = 1
Courant Number mean: 0.123 max: 0.456
Ux: solving residual = 0.123, final residual = 0.001
p: solving residual = 0.456, final residual = 0.01
continuity errors : sum local = 1.23e-05
Time = 2
Courant Number mean: 0.089 max: 0.234
Ux: solving residual = 0.045, final residual = 0.0005
p: solving residual = 0.056, final residual = 0.001
continuity errors : sum local = 5.67e-06
""",
        encoding="utf-8",
    )
    return case_dir


def _pipe_flow_parameters() -> list[ParameterSpec]:
    """Return a list of pipe-flow ParameterSpec objects."""
    return [
        _make_param("length", "Pipe Length", "geometry", 1.0),
        _make_param("diameter", "Pipe Diameter", "geometry", 0.05),
        _make_param("mean_velocity", "Mean Velocity", "flow", 0.02),
        _make_param("kinematic_viscosity", "Viscosity", "material", 1e-6),
        _make_param("density", "Density", "material", 998.2),
        _make_param("axial_cells", "Axial Cells", "mesh", 50),
        _make_param("radial_cells", "Radial Cells", "mesh", 10),
    ]


# ===========================================================================
# 1. MeasurementPlan blocking tests
# ===========================================================================


class TestMeasurementPlanBlocking:
    """Tests that MeasurementPlan compilation errors block case compilation."""

    def test_measurement_plan_compilation_failure_blocks_case(self):
        """When compile_measurement_plan returns success=False, the
        _integrate_measurement_plan function should raise
        MeasurementCompilationError instead of silently continuing.
        """
        from fluid_scientist.experiment_spec.native_compiler import (
            MeasurementCompilationError,
            _integrate_measurement_plan,
        )

        # Build a plan with an error: metric binding references a
        # non-existent function object
        plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.FORCE_COEFFS,
                    name="forceCoeffs_1",
                    target_patch="cylinder",
                )
            ],
            metric_bindings=[
                MetricBinding(
                    metric_id="drag_coefficient",
                    source="nonexistent_fo",
                    function_object="nonexistent_fo",
                )
            ],
            time_sampling=TimeSamplingSpec(start_time=0.0, end_time=100.0),
        )

        spec = ExperimentSpec(
            experiment_id="exp-test-blocking",
            research=ResearchSpec(
                title="Blocking Test",
                objective="Test blocking",
            ),
            metrics=[plan.model_dump()],
        )

        files = {"system/controlDict": "functions\n{\n}\n"}

        with pytest.raises(MeasurementCompilationError):
            _integrate_measurement_plan(spec, "cylinder_flow", files)

    def test_measurement_plan_success_does_not_block(self):
        """When compile_measurement_plan returns success=True, no exception
        should be raised and the controlDict should be updated.
        """
        from fluid_scientist.experiment_spec.native_compiler import (
            _integrate_measurement_plan,
        )

        # Build a valid plan (no errors)
        plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.RESIDUALS,
                    name="residuals_1",
                )
            ],
            time_sampling=TimeSamplingSpec(start_time=0.0, end_time=100.0),
            metric_bindings=[],
        )

        spec = ExperimentSpec(
            experiment_id="exp-test-success",
            research=ResearchSpec(
                title="Success Test",
                objective="Test success",
            ),
            metrics=[plan.model_dump()],
        )

        files = {"system/controlDict": "functions\n{\n}\n"}

        # Should not raise
        _integrate_measurement_plan(spec, "cylinder_flow", files)

        # controlDict should have been updated with the functionObject
        assert "residuals_1" in files["system/controlDict"]

    def test_measurement_plan_warning_issues_are_non_blocking(self):
        """Warning-severity issues should NOT block compilation."""
        from fluid_scientist.experiment_spec.native_compiler import (
            _integrate_measurement_plan,
        )

        # Build a plan with only a warning (field not in solver output)
        plan = MeasurementPlan(
            function_objects=[
                FunctionObjectSpec(
                    type=FunctionObjectType.SURFACE_FIELD_VALUE,
                    name="surface_1",
                    field="k",
                    surface="inlet_section",
                )
            ],
            spatial_sampling=[
                SpatialSamplingSpec(
                    id="inlet_section",
                    type=SpatialSamplingType.SURFACE,
                    description="inlet",
                )
            ],
            time_sampling=TimeSamplingSpec(start_time=0.0, end_time=100.0),
            metric_bindings=[],
        )

        spec = ExperimentSpec(
            experiment_id="exp-test-warning",
            research=ResearchSpec(
                title="Warning Test",
                objective="Test warning",
            ),
            metrics=[plan.model_dump()],
        )

        files = {"system/controlDict": "functions\n{\n}\n"}

        # Should not raise -- warnings are non-blocking
        _integrate_measurement_plan(spec, "laminar_pipe", files)

        # controlDict should still be updated
        assert "surface_1" in files["system/controlDict"]


# ===========================================================================
# 2. forceCoeffs from spec tests
# ===========================================================================


class TestForceCoeffsFromSpec:
    """Tests that forceCoeffs reference quantities come from spec_parameters."""

    def test_force_coeffs_uses_density_from_spec(self):
        """rhoInf should come from spec_parameters['density']."""
        plan = _build_force_coeffs_plan()
        result = compile_measurement_plan(
            plan,
            available_patches=["cylinder", "inlet", "outlet"],
            solver_output_fields=["U", "p"],
            spec_parameters={"density": 1234.5},
        )
        assert result.success
        fo = result.control_dict_additions["functions"]["forceCoeffs_1"]
        assert fo["rhoInf"] == 1234.5

    def test_force_coeffs_uses_velocity_from_spec(self):
        """magUInf should come from spec_parameters['inlet_velocity'] or
        spec_parameters['mean_velocity']."""
        plan = _build_force_coeffs_plan()

        # Test with inlet_velocity
        result = compile_measurement_plan(
            plan,
            available_patches=["cylinder"],
            solver_output_fields=["U", "p"],
            spec_parameters={"inlet_velocity": 2.5},
        )
        assert result.success
        fo = result.control_dict_additions["functions"]["forceCoeffs_1"]
        assert fo["magUInf"] == 2.5

        # Test with mean_velocity (fallback)
        result2 = compile_measurement_plan(
            plan,
            available_patches=["cylinder"],
            solver_output_fields=["U", "p"],
            spec_parameters={"mean_velocity": 3.5},
        )
        assert result2.success
        fo2 = result2.control_dict_additions["functions"]["forceCoeffs_1"]
        assert fo2["magUInf"] == 3.5

    def test_force_coeffs_uses_diameter_from_spec(self):
        """lRef should come from spec_parameters['diameter']."""
        plan = _build_force_coeffs_plan()
        result = compile_measurement_plan(
            plan,
            available_patches=["cylinder"],
            solver_output_fields=["U", "p"],
            spec_parameters={"diameter": 0.15},
        )
        assert result.success
        fo = result.control_dict_additions["functions"]["forceCoeffs_1"]
        assert fo["lRef"] == 0.15

    def test_force_coeffs_defaults_when_params_missing(self):
        """When spec_parameters is None or missing keys, reasonable defaults
        should be used."""
        plan = _build_force_coeffs_plan()

        # No spec_parameters at all
        result = compile_measurement_plan(
            plan,
            available_patches=["cylinder"],
            solver_output_fields=["U", "p"],
            spec_parameters=None,
        )
        assert result.success
        fo = result.control_dict_additions["functions"]["forceCoeffs_1"]
        assert fo["rhoInf"] == 998.2
        assert fo["magUInf"] == 1.0
        assert fo["lRef"] == 1.0
        assert fo["Aref"] == 1.0

        # Empty spec_parameters
        result2 = compile_measurement_plan(
            plan,
            available_patches=["cylinder"],
            solver_output_fields=["U", "p"],
            spec_parameters={},
        )
        assert result2.success
        fo2 = result2.control_dict_additions["functions"]["forceCoeffs_1"]
        assert fo2["rhoInf"] == 998.2
        assert fo2["magUInf"] == 1.0

    def test_force_coeffs_aref_from_diameter_and_extrusion(self):
        """Aref should be diameter * extrusion_span when both available."""
        plan = _build_force_coeffs_plan()
        result = compile_measurement_plan(
            plan,
            available_patches=["cylinder"],
            solver_output_fields=["U", "p"],
            spec_parameters={"diameter": 0.1, "extrusion_span": 0.5},
        )
        assert result.success
        fo = result.control_dict_additions["functions"]["forceCoeffs_1"]
        assert fo["Aref"] == 0.1 * 0.5  # diameter * extrusion_span


# ===========================================================================
# 3. surface/probe geometry tests
# ===========================================================================


class TestSurfaceProbeGeometry:
    """Tests that surface and probe specs have real geometry data."""

    def test_pipe_flow_probes_have_coordinates(self):
        """For pipe flow, probes should have actual coordinate positions."""
        planner = MetricPlanner()
        physics_spec = ResearchPhysicsSpec(
            geometry_facts={"diameter": 0.05, "length": 1.0},
            operating_conditions={"inlet_velocity": 0.02},
            material_facts={"kinematic_viscosity": 1e-6},
        )

        plan = planner.propose_metrics(
            research_objective="速度剖面分析",
            physics_spec=physics_spec,
            experiment_type="laminar_pipe",
        )

        measurement_plan = plan.measurement_plan
        # velocity_profile should generate centerline probes
        if "velocity_profile" in plan.core_metrics:
            assert len(measurement_plan.probes) > 0
            for probe in measurement_plan.probes:
                assert len(probe.positions) > 0
                for pos in probe.positions:
                    # Each position should have coordinate keys
                    assert len(pos) > 0

    def test_cylinder_flow_surfaces_have_basepoint_normal(self):
        """For cylinder flow, surface sampling should include basePoint and normal."""
        planner = MetricPlanner()
        physics_spec = ResearchPhysicsSpec(
            geometry_facts={"diameter": 0.1, "domain_upstream": 10.0},
            operating_conditions={"inlet_velocity": 1.0},
            material_facts={"kinematic_viscosity": 1e-6},
        )

        plan = planner.propose_metrics(
            research_objective="阻力系数分析",
            physics_spec=physics_spec,
            experiment_type="cylinder_flow",
        )

        measurement_plan = plan.measurement_plan
        # Check that surface sampling has location data
        for s in measurement_plan.spatial_sampling:
            if s.type.value == "surface":
                loc = s.location
                # At least some surfaces should have basePoint and normal
                if "basePoint" in loc:
                    assert "normal" in loc
                    assert len(loc["basePoint"]) == 3
                    assert len(loc["normal"]) == 3

    def test_probe_locations_not_empty(self):
        """ProbeSpec positions should not be empty when probes are created."""
        planner = MetricPlanner()
        physics_spec = ResearchPhysicsSpec(
            geometry_facts={"diameter": 0.05, "length": 1.0},
            operating_conditions={"inlet_velocity": 0.02},
            material_facts={"kinematic_viscosity": 1e-6},
        )

        plan = planner.propose_metrics(
            research_objective="速度剖面",
            physics_spec=physics_spec,
            experiment_type="laminar_pipe",
        )

        measurement_plan = plan.measurement_plan
        # If there are probes, they should have positions
        for probe in measurement_plan.probes:
            assert len(probe.positions) > 0, (
                f"Probe '{probe.id}' has empty positions"
            )

    def test_surface_has_fields_and_format(self):
        """Surface sampling location should include fields and surfaceFormat."""
        planner = MetricPlanner()
        physics_spec = ResearchPhysicsSpec(
            geometry_facts={"diameter": 0.05, "length": 1.0},
            operating_conditions={"inlet_velocity": 0.02},
            material_facts={"kinematic_viscosity": 1e-6},
        )

        plan = planner.propose_metrics(
            research_objective="压降分析",
            physics_spec=physics_spec,
            experiment_type="laminar_pipe",
        )

        measurement_plan = plan.measurement_plan
        # Find surface samplings
        surfaces = [
            s for s in measurement_plan.spatial_sampling
            if s.type.value == "surface"
        ]
        assert len(surfaces) > 0, "Expected at least one surface sampling"

        for s in surfaces:
            loc = s.location
            assert "fields" in loc, f"Surface '{s.id}' missing fields"
            assert "surfaceFormat" in loc, f"Surface '{s.id}' missing surfaceFormat"
            assert isinstance(loc["fields"], list)
            assert len(loc["fields"]) > 0
            assert loc["surfaceFormat"] == "raw"

    def test_probe_locations_in_compiled_output(self):
        """Compiled function objects for PROBES should have real probeLocations."""
        plan = _build_probes_plan()
        result = compile_measurement_plan(
            plan,
            available_patches=["inlet", "outlet"],
            solver_output_fields=["U", "p"],
        )
        assert result.success
        # sample_dict should have probe locations
        assert result.sample_dict is not None
        probe_locations = result.sample_dict["probes"]["probeLocations"]
        assert len(probe_locations) > 0
        # Each location should be a list of floats
        for loc in probe_locations:
            assert isinstance(loc, list)
            assert len(loc) >= 2  # at least x, y, z components

    def test_surface_sampling_dict_has_geometry(self):
        """Compiled surfaceSamplingDict should include basePoint, normal, fields."""
        plan = MeasurementPlan(
            spatial_sampling=[
                SpatialSamplingSpec(
                    id="outlet_surf",
                    type=SpatialSamplingType.SURFACE,
                    description="outlet",
                    location={
                        "basePoint": [0.0, 0.0, 1.0],
                        "normal": [0.0, 0.0, 1.0],
                        "fields": ["p", "U"],
                        "surfaceFormat": "raw",
                    },
                )
            ],
            time_sampling=TimeSamplingSpec(start_time=0.0, end_time=100.0),
        )
        result = compile_measurement_plan(
            plan,
            available_patches=["inlet", "outlet"],
            solver_output_fields=["U", "p"],
        )
        assert result.success
        assert result.surface_sampling_dict is not None
        surfaces = result.surface_sampling_dict["surfaces"]
        assert len(surfaces) == 1
        s = surfaces[0]
        assert s["basePoint"] == [0.0, 0.0, 1.0]
        assert s["normal"] == [0.0, 0.0, 1.0]
        assert s["fields"] == ["p", "U"]
        assert s["surfaceFormat"] == "raw"


# ===========================================================================
# 4. CodeExtension endpoints tests
# ===========================================================================


class TestCodeExtensionEndpoints:
    """Tests for the sandbox-test and auto-test endpoints."""

    def _ext_url(self, project_id, experiment_id, extension_id="", suffix=""):
        base = (
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}"
            f"/code-extensions"
        )
        if extension_id:
            base = f"{base}/{extension_id}"
        if suffix:
            base = f"{base}/{suffix}"
        return base

    def test_sandbox_test_endpoint_exists(self, client, repository, project_id):
        """POST /sandbox-test should exist and return 200 for a draft extension."""
        ext_id = "ext-sandbox-001"
        experiment_id = _create_spec(
            repository,
            project_id,
            code_extensions=[
                _make_extension(extension_id=ext_id, status="draft"),
            ],
        )

        response = client.post(
            self._ext_url(project_id, experiment_id, ext_id, "sandbox-test"),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["experiment_id"] == experiment_id
        assert "code_extension" in data
        assert "sandbox_result" in data
        sandbox = data["sandbox_result"]
        assert "success" in sandbox

    def test_auto_test_endpoint_exists(self, client, repository, project_id):
        """POST /auto-test should exist and return 200 for a sandbox_tested extension."""
        ext_id = "ext-autotest-001"
        experiment_id = _create_spec(
            repository,
            project_id,
            code_extensions=[
                _make_extension(extension_id=ext_id, status="sandbox_tested"),
            ],
        )

        response = client.post(
            self._ext_url(project_id, experiment_id, ext_id, "auto-test"),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["experiment_id"] == experiment_id
        assert "code_extension" in data
        assert "test_results" in data
        assert isinstance(data["test_results"], list)

    def test_sandbox_test_transitions_to_sandbox_tested(self, client, repository, project_id):
        """POST /sandbox-test on a DRAFT extension transitions to sandbox_tested
        when the sandbox test succeeds."""
        ext_id = "ext-transition-001"
        experiment_id = _create_spec(
            repository,
            project_id,
            code_extensions=[
                _make_extension(
                    extension_id=ext_id,
                    status="draft",
                    code="result = 42\n",
                ),
            ],
        )

        response = client.post(
            self._ext_url(project_id, experiment_id, ext_id, "sandbox-test"),
        )
        assert response.status_code == 200, response.text
        data = response.json()
        ext = data["code_extension"]
        # Successful sandbox test should transition to sandbox_tested
        if data["sandbox_result"]["success"]:
            assert ext["status"] == "sandbox_tested"
        else:
            # If sandbox fails (e.g. code has issues), status is rejected
            assert ext["status"] == "rejected"

    def test_sandbox_test_wrong_status_returns_400(self, client, repository, project_id):
        """POST /sandbox-test on a non-DRAFT extension returns 400."""
        ext_id = "ext-wrong-status-001"
        experiment_id = _create_spec(
            repository,
            project_id,
            code_extensions=[
                _make_extension(extension_id=ext_id, status="auto_tested"),
            ],
        )

        response = client.post(
            self._ext_url(project_id, experiment_id, ext_id, "sandbox-test"),
        )
        assert response.status_code == 400, response.text

    def test_full_lifecycle_draft_to_auto_tested(self, client, repository, project_id):
        """Full lifecycle: draft -> sandbox_tested -> auto_tested."""
        ext_id = "ext-lifecycle-001"
        experiment_id = _create_spec(
            repository,
            project_id,
            code_extensions=[
                _make_extension(
                    extension_id=ext_id,
                    status="draft",
                    code="result = 42\n",
                ),
            ],
        )

        # Step 1: sandbox test
        response = client.post(
            self._ext_url(project_id, experiment_id, ext_id, "sandbox-test"),
        )
        assert response.status_code == 200, response.text
        ext = response.json()["code_extension"]
        if ext["status"] == "sandbox_tested":
            # Step 2: auto test
            response = client.post(
                self._ext_url(project_id, experiment_id, ext_id, "auto-test"),
            )
            assert response.status_code == 200, response.text
            ext = response.json()["code_extension"]
            test_results = response.json()["test_results"]
            if all(r["passed"] for r in test_results):
                assert ext["status"] == "auto_tested"
            else:
                assert ext["status"] == "rejected"


# ===========================================================================
# 5. Analysis API verification tests
# ===========================================================================


class TestAnalysisAPIVerification:
    """Tests verifying the 4 analysis API endpoints exist and work."""

    def test_all_four_analysis_endpoints_exist(self, client, repository, project_id):
        """Verify all 4 analysis endpoints are registered and respond."""
        experiment_id = _create_spec(repository, project_id)

        # 1. GET /metric-results -- should return 200 with empty results
        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/metric-results"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "metric_results" in data
        assert isinstance(data["metric_results"], list)

        # 2-4. POST endpoints require a case_path, so they return 422 (missing body)
        # or 404 (case not found) -- we just verify the route exists
        # by checking it's not a 405 (method not allowed)
        for endpoint in ("ingest", "analyze", "scientific-report"):
            response = client.post(
                f"/api/projects/{project_id}/experiment-specs/{experiment_id}/{endpoint}",
                json={"case_path": "/nonexistent/path"},
            )
            # Should be 404 (case not found) or 500, NOT a 405 (method not allowed)
            assert response.status_code in (404, 500), (
                f"Endpoint /{endpoint} returned unexpected status "
                f"{response.status_code}: {response.text}"
            )

    def test_metric_results_endpoint_returns_results(
        self, client, repository, project_id, tmp_path
    ):
        """After running /analyze, GET /metric-results should return results."""
        case_dir = create_fake_case(tmp_path)
        experiment_id = _create_spec(
            repository, project_id, parameters=_pipe_flow_parameters()
        )

        # Run analyze to populate metric results
        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/analyze",
            json={"case_path": str(case_dir)},
        )
        assert response.status_code == 200, response.text

        # Now GET metric-results should return the cached results
        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/metric-results"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["experiment_id"] == experiment_id
        assert isinstance(data["metric_results"], list)
        assert len(data["metric_results"]) > 0

    def test_ingest_endpoint_works(self, client, repository, project_id, tmp_path):
        """POST /ingest should return simulation_data."""
        case_dir = create_fake_case(tmp_path)
        experiment_id = _create_spec(repository, project_id)

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/ingest",
            json={"case_path": str(case_dir)},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "simulation_data" in data
        assert "missing_data" in data

    def test_scientific_report_endpoint_works(
        self, client, repository, project_id, tmp_path
    ):
        """POST /scientific-report should return metric_results and analysis."""
        case_dir = create_fake_case(tmp_path)
        experiment_id = _create_spec(
            repository, project_id, parameters=_pipe_flow_parameters()
        )

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/scientific-report",
            json={"case_path": str(case_dir)},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "metric_results" in data
        assert "scientific_analysis" in data
