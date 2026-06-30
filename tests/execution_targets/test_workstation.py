import json

from fluid_scientist.execution.ssh import ProcessResult, RemoteExecutionError, RemoteProgram
from fluid_scientist.execution_targets.workstation import WorkstationOpenFOAMTarget


class FakeTransport:
    def __init__(self, payload=None, error=None) -> None:
        self.payload = payload
        self.error = error
        self.calls = []

    def execute(self, program, args, *, timeout):
        self.calls.append((program, args, timeout))
        if self.error:
            raise self.error
        return ProcessResult(0, json.dumps(self.payload), "")


def capability(version: int = 1) -> dict:
    return {
        "protocol_version": version,
        "foam_version": "OpenFOAM-v2312",
        "cpu_count": 32,
        "memory_gb": 128.0,
        "disk_free_gb": 500.0,
        "commands": ["blockMesh", "checkMesh", "simpleFoam", "postProcess"],
    }


def test_doctor_selects_first_compatible_candidate_without_exposing_host() -> None:
    unavailable = FakeTransport(error=RemoteExecutionError("unreachable"))
    available = FakeTransport(payload=capability())
    target = WorkstationOpenFOAMTarget(
        target_id="workstation-openfoam",
        candidates=(("primary", unavailable), ("secondary", available)),
    )

    result = target.doctor()

    assert result.available is True
    assert result.selected_candidate == "secondary"
    assert result.foam_version == "OpenFOAM-v2312"
    assert available.calls[0][0] == RemoteProgram.FLUID_WORKER


def test_doctor_rejects_worker_protocol_mismatch() -> None:
    target = WorkstationOpenFOAMTarget(
        target_id="workstation-openfoam",
        candidates=(("primary", FakeTransport(payload=capability(version=2))),),
    )

    result = target.doctor()

    assert result.available is False
    assert "protocol" in result.reason

