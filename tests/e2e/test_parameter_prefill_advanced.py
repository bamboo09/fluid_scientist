"""Tests for advanced parameter pre-fill with derived computation and accept-all API.

Commit 2: Enhanced parameter pre-filling with proper source tracking and
an "accept all recommendations" API endpoint.
"""
from __future__ import annotations

import math
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.derivation import (
    accept_all_recommendations,
    compute_derived_parameters,
)
from fluid_scientist.experiment_spec.models import (
    Criticality,
    ExperimentSpec,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
    ResearchSpec,
)
from fluid_scientist.ports import StoredExperimentSpec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_param(
    parameter_id: str,
    display_name: str,
    category: str,
    value: float | int | str | None,
    *,
    source_type: ParameterSource = ParameterSource.USER,
    status: ParameterStatus = ParameterStatus.PENDING,
    criticality: Criticality = Criticality.MEDIUM,
    unit: str | None = None,
) -> ParameterSpec:
    """Build a minimal ParameterSpec with proper enum values."""
    return ParameterSpec(
        parameter_id=parameter_id,
        display_name=display_name,
        category=category,
        value=value,
        unit=unit,
        source=ParameterSourceInfo(type=source_type),
        status=status,
        criticality=criticality,
    )


def _make_spec(
    parameters: list[ParameterSpec],
    *,
    experiment_id: str | None = None,
) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id=experiment_id or f"exp-{uuid4().hex[:16]}",
        research=ResearchSpec(title="Test", objective="Test objective"),
        parameters=parameters,
    )


def _function_body(js: str, signature: str) -> str:
    """Extract the body of a function from JS source by its signature."""
    start = js.find(signature)
    assert start != -1, f"function not found: {signature}"
    search_from = start + len(signature)
    end = len(js)
    for marker in ("\nfunction ", "\nasync function "):
        pos = js.find(marker, search_from)
        if pos != -1 and pos < end:
            end = pos
    return js[start:end]


# ---------------------------------------------------------------------------
# Tests 1-3: compute_derived_parameters
# ---------------------------------------------------------------------------


class TestComputeDerivedParameters:
    """Verify compute_derived_parameters correctly derives values."""

    def test_computes_mean_velocity_from_mass_flow(self):
        """mean_velocity must be computed from mass_flow_rate, density, diameter."""
        params = [
            _make_param("mass_flow_rate", "Mass Flow Rate", "boundary_condition",
                        0.1, source_type=ParameterSource.USER, unit="kg/s"),
            _make_param("density", "Density", "material",
                        998.2, source_type=ParameterSource.USER, unit="kg/m^3"),
            _make_param("diameter", "Diameter", "geometry",
                        0.01, source_type=ParameterSource.USER, unit="m"),
            _make_param("mean_velocity", "Mean Velocity", "boundary_condition",
                        None, source_type=ParameterSource.UNKNOWN, unit="m/s"),
        ]
        spec = _make_spec(params)
        result = compute_derived_parameters(spec)
        mv = result.get_parameter("mean_velocity")
        assert mv is not None
        assert mv.value is not None
        expected = 0.1 / (998.2 * math.pi * (0.01 / 2) ** 2)
        assert mv.value == pytest.approx(expected, rel=1e-6)
        assert mv.source.type == ParameterSource.DERIVED

    def test_computes_reynolds_number(self):
        """reynolds_number must be computed from velocity, diameter, viscosity."""
        params = [
            _make_param("mean_velocity", "Mean Velocity", "boundary_condition",
                        0.1, source_type=ParameterSource.USER, unit="m/s"),
            _make_param("diameter", "Diameter", "geometry",
                        0.01, source_type=ParameterSource.USER, unit="m"),
            _make_param("kinematic_viscosity", "Kinematic Viscosity", "material",
                        1.0e-6, source_type=ParameterSource.USER, unit="m^2/s"),
            _make_param("reynolds_number", "Reynolds Number", "physics",
                        None, source_type=ParameterSource.UNKNOWN),
        ]
        spec = _make_spec(params)
        result = compute_derived_parameters(spec)
        re = result.get_parameter("reynolds_number")
        assert re is not None
        assert re.value is not None
        assert re.value == pytest.approx(0.1 * 0.01 / 1.0e-6, rel=1e-6)
        assert re.source.type == ParameterSource.DERIVED

    def test_does_not_overwrite_user_values(self):
        """compute_derived_parameters must NOT overwrite user-confirmed values."""
        params = [
            _make_param("mass_flow_rate", "Mass Flow Rate", "boundary_condition",
                        0.1, source_type=ParameterSource.USER, unit="kg/s"),
            _make_param("density", "Density", "material",
                        998.2, source_type=ParameterSource.USER, unit="kg/m^3"),
            _make_param("diameter", "Diameter", "geometry",
                        0.01, source_type=ParameterSource.USER, unit="m"),
            _make_param("mean_velocity", "Mean Velocity", "boundary_condition",
                        0.5, source_type=ParameterSource.USER, unit="m/s"),
        ]
        spec = _make_spec(params)
        result = compute_derived_parameters(spec)
        mv = result.get_parameter("mean_velocity")
        assert mv is not None
        assert mv.value == 0.5
        assert mv.source.type == ParameterSource.USER


