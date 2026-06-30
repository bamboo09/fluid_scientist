from fastapi.testclient import TestClient

from fluid_scientist.api.app import create_app
from fluid_scientist.execution_targets.base import ExecutionTargetCapability
from fluid_scientist.settings import AppSettings


class StaticTarget:
    target_id = "workstation-openfoam"

    def doctor(self):
        return ExecutionTargetCapability(
            target_id=self.target_id,
            kind="workstation_openfoam",
            available=True,
            selected_candidate="primary",
            foam_version="OpenFOAM-v2312",
            cpu_count=32,
            memory_gb=128,
            disk_free_gb=500,
            commands=("blockMesh", "checkMesh", "simpleFoam", "postProcess"),
            worker_protocol=1,
        )


def test_execution_targets_api_lists_capabilities() -> None:
    client = TestClient(create_app(execution_targets=(StaticTarget(),)))

    response = client.get("/api/execution-targets")

    assert response.status_code == 200
    assert response.json()[0]["kind"] == "workstation_openfoam"
    assert response.json()[0]["available"] is True
    assert 'id="execution-target"' in client.get("/").text


def test_app_builds_workstation_candidates_from_runtime_settings_only() -> None:
    created_nodes = []

    class RecordingTransport:
        def __init__(self, node) -> None:
            created_nodes.append(node)

        def execute(self, program, args, *, timeout):
            raise RuntimeError("not used")

    settings = AppSettings(
        app_mode="fake",
        workstation={
            "hosts": ("workstation-a.internal", "workstation-b.internal"),
            "username": "ls",
            "identity_file": "runtime-key",
            "known_hosts_file": "runtime-known-hosts",
        },
    )

    application = create_app(settings=settings, transport_factory=RecordingTransport)
    targets = application.state.execution_targets

    assert len(targets) == 1
    assert [node.host for node in created_nodes] == [
        "workstation-a.internal",
        "workstation-b.internal",
    ]
    assert targets[0]._candidates[0][0] == "candidate-1"
