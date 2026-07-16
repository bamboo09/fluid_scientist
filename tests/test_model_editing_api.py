"""Tests for the model-driven spec editing API router.

These tests use the FastAPI :class:`TestClient` to exercise every
endpoint in :mod:`fluid_scientist.api.model_editing_router`.  No real
LLM is required — the tests verify that:

* Session creation and retrieval work.
* Turn processing returns ``MODEL_UNAVAILABLE`` when no LLM is
  configured (no silent fallback).
* Direct patch application via PATCH works.
* Confirm / reject / undo lifecycle works.
* Patch history, model traces, schema export, and legacy migration all
  function correctly.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fluid_scientist.api import model_editing_router as _router_mod
from fluid_scientist.api.model_editing_router import router as _model_editing_router
from fluid_scientist.session_state.session_manager import SessionManager
from fluid_scientist.spec_editing.patch_engine import PatchEngine
from fluid_scientist.spec_editing.models import SimulationSpecPatch
from fluid_scientist.study_spec import (
    BoundaryCondition,
    BoundaryDefinition,
    DomainSpec,
    ExecutionDefinition,
    GeometryDefinition,
    GeometryEntity,
    MeshDefinition,
    NumericsDefinition,
    ObservationDefinition,
    ObservationTarget,
    PhysicsDefinition,
    PlacementSpec,
    ProbeSpec,
    Quantity,
    SimulationStudySpec,
    SpecProvenance,
    SourcedValue,
    StudyDefinition,
    TimeControl,
    ValidationDefinition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sourced(
    value: Any,
    unit: str | None = None,
    status: str = "user_explicit",
    confidence: float = 0.9,
) -> SourcedValue:
    return SourcedValue(
        value=value,
        unit=unit,
        status=status,  # type: ignore[arg-type]
        confidence=confidence,
    )


def make_study_spec(spec_id: str | None = None) -> SimulationStudySpec:
    """Build a fully-populated SimulationStudySpec for testing."""
    sid = spec_id or f"test_spec_{uuid.uuid4().hex[:8]}"
    study = StudyDefinition(
        title="Cylinder Flow Re=100",
        objective="Investigate vortex shedding behind a cylinder",
        research_questions=["What is the Strouhal number at Re=100?"],
    )
    physics = PhysicsDefinition(
        material=_sourced("water", status="user_confirmed"),
        density=_sourced(998.2, unit="kg/m^3", status="user_confirmed"),
        kinematic_viscosity=_sourced(1.0e-6, unit="m^2/s", status="derived"),
        reynolds_number=_sourced(100.0, status="derived"),
        velocity=_sourced(0.1, unit="m/s", status="derived"),
        characteristic_length=_sourced(0.001, unit="m", status="derived"),
    )
    geometry = GeometryDefinition(
        domain=DomainSpec(
            length=_sourced(12.0, unit="m"),
            width=_sourced(8.0, unit="m"),
            dimensions="2d",
        ),
        entities={
            "cylinder": GeometryEntity(
                entity_id="cylinder",
                semantic_type="cylinder_2d",
                primitive={"type": "circle", "radius": 0.2, "diameter": 0.4},
                original_user_semantics="cylinder",
                placement=PlacementSpec(
                    x=_sourced(4.0, unit="m"),
                    y=_sourced(4.0, unit="m"),
                ),
            ),
        },
        relations=[],
    )
    boundaries = BoundaryDefinition(
        conditions=[
            BoundaryCondition(
                patch_name="inlet",
                role="inlet",
                bc_type="velocityInlet",
                parameters={"velocity": 0.1},
                source_status="user_explicit",
            ),
            BoundaryCondition(
                patch_name="outlet",
                role="outlet",
                bc_type="pressureOutlet",
                parameters={"pressure": 0.0},
                source_status="derived",
            ),
            BoundaryCondition(
                patch_name="cylinder",
                role="wall",
                bc_type="noSlipWall",
                parameters={},
                source_status="derived",
            ),
        ],
    )
    numerics = NumericsDefinition(
        time=TimeControl(
            mode="transient",
            start_time=Quantity(value=0.0, unit="s"),
            end_time=Quantity(value=10.0, unit="s"),
            delta_t=Quantity(value=0.01, unit="s"),
            adaptive=False,
            max_courant=0.5,
            write_control="runTime",
            write_interval=Quantity(value=0.1, unit="s"),
        ),
        solver="icoFoam",
        discretization={"ddtSchemes": {"ddtScheme": "backward"}},
        turbulence_model="laminar",
    )
    mesh = MeshDefinition(
        resolution=_sourced(1200, unit="cells", status="derived"),
        mesh_type="blockMesh",
        refinement_regions=[],
    )
    observations = ObservationDefinition(
        targets=[
            ObservationTarget(
                target_id="drag",
                metric="cd",
                parameters={"patches": ["cylinder"]},
                function_object_type="forceCoeffs",
            ),
        ],
        probes=[
            ProbeSpec(
                probe_id="wake_probe_1",
                location={"x": 5.0, "y": 4.0, "z": 0.0},
                field="U",
            ),
        ],
        postprocessing=["streamlines"],
    )
    execution = ExecutionDefinition(
        target_id="workstation",
        parallel=False,
        cores=None,
    )
    validation = ValidationDefinition(checks=["courant_number", "mass_balance"])
    provenance = SpecProvenance(
        created_at="2026-01-01T00:00:00+00:00",
        created_by="test_user",
        parent_version=None,
        creation_turn_id="turn_0",
    )
    return SimulationStudySpec(
        spec_id=sid,
        session_id="test_session",
        version=1,
        parent_version=None,
        study=study,
        physics=physics,
        geometry=geometry,
        boundaries=boundaries,
        initial_conditions=[],
        numerics=numerics,
        mesh=mesh,
        observations=observations,
        execution=execution,
        validation=validation,
        extensions={},
        provenance=provenance,
    )


def make_patch_dict(
    spec: SimulationStudySpec,
    *,
    patch_id: str | None = None,
    intent: str = "modify_existing_spec",
    operations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a SimulationSpecPatch as a plain dict (for JSON requests)."""
    if operations is None:
        operations = [
            {
                "op": "replace",
                "path": "/numerics/time/end_time",
                "value": 15.0,
                "source_quote": "Set end time to 15 seconds",
                "confidence": 0.95,
            },
        ]
    return {
        "patch_id": patch_id or f"patch_{uuid.uuid4().hex[:8]}",
        "session_id": spec.session_id,
        "base_spec_id": spec.spec_id,
        "base_version": spec.version,
        "intent": intent,
        "operations": operations,
        "clarifications": [],
        "impact_requests": [],
        "untouched_guarantee": True,
        "assistant_message": "Applying user requested changes",
    }


