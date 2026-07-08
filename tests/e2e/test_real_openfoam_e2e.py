"""Comprehensive real OpenFOAM E2E tests — full pipeline verification.

These tests exercise the complete workflow from spec creation through
compilation, archive extraction, result ingestion, metric execution,
and scientific analysis.  They verify that:

1. Each experiment type (pipe, cylinder, cavity) compiles to a real
   OpenFOAM case archive containing all expected files.
2. The compiled spec.json contains experiment_id and parameters (not a
   plan dump).
3. The compiled controlDict contains functionObjects.
4. The compile → ingest → analyze pipeline produces metric results and
   scientific analysis.
5. The native compiler does NOT call any old compile_plan functions.
6. MeasurementPlan functionObjects appear in the compiled controlDict.
"""

from __future__ import annotations

import io
import json
import tarfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.experiment_spec.compilation import compile_spec
from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ExperimentStatus,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)

# ========================================================================== #
# Fixtures
# ========================================================================== #


@pytest.fixture
def client():
    repository = SQLWorkflowRepository("sqlite:///:memory:")
    app = create_app(repository=repository, execution_targets=[])
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def project_id(client):
    response = client.post("/api/projects", json={"question": "real openfoam e2e test"})
    assert response.status_code == 201
    return response.json()["project_id"]


# ========================================================================== #
# Helper functions
# ========================================================================== #


def _make_param(
    parameter_id: str,
    display_name: str,
    category: str,
    value: float | int | str,
    unit: str = "",
    data_type: str = "float",
) -> ParameterSpec:
    """Create a ParameterSpec with sensible defaults."""
    return ParameterSpec(
        parameter_id=parameter_id,
        display_name=display_name,
        category=category,
        value=value,
        unit=unit,
        data_type=data_type,
        source=ParameterSourceInfo(type=ParameterSource.USER),
    )


def _set_parameter(client, project_id, spec_id, parameter_id, value):
    """Set a parameter value on a draft spec via the API."""
    response = client.patch(
        f"/api/projects/{project_id}/experiment-specs/{spec_id}/parameters/{parameter_id}",
        json={"value": value},
    )
    assert response.status_code == 200


def _create_session_and_get_draft(client, project_id, message):
    """Create a research session, follow clarifications, return draft result."""
    response = client.post(
        "/api/research-sessions",
        json={"project_id": project_id, "message": message},
    )
    assert response.status_code == 201
    result = response.json()
    while result["type"] == "clarification_required":
        response = client.post(
            f"/api/research-sessions/{result['session_id']}/turns",
            json={"message": "层流流动，水，管径0.05米，流速0.02米每秒，关注压降"},
        )
        assert response.status_code == 200
        result = response.json()
    return result


def create_confirmed_pipe_spec() -> ExperimentSpec:
    """Create a confirmed pipe flow ExperimentSpec with all required parameters.

    Required parameters: diameter, length, mean_velocity, kinematic_viscosity,
    density, axial_cells, radial_cells.
    """
    params = [
        _make_param("diameter", "Diameter", "geometry", 0.05, "m"),
        _make_param("length", "Length", "geometry", 1.0, "m"),
        _make_param("mean_velocity", "Mean Velocity", "flow", 0.02, "m/s"),
        _make_param("kinematic_viscosity", "Kinematic Viscosity", "fluid", 1e-6, "m2/s"),
        _make_param("density", "Density", "fluid", 998.2, "kg/m3"),
        _make_param("axial_cells", "Axial Cells", "mesh", 80, "", "integer"),
        _make_param("radial_cells", "Radial Cells", "mesh", 10, "", "integer"),
    ]
    return ExperimentSpec(
        experiment_id="exp-pipe-e2e-0001",
        schema_version="1.0.0",
        experiment_version=1,
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(
            title="Pipe Flow E2E Test",
            objective="Verify pipe flow compilation produces real OpenFOAM case files",
        ),
        parameters=params,
    )


