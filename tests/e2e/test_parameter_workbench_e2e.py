"""Comprehensive E2E tests for the parameter workbench workflow.

Commit 7: End-to-end tests covering the full parameter workbench workflow
including API-level integration, frontend interaction patterns, pre-fill
quality, batch save integration, and natural language edit integration.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_planning.models import (
    CavityExperimentPlan,
    ConvergenceTargets,
    CylinderExperimentPlan,
    CylinderFlowCase,
    LaminarPipeCase,
    LidDrivenCavityCase,
    PipeExperimentPlan,
)
from fluid_scientist.experiment_spec.migration import migrate_plan
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
        "/api/projects", json={"question": "e2e parameter workbench test"}
    )
    assert response.status_code == 201
    return response.json()["project_id"]


# ---------------------------------------------------------------------------
# Helpers: plan creation
# ---------------------------------------------------------------------------


def _convergence_targets() -> ConvergenceTargets:
    return ConvergenceTargets(residual_tolerance=1e-6, mass_imbalance_percent=0.1)


def _make_cylinder_plan() -> CylinderExperimentPlan:
    """Build a valid CylinderExperimentPlan for migration tests."""
    case = CylinderFlowCase(
        diameter_m=0.1,
        reynolds_number=100.0,
        end_time_s=10.0,
        density_kg_m3=998.2,
        kinematic_viscosity_m2_s=1.0e-6,
        mean_velocity_m_s=0.001,
        max_courant=0.5,
    )
    return CylinderExperimentPlan(
        experiment_name="Cylinder Flow E2E",
        objective="E2E test of cylinder flow workflow",
        rationale="Benchmark validation for laminar flow regime",
        assumptions=("2D flow", "incompressible"),
        limitations=("laminar only",),
        requested_outputs=("drag_coefficient", "strouhal_number"),
        convergence_targets=_convergence_targets(),
        case=case,
        experiment_type="cylinder_flow",
    )


def _make_pipe_plan() -> PipeExperimentPlan:
    """Build a valid PipeExperimentPlan for migration tests."""
    case = LaminarPipeCase(
        diameter_m=0.01,
        length_m=1.0,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1.0e-6,
        density_kg_m3=998.2,
    )
    return PipeExperimentPlan(
        experiment_name="Laminar Pipe E2E",
        objective="E2E test of laminar pipe workflow",
        rationale="Classic benchmark for laminar pressure-loss validation",
        assumptions=("steady flow", "fully developed"),
        limitations=("laminar only",),
        requested_outputs=("pressure_drop",),
        convergence_targets=_convergence_targets(),
        case=case,
        experiment_type="laminar_pipe",
    )


def _make_cavity_plan() -> CavityExperimentPlan:
    """Build a valid CavityExperimentPlan for migration tests."""
    case = LidDrivenCavityCase(
        side_length_m=0.1,
        lid_velocity_m_s=1.0,
        kinematic_viscosity_m2_s=0.01,
        density_kg_m3=1.0,
        end_time_s=10.0,
    )
    return CavityExperimentPlan(
        experiment_name="Lid-Driven Cavity E2E",
        objective="E2E test of lid-driven cavity workflow",
        rationale="Standard CFD validation case for viscous flow",
        assumptions=("2D flow", "incompressible"),
        limitations=("laminar only",),
        requested_outputs=("velocity_probes",),
        convergence_targets=_convergence_targets(),
        case=case,
        experiment_type="lid_driven_cavity",
    )


# ---------------------------------------------------------------------------
# Helpers: spec creation
# ---------------------------------------------------------------------------


def _save_migrated_spec(
    repository, spec: ExperimentSpec, project_id: str
) -> str:
    """Save a migrated ExperimentSpec to the repository.

    Returns the experiment_id.
    """
    now = datetime.now(UTC).isoformat()
    stored = StoredExperimentSpec(
        experiment_id=spec.experiment_id,
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
    return spec.experiment_id


def _make_param(
    parameter_id: str,
    display_name: str,
    category: str,
    value: float | int | str,
    *,
    unit: str | None = None,
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
        unit=unit,
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
            title="E2E Workbench Test",
            objective="Test full parameter workbench workflow end-to-end",
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


# ---------------------------------------------------------------------------
# Helpers: JS source inspection
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


# ===========================================================================
# Test Category 1: Full Workflow E2E (API level)
# ===========================================================================


class TestFullWorkflowE2E:
    """End-to-end test of the full parameter workbench workflow."""

    def test_full_workflow_create_prefill_batch_update_nl_edit(
        self, client: TestClient, repository, project_id: str
    ):
        """Test: create project -> migrate plan -> pre-fill -> batch update -> NL edit."""
        # 1. Create a cylinder plan and migrate to spec
        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "e2e-spec-001", project_id)
        experiment_id = _save_migrated_spec(repository, spec, project_id)

        # 2. Verify spec parameters are pre-filled with recommendations
        response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}"
        )
        assert response.status_code == 200, response.text
        spec_data = response.json()
        assert len(spec_data["parameters"]) > 0

        # Check that some parameters have system_recommended source
        recommended = [
            p
            for p in spec_data["parameters"]
            if p.get("source", {}).get("type") == "system_recommended"
        ]
        assert len(recommended) > 0, "Expected at least one system_recommended parameter"

        # Verify density and kinematic_viscosity have values
        params_map = {p["parameter_id"]: p for p in spec_data["parameters"]}
        assert params_map["density"]["value"] is not None
        assert params_map["kinematic_viscosity"]["value"] is not None

        # 3. Batch update multiple parameters
        original_version = spec_data["experiment_version"]
        batch_response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": original_version,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.2},
                    {"parameter_id": "reynolds_number", "value": 200},
                ],
            },
        )
        assert batch_response.status_code == 200, batch_response.text
        batch_body = batch_response.json()

        # 4. Verify response includes direct_updates, derived_updates, invalidated
        propagation = batch_body["_batch_propagation"]
        assert "direct_updates" in propagation
        assert "derived_updates" in propagation
        assert "invalidated" in propagation

        # direct_updates should have entries for both updated parameters
        direct_ids = [d["parameter_id"] for d in propagation["direct_updates"]]
        assert "diameter" in direct_ids
        assert "reynolds_number" in direct_ids

        # Each direct_update should have old_value and new_value
        for d in propagation["direct_updates"]:
            assert "old_value" in d
            assert "new_value" in d

        # derived_updates should be a list (may have inlet_velocity)
        assert isinstance(propagation["derived_updates"], list)

        # invalidated should be a list
        assert isinstance(propagation["invalidated"], list)

        # 5. Verify version is present and valid
        new_version = batch_body["experiment_version"]
        assert isinstance(new_version, int)
        assert new_version >= original_version

        # 6. Natural language edit on the updated spec
        nl_response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": new_version,
                "instruction": "把管径改成50毫米",
            },
        )
        assert nl_response.status_code == 200, nl_response.text
        nl_body = nl_response.json()

        # 7. Verify NL proposed changes match expected parameters
        proposed = nl_body["proposed_changes"]
        assert len(proposed) == 1
        assert proposed[0]["parameter_id"] == "diameter"
        assert proposed[0]["new_value"] == pytest.approx(0.05)

        # NL edit must not modify the spec
        get_after_nl = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}"
        )
        assert get_after_nl.status_code == 200
        params_after_nl = {
            p["parameter_id"]: p for p in get_after_nl.json()["parameters"]
        }
        # diameter should still be 0.2 (from batch update, not NL proposal)
        assert params_after_nl["diameter"]["value"] == 0.2


# ===========================================================================
# Test Category 2: Frontend Interaction Patterns (Static code analysis)
# ===========================================================================


class TestFrontendInteractionPatterns:
    """Verify app.js source code has correct interaction patterns."""

    def test_save_spec_draft_does_not_call_append_conversation(
        self, client: TestClient
    ):
        """saveSpecDraft must NOT call appendConversation; use showWorkbenchToast."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "async function saveSpecDraft()")
        assert "appendConversation" not in body, (
            "saveSpecDraft must not call appendConversation"
        )
        assert "showWorkbenchToast" in body, (
            "saveSpecDraft must use showWorkbenchToast"
        )

    def test_transition_spec_does_not_call_append_conversation(
        self, client: TestClient
    ):
        """transitionSpec must NOT call appendConversation; use showWorkbenchToast."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "async function transitionSpec(")
        assert "appendConversation" not in body, (
            "transitionSpec must not call appendConversation"
        )
        assert "showWorkbenchToast" in body, (
            "transitionSpec must use showWorkbenchToast"
        )

    def test_update_spec_parameter_does_not_call_render_error(
        self, client: TestClient
    ):
        """updateSpecParameter must NOT call renderError; use showWorkbenchToast."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "async function updateSpecParameter(")
        assert "renderError" not in body, (
            "updateSpecParameter must not call renderError"
        )
        assert "showWorkbenchToast" in body, (
            "updateSpecParameter must use showWorkbenchToast"
        )

    def test_render_parameter_row_uses_input_event_with_mark_parameter_dirty(
        self, client: TestClient
    ):
        """renderParameterRow must use 'input' event with markParameterDirty,
        not 'change' with updateSpecParameter."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "function renderParameterRow(")
        assert "markParameterDirty" in body
        assert 'addEventListener("input"' in body
        assert 'addEventListener("change"' not in body

    def test_apply_pending_parameter_changes_sends_one_patch_request(
        self, client: TestClient
    ):
        """applyPendingParameterChanges must send exactly 1 PATCH to /parameters."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "async function applyPendingParameterChanges()")
        assert "/parameters" in body
        assert "PATCH" in body
        assert "updates" in body
        # Exactly one PATCH method in the function body
        assert body.count('method: "PATCH"') == 1, (
            "applyPendingParameterChanges must send exactly 1 PATCH request"
        )

    def test_apply_pending_parameter_changes_preserves_scroll_position(
        self, client: TestClient
    ):
        """applyPendingParameterChanges must preserve scroll position."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "async function applyPendingParameterChanges()")
        assert "savedScrollY" in body
        assert "window.scrollTo" in body

    def test_apply_pending_parameter_changes_does_not_call_append_conversation(
        self, client: TestClient
    ):
        """applyPendingParameterChanges must NOT call appendConversation."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "async function applyPendingParameterChanges()")
        assert "appendConversation" not in body, (
            "applyPendingParameterChanges must not call appendConversation"
        )

    def test_pending_parameter_changes_is_a_map(self, client: TestClient):
        """pendingParameterChanges must be a Map."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert "pendingParameterChanges" in js
        assert "new Map()" in js

    def test_show_workbench_toast_creates_workbench_toast_class(
        self, client: TestClient
    ):
        """showWorkbenchToast must create elements with 'workbench-toast' class."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "function showWorkbenchToast(")
        assert "workbench-toast" in body

    def test_update_parameter_row_in_place_does_not_call_render_parameter_row(
        self, client: TestClient
    ):
        """updateParameterRowInPlace must NOT call renderParameterRow."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "function updateParameterRowInPlace(")
        assert "renderParameterRow" not in body, (
            "updateParameterRowInPlace must not call renderParameterRow"
        )

    def test_render_batch_propagation_includes_change_css_classes(
        self, client: TestClient
    ):
        """renderBatchPropagation must include spec-change-old and spec-change-new."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "function renderBatchPropagation(")
        assert "spec-change-old" in body
        assert "spec-change-new" in body

    def test_workflow_mode_is_v2(self, client: TestClient):
        """workflowMode must be defined as 'v2'."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        assert 'const workflowMode = "v2";' in js

    def test_render_plan_card_calls_guarded_by_legacy_mode(
        self, client: TestClient
    ):
        """renderPlanCard(response) calls must be guarded by workflowMode === 'legacy'."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        # Check that every non-definition call to renderPlanCard(response)
        # is preceded by a workflowMode === "legacy" guard within 500 chars.
        pos = 0
        found_call = False
        while True:
            pos = js.find("renderPlanCard(response)", pos)
            if pos == -1:
                break
            # Skip function definitions
            prefix = js[max(0, pos - 20) : pos]
            if "function " in prefix:
                pos += 1
                continue
            found_call = True
            window = js[max(0, pos - 500) : pos]
            assert 'workflowMode === "legacy"' in window, (
                "renderPlanCard(response) must be guarded by workflowMode === 'legacy'"
            )
            pos += 1
        assert found_call, "Expected at least one renderPlanCard(response) call"

    def test_confirm_and_submit_plan_guarded_by_non_legacy(
        self, client: TestClient
    ):
        """confirmAndSubmitPlan must be guarded by workflowMode !== 'legacy'."""
        response = client.get("/assets/app.js")
        assert response.status_code == 200
        js = response.text
        body = _function_body(js, "async function confirmAndSubmitPlan(")
        assert 'workflowMode !== "legacy"' in body


# ===========================================================================
# Test Category 3: Pre-fill Quality
# ===========================================================================


class TestPrefillQuality:
    """Verify migrated specs from all three plan types have pre-filled recommendations."""

    def test_cylinder_plan_migration_density_and_viscosity_have_values(self):
        """Cylinder plan: density and kinematic_viscosity have values."""
        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "prefill-cyl-001")
        density = spec.get_parameter("density")
        assert density is not None
        assert density.value is not None
        kinematic_viscosity = spec.get_parameter("kinematic_viscosity")
        assert kinematic_viscosity is not None
        assert kinematic_viscosity.value is not None

    def test_pipe_plan_migration_density_has_value(self):
        """Pipe plan: density has a value."""
        plan = _make_pipe_plan()
        spec = migrate_plan(plan, "prefill-pipe-001")
        density = spec.get_parameter("density")
        assert density is not None
        assert density.value is not None

    def test_cavity_plan_migration_density_has_value(self):
        """Cavity plan: density has a value."""
        plan = _make_cavity_plan()
        spec = migrate_plan(plan, "prefill-cavity-001")
        density = spec.get_parameter("density")
        assert density is not None
        assert density.value is not None

    def test_cylinder_plan_has_system_recommended_with_reason_and_confidence(self):
        """At least some cylinder parameters have system_recommended source
        with reason and confidence."""
        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "prefill-rec-001")
        recommended = [
            p
            for p in spec.parameters
            if p.source.type == ParameterSource.SYSTEM_RECOMMENDED
        ]
        assert len(recommended) > 0, (
            "Expected at least one system_recommended parameter"
        )
        for p in recommended:
            assert p.source.reason is not None
            assert len(p.source.reason) > 0
            assert p.source.confidence is not None
            assert len(p.source.confidence) > 0

    def test_user_specified_parameters_have_user_source_and_pending_status(self):
        """User-specified parameters (diameter, reynolds_number) have source 'user'
        and status 'pending'."""
        plan = _make_cylinder_plan()
        spec = migrate_plan(plan, "prefill-user-001")
        for pid in ("diameter", "reynolds_number"):
            p = spec.get_parameter(pid)
            assert p is not None, f"parameter {pid} not found"
            assert p.source.type == ParameterSource.USER
            assert p.status.value == "pending"


# ===========================================================================
# Test Category 4: Batch Save Integration
# ===========================================================================


@pytest.fixture
def spec_with_batch_params(repository, project_id):
    """Create an experiment spec with 3+ parameters for batch save testing."""
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
        _make_param(
            "density",
            "Fluid Density",
            "material",
            998.2,
            criticality=Criticality.HIGH,
            source_type=ParameterSource.SYSTEM_RECOMMENDED,
        ),
    ]
    experiment_id = _create_spec(repository, project_id, parameters=parameters)
    return {
        "project_id": project_id,
        "experiment_id": experiment_id,
    }


