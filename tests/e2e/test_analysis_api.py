"""E2E tests for the analysis main flow API endpoints.

Verifies the Ingestor -> MetricExecutor -> ScientificAnalyzer pipeline
exposed via:
  POST /api/projects/{project_id}/experiment-specs/{experiment_id}/ingest
  POST /api/projects/{project_id}/experiment-specs/{experiment_id}/analyze
  POST /api/projects/{project_id}/experiment-specs/{experiment_id}/scientific-report
  GET  /api/projects/{project_id}/experiment-specs/{experiment_id}/metric-results
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)
from fluid_scientist.ports import StoredExperimentSpec

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
        "/api/projects", json={"question": "analysis api e2e test"}
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
) -> str:
    """Create an experiment spec directly in the repository.

    Returns the experiment_id.
    """
    from uuid import uuid4

    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        research=ResearchSpec(
            title="Analysis API Test",
            objective="Test the analysis main flow endpoints",
        ),
        parameters=parameters or [],
        metrics=metrics or [],
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


def create_fake_pipe_case(tmp_path: Path) -> Path:
    """Create a fake OpenFOAM case with solver log and surfaceFieldValue data.

    The postProcessing directories are named so that the ingestor's fallback
    type-detection recognises them as ``surfaceFieldValue`` function objects,
    and the ``.dat`` files inside are named so that the parsed keys contain
    ``inlet`` / ``outlet`` (which the MetricExecutor's pressure_drop and
    friction_factor calculators look for).
    """
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    # Solver log
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

    # postProcessing - inlet pressure
    inlet_dir = case_dir / "postProcessing" / "surfaceFieldValue_inlet" / "0"
    inlet_dir.mkdir(parents=True)
    (inlet_dir / "inlet.dat").write_text(
        "# Time  areaAverage(p)\n0.0  100.0\n1.0  102.0\n2.0  101.5\n",
        encoding="utf-8",
    )

    # postProcessing - outlet pressure
    outlet_dir = case_dir / "postProcessing" / "surfaceFieldValue_outlet" / "0"
    outlet_dir.mkdir(parents=True)
    (outlet_dir / "outlet.dat").write_text(
        "# Time  areaAverage(p)\n0.0  50.0\n1.0  52.0\n2.0  51.5\n",
        encoding="utf-8",
    )

    return case_dir


def _pipe_flow_parameters() -> list[ParameterSpec]:
    """Return a list of pipe-flow ParameterSpec objects."""
    return [
        _make_param("length", "Pipe Length", "geometry", 1.0),
        _make_param("diameter", "Pipe Diameter", "geometry", 0.05),
        _make_param("mean_velocity", "Mean Velocity", "flow", 0.02),
        _make_param("density", "Density", "material", 998.2),
    ]


# ---------------------------------------------------------------------------
# Test 1: Ingest endpoint
# ---------------------------------------------------------------------------


def test_ingest_endpoint(client, repository, project_id, tmp_path):
    """POST /ingest returns simulation_data parsed from the case directory."""
    case_dir = create_fake_case(tmp_path)
    experiment_id = _create_spec(repository, project_id)

    response = client.post(
        f"/api/projects/{project_id}/experiment-specs/{experiment_id}/ingest",
        json={"case_path": str(case_dir)},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["experiment_id"] == experiment_id
    assert "simulation_data" in data
    sim_data = data["simulation_data"]
    # Solver log should have been parsed
    assert sim_data["max_courant"] == 0.456
    assert "missing_data" in data
    assert isinstance(data["missing_data"], list)
    assert "warnings" in data


# ---------------------------------------------------------------------------
# Test 2: Analyze endpoint
# ---------------------------------------------------------------------------


def test_analyze_endpoint(client, repository, project_id, tmp_path):
    """POST /analyze returns metric_results for pipe-flow parameters."""
    case_dir = create_fake_pipe_case(tmp_path)
    experiment_id = _create_spec(
        repository, project_id, parameters=_pipe_flow_parameters()
    )

    response = client.post(
        f"/api/projects/{project_id}/experiment-specs/{experiment_id}/analyze",
        json={"case_path": str(case_dir)},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["experiment_id"] == experiment_id
    assert "metric_results" in data
    metric_results = data["metric_results"]
    assert isinstance(metric_results, list)
    assert len(metric_results) > 0

    # With "length" in parameters, default metrics should be
    # pressure_drop, friction_factor, mass_flow_rate
    metric_ids = {r["metric_id"] for r in metric_results}
    assert "pressure_drop" in metric_ids
    assert "friction_factor" in metric_ids
    assert "mass_flow_rate" in metric_ids

    # pressure_drop should have a value (inlet/outlet data available)
    pd_result = next(
        r for r in metric_results if r["metric_id"] == "pressure_drop"
    )
    assert pd_result["value"] is not None
    assert pd_result["unit"] == "Pa"

    # mass_flow_rate should also have a value
    mfr_result = next(
        r for r in metric_results if r["metric_id"] == "mass_flow_rate"
    )
    assert mfr_result["value"] is not None
    assert mfr_result["unit"] == "kg/s"

    assert "missing_data" in data


# ---------------------------------------------------------------------------
# Test 3: Scientific report endpoint
# ---------------------------------------------------------------------------


def test_scientific_report_endpoint(client, repository, project_id, tmp_path):
    """POST /scientific-report returns metric_results AND scientific_analysis."""
    case_dir = create_fake_pipe_case(tmp_path)
    experiment_id = _create_spec(
        repository, project_id, parameters=_pipe_flow_parameters()
    )

    response = client.post(
        f"/api/projects/{project_id}/experiment-specs/{experiment_id}/scientific-report",
        json={"case_path": str(case_dir)},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["experiment_id"] == experiment_id
    assert "metric_results" in data
    assert isinstance(data["metric_results"], list)
    assert len(data["metric_results"]) > 0

    assert "scientific_analysis" in data
    analysis = data["scientific_analysis"]
    # ScientificAnalysis should have the 6 layers
    assert "direct_facts" in analysis
    assert "numerical_credibility" in analysis
    assert "comparisons" in analysis
    assert "physical_interpretation" in analysis
    assert "hypotheses" in analysis
    assert "recommendations" in analysis
    assert "overall_confidence" in analysis
    assert "key_findings" in analysis
    assert "limitations" in analysis

    # With successful metrics, there should be at least one direct fact
    assert len(analysis["direct_facts"]) > 0

    assert "missing_data" in data
    assert "warnings" in data


# ---------------------------------------------------------------------------
# Test 4: Analyze with MeasurementPlan
# ---------------------------------------------------------------------------


def test_analyze_with_measurement_plan(client, repository, project_id, tmp_path):
    """POST /analyze with a MeasurementPlan calculates the specified metrics."""
    case_dir = create_fake_case(tmp_path)

    # Build a MeasurementPlan with metric_bindings for max_courant and
    # residual_tolerance - these only need solver-log data.
    measurement_plan = {
        "metric_bindings": [
            {"metric_id": "max_courant", "source": "solver_log"},
            {"metric_id": "residual_tolerance", "source": "solver_log"},
        ],
        "function_objects": [],
    }

    experiment_id = _create_spec(
        repository,
        project_id,
        parameters=[_make_param("reynolds_number", "Reynolds Number", "flow", 1000)],
        metrics=[measurement_plan],
    )

    response = client.post(
        f"/api/projects/{project_id}/experiment-specs/{experiment_id}/analyze",
        json={"case_path": str(case_dir)},
    )
    assert response.status_code == 200, response.text
    data = response.json()

    assert "metric_results" in data
    metric_results = data["metric_results"]
    metric_ids = {r["metric_id"] for r in metric_results}

    # The metrics should come from the MeasurementPlan, NOT from the
    # default "reynolds_number" branch (which would give drag/lift/strouhal)
    assert metric_ids == {"max_courant", "residual_tolerance"}

    # max_courant should have a value (0.456 from the solver log)
    mc_result = next(
        r for r in metric_results if r["metric_id"] == "max_courant"
    )
    assert mc_result["value"] == 0.456

    # residual_tolerance should have a value (max final residual)
    rt_result = next(
        r for r in metric_results if r["metric_id"] == "residual_tolerance"
    )
    assert rt_result["value"] is not None


# ---------------------------------------------------------------------------
# Test 5: Ingest returns 404 for missing spec
# ---------------------------------------------------------------------------


def test_ingest_returns_404_for_missing_spec(client, project_id, tmp_path):
    """POST /ingest with a non-existent experiment_id returns 404."""
    case_dir = create_fake_case(tmp_path)

    response = client.post(
        f"/api/projects/{project_id}/experiment-specs/non-existent-exp/ingest",
        json={"case_path": str(case_dir)},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 6: GET metric-results endpoint
# ---------------------------------------------------------------------------


def test_metric_results_get_endpoint(client, repository, project_id):
    """GET /metric-results returns a (possibly empty) list for an existing spec."""
    experiment_id = _create_spec(repository, project_id)

    response = client.get(
        f"/api/projects/{project_id}/experiment-specs/{experiment_id}/metric-results"
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["experiment_id"] == experiment_id
    assert "metric_results" in data
    assert isinstance(data["metric_results"], list)


# ---------------------------------------------------------------------------
# Test 7: GET metric-results returns 404 for missing spec
# ---------------------------------------------------------------------------


def test_metric_results_get_returns_404_for_missing_spec(client, project_id):
    """GET /metric-results with a non-existent experiment_id returns 404."""
    response = client.get(
        f"/api/projects/{project_id}/experiment-specs/non-existent-exp/metric-results"
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 8: GET /api/system/version endpoint
# ---------------------------------------------------------------------------


def test_system_version_endpoint(client):
    """GET /api/system/version returns version info."""
    response = client.get("/api/system/version")
    assert response.status_code == 200
    data = response.json()
    assert "git_commit" in data
    assert "workflow" in data
    assert data["workflow"] == "v2"
    assert data["native_compile_enabled"] is True