def create_confirmed_cylinder_spec() -> ExperimentSpec:
    """Create a confirmed cylinder flow ExperimentSpec.

    Required parameters: diameter, reynolds_number, kinematic_viscosity,
    density, end_time.
    """
    params = [
        _make_param("diameter", "Diameter", "geometry", 0.1, "m"),
        _make_param("reynolds_number", "Reynolds Number", "flow", 100.0, ""),
        _make_param("kinematic_viscosity", "Kinematic Viscosity", "fluid", 1e-6, "m2/s"),
        _make_param("density", "Density", "fluid", 998.2, "kg/m3"),
        _make_param("end_time", "End Time", "temporal", 10.0, "s"),
        # cells_wake and cells_radial are needed for experiment-type detection
        # and mesh generation (optional with defaults in the compiler).
        _make_param("cells_wake", "Wake Cells", "mesh", 120, "", "integer"),
        _make_param("cells_radial", "Radial Cells", "mesh", 40, "", "integer"),
    ]
    return ExperimentSpec(
        experiment_id="exp-cylinder-e2e-0001",
        schema_version="1.0.0",
        experiment_version=1,
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(
            title="Cylinder Flow E2E Test",
            objective="Verify cylinder flow compilation produces real OpenFOAM case files",
        ),
        parameters=params,
    )


def create_confirmed_cavity_spec() -> ExperimentSpec:
    """Create a confirmed cavity flow ExperimentSpec.

    Required parameters: side_length, lid_velocity, kinematic_viscosity,
    density, cells_per_side, end_time.
    """
    params = [
        _make_param("side_length", "Side Length", "geometry", 0.1, "m"),
        _make_param("lid_velocity", "Lid Velocity", "flow", 1.0, "m/s"),
        _make_param("kinematic_viscosity", "Kinematic Viscosity", "fluid", 1e-6, "m2/s"),
        _make_param("density", "Density", "fluid", 998.2, "kg/m3"),
        _make_param("cells_per_side", "Cells Per Side", "mesh", 64, "", "integer"),
        _make_param("end_time", "End Time", "temporal", 10.0, "s"),
    ]
    return ExperimentSpec(
        experiment_id="exp-cavity-e2e-0001",
        schema_version="1.0.0",
        experiment_version=1,
        status=ExperimentStatus.CONFIRMED,
        research=ResearchSpec(
            title="Cavity Flow E2E Test",
            objective="Verify cavity flow compilation produces real OpenFOAM case files",
        ),
        parameters=params,
    )


