"""Tests verifying parameter workbench interactions don't pollute conversation stream.

Commit 1: Parameter updates must not append messages to the conversation stream,
must not trigger page scrolling, and the workbench must show toast notifications
instead of conversation cards.
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
    Criticality,
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
        "/api/projects", json={"question": "parameter workbench interaction test"}
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
) -> ParameterSpec:
    """Build a minimal ParameterSpec with proper enum values."""
    return ParameterSpec(
        parameter_id=parameter_id,
        display_name=display_name,
        category=category,
        value=value,
        source=ParameterSourceInfo(type=source_type),
        criticality=criticality,
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
            title="Parameter Workbench Test",
            objective="Test parameter updates do not pollute conversation stream",
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
    """Create an experiment spec with parameters for testing.

    All critical parameters have values and non-unknown sources so that
    transitions to 'ready' succeed.
    """
    parameters = [
        _make_param(
            "diameter",
            "Cylinder Diameter",
            "geometry",
            0.1,
            criticality=Criticality.CRITICAL,
            source_type=ParameterSource.USER,
        ),
        _make_param(
            "density",
            "Fluid Density",
            "physics",
            998.2,
            criticality=Criticality.HIGH,
            source_type=ParameterSource.SYSTEM_RECOMMENDED,
        ),
        _make_param(
            "inlet_velocity",
            "Inlet Velocity",
            "boundary_condition",
            0.01,
            criticality=Criticality.CRITICAL,
            source_type=ParameterSource.USER,
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
    # Find the next top-level function declaration after this one
    search_from = start + len(signature)
    end = len(js)
    for marker in ("\nfunction ", "\nasync function "):
        pos = js.find(marker, search_from)
        if pos != -1 and pos < end:
            end = pos
    return js[start:end]


# ---------------------------------------------------------------------------
# Test 1: Parameter updates do not produce conversation messages
# ---------------------------------------------------------------------------


class TestParameterUpdateNoConversationPollution:
    """Verify parameter updates do not produce conversation messages."""

    def test_single_parameter_update_returns_no_conversation_message(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """PATCH /parameters/{id} must return only spec data, no conversation message."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters/diameter",
            json={"value": 0.05},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        # The response should be the updated spec, NOT a conversation message
        assert "experiment_id" in body
        assert "parameters" in body
        # No conversation-related fields should be present
        assert "message" not in body
        assert "conversation" not in body

    def test_spec_transition_returns_no_conversation_message(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """POST /transition must return only spec data, no conversation message."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/transition",
            json={"target_status": "ready"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert "experiment_id" in body
        assert "status" in body
        # No conversation-related fields
        assert "message" not in body
        assert "conversation" not in body

    def test_spec_save_returns_no_conversation_message(
        self, client: TestClient, spec_with_parameters: dict
    ):
        """GET spec (used by saveSpecDraft) must return only spec data."""
        project_id = spec_with_parameters["project_id"]
        experiment_id = spec_with_parameters["experiment_id"]

        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}",
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert "experiment_id" in body
        assert "parameters" in body
        # No conversation-related fields
        assert "message" not in body
        assert "conversation" not in body


# ---------------------------------------------------------------------------
# Test 2: Web assets include workbench toast infrastructure
# ---------------------------------------------------------------------------


class TestWorkbenchToastHost:
    """Verify the web assets include workbench toast infrastructure."""

    def test_styles_include_workbench_toast_css(self, client: TestClient):
        """CSS must include workbench toast styles."""
        response = client.get("/assets/styles.css")
        assert response.status_code == 200
        css = response.text
        assert "workbench-toast" in css
        assert "workbench-toast-host" in css
        assert "workbench-toast-success" in css
        assert "workbench-toast-error" in css

    def test_app_js_includes_show_workbench_toast(self, client: TestClient):
        """app.js must include showWorkbenchToast function."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "showWorkbenchToast" in js
        assert "workbench-toast-host" in js

    def test_app_js_does_not_call_append_conversation_in_save_spec_draft(
        self, client: TestClient
    ):
        """saveSpecDraft must not call appendConversation."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        func_body = _function_body(js, "async function saveSpecDraft()")
        assert "appendConversation" not in func_body, (
            "saveSpecDraft must not call appendConversation"
        )

    def test_app_js_does_not_call_append_conversation_in_transition_spec(
        self, client: TestClient
    ):
        """transitionSpec must not call appendConversation."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        func_body = _function_body(js, "async function transitionSpec(")
        assert "appendConversation" not in func_body, (
            "transitionSpec must not call appendConversation"
        )

    def test_app_js_does_not_call_render_error_in_update_spec_parameter(
        self, client: TestClient
    ):
        """updateSpecParameter error handler must not call renderError."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        func_body = _function_body(js, "async function updateSpecParameter(")
        assert "renderError" not in func_body, (
            "updateSpecParameter must not call renderError"
        )