class TestBatchSaveIntegration:
    """Test the full batch save flow with multiple parameters."""

    def test_batch_save_three_parameters_returns_all_ids(
        self, client: TestClient, spec_with_batch_params: dict
    ):
        """Batch update 3 parameters and verify updated_parameters has all 3 IDs."""
        project_id = spec_with_batch_params["project_id"]
        experiment_id = spec_with_batch_params["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.05},
                    {"parameter_id": "inlet_velocity", "value": 0.02},
                    {"parameter_id": "density", "value": 1000.0},
                ],
            },
        )
        assert response.status_code == 200, response.text
        propagation = response.json()["_batch_propagation"]

        assert "diameter" in propagation["updated_parameters"]
        assert "inlet_velocity" in propagation["updated_parameters"]
        assert "density" in propagation["updated_parameters"]
        assert len(propagation["updated_parameters"]) == 3

    def test_batch_save_returns_direct_updates_with_old_new_values(
        self, client: TestClient, spec_with_batch_params: dict
    ):
        """Batch save response must have direct_updates with old/new values."""
        project_id = spec_with_batch_params["project_id"]
        experiment_id = spec_with_batch_params["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.05},
                    {"parameter_id": "inlet_velocity", "value": 0.02},
                    {"parameter_id": "density", "value": 1000.0},
                ],
            },
        )
        assert response.status_code == 200, response.text
        propagation = response.json()["_batch_propagation"]
        direct = propagation["direct_updates"]
        assert len(direct) == 3

        by_id = {d["parameter_id"]: d for d in direct}
        assert by_id["diameter"]["old_value"] == 0.1
        assert by_id["diameter"]["new_value"] == 0.05
        assert by_id["inlet_velocity"]["old_value"] == 0.01
        assert by_id["inlet_velocity"]["new_value"] == 0.02
        assert by_id["density"]["old_value"] == 998.2
        assert by_id["density"]["new_value"] == 1000.0

    def test_batch_save_includes_summary_text(
        self, client: TestClient, spec_with_batch_params: dict
    ):
        """Batch save response must have a summary text."""
        project_id = spec_with_batch_params["project_id"]
        experiment_id = spec_with_batch_params["experiment_id"]

        response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.05},
                    {"parameter_id": "inlet_velocity", "value": 0.02},
                    {"parameter_id": "density", "value": 1000.0},
                ],
            },
        )
        assert response.status_code == 200, response.text
        propagation = response.json()["_batch_propagation"]
        assert propagation["summary"]
        assert "3" in propagation["summary"]

    def test_batch_save_version_present_in_response(
        self, client: TestClient, spec_with_batch_params: dict
    ):
        """Batch save response must include experiment_version."""
        project_id = spec_with_batch_params["project_id"]
        experiment_id = spec_with_batch_params["experiment_id"]

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
        assert "experiment_version" in body
        assert isinstance(body["experiment_version"], int)
        assert body["experiment_version"] >= 1

    def test_batch_save_stale_version_returns_409(
        self, client: TestClient, spec_with_batch_params: dict
    ):
        """Second batch update with stale/wrong version must return 409."""
        project_id = spec_with_batch_params["project_id"]
        experiment_id = spec_with_batch_params["experiment_id"]

        # First batch update (succeeds with version 1)
        first_response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.05},
                    {"parameter_id": "inlet_velocity", "value": 0.02},
                    {"parameter_id": "density", "value": 1000.0},
                ],
            },
        )
        assert first_response.status_code == 200, first_response.text

        # Second batch update with stale version (99 != current)
        second_response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 99,
                "updates": [
                    {"parameter_id": "diameter", "value": 0.08},
                ],
            },
        )
        assert second_response.status_code == 409
        detail = second_response.json()["detail"]
        assert detail["error"] == "version_conflict"


