"""OpenFOAM workstation target backed by the fixed fluid-worker protocol."""

import json

from pydantic import BaseModel, ConfigDict, ValidationError

from fluid_scientist.execution.ssh import (
    RemoteArg,
    RemoteExecutionError,
    RemoteProgram,
    SSHTransport,
)
from fluid_scientist.execution_targets.base import ExecutionTargetCapability


class WorkerDoctor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: int
    foam_version: str
    cpu_count: int
    memory_gb: float
    disk_free_gb: float
    commands: tuple[str, ...]


class WorkstationOpenFOAMTarget:
    protocol_version = 1

    def __init__(
        self,
        *,
        target_id: str,
        candidates: tuple[tuple[str, SSHTransport], ...],
        doctor_timeout: float = 15.0,
    ) -> None:
        self.target_id = target_id
        self._candidates = candidates
        self._doctor_timeout = doctor_timeout

    def doctor(self) -> ExecutionTargetCapability:
        reasons: list[str] = []
        for label, transport in self._candidates:
            try:
                result = transport.execute(
                    RemoteProgram.FLUID_WORKER,
                    (RemoteArg("doctor"), RemoteArg("--json")),
                    timeout=self._doctor_timeout,
                )
                doctor = WorkerDoctor.model_validate(json.loads(result.stdout))
            except (RemoteExecutionError, ValueError, json.JSONDecodeError, ValidationError):
                reasons.append(f"{label}: capability check failed")
                continue
            if doctor.protocol_version != self.protocol_version:
                reasons.append(f"{label}: worker protocol mismatch")
                continue
            return ExecutionTargetCapability(
                target_id=self.target_id,
                kind="workstation_openfoam",
                available=True,
                selected_candidate=label,
                foam_version=doctor.foam_version,
                cpu_count=doctor.cpu_count,
                memory_gb=doctor.memory_gb,
                disk_free_gb=doctor.disk_free_gb,
                commands=doctor.commands,
                worker_protocol=doctor.protocol_version,
            )
        return ExecutionTargetCapability(
            target_id=self.target_id,
            kind="workstation_openfoam",
            available=False,
            reason="; ".join(reasons) or "no workstation candidates configured",
        )
