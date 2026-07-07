from fluid_scientist.adapters.slurm import (
    SlurmAdapter,
    SlurmState,
    parse_sacct_state,
    parse_sbatch_job_id,
)
from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.execution.ssh import ProcessResult, RemoteProgram


class FakeTransport:
    def __init__(self) -> None:
        self.calls = []
        self.outputs = {
            RemoteProgram.SBATCH: ProcessResult(0, "Submitted batch job 12345\n", ""),
            RemoteProgram.SQUEUE: ProcessResult(0, "RUNNING\n", ""),
            RemoteProgram.SACCT: ProcessResult(0, "COMPLETED|0:0\n", ""),
            RemoteProgram.SCANCEL: ProcessResult(0, "", ""),
        }

    def execute(self, program, args, *, timeout):
        self.calls.append((program, args, timeout))
        return self.outputs[program]


def test_slurm_parsers_are_exact() -> None:
    assert parse_sbatch_job_id("Submitted batch job 12345\n") == "12345"
    assert parse_sacct_state("COMPLETED|0:0\n") == SlurmState.COMPLETED
    assert parse_sacct_state("FAILED|1:0\n") == SlurmState.FAILED
    assert parse_sacct_state("CANCELLED by 1000|0:0\n") == SlurmState.CANCELLED


def test_submit_once_reuses_persisted_job_id(tmp_path) -> None:
    repo = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'jobs.db'}")
    repo.save_snapshot("project-1", '{"name":"PILOT_RUNNING"}', expected_version=0)
    transport = FakeTransport()
    slurm = SlurmAdapter(transport=transport, repository=repo)

    first = slurm.submit_once("project-1", "case-1", "jobs/case-1.sbatch")
    second = slurm.submit_once("project-1", "case-1", "jobs/case-1.sbatch")

    assert first == second == "12345"
    assert [call[0] for call in transport.calls].count(RemoteProgram.SBATCH) == 1


def test_status_prefers_squeue_and_falls_back_to_sacct(tmp_path) -> None:
    repo = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'jobs.db'}")
    transport = FakeTransport()
    slurm = SlurmAdapter(transport=transport, repository=repo)

    assert slurm.status("12345") == SlurmState.RUNNING

    transport.outputs[RemoteProgram.SQUEUE] = ProcessResult(0, "", "")
    assert slurm.status("12345") == SlurmState.COMPLETED


def test_cancel_uses_typed_scancel(tmp_path) -> None:
    repo = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'jobs.db'}")
    transport = FakeTransport()
    slurm = SlurmAdapter(transport=transport, repository=repo)

    slurm.cancel("12345")

    assert transport.calls[-1][0] == RemoteProgram.SCANCEL