# ===========================================================================
# Test Category 5: NL Edit Integration
# ===========================================================================


@pytest.fixture
def spec_with_nl_params(repository, project_id):
    """Create an experiment spec with diameter and length for NL edit testing."""
    parameters = [
        _make_param(
            "diameter",
            "Cylinder Diameter",
            "geometry",
            0.1,
            unit="m",
            criticality=Criticality.CRITICAL,
        ),
        _make_param(
            "length",
            "Pipe Length",
            "geometry",
            1.0,
            unit="m",
        ),
        _make_param(
            "density",
            "Density",
            "fluid_property",
            998.2,
            unit="kg/m^3",
        ),
    ]
    experiment_id = _create_spec(repository, project_id, parameters=parameters)
    return {
        "project_id": project_id,
        "experiment_id": experiment_id,
    }


class TestNLEditIntegration:
    """Test natural language editing integration."""

    def test_nl_edit_returns_two_proposed_changes(
        self, client: TestClient, spec_with_nl_params: dict
    ):
        """NL edit with two parameters returns 2 proposed changes."""
        project_id = spec_with_nl_params["project_id"]
        experiment_id = spec_with_nl_params["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "把管径改成50毫米，长度改成5米",
            },
        )
        assert response.status_code == 200, response.text
        proposed = response.json()["proposed_changes"]
        assert len(proposed) == 2

    def test_nl_edit_diameter_converts_mm_to_m(
        self, client: TestClient, spec_with_nl_params: dict
    ):
        """Diameter new_value must be 0.05 (50 mm -> 0.05 m)."""
        project_id = spec_with_nl_params["project_id"]
        experiment_id = spec_with_nl_params["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "把管径改成50毫米，长度改成5米",
            },
        )
        assert response.status_code == 200, response.text
        proposed = response.json()["proposed_changes"]
        by_id = {c["parameter_id"]: c for c in proposed}
        assert by_id["diameter"]["new_value"] == pytest.approx(0.05)

    def test_nl_edit_length_is_5_meters(
        self, client: TestClient, spec_with_nl_params: dict
    ):
        """Length new_value must be 5.0."""
        project_id = spec_with_nl_params["project_id"]
        experiment_id = spec_with_nl_params["experiment_id"]

        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "把管径改成50毫米，长度改成5米",
            },
        )
        assert response.status_code == 200, response.text
        proposed = response.json()["proposed_changes"]
        by_id = {c["parameter_id"]: c for c in proposed}
        assert by_id["length"]["new_value"] == pytest.approx(5.0)

    def test_nl_edit_does_not_modify_spec(
        self, client: TestClient, spec_with_nl_params: dict
    ):
        """NL edit must NOT modify the spec (GET returns original values)."""
        project_id = spec_with_nl_params["project_id"]
        experiment_id = spec_with_nl_params["experiment_id"]

        # Call NL edit
        response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "把管径改成50毫米，长度改成5米",
            },
        )
        assert response.status_code == 200

        # Verify spec was NOT modified
        get_response = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}"
        )
        assert get_response.status_code == 200
        params = {p["parameter_id"]: p for p in get_response.json()["parameters"]}
        assert params["diameter"]["value"] == 0.1  # original value
        assert params["length"]["value"] == 1.0  # original value

    def test_nl_edit_apply_proposed_changes_via_batch_patch(
        self, client: TestClient, spec_with_nl_params: dict
    ):
        """Apply NL proposed changes via batch PATCH and verify updated values."""
        project_id = spec_with_nl_params["project_id"]
        experiment_id = spec_with_nl_params["experiment_id"]

        # 1. Get NL proposed changes
        nl_response = client.post(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
            json={
                "experiment_version": 1,
                "instruction": "把管径改成50毫米，长度改成5米",
            },
        )
        assert nl_response.status_code == 200, nl_response.text
        proposed = nl_response.json()["proposed_changes"]
        assert len(proposed) == 2

        # 2. Verify spec still has original values
        get_before = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}"
        )
        assert get_before.status_code == 200
        params_before = {
            p["parameter_id"]: p for p in get_before.json()["parameters"]
        }
        assert params_before["diameter"]["value"] == 0.1
        assert params_before["length"]["value"] == 1.0

        # 3. Apply proposed changes via batch PATCH
        updates = [
            {"parameter_id": c["parameter_id"], "value": c["new_value"]}
            for c in proposed
        ]
        patch_response = client.patch(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
            json={
                "experiment_version": 1,
                "updates": updates,
            },
        )
        assert patch_response.status_code == 200, patch_response.text

        # 4. Verify spec now has updated values
        get_after = client.get(
            f"/api/projects/{project_id}/experiment-specs/{experiment_id}"
        )
        assert get_after.status_code == 200
        params_after = {
            p["parameter_id"]: p for p in get_after.json()["parameters"]
        }
        assert params_after["diameter"]["value"] == pytest.approx(0.05)
        assert params_after["length"]["value"] == pytest.approx(5.0)
