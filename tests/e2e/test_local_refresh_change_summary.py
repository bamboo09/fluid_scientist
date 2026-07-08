"""Tests for local row refresh and change summary panel (Commit 4).

Commit 4 enhances the parameter workbench so that:
- Batch updates only refresh the changed rows in the DOM (no full re-render).
- The change-summary panel shows old->new value diffs for directly modified
  parameters and for derived (cascaded) updates.
- The backend tracks old values for directly modified parameters.
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
        "/api/projects", json={"question": "local refresh change summary test"}
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
            title="Local Refresh Change Summary Test",
            objective="Test local row refresh and change summary diffs",
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
    """Create an experiment spec with parameters for change-summary testing.

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
# Backend tests: direct_updates with old/new values
# ---------------------------------------------------------------------------


class TestDirectUpdatesTracking:
    """Verify the batch endpoint tracks old values for direct updates."""

    def test_batch_update_returns_direct_updates_with_old_and_new_value(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """PATCH /parameters must return _batch_propagation.direct_updates
        with old_value and new_value for each directly modified parameter."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.05},
                ],
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        propagation = body["_batch_propagation"]

        assert "direct_updates" in propagation
        direct = propagation["direct_updates"]
        assert isinstance(direct, list)
        assert len(direct) == 1

        entry = direct[0]
        assert entry["parameter_id"] == "diameter"
        assert "old_value" in entry
        assert "new_value" in entry
        # old_value should be the original value (0.1)
        assert entry["old_value"] == 0.1
        # new_value should be the updated value (0.05)
        assert entry["new_value"] == 0.05

    def test_direct_updates_contains_all_updated_parameter_ids(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """direct_updates must contain every directly modified parameter id."""
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
        direct = propagation["direct_updates"]

        direct_ids = [d["parameter_id"] for d in direct]
        assert "diameter" in direct_ids
        assert "inlet_velocity" in direct_ids
        assert len(direct) == 2

        # Verify old/new values for each
        by_id = {d["parameter_id"]: d for d in direct}
        assert by_id["diameter"]["old_value"] == 0.1
        assert by_id["diameter"]["new_value"] == 0.05
        assert by_id["inlet_velocity"]["old_value"] == 0.01
        assert by_id["inlet_velocity"]["new_value"] == 0.02

    def test_derived_updates_includes_reason(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """derived_updates entries must include a reason field."""
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
        propagation = response.json()["_batch_propagation"]

        assert propagation["derived_updates"]
        for d in propagation["derived_updates"]:
            assert "parameter_id" in d
            assert "new_value" in d
            assert "reason" in d
            assert d["reason"]  # reason must be non-empty


# ---------------------------------------------------------------------------
# Frontend tests: renderBatchPropagation shows old->new diff
# ---------------------------------------------------------------------------


class TestChangeSummaryFrontend:
    """Verify the web assets include change-summary diff infrastructure."""

    def test_render_batch_propagation_shows_old_new_diff(self, client: TestClient):
        """renderBatchPropagation must create spec-change-old and spec-change-new
        elements to display the old->new value diff."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "function renderBatchPropagation(")

        # Must reference direct_updates from the propagation
        assert "direct_updates" in body
        # Must create old/new diff elements
        assert "spec-change-old" in body
        assert "spec-change-new" in body
        assert "spec-change-arrow" in body
        assert "spec-change-diff" in body
        # Must use the text() helper to render values
        assert "text(update.old_value" in body
        assert "text(update.new_value" in body

    def test_update_parameter_row_in_place_preserves_input(
        self, client: TestClient
    ):
        """updateParameterRowInPlace must preserve the input element instead of
        recreating the entire row via renderParameterRow."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "function updateParameterRowInPlace(")

        # Must query the specific row by parameter id (not iterate all params)
        assert 'data-param-id="${parameterId}"' in body or (
            "data-param-id" in body and "parameterId" in body
        )
        # Must preserve input by updating its value rather than replacing the row
        assert "input.value" in body
        # Must avoid disrupting user typing by checking focus
        assert "document.activeElement" in body
        # Must NOT call renderParameterRow (which recreates the whole row)
        assert "renderParameterRow" not in body

    def test_apply_pending_changes_only_updates_changed_rows(
        self, client: TestClient
    ):
        """applyPendingParameterChanges must only refresh changed parameter rows,
        not iterate over all parameters."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "async function applyPendingParameterChanges()")

        # Must build a set of changed ids from updates and propagation
        assert "changedIds" in body
        assert "auto_recomputed" in body
        assert "derived_updates" in body

    def test_styles_include_change_diff_css(self, client: TestClient):
        """CSS must include change diff styles for old/new/arrow/diff."""
        response = client.get("/assets/styles.css")
        assert response.status_code == 200
        css = response.text

        assert ".spec-change-section" in css
        assert ".spec-change-section-title" in css
        assert ".spec-change-row" in css
        assert ".spec-change-label" in css
        assert ".spec-change-diff" in css
        assert ".spec-change-old" in css
        assert ".spec-change-arrow" in css
        assert ".spec-change-new" in css
        assert ".spec-change-reason" in css