def extract_archive_files(compiled_case) -> dict[str, str]:
    """Extract files from a CompiledCase archive.

    Returns a mapping of archive member names to file contents (decoded as
    UTF-8).
    """
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(compiled_case.archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                f = tar.extractfile(member)
                if f is not None:
                    files[member.name] = f.read().decode("utf-8")
    return files


def _assert_expected_case_files(files: dict[str, str]) -> None:
    """Assert that the archive contains all expected OpenFOAM case files."""
    expected_files = [
        "0/U",
        "0/p",
        "constant/momentumTransport",
        "constant/physicalProperties",
        "system/blockMeshDict",
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
        "fluidScientist/spec.json",
    ]
    for expected in expected_files:
        assert any(
            name.endswith(expected) or name == expected for name in files
        ), f"Expected file '{expected}' not found in archive. Files: {sorted(files.keys())}"


def _assert_spec_json_content(files: dict[str, str]) -> None:
    """Assert that spec.json contains experiment_id and parameters."""
    spec_file = next(
        (v for k, v in files.items() if k.endswith("spec.json")),
        None,
    )
    assert spec_file is not None, "spec.json not found in archive"
    spec_data = json.loads(spec_file)
    assert "experiment_id" in spec_data, "spec.json must contain experiment_id"
    assert "parameters" in spec_data, "spec.json must contain parameters"
    # Verify it is NOT a plan dump (plan dumps have 'experiment_type' at top
    # level but lack 'source' and 'compilation' keys)
    assert spec_data.get("source") == "native_compiler", (
        "spec.json source must be 'native_compiler'"
    )


def _assert_control_dict_has_functions(files: dict[str, str]) -> None:
    """Assert that the controlDict contains a functions block."""
    cd = next(
        (v for k, v in files.items() if k.endswith("controlDict")),
        None,
    )
    assert cd is not None, "controlDict not found in archive"
    assert "functions" in cd, "controlDict must contain a functions block"


# ========================================================================== #
# Test 1: Full Pipe Flow Pipeline
# ========================================================================== #


def test_full_pipe_flow_pipeline(client, project_id):
    """Complete pipe flow pipeline: research -> spec -> compile -> verify case files."""
    # 1. Create draft via research session
    result = _create_session_and_get_draft(
        client, project_id,
        "研究层流圆管内流动的压降特性，水，管径0.05米，流速0.02米每秒",
    )
    assert result["type"] == "draft_ready"
    spec_id = result["experiment_spec_id"]
    session_id = result["session_id"]

    # 2. Set ALL required parameters for laminar_pipe
    _set_parameter(client, project_id, spec_id, "mean_velocity", 0.02)
    _set_parameter(client, project_id, spec_id, "length", 1.0)
    _set_parameter(client, project_id, spec_id, "kinematic_viscosity", 1e-6)
    _set_parameter(client, project_id, spec_id, "density", 998.2)
    _set_parameter(client, project_id, spec_id, "axial_cells", 80)
    _set_parameter(client, project_id, spec_id, "radial_cells", 10)

    # 3. Fetch the spec and transition to confirmed
    response = client.get(f"/api/research-sessions/{session_id}/experiment-spec")
    assert response.status_code == 200
    spec_json = response.json()

    spec_obj = ExperimentSpec.model_validate_json(json.dumps(spec_json))
    spec_obj = spec_obj.model_copy(update={"status": ExperimentStatus.CONFIRMED})

    # 4. Compile
    compiled_case, manifest = compile_spec(spec_obj)
    assert len(manifest.generated_files) > 0

    # 5. Verify archive contains all expected files
    files = extract_archive_files(compiled_case)
    _assert_expected_case_files(files)

    # 6. Verify spec.json contains experiment_id and parameters (NOT plan dump)
    _assert_spec_json_content(files)

    # 7. Verify controlDict contains functionObjects
    _assert_control_dict_has_functions(files)


# ========================================================================== #
# Test 2: Full Cylinder Flow Pipeline
# ========================================================================== #


def test_full_cylinder_flow_pipeline():
    """Complete cylinder flow pipeline: spec -> compile -> verify case files."""
    # 1. Create a confirmed cylinder spec directly
    spec = create_confirmed_cylinder_spec()

    # 2. Compile
    compiled_case, manifest = compile_spec(spec)
    assert len(manifest.generated_files) > 0

    # 3. Verify archive contains all expected files
    files = extract_archive_files(compiled_case)
    _assert_expected_case_files(files)

    # 4. Verify spec.json contains experiment_id and parameters
    _assert_spec_json_content(files)
    spec_file = next(v for k, v in files.items() if k.endswith("spec.json"))
    spec_data = json.loads(spec_file)
    assert spec_data["experiment_type"] == "cylinder_flow"

    # 5. Verify controlDict contains functionObjects
    _assert_control_dict_has_functions(files)

    # 6. Verify cylinder-specific files (mirrorMeshDict)
    assert any("mirrorMeshDict" in name for name in files), (
        "Cylinder case must contain system/mirrorMeshDict"
    )


# ========================================================================== #
# Test 3: Full Cavity Flow Pipeline
# ========================================================================== #


def test_full_cavity_flow_pipeline():
    """Complete cavity flow pipeline: spec -> compile -> verify case files."""
    # 1. Create a confirmed cavity spec directly
    spec = create_confirmed_cavity_spec()

    # 2. Compile
    compiled_case, manifest = compile_spec(spec)
    assert len(manifest.generated_files) > 0

    # 3. Verify archive contains all expected files
    files = extract_archive_files(compiled_case)
    _assert_expected_case_files(files)

    # 4. Verify spec.json contains experiment_id and parameters
    _assert_spec_json_content(files)
    spec_file = next(v for k, v in files.items() if k.endswith("spec.json"))
    spec_data = json.loads(spec_file)
    assert spec_data["experiment_type"] == "lid_driven_cavity"

    # 5. Verify controlDict contains functionObjects
    _assert_control_dict_has_functions(files)


# ========================================================================== #
# Test 4: Compile + Ingest + Analyze Pipeline
# ========================================================================== #


def test_compile_ingest_analyze_pipeline(tmp_path):
    """Compile a spec, create fake results, ingest and analyze."""
    from fluid_scientist.results.analysis import ScientificAnalyzer
    from fluid_scientist.results.ingestor import OpenFOAMResultIngestor
    from fluid_scientist.results.metric_executor import MetricExecutor
    from fluid_scientist.results.models import ResultManifest

    # 1. Create a confirmed pipe flow spec directly
    spec = create_confirmed_pipe_spec()

    # 2. Compile it to get a CompiledCase
    compiled_case, manifest = compile_spec(spec)

    # 3. Extract the case archive to a temp directory
    case_dir = tmp_path / "pipe_case"
    case_dir.mkdir()
    with tarfile.open(fileobj=io.BytesIO(compiled_case.archive), mode="r:gz") as tar:
        import sys
        if sys.version_info >= (3, 12):
            tar.extractall(path=str(case_dir), filter="data")
        else:
            tar.extractall(path=str(case_dir))

    # The archive may have a top-level directory; find the actual case dir
    # by looking for the system/ folder.
    if not (case_dir / "system").exists():
        for child in case_dir.iterdir():
            if (child / "system").exists():
                case_dir = child
                break

    # 4. Create fake postProcessing results
    #    - Solver log with residuals, continuity, Courant, convergence
    log_file = case_dir / "log.simpleFoam"
    log_file.write_text(
        "OpenFOAM-13\n"
        "Time = 0.1\n"
        "Ux: initial residual = 0.001, final residual = 0.0001\n"
        "continuity error = 1.5e-06\n"
        "Courant Number mean: 0.15 max: 0.85\n"
        "Time = 0.2\n"
        "Ux: initial residual = 0.0005, final residual = 0.00005\n"
        "continuity error = 8e-07\n"
        "Courant Number mean: 0.12 max: 0.80\n"
        "solution converged\n"
    )

    #    - postProcessing with surfaceFieldValue for inlet and outlet pressure
    pp_inlet = case_dir / "postProcessing" / "surfaceFieldValue_inlet" / "0"
    pp_inlet.mkdir(parents=True)
    (pp_inlet / "inlet_pressure.dat").write_text(
        "# Time areaAverage(p)\n"
        "0.1 100.0\n"
        "0.2 100.1\n"
        "0.3 100.05\n"
        "0.4 100.02\n"
        "0.5 100.01\n"
    )

    pp_outlet = case_dir / "postProcessing" / "surfaceFieldValue_outlet" / "0"
    pp_outlet.mkdir(parents=True)
    (pp_outlet / "outlet_pressure.dat").write_text(
        "# Time areaAverage(p)\n"
        "0.1 50.0\n"
        "0.2 50.1\n"
        "0.3 50.05\n"
        "0.4 50.02\n"
        "0.5 50.01\n"
    )

    # 5. Ingest results
    result_manifest = ResultManifest(
        run_id="run-e2e-001",
        experiment_id=spec.experiment_id,
        experiment_version=1,
        spec_hash=manifest.spec_hash,
        case_hash=manifest.case_hash,
    )
    ingestor = OpenFOAMResultIngestor()
    sim_data = ingestor.ingest(case_path=case_dir, result_manifest=result_manifest)

    # Verify ingestion parsed real files
    assert "Ux" in sim_data.residuals
    assert sim_data.converged is True
    assert sim_data.max_courant is not None

    # 6. Execute metrics
    executor = MetricExecutor()
    metric_results = executor.execute_all(
        ["pressure_drop", "residual_tolerance", "max_courant"],
        sim_data,
    )

    # 7. Verify metric_results are returned
    assert len(metric_results) == 3
    # pressure_drop should have a value (inlet - outlet pressure)
    pd_result = next(r for r in metric_results if r.metric_id == "pressure_drop")
    assert pd_result.value is not None, "pressure_drop must have a value"
    assert pd_result.unit == "Pa"
    # residual_tolerance should have a value
    rt_result = next(r for r in metric_results if r.metric_id == "residual_tolerance")
    assert rt_result.value is not None, "residual_tolerance must have a value"
    # max_courant should have a value
    mc_result = next(r for r in metric_results if r.metric_id == "max_courant")
    assert mc_result.value is not None, "max_courant must have a value"

    # 8. Analyze
    analyzer = ScientificAnalyzer()
    analysis = analyzer.analyze(
        metric_results,
        sim_data,
        benchmark_values={"pressure_drop": 50.0},
    )

    # 9. Verify scientific_analysis is returned
    assert len(analysis.direct_facts) > 0, "Must have direct calculation facts"
    assert len(analysis.numerical_credibility) > 0, "Must have numerical credibility"
    assert analysis.overall_confidence in ("high", "medium", "low")

    # Direct facts must contain calculated values
    fact_text = " ".join(f.content for f in analysis.direct_facts)
    assert "pressure_drop" in fact_text


# ========================================================================== #
# Test 5: Spy Test - No Old Functions Called
# ========================================================================== #


def test_no_old_functions_called_in_pipe_compile():
    """Verify that compiling a pipe spec does NOT call ANY old functions."""
    spec = create_confirmed_pipe_spec()

    # Spy on ALL old functions
    with patch("fluid_scientist.experiment_planning.compilers.compile_pipe_plan") as spy_pipe, \
         patch("fluid_scientist.experiment_planning.compilers.compile_cylinder_plan") as spy_cyl, \
         patch("fluid_scientist.experiment_planning.compilers.compile_cavity_plan") as spy_cav, \
         patch("fluid_scientist.experiment_planning.compilers.compile_plan") as spy_plan:

        compiled_case, manifest = compile_spec(spec)

        assert spy_pipe.call_count == 0, "compile_pipe_plan must not be called"
        assert spy_cyl.call_count == 0, "compile_cylinder_plan must not be called"
        assert spy_cav.call_count == 0, "compile_cavity_plan must not be called"
        assert spy_plan.call_count == 0, "compile_plan must not be called"

    # Compilation must have succeeded
    assert len(manifest.generated_files) > 0
    assert manifest.spec_hash is not None
    assert manifest.case_hash is not None


# ========================================================================== #
# Test 6: MeasurementPlan in Compiled Case
# ========================================================================== #


def test_measurement_plan_in_compiled_case():
    """Verify MeasurementPlan functionObjects appear in the compiled controlDict."""
    from fluid_scientist.measurement.models import (
        FieldOutputSpec,
        FunctionObjectSpec,
        FunctionObjectType,
        MeasurementPlan,
        MetricBinding,
        SpatialSamplingSpec,
        SpatialSamplingType,
        TimeSamplingSpec,
    )

    # Create a spec with a MeasurementPlan
    spec = create_confirmed_pipe_spec()

    # Build a MeasurementPlan with a surfaceFieldValue functionObject
    # that references the "inlet" patch (available for laminar_pipe).
    plan = MeasurementPlan(
        required_fields=[
            FieldOutputSpec(field_name="U"),
            FieldOutputSpec(field_name="p"),
        ],
        function_objects=[
            FunctionObjectSpec(
                type=FunctionObjectType.SURFACE_FIELD_VALUE,
                name="pressure_inlet_avg",
                field="p",
                operation="areaAverage",
                surface="inlet_section",
                target_patch="inlet",
            ),
        ],
        spatial_sampling=[
            SpatialSamplingSpec(id="inlet_section", type=SpatialSamplingType.SURFACE),
        ],
        time_sampling=TimeSamplingSpec(start_time=10.0, end_time=100.0, interval=0.01),
        metric_bindings=[
            MetricBinding(
                metric_id="pressure_drop",
                source="inlet_section",
                function_object="pressure_inlet_avg",
            ),
        ],
    )

    # Put the serialized MeasurementPlan into spec.metrics
    spec = spec.model_copy(
        update={"metrics": [plan.model_dump(mode="json")]}
    )

    # Compile
    compiled_case, manifest = compile_spec(spec)
    assert len(manifest.generated_files) > 0

    # Extract controlDict from archive
    files = extract_archive_files(compiled_case)
    cd = next(v for k, v in files.items() if k.endswith("controlDict"))
    assert cd is not None, "controlDict not found in archive"

    # Verify functionObject name from MeasurementPlan appears in controlDict
    assert "pressure_inlet_avg" in cd, (
        "MeasurementPlan functionObject 'pressure_inlet_avg' must appear in controlDict"
    )
