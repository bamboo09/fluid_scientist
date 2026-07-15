"""Tests for the obstacle flow API router."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from fluid_scientist.api.obstacle_flow_router import router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _simple_spec() -> dict:
    return {
        "domain": {"length_m": 30, "height_m": 10, "thickness_m": 1.0},
        "fluid": {
            "type": "water",
            "density_kg_m3": 998.0,
            "kinematic_viscosity_m2_s": 1.004e-6,
        },
        "flow_definition": {"mode": "inlet_outlet"},
        "boundaries": {
            "left": {"type": "velocity_inlet", "inlet_velocity": 1.0},
            "right": {"type": "pressure_outlet", "pressure_value": 0.0},
            "top": {"type": "slip_wall"},
            "bottom_flat": {"type": "no_slip_wall"},
        },
        "simulation": {"time_mode": "steady"},
    }


def _cylinder_spec() -> dict:
    spec = _simple_spec()
    spec["cylinders"] = [
        {
            "id": "c1",
            "center_x_m": 10,
            "center_y_m": 5,
            "diameter_m": 2,
        }
    ]
    spec["simulation"] = {"time_mode": "transient", "end_time": 10.0}
    return spec


class TestHealthEndpoint:
    def test_health_check(self):
        client = TestClient(_make_app())
        resp = client.get("/api/v5/obstacle-flow/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["module"] == "obstacle_flow"


class TestCompileEndpoint:
    def test_compile_simple_case(self):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v5/obstacle-flow/compile",
            json={"spec": _simple_spec()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["flow_mode"] == "inlet_outlet"
        assert data["static_validation_passed"] is True
        assert data["security_validation_passed"] is True
        assert len(data["generated_files"]) > 0

    def test_compile_cylinder_case(self):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v5/obstacle-flow/compile",
            json={"spec": _cylinder_spec()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["has_cylinder"] is True

    def test_compile_invalid_spec(self):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v5/obstacle-flow/compile",
            json={"spec": {"invalid": "spec"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "error" in data

    def test_compile_without_security_validation(self):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v5/obstacle-flow/compile",
            json={"spec": _simple_spec(), "run_security_validation": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["security_validation_passed"] is None


class TestValidateEndpoint:
    def test_validate_simple_case(self):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v5/obstacle-flow/validate",
            json={"spec": _simple_spec()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["passed"] is True
        assert data["flow_mode"] == "inlet_outlet"


class TestPostprocessEndpoint:
    def test_create_postprocess_spec(self):
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v5/obstacle-flow/postprocess",
            json={
                "spec": _cylinder_spec(),
                "run_id": "run_001",
                "case_path": "/cases/run_001",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "plot_spec" in data
        assert "postprocess_script" in data
        assert data["n_plots"] > 0


class TestSchemaEndpoint:
    def test_get_schema(self):
        client = TestClient(_make_app())
        resp = client.get("/api/v5/obstacle-flow/schema")
        assert resp.status_code == 200
        data = resp.json()
        assert "properties" in data
        assert "domain" in data["properties"]
        assert "fluid" in data["properties"]
        assert "boundaries" in data["properties"]
