"""E2E tests for Commit 11: Legacy cleanup and database migration.

Verifies:
1. compile_spec() raises ValueError (no fallback) when no native compiler is found
2. Old API endpoints (submit/results/analysis) are marked as deprecated
3. StoredCompiledExperiment model uses experiment_id field (not plan_id)
4. Compile endpoint stores experiment_id in the compiled experiment record
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.compilation import compile_spec
from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ExperimentStatus,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)
from fluid_scientist.ports import StoredCompiledExperiment, StoredExperimentSpec

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
        "/api/projects", json={"question": "legacy cleanup e2e test"}
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


def _pipe_flow_parameters() -> list[ParameterSpec]:
    """Return a list of pipe-flow ParameterSpec objects."""
    return [
        _make_param("diameter", "Pipe Diameter", "geometry", 0.05),
        _make_param("length", "Pipe Length", "geometry", 1.0),
        _make_param("mean_velocity", "Mean Velocity", "flow", 0.02),
        _make_param("kinematic_viscosity", "Kinematic Viscosity", "material", 1e-6),
        _make_param("density", "Density", "material", 998.2),
        _make_param("axial_cells", "Axial Cells", "mesh", 80),
        _make_param("radial_cells", "Radial Cells", "mesh", 10),
    ]


def _create_confirmed_spec(
    repository,
    project_id: str,
    *,
    parameters: list[ParameterSpec] | None = None,
) -> str:
    """Create a confirmed experiment spec directly in the repository.

    Returns the experiment_id.
    """
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        research=ResearchSpec(
            title="Legacy Cleanup Test",
            objective="Test legacy cleanup and database migration",
        ),
        parameters=parameters or [],
    )

    # Transition to confirmed status
    spec_confirmed = spec.model_copy(
        update={"status": ExperimentStatus.CONFIRMED}
    )

    stored = StoredExperimentSpec(
        experiment_id=experiment_id,
        project_id=project_id,
        schema_version=spec.schema_version,
        experiment_version=spec.experiment_version,
        status=spec_confirmed.status.value,
        task_type=spec.task_type.value,
        interaction_mode=spec.interaction_mode.value,
        spec_json=spec_confirmed.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    repository.save_experiment_spec(stored)
    return experiment_id


# ---------------------------------------------------------------------------
# Test 1: compile_spec() no longer falls back to compile_confirmed_spec
# ---------------------------------------------------------------------------


def test_compile_spec_no_fallback():
    """compile_spec() must raise ValueError when no native compiler is found.

    The legacy compile_confirmed_spec fallback has been removed.
    A spec with unrecognisable parameters should cause ValueError, not
    a silent fallback to the deprecated path.
    """
    # Create a spec with parameters that don't match any experiment type.
    # This will cause ValueError either from _detect_experiment_type or
    # from the "No native compiler available" check — both prove the
    # fallback to compile_confirmed_spec is NOT used.
    spec = ExperimentSpec(
        experiment_id=f"exp-{uuid4().hex[:16]}",
        research=ResearchSpec(
            title="Unknown Type Test",
            objective="Test no-fallback behavior",
        ),
        parameters=[
            _make_param("unknown_param_1", "Unknown 1", "misc", 42),
            _make_param("unknown_param_2", "Unknown 2", "misc", 99),
        ],
    )
    # Transition to confirmed
    spec_confirmed = spec.model_copy(
        update={"status": ExperimentStatus.CONFIRMED}
    )

    # compile_spec should raise ValueError, not fall back to compile_confirmed_spec.
    # The ValueError may come from _detect_experiment_type (unknown params)
    # or from the "No native compiler available" check — both are acceptable.
    with pytest.raises(ValueError):
        compile_spec(spec_confirmed)


def test_compile_spec_no_fallback_does_not_call_compile_confirmed_spec():
    """Verify that compile_spec does not call compile_confirmed_spec.

    Even with DeprecationWarning filters, no DeprecationWarning from
    compile_confirmed_spec should be emitted when compile_spec fails.
    """
    import warnings

    spec = ExperimentSpec(
        experiment_id=f"exp-{uuid4().hex[:16]}",
        research=ResearchSpec(
            title="No Fallback Test",
            objective="Verify no deprecated path is used",
        ),
        parameters=[
            _make_param("unknown_param", "Unknown", "misc", 1),
        ],
    )
    spec_confirmed = spec.model_copy(
        update={"status": ExperimentStatus.CONFIRMED}
    )

    # Capture all warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        with pytest.raises(ValueError):
            compile_spec(spec_confirmed)

        # Check that no DeprecationWarning from compile_confirmed_spec was emitted
        deprecation_warnings = [
            warning for warning in w
            if issubclass(warning.category, DeprecationWarning)
            and "compile_confirmed_spec" in str(warning.message)
        ]
        assert len(deprecation_warnings) == 0, (
            "compile_spec() should not call compile_confirmed_spec; "
            "found deprecation warning from compile_confirmed_spec"
        )


# ---------------------------------------------------------------------------
# Test 2: Old API endpoints are marked as deprecated
# ---------------------------------------------------------------------------


def test_old_endpoints_marked_deprecated(client):
    """Verify that submit/results/analysis endpoints have deprecated: true.

    Checks the OpenAPI schema for the three old experiment-plans endpoints:
      POST /api/projects/{project_id}/experiment-plans/{plan_id}/submit
      GET  /api/projects/{project_id}/experiment-plans/{plan_id}/results
      POST /api/projects/{project_id}/experiment-plans/{plan_id}/analysis
    """
    response = client.get("/openapi.json")
    assert response.status_code == 200
    openapi = response.json()

    # Check submit endpoint
    submit_path = (
        openapi["paths"]
        .get("/api/projects/{project_id}/experiment-plans/{plan_id}/submit", {})
        .get("post", {})
    )
    assert submit_path.get("deprecated") is True, (
        "POST /experiment-plans/{plan_id}/submit should be deprecated"
    )
    assert "deprecated" in submit_path.get("tags", []), (
        "POST /experiment-plans/{plan_id}/submit should have 'deprecated' tag"
    )

    # Check results endpoint
    results_path = (
        openapi["paths"]
        .get("/api/projects/{project_id}/experiment-plans/{plan_id}/results", {})
        .get("get", {})
    )
    assert results_path.get("deprecated") is True, (
        "GET /experiment-plans/{plan_id}/results should be deprecated"
    )
    assert "deprecated" in results_path.get("tags", []), (
        "GET /experiment-plans/{plan_id}/results should have 'deprecated' tag"
    )

    # Check analysis endpoint
    analysis_path = (
        openapi["paths"]
        .get("/api/projects/{project_id}/experiment-plans/{plan_id}/analysis", {})
        .get("post", {})
    )
    assert analysis_path.get("deprecated") is True, (
        "POST /experiment-plans/{plan_id}/analysis should be deprecated"
    )
    assert "deprecated" in analysis_path.get("tags", []), (
        "POST /experiment-plans/{plan_id}/analysis should have 'deprecated' tag"
    )


# ---------------------------------------------------------------------------
# Test 3: StoredCompiledExperiment uses experiment_id field
# ---------------------------------------------------------------------------


def test_stored_compiled_experiment_uses_experiment_id():
    """Verify that StoredCompiledExperiment model uses experiment_id field.

    The field was renamed from plan_id to experiment_id in Commit 11.
    """
    import dataclasses

    # Get the field names of StoredCompiledExperiment
    field_names = {f.name for f in dataclasses.fields(StoredCompiledExperiment)}

    # experiment_id must be present
    assert "experiment_id" in field_names, (
        "StoredCompiledExperiment must have 'experiment_id' field"
    )
    # plan_id must NOT be present
    assert "plan_id" not in field_names, (
        "StoredCompiledExperiment must NOT have 'plan_id' field (renamed to experiment_id)"
    )

    # Verify we can construct it with experiment_id
    record = StoredCompiledExperiment(
        experiment_id="exp-test-001",
        plan_version=1,
        archive_sha256="sha256:" + "a" * 64,
        archive=b"fake archive bytes",
        preview_json='{"test": true}',
    )
    assert record.experiment_id == "exp-test-001"

    # Verify that constructing with plan_id= raises TypeError
    with pytest.raises(TypeError):
        StoredCompiledExperiment(
            plan_id="exp-test-001",  # type: ignore[call-arg]
            plan_version=1,
            archive_sha256="sha256:" + "a" * 64,
            archive=b"fake archive bytes",
            preview_json='{"test": true}',
        )


# ---------------------------------------------------------------------------
# Test 4: Compile endpoint stores experiment_id
# ---------------------------------------------------------------------------


def test_compile_endpoint_stores_experiment_id(client, repository, project_id):
    """Compile a spec and verify the stored record uses experiment_id field.

    After compiling a spec via the /compile endpoint, the stored
    StoredCompiledExperiment record should have experiment_id (not plan_id).
    """
    # Create a confirmed spec with pipe-flow parameters
    experiment_id = _create_confirmed_spec(
        repository, project_id, parameters=_pipe_flow_parameters()
    )

    # Compile the spec
    response = client.post(
        f"/api/projects/{project_id}/experiment-specs/{experiment_id}/compile",
    )
    assert response.status_code == 200, (
        f"Compile failed: {response.status_code} - {response.text}"
    )

    # Load the stored compiled experiment from the repository
    stored = repository.load_compiled_experiment(experiment_id, 1)
    assert stored is not None, (
        "Compiled experiment not found in repository after compile"
    )

    # Verify the stored record uses experiment_id field
    assert hasattr(stored, "experiment_id"), (
        "StoredCompiledExperiment should have experiment_id attribute"
    )
    assert stored.experiment_id == experiment_id, (
        f"Expected experiment_id={experiment_id}, got {stored.experiment_id}"
    )
    # Verify plan_id is NOT an attribute
    assert not hasattr(stored, "plan_id"), (
        "StoredCompiledExperiment should NOT have plan_id attribute"
    )
