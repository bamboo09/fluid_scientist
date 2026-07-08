"""Tests for batch parameter update endpoint and dirty-map frontend.

Commit 2: Batch parameter editing with a dirty map system and a batch PATCH API.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_spec.models import (
    ConfirmationPolicy,
    Criticality,
    ExperimentSpec,
    ParameterDependency,
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
        "/api/projects", json={"question": "batch parameter update test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


def _make_param(
    parameter_id: str,
    display_name: str,
    category: str,
    value: float | int | str,
    *,
    criticality: Criticality = Criticality.MEDIUM,
    source_type: ParameterSource = ParameterSource.USER,
    impact_scope: list[str] | None = None,
    dependencies: ParameterDependency | None = None,
    confirmation_policy: ConfirmationPolicy = ConfirmationPolicy.RECOMMEND_AND_NOTIFY,
) -> ParameterSpec:
    """Build a minimal ParameterSpec with proper enum values."""
    return ParameterSpec(
        parameter_id=parameter_id,
        display_name=display_name,
        category=category,
        value=value,
        source=ParameterSourceInfo(type=source_type),
        criticality=criticality,
        impact_scope=impact_scope or [],
        dependencies=dependencies or ParameterDependency(),
        confirmation_policy=confirmation_policy,
    )


def _create_spec(
    repository,
    project_id: str,
    *,
    parameters: list[ParameterSpec] | None = None,
) -> str:
    """Create an experiment spec directly in the repository.

    Returns the experiment_id.
    """
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        research=ResearchSpec(
            title="Batch Parameter Update Test",
            objective="Test batch parameter updates with propagation",
        ),
        parameters=parameters or [],
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
def spec_with_parameters(repository, project_id):
    """Create an experiment spec with parameters for batch update testing.

    Includes:
    - diameter: a parameter with mesh impact_scope (for invalidated artifacts)
    - inlet_velocity: a parameter that reynolds_number depends on
    - reynolds_number: a derived parameter depending on inlet_velocity
    """
    parameters = [
        _make_param(
            "diameter",
            "Cylinder Diameter",
            "geometry",
            0.1,
            criticality=Criticality.CRITICAL,
            source_type=ParameterSource.USER,
            impact_scope=["mesh"],
        ),
        _make_param(
            "inlet_velocity",
            "Inlet Velocity",
            "boundary_condition",
            0.01,
            criticality=Criticality.CRITICAL,
            source_type=ParameterSource.USER,
        ),
        _make_param(
            "reynolds_number",
            "Reynolds Number",
            "physics",
            1000.0,
            criticality=Criticality.HIGH,
            source_type=ParameterSource.DERIVED,
            dependencies=ParameterDependency(depends_on=["inlet_velocity"]),
        ),
    ]
    experiment_id = _create_spec(
        repository, project_id, parameters=parameters
    )
    return {
        "project_id": project_id,
        "experiment_id": experiment_id,
    }


# ---------------------------------------------------------------------------
# Helpers for JS source inspection
# ---------------------------------------------------------------------------


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
# Test 1: Batch update multiple parameters returns updated_parameters list
# ---------------------------------------------------------------------------


class TestBatchUpdateParameters:
    """Verify batch update endpoint returns correct updated_parameters."""

    def test_batch_update_multiple_parameters_returns_updated_parameters(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """PATCH /parameters must return _batch_propagation.updated_parameters."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.05},
                    {"parameter_id": "inlet_velocity", "value": 0.02},
                ],
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        propagation = body["_batch_propagation"]
        assert "diameter" in propagation["updated_parameters"]
        assert "inlet_velocity" in propagation["updated_parameters"]
        assert len(propagation["updated_parameters"]) == 2

        # Verify the actual values were updated
        params = {p["parameter_id"]: p for p in body["parameters"]}
        assert params["diameter"]["value"] == 0.05
        assert params["inlet_velocity"]["value"] == 0.02

    def test_batch_update_returns_derived_updates(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """Batch update of inlet_velocity must return derived_updates for reynolds_number."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "inlet_velocity", "value": 0.05},
                ],
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        propagation = body["_batch_propagation"]

        # reynolds_number depends on inlet_velocity and is derived
        assert "reynolds_number" in propagation["auto_recomputed"]
        derived_ids = [d["parameter_id"] for d in propagation["derived_updates"]]
        assert "reynolds_number" in derived_ids

        # Each derived_update should have parameter_id, new_value, and reason
        for d in propagation["derived_updates"]:
            assert "parameter_id" in d
            assert "new_value" in d
            assert "reason" in d

    def test_batch_update_returns_invalidated_artifacts(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """Batch update of diameter (impact_scope=mesh) must return invalidated artifacts."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.2},
                ],
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        propagation = body["_batch_propagation"]

        # diameter has impact_scope=["mesh"], so "mesh" should be invalidated
        assert "mesh" in propagation["invalidated"]

    def test_batch_update_version_conflict_returns_409(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """Batch update with wrong experiment_version must return 409."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 99,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.05},
                ],
            },
        )

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert detail["error"] == "version_conflict"
        assert detail["current_version"] == 1
        assert detail["client_version"] == 99

    def test_batch_update_empty_updates_returns_422(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """Batch update with empty updates list must return 422."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [],
            },
        )

        assert response.status_code == 422

    def test_batch_update_includes_summary(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """Batch update response must include a human-readable summary."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.05},
                    {"parameter_id": "inlet_velocity", "value": 0.02},
                ],
            },
        )

        assert response.status_code == 200, response.text
        propagation = response.json()["_batch_propagation"]
        assert propagation["summary"]
        assert "2" in propagation["summary"]


# ---------------------------------------------------------------------------
# Test 6: Frontend has pendingParameterChanges and applyPendingParameterChanges
# ---------------------------------------------------------------------------


class TestBatchEditingFrontend:
    """Verify the web assets include batch editing infrastructure."""

    def test_app_js_includes_pending_parameter_changes(self, client: TestClient):
        """app.js must include pendingParameterChanges Map."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "pendingParameterChanges" in js
        assert "new Map()" in js

    def test_app_js_includes_apply_pending_parameter_changes(self, client: TestClient):
        """app.js must include applyPendingParameterChanges function."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "applyPendingParameterChanges" in js
        body = _function_body(js, "async function applyPendingParameterChanges()")
        # Must use the batch PATCH endpoint
        assert "/parameters" in body
        assert "PATCH" in body
        assert "updates" in body

    def test_app_js_includes_discard_pending_parameter_changes(self, client: TestClient):
        """app.js must include discardPendingParameterChanges function."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "discardPendingParameterChanges" in js
        assert "markParameterDirty" in js
        assert "renderBatchPropagation" in js

    def test_app_js_input_event_uses_mark_parameter_dirty(self, client: TestClient):
        """renderParameterRow must use markParameterDirty on input event."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "function renderParameterRow(")
        assert "markParameterDirty" in body
        assert 'addEventListener("input"' in body
        # Should NOT use the old change -> updateSpecParameter pattern
        assert 'addEventListener("change"' not in body

    def test_styles_include_dirty_state_css(self, client: TestClient):
        """CSS must include dirty state styles."""
        response = client.get("/assets/styles.css")
        assert response.status_code == 200
        css = response.text
        assert "spec-param-dirty" in css
        assert "spec-pending-summary" in css
