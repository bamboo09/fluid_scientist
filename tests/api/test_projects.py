from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app


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


def test_workbench_exposes_project_and_gate_controls(tmp_path) -> None:
    client = make_client(f"sqlite:///{tmp_path / 'projects.db'}")

    html = client.get("/").text

    assert 'id="create-project"' in html
    assert 'id="gate-approve"' in html
    assert 'id="gate-reject"' in html
    assert "Skill 候选" not in html