# ---------------------------------------------------------------------------
# Tests 4-6: accept_all_recommendations
# ---------------------------------------------------------------------------


class TestAcceptAllRecommendations:
    """Verify accept_all_recommendations function."""

    def test_accepts_system_recommended(self):
        """system_recommended parameters must be accepted (status=ACCEPTED)."""
        params = [
            _make_param("density", "Density", "material",
                        998.2, source_type=ParameterSource.SYSTEM_RECOMMENDED,
                        status=ParameterStatus.PENDING, unit="kg/m^3"),
            _make_param("kinematic_viscosity", "Kinematic Viscosity", "material",
                        1.0e-6, source_type=ParameterSource.USER,
                        status=ParameterStatus.PENDING, unit="m^2/s"),
        ]
        spec = _make_spec(params)
        result = accept_all_recommendations(spec)
        density = result.get_parameter("density")
        assert density is not None
        assert density.status == ParameterStatus.ACCEPTED
        # USER params should remain PENDING
        nu = result.get_parameter("kinematic_viscosity")
        assert nu is not None
        assert nu.status == ParameterStatus.PENDING

    def test_computes_derived_params(self):
        """accept_all_recommendations must compute derived parameters."""
        params = [
            _make_param("mass_flow_rate", "Mass Flow Rate", "boundary_condition",
                        0.1, source_type=ParameterSource.USER, unit="kg/s"),
            _make_param("density", "Density", "material",
                        998.2, source_type=ParameterSource.SYSTEM_RECOMMENDED,
                        status=ParameterStatus.PENDING, unit="kg/m^3"),
            _make_param("diameter", "Diameter", "geometry",
                        0.01, source_type=ParameterSource.USER, unit="m"),
            _make_param("mean_velocity", "Mean Velocity", "boundary_condition",
                        None, source_type=ParameterSource.UNKNOWN, unit="m/s"),
        ]
        spec = _make_spec(params)
        result = accept_all_recommendations(spec)
        mv = result.get_parameter("mean_velocity")
        assert mv is not None
        assert mv.value is not None
        assert mv.source.type == ParameterSource.DERIVED
        # density should also be accepted
        density = result.get_parameter("density")
        assert density is not None
        assert density.status == ParameterStatus.ACCEPTED

    def test_leaves_unknown_unchanged(self):
        """accept_all_recommendations must leave unknown_required unchanged."""
        params = [
            _make_param("density", "Density", "material",
                        998.2, source_type=ParameterSource.SYSTEM_RECOMMENDED,
                        status=ParameterStatus.PENDING, unit="kg/m^3"),
            _make_param("end_time", "End Time", "numerics",
                        None, source_type=ParameterSource.UNKNOWN, unit="s"),
        ]
        spec = _make_spec(params)
        result = accept_all_recommendations(spec)
        end_time = result.get_parameter("end_time")
        assert end_time is not None
        assert end_time.value is None
        assert end_time.source.type == ParameterSource.UNKNOWN
        # density should still be accepted
        density = result.get_parameter("density")
        assert density is not None
        assert density.status == ParameterStatus.ACCEPTED


# ---------------------------------------------------------------------------
# Tests 7-9: API endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def repository():
    return SQLWorkflowRepository("sqlite:///:memory:")


