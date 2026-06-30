"""Safe local OpenFOAM execution primitives used by the remote worker."""

import contextlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from fluid_scientist.adapters.openfoam import LaminarPipeCase, OpenFOAM13CaseRenderer
from fluid_scientist.adapters.openfoam_parsers import parse_check_mesh, parse_solver_log
from fluid_scientist.compat import UTC, StrEnum

REQUIRED_COMMANDS = ("blockMesh", "checkMesh", "foamRun", "postProcess")


class DoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: int = 1
    foam_version: str
    cpu_count: int
    memory_gb: float
    disk_free_gb: float
    commands: tuple[str, ...]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class LocalCommandRunner(Protocol):
    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout: float) -> CommandResult: ...


class SubprocessCommandRunner:
    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout: float) -> CommandResult:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class JobRunResult:
    mesh_log: str
    solver_log: str


@dataclass(frozen=True)
class SurfaceMetrics:
    pressure_drop_pa: float
    inlet_mass_flow: float
    outlet_mass_flow: float


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    state: JobState
    spec: LaminarPipeCase
    case_manifest: dict[str, str]
    submitted_at: datetime
    pid: int | None = None
    error: str | None = None


class JobLauncher(Protocol):
    def launch(self, job_id: str) -> int: ...


class DetachedLauncher:
    def __init__(self, work_root: Path) -> None:
        self._work_root = work_root

    def launch(self, job_id: str) -> int:
        environment = os.environ.copy()
        environment["FLUID_WORKER_ROOT"] = str(self._work_root)
        process = subprocess.Popen(
            (
                sys.executable,
                "-m",
                "fluid_scientist.worker.cli",
                "_run",
                "--job-id",
                job_id,
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=True,
        )
        return process.pid


class OpenFOAM13JobRunner:
    def __init__(
        self,
        *,
        runner: LocalCommandRunner | None = None,
        command_timeout: float = 3_600.0,
    ) -> None:
        self._runner = runner or SubprocessCommandRunner()
        self._command_timeout = command_timeout

    def run(self, case_root: Path) -> JobRunResult:
        block = self._run(("blockMesh",), case_root)
        if block.returncode != 0:
            raise RuntimeError(_failure("blockMesh", block))

        mesh = self._run(("checkMesh", "-allGeometry", "-allTopology"), case_root)
        if mesh.returncode != 0:
            raise RuntimeError(_failure("checkMesh", mesh))

        solver = self._run(("foamRun", "-solver", "incompressibleFluid"), case_root)
        if solver.returncode != 0:
            raise RuntimeError(_failure("foamRun", solver))
        return JobRunResult(mesh_log=mesh.stdout, solver_log=solver.stdout)

    def _run(self, argv: tuple[str, ...], case_root: Path) -> CommandResult:
        return self._runner.run(argv, cwd=case_root, timeout=self._command_timeout)


class WorkerJobService:
    def __init__(self, work_root: Path, *, launcher: JobLauncher | None = None) -> None:
        self._work_root = work_root.resolve()
        self._jobs_root = self._work_root / "jobs"
        self._jobs_root.mkdir(parents=True, exist_ok=True)
        self._launcher = launcher or DetachedLauncher(self._work_root)

    def submit(self, job_id: str, spec: LaminarPipeCase) -> JobRecord:
        job_root = self._job_root(job_id)
        record_file = job_root / "job.json"
        if record_file.is_file():
            existing = self.status(job_id)
            if existing.spec != spec:
                raise ValueError("job id already exists with different parameters")
            return existing

        job_root.mkdir(parents=False)
        manifest = OpenFOAM13CaseRenderer(job_root).render("case", spec)
        queued = JobRecord(
            job_id=job_id,
            state=JobState.QUEUED,
            spec=spec,
            case_manifest=manifest.files,
            submitted_at=datetime.now(UTC),
        )
        self._write(queued)
        try:
            pid = self._launcher.launch(job_id)
        except OSError as error:
            failed = queued.model_copy(update={"state": JobState.FAILED, "error": str(error)})
            self._write(failed)
            raise RuntimeError("could not start OpenFOAM worker") from error
        running = queued.model_copy(update={"state": JobState.RUNNING, "pid": pid})
        self._write(running)
        return running

    def status(self, job_id: str) -> JobRecord:
        record_file = self._job_root(job_id) / "job.json"
        if not record_file.is_file():
            raise KeyError("job not found")
        return JobRecord.model_validate_json(record_file.read_text(encoding="utf-8"))

    def wait_until_runnable(self, job_id: str, *, timeout: float = 5.0) -> JobRecord:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            record = self.status(job_id)
            if record.state != JobState.QUEUED:
                return record
            time.sleep(0.02)
        raise RuntimeError("worker launch handshake timed out")

    def execute(
        self,
        job_id: str,
        *,
        runner: OpenFOAM13JobRunner | None = None,
    ) -> JobRecord:
        record = self.wait_until_runnable(job_id)
        if record.state != JobState.RUNNING:
            return record
        try:
            case_root = self._job_root(job_id) / "case"
            result = (runner or OpenFOAM13JobRunner()).run(case_root)
            metrics = extract_surface_metrics(case_root, density_kg_m3=record.spec.density_kg_m3)
            solver_log = (
                result.solver_log.rstrip()
                + f"\ninlet massFlow = {metrics.inlet_mass_flow:.12g}"
                + f"\noutlet massFlow = {metrics.outlet_mass_flow:.12g}"
                + f"\npressureDrop = {metrics.pressure_drop_pa:.12g}\n"
            )
            (self._job_root(job_id) / "checkMesh.log").write_text(result.mesh_log, encoding="utf-8")
            (self._job_root(job_id) / "solver.log").write_text(solver_log, encoding="utf-8")
            updated = record.model_copy(update={"state": JobState.SUCCEEDED})
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            updated = record.model_copy(update={"state": JobState.FAILED, "error": str(error)})
        self._write(updated)
        return updated

    def cancel(self, job_id: str) -> JobRecord:
        record = self.status(job_id)
        if record.state not in {JobState.QUEUED, JobState.RUNNING}:
            return record
        if record.pid:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(record.pid, signal.SIGTERM)
        cancelled = record.model_copy(update={"state": JobState.CANCELLED})
        self._write(cancelled)
        return cancelled

    def collect(self, job_id: str) -> dict[str, object]:
        record = self.status(job_id)
        if record.state != JobState.SUCCEEDED:
            raise RuntimeError("job results are not ready")
        job_root = self._job_root(job_id)
        mesh = parse_check_mesh((job_root / "checkMesh.log").read_text(encoding="utf-8"))
        solver = parse_solver_log((job_root / "solver.log").read_text(encoding="utf-8"))
        return {
            "job_id": job_id,
            "state": record.state.value,
            "mesh": asdict(mesh),
            "solver": asdict(solver),
            "case_manifest": record.case_manifest,
        }

    def _job_root(self, job_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", job_id):
            raise ValueError("job id contains forbidden characters")
        root = (self._jobs_root / job_id).resolve()
        if root.parent != self._jobs_root:
            raise ValueError("job id escapes work root")
        return root

    def _write(self, record: JobRecord) -> None:
        destination = self._job_root(record.job_id) / "job.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(record.model_dump_json(indent=2), encoding="utf-8", newline="\n")
        temporary.replace(destination)


def build_doctor_report(
    *,
    command_paths: dict[str, str],
    foam_version_output: str,
    cpu_count: int,
    memory_gb: float,
    disk_free_gb: float,
) -> DoctorReport:
    missing = [name for name in REQUIRED_COMMANDS if not command_paths.get(name)]
    if missing:
        raise RuntimeError("missing OpenFOAM commands: " + ", ".join(missing))
    version = foam_version_output.strip().splitlines()[0] if foam_version_output.strip() else ""
    if version != "OpenFOAM-13":
        raise RuntimeError(f"OpenFOAM Foundation 13 is required, found {version or 'unknown'}")
    return DoctorReport(
        foam_version=version,
        cpu_count=cpu_count,
        memory_gb=round(memory_gb, 2),
        disk_free_gb=round(disk_free_gb, 2),
        commands=REQUIRED_COMMANDS,
    )


def extract_surface_metrics(case_root: Path, *, density_kg_m3: float) -> SurfaceMetrics:
    if density_kg_m3 <= 0:
        raise ValueError("density must be positive")
    post_processing = case_root / "postProcessing"
    pressure_kinematic = _latest_function_value(post_processing / "pressureDrop")
    inlet_volumetric = _latest_function_value(post_processing / "inletFlow")
    outlet_volumetric = _latest_function_value(post_processing / "outletFlow")
    return SurfaceMetrics(
        pressure_drop_pa=abs(pressure_kinematic) * density_kg_m3,
        inlet_mass_flow=abs(inlet_volumetric) * density_kg_m3,
        outlet_mass_flow=-abs(outlet_volumetric) * density_kg_m3,
    )


def _latest_function_value(function_root: Path) -> float:
    latest: tuple[float, float] | None = None
    for data_file in function_root.glob("**/*.dat"):
        for line in data_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            numbers = re.findall(r"[-+]?(?:\d*\.?\d+)(?:[eE][-+]?\d+)?", stripped)
            if len(numbers) < 2:
                continue
            candidate = (float(numbers[0]), float(numbers[-1]))
            if latest is None or candidate[0] > latest[0]:
                latest = candidate
    if latest is None:
        raise RuntimeError(f"no OpenFOAM function-object data found for {function_root.name}")
    return latest[1]


def system_doctor(work_root: Path) -> DoctorReport:
    command_paths = {name: shutil.which(name) or "" for name in REQUIRED_COMMANDS}
    project_version = os.environ.get("WM_PROJECT_VERSION", "").strip()
    if project_version:
        foam_version_output = (
            project_version
            if project_version.startswith("OpenFOAM-")
            else f"OpenFOAM-{project_version}"
        )
    else:
        foam_version = subprocess.run(
            ("foamVersion",),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if foam_version.returncode != 0:
            raise RuntimeError("foamVersion failed")
        foam_version_output = foam_version.stdout
    usage = shutil.disk_usage(work_root)
    return build_doctor_report(
        command_paths=command_paths,
        foam_version_output=foam_version_output,
        cpu_count=os.cpu_count() or 1,
        memory_gb=_memory_bytes() / 1024**3,
        disk_free_gb=usage.free / 1024**3,
    )


def _memory_bytes() -> int:
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    if hasattr(os, "sysconf"):
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    return 0


def _failure(command: str, result: CommandResult) -> str:
    detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
    return f"{command} failed: {detail}"
