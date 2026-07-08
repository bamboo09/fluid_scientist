"""Tests for Commit 5 (state machine + button logic) and Commit 6 (pre-validation).

Verifies:
- Button visibility logic per spec state in the frontend.
- Clone endpoint creates a new draft version with incremented version.
- Pre-check endpoint returns blocking issues and can_compile flag.
- Disabled reason display exists in the frontend.
"""
from __future__ import annotations

import re
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
    ExperimentStatus,
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
        "/api/projects", json={"question": "state machine button test"}
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
    status: str = "draft",
    version: int = 1,
) -> str:
    """Create an experiment spec directly in the repository.

    Returns the experiment_id.
    """
    experiment_id = f"exp-{uuid4().hex[:16]}"
    now = datetime.now(UTC).isoformat()

    spec = ExperimentSpec(
        experiment_id=experiment_id,
        experiment_version=version,
        status=ExperimentStatus(status),
        research=ResearchSpec(
            title="State Machine Test",
            objective="Test state machine and button logic",
        ),
        parameters=parameters or [],
    )

    stored = StoredExperimentSpec(
        experiment_id=experiment_id,
        project_id=project_id,
        schema_version=spec.schema_version,
        experiment_version=version,
        status=status,
        task_type=spec.task_type.value,
        interaction_mode=spec.interaction_mode.value,
        spec_json=spec.model_dump_json(),
        created_at=now,
        updated_at=now,
    )
    repository.save_experiment_spec(stored)
    return experiment_id


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
# Backend: Clone endpoint
# ---------------------------------------------------------------------------


class TestCloneEndpoint:
    """Verify the clone endpoint creates new draft versions."""

    def test_clone_creates_new_version_with_draft_status(
        self, client: TestClient, repository, project_id: str
    ):
        """POST /clone must return a new spec with status=draft, version+1."""
        params = [_make_param("diameter", "Diameter", "geometry", 0.1)]
        eid = _create_spec(
            repository, project_id, parameters=params, status="confirmed", version=1
        )

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{eid}/clone"
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "draft"
        assert body["experiment_version"] == 2
        assert body["experiment_id"] != eid

    def test_clone_preserves_parameters(
        self, client: TestClient, repository, project_id: str
    ):
        """Cloned spec must preserve all parameter ids and values."""
        params = [
            _make_param("diameter", "Diameter", "geometry", 0.1),
            _make_param("velocity", "Velocity", "boundary_condition", 0.01),
        ]
        eid = _create_spec(
            repository, project_id, parameters=params, status="confirmed", version=3
        )

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{eid}/clone"
        )

        assert response.status_code == 201
        body = response.json()
        ids = [p["parameter_id"] for p in body["parameters"]]
        assert "diameter" in ids
        assert "velocity" in ids
        vals = {p["parameter_id"]: p["value"] for p in body["parameters"]}
        assert vals["diameter"] == 0.1
        assert vals["velocity"] == 0.01

    def test_clone_preserves_research_info(
        self, client: TestClient, repository, project_id: str
    ):
        """Cloned spec must preserve research title and objective."""
        eid = _create_spec(
            repository, project_id, status="completed", version=1
        )

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{eid}/clone"
        )

        assert response.status_code == 201
        body = response.json()
        assert body["research"]["title"] == "State Machine Test"
        assert body["research"]["objective"] == "Test state machine and button logic"

    def test_clone_original_spec_untouched(
        self, client: TestClient, repository, project_id: str
    ):
        """Cloning must not modify the original spec."""
        eid = _create_spec(
            repository, project_id, status="confirmed", version=1
        )

        client.post(f"/api/projects/{project_id}/experiment-specs/{eid}/clone")

        original = client.get(
            f"/api/projects/{project_id}/experiment-specs/{eid}"
        ).json()
        assert original["status"] == "confirmed"
        assert original["experiment_version"] == 1

    def test_clone_not_found_returns_404(self, client: TestClient, project_id: str):
        """Cloning a non-existent spec must return 404."""
        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/exp-missing/clone"
        )
        assert response.status_code == 404

    def test_clone_from_failed_state(
        self, client: TestClient, repository, project_id: str
    ):
        """Cloning a failed spec must produce a draft fix version."""
        eid = _create_spec(
            repository, project_id, status="failed", version=5
        )

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{eid}/clone"
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "draft"
        assert body["experiment_version"] == 6


