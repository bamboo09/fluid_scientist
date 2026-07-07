"""Recoverable Slurm adapter over the typed SSH transport."""

import re
from typing import Protocol

from fluid_scientist.compat import StrEnum
from fluid_scientist.execution.ssh import RemoteArg, RemoteProgram, SSHTransport


class JobBindingRepository(Protocol):
    def list_external_jobs(self, project_id: str) -> dict[str, str]: ...

    def bind_external_job(self, project_id: str, case_id: str, job_id: str) -> str: ...


class SlurmState(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


_SBATCH = re.compile(r"Submitted batch job ([0-9]+)\s*")


def parse_sbatch_job_id(output: str) -> str:
    match = _SBATCH.fullmatch(output)
    if match is None:
        raise ValueError("unrecognized sbatch output")
    return match.group(1)


def parse_sacct_state(output: str) -> SlurmState:
    line = next((item.strip() for item in output.splitlines() if item.strip()), "")
    if not line:
        return SlurmState.UNKNOWN
    token = line.split("|", 1)[0].split()[0].rstrip("+").upper()
    if token.startswith("CANCELLED"):
        return SlurmState.CANCELLED
    if token in {"FAILED", "NODE_FAIL", "OUT_OF_MEMORY", "TIMEOUT", "BOOT_FAIL"}:
        return SlurmState.FAILED
    if token in {"PENDING", "CONFIGURING", "COMPLETING"}:
        return SlurmState.PENDING
    if token == "RUNNING":
        return SlurmState.RUNNING
    if token == "COMPLETED":
        return SlurmState.COMPLETED
    return SlurmState.UNKNOWN


class SlurmAdapter:
    def __init__(
        self,
        *,
        transport: SSHTransport,
        repository: JobBindingRepository,
        command_timeout: float = 30.0,
    ) -> None:
        self._transport = transport
        self._repository = repository
        self._command_timeout = command_timeout

    def submit_once(self, project_id: str, case_id: str, script_path: str) -> str:
        existing = self._repository.list_external_jobs(project_id).get(case_id)
        if existing is not None:
            return existing
        result = self._transport.execute(
            RemoteProgram.SBATCH,
            (RemoteArg(script_path),),
            timeout=self._command_timeout,
        )
        job_id = parse_sbatch_job_id(result.stdout)
        return self._repository.bind_external_job(project_id, case_id, job_id)

    def status(self, job_id: str) -> SlurmState:
        job_arg = RemoteArg(job_id)
        queued = self._transport.execute(
            RemoteProgram.SQUEUE,
            (RemoteArg("--jobs"), job_arg, RemoteArg("--noheader"), RemoteArg("--format=%T")),
            timeout=self._command_timeout,
        )
        state = parse_sacct_state(queued.stdout)
        if state != SlurmState.UNKNOWN:
            return state
        accounted = self._transport.execute(
            RemoteProgram.SACCT,
            (
                RemoteArg("--jobs"),
                job_arg,
                RemoteArg("--parsable2"),
                RemoteArg("--noheader"),
                RemoteArg("--format=State,ExitCode"),
            ),
            timeout=self._command_timeout,
        )
        return parse_sacct_state(accounted.stdout)

    def cancel(self, job_id: str) -> None:
        self._transport.execute(
            RemoteProgram.SCANCEL,
            (RemoteArg(job_id),),
            timeout=self._command_timeout,
        )
