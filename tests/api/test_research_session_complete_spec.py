from __future__ import annotations

from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app


def test_research_session_generates_closed_executable_draft(tmp_path) -> None:
    client = TestClient(
        create_app(
            repository=SQLWorkflowRepository(
                f"sqlite:///{tmp_path / 'complete_spec.db'}"
            )
        )
    )
    message = (
        "研究旋转圆柱在雷诺数800下以20度攻角作俯仰振荡的非定常湍流；"
        "初始全场静止，随后施加均匀来流，圆柱表面无滑移，远场自由滑移，"
        "出口对流，展向对称；重点观测前缘涡不对称破裂、侧向力迟滞、"
        "尾迹偏斜、展向翻转、壁面涡结构和阻升力频谱。流体为水。"
    )

    project = client.post("/api/projects", json={"question": message})
    assert project.status_code == 201
    project_id = project.json()["project_id"]

    created = client.post(
        "/api/research-sessions",
        json={"project_id": project_id, "message": message},
    )

    assert created.status_code == 201
    result = created.json()
    assert result["type"] == "draft_ready"
    assert result["experiment_spec_id"]

    spec_response = client.get(
        f"/api/research-sessions/{result['session_id']}/experiment-spec"
    )
    assert spec_response.status_code == 200
    spec = spec_response.json()

    missing = [p for p in spec["parameters"] if p["value"] in (None, "", "unknown")]
    assert missing == []
    assert {p["status"] for p in spec["parameters"]} == {"accepted"}
    assert {p["confirmation_policy"] for p in spec["parameters"]} == {"auto_accept"}
    assert "unknown" not in {p["source"]["type"] for p in spec["parameters"]}
    assert "system_recommended" not in {p["source"]["type"] for p in spec["parameters"]}

    param_ids = {p["parameter_id"] for p in spec["parameters"]}
    assert "solver_name" in param_ids
    assert "mesh_strategy_target_cells" in param_ids
    assert "time_control_delta_t" in param_ids
    assert "sampling_strategy_sampling_frequency" in param_ids
    assert "compute_resources_parallel_ranks" in param_ids
    values = {p["parameter_id"]: p["value"] for p in spec["parameters"]}
    assert values["Re"] == 800.0

    assert spec["physics"]["solver"] == "pimpleFoam"
    assert spec["physics"]["turbulence_model"] == "LES"
    assert spec["sampling_plan"]
    assert spec["compute_plan"]

    compiled = next(m for m in spec["metrics"] if m["kind"] == "compiled_metrics")
    scientific_ids = {
        metric["metric_id"] for metric in compiled["scientific_metrics"]
    }
    boundary_ids = {
        metric["metric_id"] for metric in compiled["boundary_verification_metrics"]
    }

    assert {
        "wake_center_offset",
        "wake_deflection_angle",
        "sign_change_rate",
        "phase_difference",
        "spanwise_correlation",
        "Q",
        "lambda2",
        "wall_vorticity",
        "wall_shear_stress",
        "force_mean",
        "force_rms",
        "force_psd",
        "dominant_frequency",
        "strouhal",
    }.issubset(scientific_ids)
    assert {
        "inlet_profile_error",
        "no_slip_wall_error",
        "free_slip_normal_velocity_error",
        "outlet_backflow_ratio",
        "mass_conservation_error",
    }.issubset(boundary_ids)
    assert spec["code_extensions"] == []