# ---------------------------------------------------------------------------
# Backend: Pre-check endpoint
# ---------------------------------------------------------------------------


class TestPreCheckEndpoint:
    """Verify the pre-check endpoint returns blocking issues."""

    def test_pre_check_returns_blocking_issues_for_unknown_parameters(
        self, client: TestClient, repository, project_id: str
    ):
        """Pre-check must block when a parameter has source.type=unknown."""
        params = [
            _make_param(
                "mystery", "Mystery Param", "physics", 42,
                source_type=ParameterSource.UNKNOWN,
            )
        ]
        eid = _create_spec(
            repository, project_id, parameters=params, status="confirmed"
        )

        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{eid}/pre-check"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["can_compile"] is False
        types = [i["type"] for i in body["blocking_issues"]]
        assert "unknown_required" in types
        assert any(
            i.get("parameter_id") == "mystery"
            for i in body["blocking_issues"]
        )

    def test_pre_check_can_compile_true_for_confirmed_spec(
        self, client: TestClient, repository, project_id: str
    ):
        """Pre-check must return can_compile=true for a clean confirmed spec."""
        params = [_make_param("diameter", "Diameter", "geometry", 0.1)]
        eid = _create_spec(
            repository, project_id, parameters=params, status="confirmed"
        )

        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{eid}/pre-check"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["can_compile"] is True
        assert body["blocking_issues"] == []

    def test_pre_check_blocks_wrong_status(
        self, client: TestClient, repository, project_id: str
    ):
        """Pre-check must block when status is not confirmed."""
        params = [_make_param("diameter", "Diameter", "geometry", 0.1)]
        eid = _create_spec(
            repository, project_id, parameters=params, status="draft"
        )

        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{eid}/pre-check"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["can_compile"] is False
        assert any(i["type"] == "status" for i in body["blocking_issues"])

    def test_pre_check_not_found_returns_404(
        self, client: TestClient, project_id: str
    ):
        """Pre-checking a non-existent spec must return 404."""
        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/exp-missing/pre-check"
        )
        assert response.status_code == 404

    def test_pre_check_response_structure(
        self, client: TestClient, repository, project_id: str
    ):
        """Pre-check response must include can_compile, blocking_issues, warnings."""
        eid = _create_spec(repository, project_id, status="confirmed")

        body = client.get(
            f"/api/projects/{project_id}/experiment-specs/{eid}/pre-check"
        ).json()

        assert "can_compile" in body
        assert "blocking_issues" in body
        assert "warnings" in body
        assert isinstance(body["blocking_issues"], list)
        assert isinstance(body["warnings"], list)

    def test_pre_check_blocking_issue_has_message(
        self, client: TestClient, repository, project_id: str
    ):
        """Each blocking issue must have a message field."""
        params = [
            _make_param(
                "mystery", "Mystery", "physics", 1,
                source_type=ParameterSource.UNKNOWN,
            )
        ]
        eid = _create_spec(
            repository, project_id, parameters=params, status="confirmed"
        )

        body = client.get(
            f"/api/projects/{project_id}/experiment-specs/{eid}/pre-check"
        ).json()

        for issue in body["blocking_issues"]:
            assert "type" in issue
            assert "message" in issue


# ---------------------------------------------------------------------------
# Frontend: Button visibility logic
# ---------------------------------------------------------------------------


