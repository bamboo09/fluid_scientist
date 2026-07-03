from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.settings import AppSettings


def make_client(db_url: str) -> TestClient:
    return TestClient(create_app(repository=SQLWorkflowRepository(db_url)))


def test_project_stops_for_gate_one_and_blocks_early_action(tmp_path) -> None:
    client = make_client(f"sqlite:///{tmp_path / 'projects.db'}")
    created = client.post(
        "/api/projects",
        json={"question": "How does curvature affect bend pressure drop?"},
    )

    assert created.status_code == 201
    project = created.json()
    assert project["workflow_state"] == "SPEC_READY"

    blocked = client.post(
        f"/api/projects/{project['project_id']}/actions",
        json={"action": "RETRIEVE_EVIDENCE"},
    )
    assert blocked.status_code == 409
    assert "GATE_1" in blocked.json()["detail"]


def test_gate_approval_allows_action_and_persists_across_app_instances(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'projects.db'}"
    first = make_client(db_url)
    project = first.post(
        "/api/projects",
        json={"question": "How does curvature affect bend pressure drop?"},
    ).json()

    approved = first.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_1",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": 1,
        },
    )
    assert approved.status_code == 200
    advanced = first.post(
        f"/api/projects/{project['project_id']}/actions",
        json={"action": "RETRIEVE_EVIDENCE"},
    )
    assert advanced.status_code == 200
    assert advanced.json()["workflow_state"] == "EVIDENCE_READY"

    reopened = make_client(db_url)
    stored = reopened.get(f"/api/projects/{project['project_id']}")
    assert stored.status_code == 200
    assert stored.json()["workflow_state"] == "EVIDENCE_READY"
    assert stored.json()["approvals"][0]["gate"] == "GATE_1"


def test_gate_rejection_is_audited_without_advancing(tmp_path) -> None:
    client = make_client(f"sqlite:///{tmp_path / 'projects.db'}")
    project = client.post(
        "/api/projects",
        json={"question": "How does curvature affect bend pressure drop?"},
    ).json()

    rejected = client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_1",
            "decision": "reject",
            "actor": "researcher",
            "subject_version": 1,
            "reason": "Diameter range must be narrowed.",
        },
    )

    assert rejected.status_code == 200
    assert rejected.json()["workflow_state"] == "SPEC_READY"
    assert rejected.json()["audit_event_count"] == 2
    blocked = client.post(
        f"/api/projects/{project['project_id']}/actions",
        json={"action": "RETRIEVE_EVIDENCE"},
    )
    assert blocked.status_code == 409


def test_workbench_exposes_conversation_driven_project_and_gate_workflow(tmp_path) -> None:
    client = make_client(f"sqlite:///{tmp_path / 'projects.db'}")

    html = client.get("/").text
    script = client.get("/assets/app.js").text

    assert 'id="experiment-prompt"' in html
    assert 'id="design-experiment"' in html
    assert "Skill 候选" not in html

    assert 'requestJson("/api/projects"' in script
    assert 'approveGate(currentProject, "GATE_1")' in script
    assert 'applyWorkflowAction(currentProject, "RETRIEVE_EVIDENCE")' in script
    assert 'applyWorkflowAction(currentProject, "DESIGN_PILOT")' in script
    assert '`/api/projects/${project.project_id}/approvals`' in script
    assert '`/api/projects/${project.project_id}/actions`' in script
    assert 'gate: "GATE_2"' in script


def test_app_uses_configured_database_when_repository_is_not_injected(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'configured.db'}"
    settings = AppSettings(app_mode="fake", database={"url": database_url})
    first = TestClient(create_app(settings=settings))
    project = first.post(
        "/api/projects",
        json={"question": "Can this project survive an application restart?"},
    ).json()

    reopened = TestClient(create_app(settings=settings))
    response = reopened.get(f"/api/projects/{project['project_id']}")

    assert response.status_code == 200
    assert response.json()["workflow_state"] == "SPEC_READY"


def test_recent_project_endpoint_returns_latest_persistent_project(tmp_path) -> None:
    client = make_client(f"sqlite:///{tmp_path / 'projects.db'}")
    first = client.post(
        "/api/projects",
        json={"question": "Does the first project remain available?"},
    ).json()
    latest = client.post(
        "/api/projects",
        json={"question": "Should this latest project be restored after refresh?"},
    ).json()

    response = client.get("/api/projects/recent")

    assert response.status_code == 200
    assert response.json()["project_id"] == latest["project_id"]
    assert response.json()["project_id"] != first["project_id"]
