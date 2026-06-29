from fastapi.testclient import TestClient

from fluid_scientist.api.app import app


def client() -> TestClient:
    return TestClient(app)


def test_health_endpoint_is_credential_free() -> None:
    response = client().get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "fake"}


def test_demo_endpoint_returns_reported_project() -> None:
    response = client().post(
        "/api/demo",
        json={"question": "How does bend curvature affect pressure drop?"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["workflow_state"] == "REPORTED"
    assert body["report"]["claims"]

    stored = client().get(f"/api/projects/{body['project_id']}")
    assert stored.status_code == 200
    assert stored.json()["project_id"] == body["project_id"]


def test_demo_endpoint_rejects_short_question() -> None:
    response = client().post("/api/demo", json={"question": "bend"})

    assert response.status_code == 422


def test_static_workbench_does_not_expose_skill_navigation() -> None:
    html = client().get("/").text

    assert "实验结果分析与报告" in html
    assert "Skill 候选" not in html