@pytest.fixture
def client(repository):
    app = create_app(repository=repository, execution_targets=[])
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def project_id(client):
    response = client.post(
        "/api/projects", json={"question": "accept recommendations test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


def _create_spec_in_repo(
    repository,
    project_id: str,
    parameters: list[ParameterSpec],
) -> str:
    """Create an experiment spec directly in the repository."""
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()
    spec = ExperimentSpec(
        experiment_id=experiment_id,
        research=ResearchSpec(
            title="Accept Recommendations Test",
            objective="Test accepting all recommendations",
        ),
        parameters=parameters,
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


@pytest.fixture
def spec_with_recommendations(repository, project_id):
    """Create a spec with system_recommended, derivable, and unknown parameters."""
    parameters = [
        _make_param("density", "Density", "material",
                    998.2, source_type=ParameterSource.SYSTEM_RECOMMENDED,
                    status=ParameterStatus.PENDING, unit="kg/m^3"),
        _make_param("kinematic_viscosity", "Kinematic Viscosity", "material",
                    1.0e-6, source_type=ParameterSource.SYSTEM_RECOMMENDED,
                    status=ParameterStatus.PENDING, unit="m^2/s"),
        _make_param("mass_flow_rate", "Mass Flow Rate", "boundary_condition",
                    0.1, source_type=ParameterSource.USER, unit="kg/s"),
        _make_param("diameter", "Diameter", "geometry",
                    0.01, source_type=ParameterSource.USER, unit="m"),
        _make_param("mean_velocity", "Mean Velocity", "boundary_condition",
                    None, source_type=ParameterSource.UNKNOWN, unit="m/s"),
        _make_param("reynolds_number", "Reynolds Number", "physics",
                    None, source_type=ParameterSource.UNKNOWN),
        _make_param("end_time", "End Time", "numerics",
                    None, source_type=ParameterSource.UNKNOWN, unit="s"),
    ]
    experiment_id = _create_spec_in_repo(repository, project_id, parameters)
    return {"project_id": project_id, "experiment_id": experiment_id}


class TestAcceptRecommendationsAPI:
    """Verify the accept-recommendations API endpoint."""

    def test_returns_acceptance_summary(
        self, client: TestClient, spec_with_recommendations: dict
    ):
        """POST accept-recommendations must return _acceptance_summary."""
        project_id = spec_with_recommendations["project_id"]
        experiment_id = spec_with_recommendations["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/accept-recommendations"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        summary = body["_acceptance_summary"]
        assert "accepted_recommendations" in summary
        assert "derived_parameters" in summary
        assert "still_unknown_required" in summary
        assert "summary" in summary
        assert len(summary["summary"]) > 0

    def test_returns_derived_parameters(
        self, client: TestClient, spec_with_recommendations: dict
    ):
        """accept-recommendations must return derived_parameters list."""
        project_id = spec_with_recommendations["project_id"]
        experiment_id = spec_with_recommendations["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/accept-recommendations"
        )
        assert response.status_code == 200, response.text
        summary = response.json()["_acceptance_summary"]
        derived = summary["derived_parameters"]
        assert "mean_velocity" in derived
        assert "reynolds_number" in derived

    def test_returns_still_unknown_required(
        self, client: TestClient, spec_with_recommendations: dict
    ):
        """accept-recommendations must return still_unknown_required list."""
        project_id = spec_with_recommendations["project_id"]
        experiment_id = spec_with_recommendations["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/accept-recommendations"
        )
        assert response.status_code == 200, response.text
        summary = response.json()["_acceptance_summary"]
        still_unknown = summary["still_unknown_required"]
        assert "end_time" in still_unknown
        # mean_velocity and reynolds_number should NOT be in still_unknown
        assert "mean_velocity" not in still_unknown
        assert "reynolds_number" not in still_unknown


# ---------------------------------------------------------------------------
# Tests 10-12: Frontend assets
# ---------------------------------------------------------------------------


class TestFrontendAcceptRecommendations:
    """Verify the web assets include accept-recommendations infrastructure."""

    def test_app_js_has_accept_all_recommendations(self, client: TestClient):
        """app.js must include acceptAllRecommendations function."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "acceptAllRecommendations" in js
        body = _function_body(js, "async function acceptAllRecommendations()")
        assert "accept-recommendations" in body
        assert "method: \"POST\"" in body

    def test_app_js_has_accept_rec_button(self, client: TestClient):
        """app.js must include the accept-rec button with proper id."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "spec-accept-rec-btn" in js
        assert "\u63a5\u53d7\u63a8\u8350\u503c" in js or "\u63a5\u53d7\u63a8\u8350\u503c" in js

    def test_css_has_spec_param_unknown(self, client: TestClient):
        """CSS must include spec-param-unknown class."""
        response = client.get("/assets/styles.css")
        assert response.status_code == 200
        css = response.text
        assert ".spec-param-unknown" in css