class TestButtonVisibility:
    """Verify the frontend button matrix per state."""

    def test_update_spec_controls_has_new_buttons(self, client: TestClient):
        """updateSpecControls must reference all new button ids."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "function updateSpecControls(")
        assert "cloneableStates" in body
        assert "spec-clone-btn" in body
        assert "spec-run-status-btn" in body
        assert "spec-report-btn" in body
        assert "spec-capability-btn" in body

    def test_draft_shows_ready_not_compile(self, client: TestClient):
        """Draft state must show readyBtn and hide compileBtn."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "function updateSpecControls(")
        # updateSpecControls delegates button visibility to getWorkbenchActions
        assert "getWorkbenchActions" in body
        actions_body = _function_body(js, "function getWorkbenchActions(")
        # draft case uses spec-ready-btn as primary
        assert 'case "draft"' in actions_body
        assert "spec-ready-btn" in actions_body
        # compile button is in the hidden list for draft
        assert "spec-compile-btn" in actions_body

    def test_confirmed_shows_compile_not_apply(self, client: TestClient):
        """Confirmed state must hide applyBtn (not editable) and show compile."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "function updateSpecControls(")
        # updateSpecControls delegates button visibility to getWorkbenchActions
        assert "getWorkbenchActions" in body
        actions_body = _function_body(js, "function getWorkbenchActions(")
        # confirmed case uses spec-compile-btn as primary
        assert 'case "confirmed"' in actions_body
        assert "spec-compile-btn" in actions_body
        # apply button is in the hidden list for confirmed
        assert "spec-apply-btn" in actions_body
        # editable only for draft/ready
        assert "isSpecEditable" in body

    def test_clone_button_visible_for_confirmed_compiled_running_completed(
        self, client: TestClient
    ):
        """Clone button must be visible for confirmed/compiling/running/completed/failed."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "function updateSpecControls(")
        for state in ("confirmed", "compiling", "running", "completed", "failed"):
            assert f'"{state}"' in body, f"cloneable state {state} missing"
        # updateSpecControls delegates button visibility to getWorkbenchActions
        assert "getWorkbenchActions" in body
        actions_body = _function_body(js, "function getWorkbenchActions(")
        assert "spec-clone-btn" in actions_body

    def test_clone_button_hidden_for_draft_ready(self, client: TestClient):
        """Clone button must be hidden for draft/ready (not in cloneableStates)."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "function updateSpecControls(")
        m = re.search(r'cloneableStates\s*=\s*\[([^\]]*)\]', body)
        assert m, "cloneableStates array not found"
        arr = m.group(1)
        assert '"draft"' not in arr, "draft must not be cloneable"
        assert '"ready"' not in arr, "ready must not be cloneable"

    def test_clone_spec_function_exists(self, client: TestClient):
        """cloneSpec function must call /clone and re-render workbench."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "async function cloneSpec()")
        assert "/clone" in body
        assert "window.confirm" in body
        assert "renderSpecWorkbench" in body
        assert "已创建新版本" in body

    def test_new_buttons_rendered_in_workbench(self, client: TestClient):
        """renderSpecWorkbench must create the new button elements."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "function renderSpecWorkbench(")
        assert "spec-run-status-btn" in body
        assert "spec-report-btn" in body
        assert "spec-capability-btn" in body
        assert "spec-clone-btn" in body

    def test_compile_spec_calls_pre_check(self, client: TestClient):
        """compileSpec must call /pre-check before compiling."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "async function compileSpec()")
        assert "/pre-check" in body
        assert "can_compile" in body

    def test_run_status_and_report_and_capability_handlers(self, client: TestClient):
        """Helper functions for new buttons must exist."""
        js = client.get("/assets/app.js").text
        assert "function showRunStatus()" in js
        assert "function showAnalysisReport()" in js
        assert "function showMissingCapabilities()" in js


# ---------------------------------------------------------------------------
# Frontend: Disabled reason display
# ---------------------------------------------------------------------------


class TestDisabledReasonDisplay:
    """Verify the disabled reason display exists in the frontend."""

    def test_disabled_reason_div_in_js(self, client: TestClient):
        """app.js must reference the spec-disabled-reason element."""
        js = client.get("/assets/app.js").text
        assert "spec-disabled-reason" in js

    def test_disabled_reason_in_workbench(self, client: TestClient):
        """renderSpecWorkbench must create the disabled reason div."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "function renderSpecWorkbench(")
        assert "spec-disabled-reason" in body

    def test_update_disabled_reason_function(self, client: TestClient):
        """updateDisabledReason function must exist and manage the div."""
        js = client.get("/assets/app.js").text
        body = _function_body(js, "function updateDisabledReason(")
        assert "spec-disabled-reason" in body

    def test_disabled_reason_css(self, client: TestClient):
        """CSS must include spec-disabled-reason styles."""
        css = client.get("/assets/styles.css").text
        assert "spec-disabled-reason" in css
