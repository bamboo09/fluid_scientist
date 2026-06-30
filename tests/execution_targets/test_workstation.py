import json

from fluid_scientist.adapters.openfoam import LaminarPipeCase
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


class SequenceTransport:
    def __init__(self, payloads) -> None:
        self.payloads = iter(payloads)
        self.calls = []

    def execute(self, program, args, *, timeout):
        self.calls.append((program, tuple(str(arg) for arg in args), timeout))
        return ProcessResult(0, json.dumps(next(self.payloads)), "")


def worker_job(state="running") -> dict:
    return {
        "job_id": "benchmark-001",
        "state": state,
        "spec": {
            "diameter_m": 0.02,
            "length_m": 2.0,
            "mean_velocity_m_s": 0.1,
            "kinematic_viscosity_m2_s": 1e-6,
            "axial_cells": 80,
            "radial_cells": 10,
        },
        "case_manifest": {"system/controlDict": "a" * 64},
        "submitted_at": "2026-06-30T00:00:00Z",
        "pid": 123,
        "error": None,
    }


def test_submit_uses_fixed_worker_arguments_after_capability_selection() -> None:
    transport = SequenceTransport((capability(), worker_job()))
    target = WorkstationOpenFOAMTarget(
        target_id="workstation-openfoam",
        candidates=(("primary", transport),),
    )
    spec = LaminarPipeCase(
        diameter_m=0.02,
        length_m=2.0,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1e-6,
    )

    submitted = target.submit("benchmark-001", spec)

    assert submitted.job_id == "benchmark-001"
    assert submitted.state.value == "running"
    assert transport.calls[1][0] == RemoteProgram.FLUID_WORKER
    assert transport.calls[1][1] == (
        "submit",
        "--job-id",
        "benchmark-001",
        "--diameter",
        "0.02",
        "--length",
        "2",
        "--velocity",
        "0.1",
        "--nu",
        "1e-06",
        "--axial-cells",
        "80",
        "--radial-cells",
        "10",
        "--json",
    )


def test_status_and_cancel_use_job_id_as_typed_argument() -> None:
    transport = SequenceTransport((capability(), worker_job(), worker_job("cancelled")))
    target = WorkstationOpenFOAMTarget(
        target_id="workstation-openfoam",
        candidates=(("primary", transport),),
    )

    assert target.status("benchmark-001").state.value == "running"
    assert target.cancel("benchmark-001").state.value == "cancelled"
    assert transport.calls[1][1] == ("status", "benchmark-001", "--json")
    assert transport.calls[2][1] == ("cancel", "benchmark-001", "--json")
