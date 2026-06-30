"""OpenFOAM workstation target backed by the fixed fluid-worker protocol."""

import json

from pydantic import BaseModel, ConfigDict, ValidationError

from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.execution.ssh import (
    RemoteArg,
    RemoteExecutionError,
    RemoteProgram,
    SSHTransport,
)
from fluid_scientist.execution_targets.base import ExecutionTargetCapability
from fluid_scientist.worker.service import JobRecord


class WorkerDoctor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: int
    foam_version: str
    cpu_count: int
    memory_gb: float
    disk_free_gb: float
    commands: tuple[str, ...]


class WorkerMeshResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    cells: int
    max_aspect_ratio: float
    max_non_orthogonality: float
    average_non_orthogonality: float
    max_skewness: float


class WorkerSolverResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed: bool
    final_residuals: dict[str, float]
    global_continuity_error: float | None
    cumulative_continuity_error: float | None
    inlet_mass_flow: float | None
    outlet_mass_flow: float | None
    pressure_drop_pa: float | None


class WorkerCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    state: str
    mesh: WorkerMeshResult
    solver: WorkerSolverResult
    case_manifest: dict[str, str]


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
        self._selected_transport: SSHTransport | None = None

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
            self._selected_transport = transport
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

    def submit(self, job_id: str, spec: LaminarPipeCase) -> JobRecord:
        args = (
            RemoteArg("submit"),
            RemoteArg("--job-id"),
            RemoteArg(job_id),
            RemoteArg("--diameter"),
            RemoteArg(_number(spec.diameter_m)),
            RemoteArg("--length"),
            RemoteArg(_number(spec.length_m)),
            RemoteArg("--velocity"),
            RemoteArg(_number(spec.mean_velocity_m_s)),
            RemoteArg("--nu"),
            RemoteArg(_number(spec.kinematic_viscosity_m2_s)),
            RemoteArg("--axial-cells"),
            RemoteArg(str(spec.axial_cells)),
            RemoteArg("--radial-cells"),
            RemoteArg(str(spec.radial_cells)),
            RemoteArg("--json"),
        )
        return self._job_command(args)

    def status(self, job_id: str) -> JobRecord:
        return self._job_command(
            (RemoteArg("status"), RemoteArg(job_id), RemoteArg("--json"))
        )

    def cancel(self, job_id: str) -> JobRecord:
        return self._job_command(
            (RemoteArg("cancel"), RemoteArg(job_id), RemoteArg("--json"))
        )

    def collect(self, job_id: str) -> WorkerCollection:
        transport = self._transport()
        result = transport.execute(
            RemoteProgram.FLUID_WORKER,
            (RemoteArg("collect"), RemoteArg(job_id), RemoteArg("--json")),
            timeout=self._doctor_timeout,
        )
        try:
            return WorkerCollection.model_validate_json(result.stdout)
        except ValidationError as error:
            raise RemoteExecutionError(
                "fluid-worker returned an invalid collection response"
            ) from error

    def _job_command(self, args: tuple[RemoteArg, ...]) -> JobRecord:
        transport = self._transport()
        result = transport.execute(RemoteProgram.FLUID_WORKER, args, timeout=self._doctor_timeout)
        try:
            return JobRecord.model_validate_json(result.stdout)
        except ValidationError as error:
            raise RemoteExecutionError("fluid-worker returned an invalid job response") from error

    def _transport(self) -> SSHTransport:
        if self._selected_transport is None:
            capability = self.doctor()
            if not capability.available or self._selected_transport is None:
                raise RemoteExecutionError(capability.reason or "workstation is unavailable")
        return self._selected_transport


def _number(value: float) -> str:
    return f"{value:.12g}"