def make_legacy_spec() -> dict[str, Any]:
    """Build a minimal legacy CylinderFlow2DExperimentSpecV1 dict."""
    return {
        "experiment_id": "legacy_exp_001",
        "spec_id": "legacy_spec_001",
        "title": "Legacy Cylinder Flow",
        "objective": "Test migration",
        "fluid": {
            "type": {"value": "water", "source": "USER_EXPLICIT"},
            "density_kg_m3": {"value": 998.2, "source": "USER_EXPLICIT"},
            "kinematic_viscosity_m2_s": {"value": 1.0e-6, "source": "USER_EXPLICIT"},
        },
        "cylinder": {
            "radius_m": {"value": 0.5, "source": "USER_EXPLICIT"},
            "diameter_m": {"value": 1.0, "source": "USER_EXPLICIT"},
            "center_x_m": {"value": 2.0, "source": "USER_EXPLICIT"},
            "center_y_m": {"value": 2.0, "source": "USER_EXPLICIT"},
        },
        "domain": {
            "length_m": {"value": 10.0, "source": "USER_EXPLICIT"},
            "height_m": {"value": 5.0, "source": "USER_EXPLICIT"},
            "dimensionality": "2D",
        },
        "boundaries": {
            "left": {
                "semantic_type": "uniform_velocity_inlet",
                "inlet_velocity": 1.0,
                "source": "USER_EXPLICIT",
            },
            "right": {
                "semantic_type": "pressure_outlet",
                "source": "USER_EXPLICIT",
            },
        },
        "simulation": {
            "end_time": 10.0,
            "delta_t": 0.01,
            "time_mode": "transient",
            "flow_regime": "auto",
            "max_courant_number": 0.5,
        },
        "observables": [
            {"type": "cylinder_drag", "label": "drag"},
        ],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    """Create a TestClient with fresh global state for each test."""
    # Save originals.
    orig_sm = _router_mod._session_manager
    orig_pe = _router_mod._patch_engine
    orig_tr = _router_mod._trace_recorder

    # Inject fresh instances.
    _router_mod._session_manager = SessionManager()
    _router_mod._patch_engine = PatchEngine()
    from fluid_scientist.model_runtime.tracing import TraceRecorder
    _router_mod._trace_recorder = TraceRecorder()

    app = FastAPI()
    app.include_router(_model_editing_router)
    tc = TestClient(app)
    yield tc

    # Restore originals.
    _router_mod._session_manager = orig_sm
    _router_mod._patch_engine = orig_pe
    _router_mod._trace_recorder = orig_tr


@pytest.fixture()
def session_with_spec(client: TestClient) -> dict[str, Any]:
    """Create a session with an active spec for patch tests."""
    # Create session.
    resp = client.post("/api/v5/model-editing/sessions", json={"project_id": "test"})
    assert resp.status_code == 200
    session_id = resp.json()["session_id"]

    # Build a spec and set it directly via the session manager.
    spec = make_study_spec()
    _router_mod._session_manager.set_active_spec(session_id, spec)

    return {"session_id": session_id, "spec": spec}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSessionCreation:
    """Test POST /sessions and GET /sessions/{session_id}."""

    def test_create_session_basic(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "proj_001"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["session_id"].startswith("session_")
        assert data["phase"] == "understanding"
        assert data["spec"] is None

    def test_create_session_no_project_id(self, client: TestClient) -> None:
        resp = client.post("/api/v5/model-editing/sessions", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data

    def test_create_session_with_legacy_spec(self, client: TestClient) -> None:
        legacy = make_legacy_spec()
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "proj_002", "legacy_spec": legacy},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["spec"] is not None
        assert data["spec"]["schema_version"] == "1.0"
        assert data["spec"]["study"]["title"] == "Legacy Cylinder Flow"

    def test_get_session_state(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "proj_003"},
        )
        session_id = resp.json()["session_id"]

        resp2 = client.get(f"/api/v5/model-editing/sessions/{session_id}")
        assert resp2.status_code == 200
        state = resp2.json()
        assert state["session_id"] == session_id
        assert state["project_id"] == "proj_003"
        assert state["current_phase"] == "understanding"

    def test_get_session_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v5/model-editing/sessions/nonexistent")
        assert resp.status_code == 404


class TestTurnProcessing:
    """Test POST /sessions/{session_id}/turns."""

    def test_turn_no_model_returns_model_unavailable(
        self, client: TestClient,
    ) -> None:
        """Without an LLM configured, MODIFY_EXISTING_SPEC returns
        ``MODEL_UNAVAILABLE`` — no silent fallback."""
        from unittest.mock import patch

        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        # Simulate "no LLM available" by patching _get_llm_client.
        with patch.object(_router_mod, "_get_llm_client", return_value=None):
            resp2 = client.post(
                f"/api/v5/model-editing/sessions/{session_id}/turns",
                json={"user_message": "Change the inlet velocity to 3 m/s"},
            )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["session_id"] == session_id
        assert data["intent"] == "modify_existing_spec"
        assert any("MODEL_UNAVAILABLE" in e for e in data["errors"])

    def test_turn_explanation_intent(self, client: TestClient) -> None:
        """REQUEST_EXPLANATION works without a model (echo)."""
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        resp2 = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/turns",
            json={"user_message": "什么是雷诺数?"},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["intent"] == "request_explanation"
        assert data["errors"] == []
        assert "什么是雷诺数" in data["assistant_message"]

    def test_turn_undo_no_spec(self, client: TestClient) -> None:
        """UNDO_LAST_PATCH without a spec returns an error."""
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        resp2 = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/turns",
            json={"user_message": "撤销上一步"},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["intent"] == "undo_last_patch"
        assert any("NO_SPEC" in e for e in data["errors"])

    def test_turn_confirm_with_pending(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        """CONFIRM_PENDING_PATCH via /turns confirms and applies the patch."""
        session_id = session_with_spec["session_id"]
        spec = session_with_spec["spec"]

        # Set a pending patch directly.
        patch = SimulationSpecPatch.model_validate(make_patch_dict(spec))
        _router_mod._session_manager.set_pending_patch(session_id, patch)

        resp = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/turns",
            json={"user_message": "确认"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "confirm_pending_patch"
        assert data["errors"] == []
        assert data["spec_version"] == 2  # version incremented
        assert data["diff"] is not None

    def test_turn_reject_with_pending(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        """REJECT_PENDING_PATCH via /turns clears the pending patch."""
        session_id = session_with_spec["session_id"]
        spec = session_with_spec["spec"]

        # Set a pending patch directly.
        patch = SimulationSpecPatch.model_validate(make_patch_dict(spec))
        _router_mod._session_manager.set_pending_patch(session_id, patch)

        resp = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/turns",
            json={"user_message": "取消"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["intent"] == "reject_pending_patch"
        assert data["errors"] == []

        # Verify the pending patch was cleared.
        session = _router_mod._session_manager.get_session(session_id)
        assert session is not None
        assert session.pending_patch is None


class TestPatchApplication:
    """Test PATCH /sessions/{session_id}/spec."""

    def test_apply_patch_directly(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        session_id = session_with_spec["session_id"]
        spec = session_with_spec["spec"]

        patch = make_patch_dict(spec)
        resp = client.patch(
            f"/api/v5/model-editing/sessions/{session_id}/spec",
            json={"patch": patch},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["errors"] == []
        assert data["new_spec"] is not None
        assert data["new_spec"]["version"] == 2
        assert data["diff"] is not None
        # end_time should now be 15.0
        assert data["new_spec"]["numerics"]["time"]["end_time"]["value"] == 15.0

    def test_apply_patch_no_spec(self, client: TestClient) -> None:
        """PATCH without an active spec returns an error."""
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        patch = make_patch_dict(make_study_spec())
        resp2 = client.patch(
            f"/api/v5/model-editing/sessions/{session_id}/spec",
            json={"patch": patch},
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert any("NO_SPEC" in e for e in data["errors"])

    def test_apply_patch_validation_error(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        """An invalid patch dict returns a validation error."""
        session_id = session_with_spec["session_id"]
        resp = client.patch(
            f"/api/v5/model-editing/sessions/{session_id}/spec",
            json={"patch": {"not_a_valid": "patch"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any("PATCH_VALIDATION_ERROR" in e for e in data["errors"])

    def test_apply_patch_version_conflict(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        """A patch with wrong base_version returns a version conflict error."""
        session_id = session_with_spec["session_id"]
        spec = session_with_spec["spec"]

        patch = make_patch_dict(spec)
        patch["base_version"] = 999  # Wrong version
        resp = client.patch(
            f"/api/v5/model-editing/sessions/{session_id}/spec",
            json={"patch": patch},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any("Version conflict" in e for e in data["errors"])


class TestConfirmPendingPatch:
    """Test POST /sessions/{session_id}/confirm."""

    def test_confirm_pending_patch(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        session_id = session_with_spec["session_id"]
        spec = session_with_spec["spec"]

        # Set a pending patch directly.
        patch = SimulationSpecPatch.model_validate(make_patch_dict(spec))
        _router_mod._session_manager.set_pending_patch(session_id, patch)

        resp = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/confirm",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["confirmed"] is True
        assert data["patch_id"] is not None
        assert data["spec_version"] == 2  # version incremented
        assert data["errors"] == []

    def test_confirm_no_pending_patch(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        resp2 = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/confirm",
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["confirmed"] is False
        assert any("NO_PENDING_PATCH" in e for e in data["errors"])


class TestRejectPendingPatch:
    """Test POST /sessions/{session_id}/reject."""

    def test_reject_pending_patch(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        session_id = session_with_spec["session_id"]
        spec = session_with_spec["spec"]

        # Set a pending patch directly.
        patch = SimulationSpecPatch.model_validate(make_patch_dict(spec))
        _router_mod._session_manager.set_pending_patch(session_id, patch)

        resp = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/reject",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rejected"] is True
        assert data["errors"] == []

        # Verify the pending patch was cleared.
        session = _router_mod._session_manager.get_session(session_id)
        assert session is not None
        assert session.pending_patch is None

    def test_reject_no_pending_patch(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        resp2 = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/reject",
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["rejected"] is False
        assert any("NO_PENDING_PATCH" in e for e in data["errors"])


class TestUndoLastPatch:
    """Test POST /sessions/{session_id}/undo."""

    def test_undo_last_patch(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        session_id = session_with_spec["session_id"]
        spec = session_with_spec["spec"]

        # First apply a patch via PATCH endpoint.
        patch = make_patch_dict(spec)
        resp = client.patch(
            f"/api/v5/model-editing/sessions/{session_id}/spec",
            json={"patch": patch},
        )
        assert resp.status_code == 200
        assert resp.json()["errors"] == []

        # Now undo it.
        resp2 = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/undo",
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["undone"] is True
        assert data["spec_version"] == 3  # 1 -> 2 (patch) -> 3 (undo)
        assert data["errors"] == []

    def test_undo_no_patches(self, session_with_spec: dict[str, Any], client: TestClient) -> None:
        """Undo without any applied patches returns an error."""
        session_id = session_with_spec["session_id"]
        resp = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/undo",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["undone"] is False
        assert any("NO_PATCH_TO_UNDO" in e for e in data["errors"])

    def test_undo_no_spec(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        resp2 = client.post(
            f"/api/v5/model-editing/sessions/{session_id}/undo",
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["undone"] is False
        assert any("NO_SPEC" in e for e in data["errors"])


class TestPatchHistory:
    """Test GET /sessions/{session_id}/history."""

    def test_get_patch_history(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        session_id = session_with_spec["session_id"]
        spec = session_with_spec["spec"]

        # Apply two patches.
        for i in range(2):
            patch = make_patch_dict(
                spec,
                patch_id=f"patch_{i}",
                operations=[
                    {
                        "op": "replace",
                        "path": "/numerics/time/end_time",
                        "value": 10.0 + i * 5,
                        "source_quote": f"Set end time to {10 + i * 5}s",
                        "confidence": 0.9,
                    },
                ],
            )
            # Update spec version for next patch.
            resp = client.patch(
                f"/api/v5/model-editing/sessions/{session_id}/spec",
                json={"patch": patch},
            )
            assert resp.status_code == 200
            assert resp.json()["errors"] == []
            # Get the updated spec for the next iteration.
            updated = _router_mod._session_manager.get_active_spec(session_id)
            assert updated is not None
            spec = updated

        # Get history.
        resp2 = client.get(
            f"/api/v5/model-editing/sessions/{session_id}/history",
        )
        assert resp2.status_code == 200
        history = resp2.json()
        assert isinstance(history, list)
        assert len(history) == 2
        assert all("patch_id" in r for r in history)
        assert all("base_version" in r for r in history)
        assert all("new_version" in r for r in history)

    def test_get_patch_history_empty(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        session_id = session_with_spec["session_id"]
        resp = client.get(
            f"/api/v5/model-editing/sessions/{session_id}/history",
        )
        assert resp.status_code == 200
        assert resp.json() == []


class TestModelTraces:
    """Test GET /sessions/{session_id}/trace."""

    def test_get_model_traces_empty(self, client: TestClient) -> None:
        """A fresh session has no traces."""
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        resp2 = client.get(
            f"/api/v5/model-editing/sessions/{session_id}/trace",
        )
        assert resp2.status_code == 200
        assert resp2.json() == []

    def test_get_model_traces_after_recording(
        self, client: TestClient,
    ) -> None:
        """Traces recorded via the router's trace recorder are returned."""
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        # Manually record a trace for the session.
        from fluid_scientist.model_runtime.tracing import ModelTrace
        trace = ModelTrace(
            role="spec_editor",
            provider="test",
            configured_model="test-model",
        )
        _router_mod._trace_recorder.record(trace)
        _router_mod._session_manager.add_model_trace(session_id, trace.trace_id)

        resp2 = client.get(
            f"/api/v5/model-editing/sessions/{session_id}/trace",
        )
        assert resp2.status_code == 200
        traces = resp2.json()
        assert len(traces) == 1
        assert traces[0]["role"] == "spec_editor"
        assert traces[0]["provider"] == "test"


class TestSchema:
    """Test GET /schema."""

    def test_get_patch_schema(self, client: TestClient) -> None:
        resp = client.get("/api/v5/model-editing/schema")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["title"] == "SimulationSpecPatch"
        assert "properties" in schema
        assert "patch_id" in schema["properties"]
        assert "operations" in schema["properties"]
        assert "session_id" in schema["properties"]
        assert "base_spec_id" in schema["properties"]
        assert "base_version" in schema["properties"]


class TestMigrate:
    """Test POST /migrate."""

    def test_migrate_legacy_spec(self, client: TestClient) -> None:
        legacy = make_legacy_spec()
        resp = client.post(
            "/api/v5/model-editing/migrate",
            json={"legacy_spec": legacy},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["errors"] == []
        assert data["spec"] is not None
        assert data["spec"]["schema_version"] == "1.0"
        assert data["spec"]["study"]["title"] == "Legacy Cylinder Flow"
        assert data["spec"]["physics"]["material"]["value"] == "water"

    def test_migrate_preserves_legacy_data(self, client: TestClient) -> None:
        """Legacy data is preserved in extensions.legacy_preservation."""
        legacy = make_legacy_spec()
        resp = client.post(
            "/api/v5/model-editing/migrate",
            json={"legacy_spec": legacy},
        )
        data = resp.json()
        ext = data["spec"]["extensions"]
        assert "legacy_preservation" in ext
        assert ext["legacy_preservation"]["original_schema"] == "CylinderFlow2DExperimentSpecV1"
        assert ext["legacy_preservation"]["raw_spec"]["experiment_id"] == "legacy_exp_001"

    def test_migrate_empty_spec(self, client: TestClient) -> None:
        """An empty legacy dict still migrates (with defaults)."""
        resp = client.post(
            "/api/v5/model-editing/migrate",
            json={"legacy_spec": {}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["errors"] == []
        assert data["spec"]["schema_version"] == "1.0"


class TestGetSpec:
    """Test GET /sessions/{session_id}/spec."""

    def test_get_spec(
        self, session_with_spec: dict[str, Any], client: TestClient,
    ) -> None:
        session_id = session_with_spec["session_id"]
        resp = client.get(
            f"/api/v5/model-editing/sessions/{session_id}/spec",
        )
        assert resp.status_code == 200
        spec = resp.json()
        assert spec["schema_version"] == "1.0"
        assert spec["study"]["title"] == "Cylinder Flow Re=100"

    def test_get_spec_no_spec(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v5/model-editing/sessions",
            json={"project_id": "test"},
        )
        session_id = resp.json()["session_id"]

        resp2 = client.get(
            f"/api/v5/model-editing/sessions/{session_id}/spec",
        )
        assert resp2.status_code == 404
