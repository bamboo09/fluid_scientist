from fastapi.testclient import TestClient

from fluid_scientist.api.app import create_app
from fluid_scientist.execution_targets.base import ExecutionTargetCapability


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
