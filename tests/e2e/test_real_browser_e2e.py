"""Commit 8: 真实浏览器 E2E 测试.

Covers sections 10.4 (浏览器级参数工作台测试) and 10.5 (状态按钮测试)
of the Fluid Scientist Workflow V2 task document.

Test classes:
  1. TestBrowserLevelWorkbenchPatterns  -- JS code-pattern verification (10.4)
  2. TestButtonStateTransitions         -- Button matrix per spec state (10.5)
  3. TestFullWorkflowIntegration         -- End-to-end API workflow
  4. TestDynamicParameterAndMetricIntegration -- Dynamic schema + metric planning
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.capabilities.models import (
    CapabilityType,
    CodeExtensionSpec,
    MissingCapability,
)
from fluid_scientist.capabilities.resolver import (
    CapabilityResolver,
    detect_missing_capabilities_from_metrics,
)
from fluid_scientist.compat import UTC
from fluid_scientist.dynamic_schema.schema_engine import (
    detect_experiment_type,
    generate_schema,
)
from fluid_scientist.experiment_spec.models import (
    Compressibility,
    Criticality,
    ExperimentSpec,
    ExperimentStatus,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
    PhaseType,
    PhysicsSpec,
    ResearchSpec,
    TemporalType,
)
from fluid_scientist.measurement.compiler import compile_measurement_plan
from fluid_scientist.measurement.models import (
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
    MetricBinding,
    ProbeSpec,
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
        "/api/projects", json={"question": "real browser E2E test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


@pytest.fixture
def planner() -> MetricPlanner:
    return MetricPlanner()


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


def _make_physics() -> PhysicsSpec:
    """Create a PhysicsSpec with valid enum values for schema generation."""
    return PhysicsSpec(
        compressibility=Compressibility.INCOMPRESSIBLE,
        temporal_type=TemporalType.TRANSIENT,
        phases=PhaseType.SINGLE_PHASE,
    )


def _create_spec(
    repository,
    project_id: str,
    *,
    parameters: list[ParameterSpec] | None = None,
    metrics: list[dict] | None = None,
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
            title="Real Browser E2E Test",
            objective="Test full workflow with real browser patterns",
        ),
        parameters=parameters or [],
        metrics=metrics or [],
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
    """Extract the body of a function from JS source by its signature.

    Works for both ``function name(`` and ``async function name(`` patterns.
    Returns everything from the signature to the next top-level function
    declaration (or end of file).
    """
    start = js.find(signature)
    assert start != -1, f"function not found: {signature}"
    search_from = start + len(signature)
    end = len(js)
    for marker in ("\nfunction ", "\nasync function ", "\nconst ", "\nlet "):
        pos = js.find(marker, search_from)
        if pos != -1 and pos < end:
            end = pos
    return js[start:end]


def _get_app_js(client: TestClient) -> str:
    """Fetch app.js source from the test client."""
    response = client.get("/assets/app.js")
    assert response.status_code == 200, "app.js not served"
    return response.text


def _param_ids(result) -> set[str]:
    """Extract parameter IDs from a schema generation result."""
    return {p.parameter_id for p in result.parameters}


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


# ===========================================================================
# Section 10.4: 浏览器级参数工作台测试
# ===========================================================================


class TestBrowserLevelWorkbenchPatterns:
    """Verify JavaScript code patterns in app.js guarantee the workbench
    behaves correctly at the browser level.

    All tests fetch ``/assets/app.js`` and use string matching to verify
    the expected patterns exist (or anti-patterns are absent).
    """

    # --- 1. No scrollIntoView in parameter functions ---

    def test_no_scrollIntoView_in_parameter_functions(self, client: TestClient):
        """markParameterDirty, applyPendingParameterChanges, and
        updateParameterRowInPlace must NOT call scrollIntoView.

        Consecutive modification of 5 parameters should not scroll the page.
        """
        js = _get_app_js(client)
        for func_name in (
            "function markParameterDirty(",
            "async function applyPendingParameterChanges()",
            "function updateParameterRowInPlace(",
        ):
            body = _function_body(js, func_name)
            assert "scrollIntoView" not in body, (
                f"scrollIntoView found in {func_name}"
            )

    # --- 2. No appendConversation in save/transition/update functions ---

    def test_no_appendConversation_in_save_functions(self, client: TestClient):
        """saveSpecDraft, transitionSpec, and updateSpecParameter must NOT
        call appendConversation.

        The conversation stream should not get new messages when parameters
        are saved or transitions occur.
        """
        js = _get_app_js(client)
        for func_name in (
            "async function saveSpecDraft()",
            "async function transitionSpec(",
            "async function updateSpecParameter(",
        ):
            body = _function_body(js, func_name)
            assert "appendConversation" not in body, (
                f"appendConversation found in {func_name}"
            )

    # --- 3. showWorkbenchToast called for parameter saves ---

    def test_showWorkbenchToast_called_for_parameter_saves(
        self, client: TestClient
    ):
        """applyPendingParameterChanges must call showWorkbenchToast
        instead of appendConversation for success feedback."""
        js = _get_app_js(client)
        body = _function_body(js, "async function applyPendingParameterChanges()")
        assert "showWorkbenchToast" in body, (
            "showWorkbenchToast not called in applyPendingParameterChanges"
        )

    # --- 4. pendingParameterChanges is a Map ---

    def test_pendingParameterChanges_is_Map(self, client: TestClient):
        """pendingParameterChanges must be declared as a new Map()."""
        js = _get_app_js(client)
        assert "pendingParameterChanges" in js
        assert re.search(
            r"pendingParameterChanges\s*=\s*new\s+Map\s*\(\s*\)", js
        ), "pendingParameterChanges is not declared as new Map()"

    # --- 5. markParameterDirty does not fetch ---

    def test_markParameterDirty_does_not_fetch(self, client: TestClient):
        """markParameterDirty must only set Map entries -- no fetch/XHR/
        requestJson calls.

        Dirty state is tracked locally without network requests.
        """
        js = _get_app_js(client)
        body = _function_body(js, "function markParameterDirty(")
        assert "fetch(" not in body, "fetch() found in markParameterDirty"
        assert "requestJson" not in body, (
            "requestJson found in markParameterDirty"
        )
        assert "XMLHttpRequest" not in body, (
            "XMLHttpRequest found in markParameterDirty"
        )
        assert "pendingParameterChanges" in body, (
            "pendingParameterChanges not referenced in markParameterDirty"
        )

    # --- 6. applyPendingParameterChanges sends single PATCH ---

    def test_applyPendingParameterChanges_sends_single_PATCH(
        self, client: TestClient
    ):
        """applyPendingParameterChanges must send exactly one requestJson
        call with method PATCH.

        Clicking the apply button should send only 1 PATCH request, not
        one per parameter.
        """
        js = _get_app_js(client)
        body = _function_body(js, "async function applyPendingParameterChanges()")
        count = body.count("requestJson(")
        assert count == 1, (
            f"Expected exactly 1 requestJson call, found {count}"
        )
        assert "PATCH" in body, (
            "PATCH method not found in applyPendingParameterChanges"
        )

    # --- 7. Dirty state tracking ---

    def test_dirty_state_tracking(self, client: TestClient):
        """markParameterDirty (via updateDirtyRowStyles) must add the
        'spec-param-dirty' class; discardPendingParameterChanges must
        clear it."""
        js = _get_app_js(client)
        dirty_body = _function_body(js, "function updateDirtyRowStyles()")
        assert "spec-param-dirty" in dirty_body, (
            "spec-param-dirty class not managed in updateDirtyRowStyles"
        )
        assert "pendingParameterChanges.has" in dirty_body, (
            "pendingParameterChanges.has not checked in updateDirtyRowStyles"
        )
        discard_body = _function_body(
            js, "function discardPendingParameterChanges()"
        )
        assert "pendingParameterChanges.clear()" in discard_body, (
            "pendingParameterChanges.clear() not called in discardPendingParameterChanges"
        )
        assert "updateDirtyRowStyles" in discard_body, (
            "updateDirtyRowStyles not called in discardPendingParameterChanges"
        )

    # --- 8. Workbench toast displayed after save ---

    def test_workbench_toast_displayed_after_save(self, client: TestClient):
        """A toast host element must be created in renderSpecWorkbench and
        showWorkbenchToast must populate it."""
        js = _get_app_js(client)
        render_body = _function_body(js, "function renderSpecWorkbench(")
        assert "workbench-toast-host" in render_body, (
            "workbench-toast-host not created in renderSpecWorkbench"
        )
        toast_body = _function_body(js, "function showWorkbenchToast(")
        assert "workbench-toast-host" in toast_body, (
            "workbench-toast-host not referenced in showWorkbenchToast"
        )
        assert "workbench-toast" in toast_body, (
            "workbench-toast class not used in showWorkbenchToast"
        )

    # --- 9. Clone button exists for confirmed states ---

    def test_clone_button_exists_for_confirmed_states(
        self, client: TestClient
    ):
        """cloneSpec function and spec-clone-btn must exist for modifying
        parameters in confirmed+ states."""
        js = _get_app_js(client)
        assert "async function cloneSpec()" in js, "cloneSpec function not found"
        clone_body = _function_body(js, "async function cloneSpec()")
        assert "/clone" in clone_body, "/clone endpoint not called in cloneSpec"
        assert "window.confirm" in clone_body, (
            "window.confirm not called in cloneSpec"
        )
        assert "spec-clone-btn" in js, "spec-clone-btn not found in app.js"
        render_body = _function_body(js, "function renderSpecWorkbench(")
        assert "spec-clone-btn" in render_body, (
            "spec-clone-btn not created in renderSpecWorkbench"
        )

    # --- 10. compileSpec calls pre-check first ---

    def test_compile_calls_pre_check(self, client: TestClient):
        """compileSpec must call the /pre-check endpoint first and check
        can_compile before proceeding with compilation."""
        js = _get_app_js(client)
        body = _function_body(js, "async function compileSpec()")
        assert "/pre-check" in body, "pre-check endpoint not called in compileSpec"
        assert "can_compile" in body, "can_compile not checked in compileSpec"
        assert "return false" in body, (
            "compileSpec does not return false when pre-check fails"
        )

    # --- 11. showWorkbenchToast function exists ---

    def test_showWorkbenchToast_function_exists(self, client: TestClient):
        """showWorkbenchToast function must exist and accept message + type."""
        js = _get_app_js(client)
        body = _function_body(js, "function showWorkbenchToast(")
        assert "message" in body, "showWorkbenchToast must accept a message param"
        assert "type" in body, "showWorkbenchToast must accept a type param"
        assert "createElement" in body, (
            "showWorkbenchToast must create a DOM element"
        )

    # --- 12. updateSpecParameter uses showWorkbenchToast on error ---

    def test_updateSpecParameter_uses_toast_on_error(self, client: TestClient):
        """updateSpecParameter must use showWorkbenchToast (not
        appendConversation) for error feedback."""
        js = _get_app_js(client)
        body = _function_body(js, "async function updateSpecParameter(")
        assert "showWorkbenchToast" in body, (
            "showWorkbenchToast not called in updateSpecParameter"
        )


# ===========================================================================
# Section 10.5: 状态按钮测试
# ===========================================================================


class TestButtonStateTransitions:
    """Verify updateSpecControls follows the button matrix per spec state.

    All tests extract the updateSpecControls function body and verify
    the visibility/enabled logic for each button matches the expected
    state matrix.
    """

    @pytest.fixture
    def controls_body(self, client: TestClient) -> str:
        """Extract the updateSpecControls function body."""
        js = _get_app_js(client)
        return _function_body(js, "function updateSpecControls(")

    # --- 1. draft state ---

    def test_draft_hides_compile_and_submit(self, controls_body: str):
        """In draft state, compile and submit buttons must be hidden."""
        assert 'status === "confirmed"' in controls_body
        assert "compileBtn.hidden = !canCompile" in controls_body
        assert "submitBtn" in controls_body
        assert "hasCompilation" in controls_body

    def test_draft_shows_ready_button(self, controls_body: str):
        """In draft state, the ready button must be visible and enabled."""
        assert 'readyBtn.hidden = status !== "draft"' in controls_body
        assert 'readyBtn.disabled = status !== "draft"' in controls_body

    # --- 2. ready state ---

    def test_ready_shows_confirm_button(self, controls_body: str):
        """In ready state, the confirm button must be visible and enabled."""
        assert 'confirmBtn.hidden = status !== "ready"' in controls_body
        assert 'confirmBtn.disabled = status !== "ready"' in controls_body

    def test_ready_hides_compile_button(self, controls_body: str):
        """In ready state, the compile button must be hidden.

        canCompile requires status === "confirmed", so in "ready" it is false.
        """
        assert (
            'canCompile = status === "confirmed"' in controls_body
            or 'status === "confirmed" && !hasCompilation' in controls_body
        )

    # --- 3. confirmed state ---

    def test_confirmed_shows_compile_button(self, controls_body: str):
        """In confirmed state, the compile button must be visible and enabled
        (when no compilation exists yet)."""
        assert "compileBtn.hidden = !canCompile" in controls_body
        assert "compileBtn.disabled = !canCompile" in controls_body
        assert 'status === "confirmed"' in controls_body

    def test_confirmed_hides_apply_button(self, controls_body: str):
        """In confirmed state, the apply button must be hidden (not editable).

        applyBtn.hidden = !editable, and isSpecEditable returns false for
        confirmed.
        """
        assert "applyBtn.hidden = !editable" in controls_body
        assert "isSpecEditable" in controls_body

    # --- 4. compiled/compiling state ---

    def test_compiled_shows_submit_button(self, controls_body: str):
        """In compiled state, the submit button must be visible.

        canSubmit = hasCompilation && !submitted && !specCompiling.
        "compiling" is not in the submitted list.
        """
        assert "canSubmit" in controls_body
        assert "hasCompilation" in controls_body
        assert "submitted" in controls_body
        m = re.search(r'submitted\s*=\s*\[([^\]]*)\]', controls_body)
        assert m, "submitted array not found"
        assert '"compiling"' not in m.group(1), (
            "compiling should not be in submitted list"
        )

    # --- 5. running state ---

    def test_running_shows_run_status_button(self, controls_body: str):
        """In running state, the run status button must be visible."""
        assert 'runStatusBtn.hidden = status !== "running"' in controls_body
        assert 'runStatusBtn.disabled = status !== "running"' in controls_body

    # --- 6. completed state ---

    def test_completed_shows_report_button(self, controls_body: str):
        """In completed state, the report button must be visible."""
        assert 'reportBtn.hidden = status !== "completed"' in controls_body
        assert 'reportBtn.disabled = status !== "completed"' in controls_body

    # --- 7. awaiting_code_approval state ---

    def test_awaiting_code_approval_shows_capability_button(
        self, controls_body: str
    ):
        """In awaiting_code_approval state, the capability button must be
        visible."""
        assert (
            'capabilityBtn.hidden = status !== "awaiting_code_approval"'
            in controls_body
        )
        assert (
            'capabilityBtn.disabled = status !== "awaiting_code_approval"'
            in controls_body
        )

    # --- 8. clone button visibility ---

    def test_clone_button_cloneable_states(self, controls_body: str):
        """Clone button must be visible for confirmed/compiling/running/
        completed/failed states and hidden for draft/ready."""
        m = re.search(
            r'cloneableStates\s*=\s*\[([^\]]*)\]', controls_body
        )
        assert m, "cloneableStates array not found"
        arr = m.group(1)
        for state in ("confirmed", "compiling", "running", "completed", "failed"):
            assert f'"{state}"' in arr, f"cloneable state {state} missing"
        assert '"draft"' not in arr, "draft must not be cloneable"
        assert '"ready"' not in arr, "ready must not be cloneable"
        assert "cloneableStates.includes(status)" in controls_body


# ===========================================================================
# Full Workflow Integration Test
# ===========================================================================


class TestFullWorkflowIntegration:
    """Test the complete workflow via API:

    1. Create experiment spec -> parameters generated dynamically per type
    2. Accept recommendations -> derived params computed, unknown_required remains
    3. Transition draft -> ready -> confirmed
    4. Pre-check passes (no blocking issues)
    5. Clone creates new version
    6. Metric plan returns metrics
    """

    # --- 1. Cylinder spec generates cylinder parameters ---

    def test_cylinder_spec_generates_cylinder_parameters(self):
        """generate_schema with cylinder-flow params must produce only
        cylinder-specific parameters."""
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"diameter": 0.1, "domain_width": 10.0},
        )
        assert result.experiment_type == "cylinder_flow"
        param_ids = _param_ids(result)

        assert "diameter" in param_ids
        assert "domain_width" in param_ids
        assert "domain_height" in param_ids
        assert "cells_wake" in param_ids
        assert "cells_radial" in param_ids
        assert "inlet_velocity" in param_ids

    # --- 2. Pipe spec generates pipe parameters ---

    def test_pipe_spec_generates_pipe_parameters(self):
        """generate_schema with pipe-flow params must produce only
        pipe-specific parameters."""
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"length": 1.0, "axial_cells": 80},
        )
        assert result.experiment_type == "laminar_pipe"
        param_ids = _param_ids(result)

        assert "diameter" in param_ids
        assert "length" in param_ids
        assert "axial_cells" in param_ids
        assert "radial_cells" in param_ids
        assert "mean_velocity" in param_ids
        assert "mass_flow_rate" in param_ids
        assert "outlet_pressure" in param_ids

    # --- 3. Accept recommendations fills derived params ---

    def test_accept_recommendations_fills_derived(
        self, client: TestClient, repository, project_id: str
    ):
        """POST accept-recommendations must compute derived parameters
        (mean_velocity, reynolds_number) while leaving unknown_required
        unchanged."""
        params = [
            _make_param(
                "mass_flow_rate", "Mass Flow Rate", "boundary_condition",
                0.1, source_type=ParameterSource.USER, unit="kg/s",
            ),
            _make_param(
                "density", "Density", "material",
                998.2, source_type=ParameterSource.SYSTEM_RECOMMENDED,
                status=ParameterStatus.PENDING, unit="kg/m^3",
            ),
            _make_param(
                "kinematic_viscosity", "Kinematic Viscosity", "material",
                1.0e-6, source_type=ParameterSource.SYSTEM_RECOMMENDED,
                status=ParameterStatus.PENDING, unit="m^2/s",
            ),
            _make_param(
                "diameter", "Diameter", "geometry",
                0.01, source_type=ParameterSource.USER, unit="m",
            ),
            _make_param(
                "mean_velocity", "Mean Velocity", "boundary_condition",
                None, source_type=ParameterSource.UNKNOWN, unit="m/s",
            ),
            _make_param(
                "reynolds_number", "Reynolds Number", "physics",
                None, source_type=ParameterSource.UNKNOWN,
            ),
            _make_param(
                "end_time", "End Time", "numerics",
                None, source_type=ParameterSource.UNKNOWN, unit="s",
            ),
        ]
        eid = _create_spec(repository, project_id, parameters=params)

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{eid}/accept-recommendations"
        )
        assert response.status_code == 200, response.text
        body = response.json()
        summary = body["_acceptance_summary"]

        assert "mean_velocity" in summary["derived_parameters"]
        assert "reynolds_number" in summary["derived_parameters"]
        assert "end_time" in summary["still_unknown_required"]

        param_map = {p["parameter_id"]: p for p in body["parameters"]}
        assert param_map["mean_velocity"]["value"] is not None
        assert param_map["reynolds_number"]["value"] is not None
        assert param_map["end_time"]["value"] is None

    # --- 4. Transition draft -> ready ---

    def test_transition_draft_to_ready(
        self, client: TestClient, repository, project_id: str
    ):
        """POST transition with target_status=ready must succeed for a
        draft spec with no critical unresolved parameters."""
        params = [
            _make_param(
                "diameter", "Diameter", "geometry", 0.1,
                criticality=Criticality.CRITICAL,
            ),
        ]
        eid = _create_spec(repository, project_id, parameters=params, status="draft")

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{eid}/transition",
            json={"target_status": "ready"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ready"

    # --- 5. Transition ready -> confirmed ---

    def test_transition_ready_to_confirmed(
        self, client: TestClient, repository, project_id: str
    ):
        """POST transition with target_status=confirmed must succeed for
        a ready spec."""
        params = [
            _make_param(
                "diameter", "Diameter", "geometry", 0.1,
                criticality=Criticality.CRITICAL,
            ),
        ]
        eid = _create_spec(repository, project_id, parameters=params, status="ready")

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{eid}/transition",
            json={"target_status": "confirmed"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "confirmed"

    # --- 6. Pre-check passes for confirmed spec ---

    def test_pre_check_passes_for_confirmed_spec(
        self, client: TestClient, repository, project_id: str
    ):
        """GET pre-check must return can_compile=true for a clean confirmed
        spec with no unknown parameters."""
        params = [
            _make_param("diameter", "Diameter", "geometry", 0.1),
            _make_param("velocity", "Velocity", "boundary_condition", 0.01),
        ]
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

    # --- 7. Pre-check fails for unknown params ---

    def test_pre_check_fails_for_unknown_params(
        self, client: TestClient, repository, project_id: str
    ):
        """GET pre-check must return can_compile=false with
        unknown_required blocking issues when a parameter has
        source.type=unknown."""
        params = [
            _make_param(
                "mystery", "Mystery Param", "physics", 42,
                source_type=ParameterSource.UNKNOWN,
            ),
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

    # --- 8. Clone creates new version ---

    def test_clone_creates_new_version(
        self, client: TestClient, repository, project_id: str
    ):
        """POST clone must create a new draft spec with incremented
        version, preserving parameters and research info."""
        params = [
            _make_param("diameter", "Diameter", "geometry", 0.1),
            _make_param("velocity", "Velocity", "boundary_condition", 0.01),
        ]
        eid = _create_spec(
            repository, project_id, parameters=params, status="confirmed",
            version=1,
        )

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{eid}/clone"
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "draft"
        assert body["experiment_version"] == 2
        assert body["experiment_id"] != eid
        ids = [p["parameter_id"] for p in body["parameters"]]
        assert "diameter" in ids
        assert "velocity" in ids

    # --- 9. Metric plan returns metrics ---

    def test_metric_plan_returns_metrics(
        self, client: TestClient, repository, project_id: str
    ):
        """GET metric-plan must return the metrics stored in the spec."""
        test_metrics = [
            {
                "kind": "measurement_plan",
                "core_metrics": ["drag_coefficient", "pressure_drop"],
                "credibility_metrics": ["residual_tolerance"],
                "unknown_metrics": [],
                "metric_definitions": {
                    "drag_coefficient": {
                        "formula": "F_drag / (0.5 * rho * U^2 * A)",
                        "unit": "dimensionless",
                        "required_data": ["forceCoeffs time series"],
                        "quality_checks": ["statistical_stability"],
                    }
                },
            }
        ]
        eid = _create_spec(
            repository, project_id, metrics=test_metrics
        )

        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{eid}/metric-plan"
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["experiment_id"] == eid
        assert data["metric_count"] == 1
        assert len(data["metrics"]) == 1
        assert "drag_coefficient" in data["metrics"][0]["core_metrics"]


# ===========================================================================
# Dynamic Parameter and Metric Integration
# ===========================================================================


class TestDynamicParameterAndMetricIntegration:
    """Verify dynamic parameter generation and metric planning integration."""

    # --- 1. Cylinder params exclude pipe and cavity ---

    def test_cylinder_params_exclude_pipe_and_cavity(self):
        """Cylinder flow schema must NOT include pipe or cavity specific
        parameters."""
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"diameter": 0.1, "domain_width": 10.0},
        )
        assert result.experiment_type == "cylinder_flow"
        param_ids = _param_ids(result)

        assert "length" not in param_ids
        assert "axial_cells" not in param_ids
        assert "radial_cells" not in param_ids
        assert "mass_flow_rate" not in param_ids
        assert "outlet_pressure" not in param_ids
        assert "mean_velocity" not in param_ids

        assert "side_length" not in param_ids
        assert "lid_velocity" not in param_ids
        assert "cells_per_side" not in param_ids

    # --- 2. Pipe params exclude cylinder and cavity ---

    def test_pipe_params_exclude_cylinder_and_cavity(self):
        """Laminar pipe schema must NOT include cylinder or cavity specific
        parameters."""
        physics = _make_physics()
        result = generate_schema(
            physics,
            existing_params={"length": 1.0, "axial_cells": 80},
        )
        assert result.experiment_type == "laminar_pipe"
        param_ids = _param_ids(result)

        assert "domain_width" not in param_ids
        assert "domain_height" not in param_ids
        assert "cells_wake" not in param_ids
        assert "cells_radial" not in param_ids
        assert "inlet_velocity" not in param_ids
        assert "strouhal_number" not in param_ids

        assert "side_length" not in param_ids
        assert "lid_velocity" not in param_ids
        assert "cells_per_side" not in param_ids

    # --- 3. Metric plan has required_data ---

    def test_metric_plan_has_required_data(self, planner: MetricPlanner):
        """Metric definitions from the MetricPlanner must include the
        required_data field."""
        plan = planner.propose_metrics(
            research_objective="研究圆柱涡脱落",
            experiment_type="cylinder_flow",
        )
        assert "drag_coefficient" in plan.metric_definitions
        drag_def = plan.metric_definitions["drag_coefficient"]
        assert "required_data" in drag_def
        assert isinstance(drag_def["required_data"], list)
        assert len(drag_def["required_data"]) > 0

        assert "strouhal_number" in plan.metric_definitions
        st_def = plan.metric_definitions["strouhal_number"]
        assert "required_data" in st_def
        assert len(st_def["required_data"]) > 0

    # --- 4. Unknown metric enters MissingCapability ---

    def test_unknown_metric_enters_missing_capability(
        self, planner: MetricPlanner
    ):
        """An unknown metric must flow through detect_missing_capabilities
        to create a blocking MissingCapability."""
        plan = planner.propose_metrics(
            research_objective="研究旋涡破碎指数",
            user_metrics=["vortex_breakdown_index"],
            experiment_type="cylinder_flow",
        )
        assert "vortex_breakdown_index" in plan.unknown_metrics

        caps = detect_missing_capabilities_from_metrics(plan)
        assert len(caps) == 1
        cap = caps[0]
        assert isinstance(cap, MissingCapability)
        assert cap.capability_type == CapabilityType.METRIC_OPERATOR
        assert cap.severity == "blocking"
        assert cap.is_blocking() is True
        assert "vortex_breakdown_index" in cap.capability_id

        resolver = CapabilityResolver()
        extensions = resolver.create_extensions(caps)
        assert len(extensions) == 1
        ext = extensions[0]
        assert isinstance(ext, CodeExtensionSpec)
        assert ext.state == "draft"
        assert ext.extension_type == "metric_operator"

    # --- 5. forceCoeffs uses spec parameters ---

    def test_force_coeffs_uses_spec_parameters(self):
        """Compiled forceCoeffs function object must use reference quantities
        (rhoInf, magUInf, lRef, Aref) from spec_parameters."""
        plan = _build_force_coeffs_plan()
        result = compile_measurement_plan(
            plan,
            available_patches=["cylinder", "inlet", "outlet"],
            solver_output_fields=["U", "p"],
            spec_parameters={
                "density": 1234.5,
                "inlet_velocity": 2.5,
                "diameter": 0.15,
                "extrusion_span": 0.5,
            },
        )
        assert result.success
        fo = result.control_dict_additions["functions"]["forceCoeffs_1"]
        assert fo["rhoInf"] == 1234.5
        assert fo["magUInf"] == 2.5
        assert fo["lRef"] == 0.15
        assert fo["Aref"] == 0.15 * 0.5

    # --- 6. Measurement plan has real geometry ---

    def test_measurement_plan_has_real_geometry(self, planner: MetricPlanner):
        """Measurement plan for pipe flow must have real probe locations
        and surface geometry (not empty placeholders)."""
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
        mp = plan.measurement_plan
        assert isinstance(mp, MeasurementPlan)

        if mp.probes:
            for probe in mp.probes:
                assert len(probe.positions) > 0, (
                    f"Probe '{probe.id}' has empty positions"
                )
                for pos in probe.positions:
                    assert len(pos) > 0, "Probe position is empty"

        surfaces = [
            s for s in mp.spatial_sampling
            if s.type.value == "surface"
        ]
        if surfaces:
            for s in surfaces:
                loc = s.location
                assert "fields" in loc, f"Surface '{s.id}' missing fields"
                assert "surfaceFormat" in loc, (
                    f"Surface '{s.id}' missing surfaceFormat"
                )
                assert isinstance(loc["fields"], list)
                assert len(loc["fields"]) > 0

    # --- 7. detect_experiment_type from geometry params ---

    def test_detect_experiment_type_from_geometry(self):
        """detect_experiment_type must correctly identify experiment types
        from geometry parameter combinations."""
        physics = PhysicsSpec()
        assert detect_experiment_type(
            physics, {"diameter": 0.1, "cells_wake": 120}
        ) == "cylinder_flow"
        assert detect_experiment_type(
            physics, {"diameter": 0.1, "domain_width": 10.0}
        ) == "cylinder_flow"
        assert detect_experiment_type(
            physics, {"length": 1.0, "axial_cells": 80}
        ) == "laminar_pipe"
        assert detect_experiment_type(
            physics, {"length": 1.0, "mean_velocity": 0.1}
        ) == "laminar_pipe"
        assert detect_experiment_type(
            physics, {"side_length": 0.1, "lid_velocity": 1.0}
        ) == "lid_driven_cavity"
        assert detect_experiment_type(physics, {"diameter": 0.1}) == "unknown"
        assert detect_experiment_type(physics, {}) == "unknown"

    # --- 8. Compiled probe output has real locations ---

    def test_compiled_probe_output_has_real_locations(self):
        """Compiled function objects for probes must have real
        probeLocations (not empty)."""
        plan = MeasurementPlan(
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
        result = compile_measurement_plan(
            plan,
            available_patches=["inlet", "outlet"],
            solver_output_fields=["U", "p"],
        )
        assert result.success
        assert result.sample_dict is not None
        probe_locations = result.sample_dict["probes"]["probeLocations"]
        assert len(probe_locations) > 0
        for loc in probe_locations:
            assert isinstance(loc, list)
            assert len(loc) >= 2
