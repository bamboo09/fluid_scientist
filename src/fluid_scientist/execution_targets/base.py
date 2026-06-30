"""Common execution-target capability contract."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict


class ExecutionTargetCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_id: str
    kind: Literal["workstation_openfoam", "hpc_slurm"]
    available: bool
    selected_candidate: str | None = None
    foam_version: str | None = None
    cpu_count: int | None = None
    memory_gb: float | None = None
    disk_free_gb: float | None = None
    commands: tuple[str, ...] = ()
    worker_protocol: int | None = None
    reason: str | None = None


class ExecutionTargetAdapter(Protocol):
    target_id: str

    def doctor(self) -> ExecutionTargetCapability: ...

