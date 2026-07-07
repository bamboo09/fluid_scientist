"""Tests for the compile parameter hard gate — required parameter validation.

These tests verify that compilation fails fast with a clear error when
required parameters are missing or set to 'unknown', rather than silently
falling back to hardcoded defaults.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.experiment_spec.compilation import (
    MissingRequiredParameterError,
    _REQUIRED_PARAMETERS,
    compile_spec,
    validate_required_parameters,
)
from fluid_scientist.experiment_spec.models import (
    Criticality,
    ExperimentSpec,
    ExperimentStatus,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ResearchSpec,
)
from fluid_scientist.experiment_spec.native_compiler import compile_spec_native
from fluid_scientist.ports import StoredExperimentSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _param(
    pid: str,
    value,
    *,
    data_type: str = "float",
    criticality: str = "medium",
) -> ParameterSpec:
    return ParameterSpec(
        parameter_id=pid,
        display_name=pid,
        category="test",
        value=value,
        data_type=data_type,
        source=ParameterSourceInfo(type=ParameterSource.TEMPLATE_DEFAULT),
        criticality=Criticality(criticality),
    )


def _full_pipe_spec(
    status: ExperimentStatus = ExperimentStatus.CONFIRMED,
) -> ExperimentSpec:
    """A confirmed pipe spec with ALL required parameters present."""
    return ExperimentSpec(
        experiment_id="pipe-gate-001",
        status=status,
        research=ResearchSpec(title="Pipe Gate Test", objective="Test pipe flow"),
        parameters=[
            _param("diameter", 0.05, criticality="critical"),
            _param("length", 1.0),
            _param("mean_velocity", 0.02, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("reynolds_number", 1000.0, criticality="critical"),
            _param("axial_cells", 80, data_type="integer"),
            _param("radial_cells", 10, data_type="integer"),
        ],
    )


def _pipe_spec_missing_diameter(
    status: ExperimentStatus = ExperimentStatus.CONFIRMED,
) -> ExperimentSpec:
    """A pipe spec where 'diameter' (required) is None."""
    return ExperimentSpec(
        experiment_id="pipe-missing-001",
        status=status,
        research=ResearchSpec(title="Pipe Missing", objective="Test missing param"),
        parameters=[
            _param("diameter", None),
            _param("length", 1.0),
            _param("mean_velocity", 0.02, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("reynolds_number", 1000.0, criticality="critical"),
            _param("axial_cells", 80, data_type="integer"),
            _param("radial_cells", 10, data_type="integer"),
        ],
    )


def _pipe_spec_unknown_diameter(
    status: ExperimentStatus = ExperimentStatus.CONFIRMED,
) -> ExperimentSpec:
    """A pipe spec where 'diameter' (required) is the string 'unknown'."""
    return ExperimentSpec(
        experiment_id="pipe-unknown-001",
        status=status,
        research=ResearchSpec(title="Pipe Unknown", objective="Test unknown param"),
        parameters=[
            _param("diameter", "unknown", data_type="string"),
            _param("length", 1.0),
            _param("mean_velocity", 0.02, criticality="critical"),
            _param("kinematic_viscosity", 1e-6),
            _param("density", 998.2),
            _param("reynolds_number", 1000.0, criticality="critical"),
            _param("axial_cells", 80, data_type="integer"),
            _param("radial_cells", 10, data_type="integer"),
        ],
    )


# ---------------------------------------------------------------------------
# 1. test_missing_required_parameter_raises
# ---------------------------------------------------------------------------


def test_missing_required_parameter_raises():
    """validate_required_parameters() raises MissingRequiredParameterError
    when a required parameter value is None."""
    spec = _pipe_spec_missing_diameter()
    with pytest.raises(MissingRequiredParameterError, match="diameter"):
        validate_required_parameters(spec)


# ---------------------------------------------------------------------------
# 2. test_unknown_parameter_raises
# ---------------------------------------------------------------------------


def test_unknown_parameter_raises():
    """validate_required_parameters() raises MissingRequiredParameterError
    when a required parameter value is the string 'unknown'."""
    spec = _pipe_spec_unknown_diameter()
    with pytest.raises(MissingRequiredParameterError, match="diameter"):
        validate_required_parameters(spec)


# ---------------------------------------------------------------------------
# 3. test_all_parameters_present_passes
# ---------------------------------------------------------------------------


def test_all_parameters_present_passes():
    """validate_required_parameters() does not raise when all required
    parameters have valid values."""
    spec = _full_pipe_spec()
    # Should not raise
    validate_required_parameters(spec)


# ---------------------------------------------------------------------------
# 4. test_compile_raises_on_missing_param
# ---------------------------------------------------------------------------


def test_compile_raises_on_missing_param():
    """compile_spec() raises MissingRequiredParameterError when a required
    parameter is missing (no silent default)."""
    spec = _pipe_spec_missing_diameter()
    with pytest.raises(MissingRequiredParameterError, match="diameter"):
        compile_spec(spec)


# ---------------------------------------------------------------------------
# 5. test_native_compiler_raises_on_missing_param
# ---------------------------------------------------------------------------


def test_native_compiler_raises_on_missing_param():
    """compile_spec_native() raises MissingRequiredParameterError when a
    required parameter is missing."""
    spec = _pipe_spec_missing_diameter()
    with pytest.raises(MissingRequiredParameterError, match="diameter"):
        compile_spec_native(spec)


# ---------------------------------------------------------------------------
# 6. test_api_compile_returns_422_on_missing_param
# ---------------------------------------------------------------------------


def test_api_compile_returns_422_on_missing_param():
    """The compile API endpoint returns HTTP 422 when a required parameter
    is missing."""
    repository = SQLWorkflowRepository("sqlite:///:memory:")
    app = create_app(repository=repository, execution_targets=[])
    client = TestClient(app, raise_server_exceptions=False)

    # Create a project first (experiment_specs has a FK to projects).
    resp = client.post(
        "/api/projects", json={"question": "test question for parameter gate"}
    )
    assert resp.status_code == 201
    project_id = resp.json()["project_id"]

    experiment_id = "pipe-missing-api-001"

    spec = _pipe_spec_missing_diameter()
    now = datetime.now().isoformat()
    stored = StoredExperimentSpec(
        experiment_id=experiment_id,
        project_id=project_id,
        schema_version="1.0.0",
        experiment_version=1,
        status="confirmed",
        task_type="new_simulation",
        interaction_mode="standard",
        spec_json=spec.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    repository.save_experiment_spec(stored)

    response = client.post(
        f"/api/projects/{project_id}/experiment-specs/{experiment_id}/compile"
    )
    assert response.status_code == 422
    body = response.json()
    assert "diameter" in body["detail"]


# ---------------------------------------------------------------------------
# 7. test_required_parameters_per_type
# ---------------------------------------------------------------------------


def test_required_parameters_per_type():
    """Verify the required parameter lists for each experiment type."""
    assert _REQUIRED_PARAMETERS["laminar_pipe"] == [
        "diameter",
        "length",
        "mean_velocity",
        "kinematic_viscosity",
        "density",
        "axial_cells",
        "radial_cells",
    ]
    assert _REQUIRED_PARAMETERS["cylinder_flow"] == [
        "diameter",
        "reynolds_number",
        "kinematic_viscosity",
        "density",
        "end_time",
    ]
    assert _REQUIRED_PARAMETERS["lid_driven_cavity"] == [
        "side_length",
        "lid_velocity",
        "kinematic_viscosity",
        "density",
        "cells_per_side",
        "end_time",
    ]
