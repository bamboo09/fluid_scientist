from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.compat import UTC
from fluid_scientist.research.models import ResearchSession, ResearchSessionStatus


def test_research_session_does_not_publish_without_openfoam_validation(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("FLUID_SCIENTIST_LLM_MODE", "mock")
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
    if result["type"] == "draft_ready":
        assert result["compile_ready_view"]["status"] == "compile_ready"
        spec_response = client.get(
            f"/api/research-sessions/{result['session_id']}/experiment-spec"
        )
        assert spec_response.status_code == 200
        spec = spec_response.json()
        assert spec["status"] == "compile_ready"
        assert spec["validation_results"]["compile_ready"] is True
        return

    assert result["type"] == "pipeline_failed"
    assert result["failure"]["failed_stage"] == "validating_case"
    assert "OpenFOAM runtime was not found" in result["failure"]["message"]
    assert result["case_dir"]


def test_selecting_reviewed_task_uses_compile_ready_pipeline(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FLUID_SCIENTIST_LLM_MODE", "mock")
    app = create_app(
        repository=SQLWorkflowRepository(
            f"sqlite:///{tmp_path / 'selected_task.db'}"
        )
    )
    client = TestClient(app)
    message = (
        "研究偏心秦勒-库埃特转流在泰勒数5000下叠加轴向压力梯度的"
        "非定常演化；初始流体静止，随后内圆柱以恒定角速度旋转，"
        "外圆柱固定；内外壁面无滑移，轴向周期。"
    )
    project = client.post("/api/projects", json={"question": message})
    assert project.status_code == 201
    project_id = project.json()["project_id"]

    now = datetime.now(UTC).isoformat()
    session_id = "selecttask01"
    app.state.research_session_store.create(
        ResearchSession(
            session_id=session_id,
            project_id=project_id,
            status=ResearchSessionStatus.COLLECTING_REQUIREMENTS,
            original_request=message,
            created_at=now,
            updated_at=now,
        )
    )

    selected = client.post(
        f"/api/research-sessions/{session_id}/turns",
        json={"message": f"选择研究任务: {message}"},
    )

    assert selected.status_code == 200
    result = selected.json()
    assert result["type"] != "draft_ready" or result.get("compile_ready_view")
    if result["type"] == "pipeline_failed":
        assert result["failure"]["failed_stage"] == "validating_case"
        assert "OpenFOAM runtime was not found" in result["failure"]["message"]
        spec_response = client.get(
            f"/api/research-sessions/{session_id}/experiment-spec"
        )
        assert spec_response.status_code == 404
