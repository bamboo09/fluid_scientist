import json

from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.worker.cli import main
from fluid_scientist.worker.service import JobRecord, JobState


class FakeService:
    def __init__(self) -> None:
        self.submitted = None
        self.executed = None

    def submit(self, job_id, spec):
        self.submitted = (job_id, spec)
        return record(job_id, spec)

    def status(self, job_id):
        return record(job_id, pipe_spec())

    def cancel(self, job_id):
        return record(job_id, pipe_spec(), state=JobState.CANCELLED)

    def collect(self, job_id):
        return {"job_id": job_id, "state": "succeeded", "mesh": {"cells": 8000}}

    def execute(self, job_id):
        self.executed = job_id
        return record(job_id, pipe_spec(), state=JobState.SUCCEEDED)


def pipe_spec():
    return LaminarPipeCase(
        diameter_m=0.02,
        length_m=2,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1e-6,
    )


def record(job_id, spec, *, state=JobState.RUNNING):
    return JobRecord(
        job_id=job_id,
        state=state,
        spec=spec,
        case_manifest={"system/controlDict": "a" * 64},
        submitted_at="2026-06-30T00:00:00Z",
        pid=123 if state == JobState.RUNNING else None,
    )


def test_submit_cli_accepts_only_typed_pipe_parameters(capsys) -> None:
    service = FakeService()

    exit_code = main(
        [
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
            "1e-6",
            "--json",
        ],
        service=service,
    )

    assert exit_code == 0
    assert service.submitted == ("benchmark-001", pipe_spec())
    assert json.loads(capsys.readouterr().out)["state"] == "running"


def test_status_cancel_collect_and_private_run_commands(capsys) -> None:
    service = FakeService()

    assert main(["status", "benchmark-001", "--json"], service=service) == 0
    assert json.loads(capsys.readouterr().out)["job_id"] == "benchmark-001"
    assert main(["cancel", "benchmark-001", "--json"], service=service) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "cancelled"
    assert main(["collect", "benchmark-001", "--json"], service=service) == 0
    assert json.loads(capsys.readouterr().out)["mesh"]["cells"] == 8000
    assert main(["_run", "--job-id", "benchmark-001"], service=service) == 0
    assert service.executed == "benchmark-001"
