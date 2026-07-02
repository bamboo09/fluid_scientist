"""Safe local OpenFOAM execution primitives used by the remote worker."""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from fluid_scientist.adapters.custom_openfoam import validate_custom_case_archive
from fluid_scientist.adapters.openfoam import LaminarPipeCase, OpenFOAM13CaseRenderer
from fluid_scientist.adapters.openfoam_parsers import parse_check_mesh, parse_solver_log
from fluid_scientist.compat import UTC, StrEnum

REQUIRED_COMMANDS = ("blockMesh", "mirrorMesh", "checkMesh", "foamRun", "postProcess")


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


class CustomCaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["custom_openfoam"] = "custom_openfoam"
    archive_sha256: str
    solver: Literal["incompressibleFluid"]
    needs_block_mesh: bool
    needs_mirror_mesh: bool = False


class JobRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    state: JobState
    spec: LaminarPipeCase | CustomCaseSpec
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

    def run(
        self,
        case_root: Path,
        *,
        needs_block_mesh: bool = True,
        needs_mirror_mesh: bool = False,
    ) -> JobRunResult:
        if needs_block_mesh:
            block = self._run(("blockMesh",), case_root)
            if block.returncode != 0:
                raise RuntimeError(_failure("blockMesh", block))

        if needs_mirror_mesh:
            mirrored = self._run(("mirrorMesh",), case_root)
            if mirrored.returncode != 0:
                raise RuntimeError(_failure("mirrorMesh", mirrored))

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

    def submit_custom(self, job_id: str, archive_name: str) -> JobRecord:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", archive_name):
            raise ValueError("archive name contains forbidden characters")
        archive_path = (self._work_root / "incoming" / archive_name).resolve()
        if archive_path.parent != (self._work_root / "incoming").resolve():
            raise ValueError("archive escapes incoming directory")
        payload = archive_path.read_bytes()
        validated = validate_custom_case_archive(payload)
        spec = CustomCaseSpec(
            archive_sha256=validated.archive_sha256,
            solver=validated.solver,
            needs_block_mesh=validated.needs_block_mesh,
            needs_mirror_mesh=validated.needs_mirror_mesh,
        )
        job_root = self._job_root(job_id)
        if (job_root / "job.json").is_file():
            existing = self.status(job_id)
            if existing.spec != spec:
                raise ValueError("job id already exists with a different case archive")
            return existing

        job_root.mkdir(parents=False)
        case_root = job_root / "case"
        case_root.mkdir()
        case_manifest: dict[str, str] = {}
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as bundle:
            for member in bundle.getmembers():
                destination = case_root.joinpath(*member.name.split("/"))
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                handle = bundle.extractfile(member)
                if handle is None:
                    raise ValueError("archive member could not be extracted")
                content = handle.read()
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
                case_manifest[member.name] = hashlib.sha256(content).hexdigest()
        queued = JobRecord(
            job_id=job_id,
            state=JobState.QUEUED,
            spec=spec,
            case_manifest=case_manifest,
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
            custom_spec = record.spec if isinstance(record.spec, CustomCaseSpec) else None
            result = (runner or OpenFOAM13JobRunner()).run(
                case_root,
                needs_block_mesh=custom_spec.needs_block_mesh if custom_spec else True,
                needs_mirror_mesh=custom_spec.needs_mirror_mesh if custom_spec else False,
            )
            solver_log = result.solver_log
            if isinstance(record.spec, LaminarPipeCase):
                metrics = extract_surface_metrics(
                    case_root, density_kg_m3=record.spec.density_kg_m3
                )
                solver_log = (
                    solver_log.rstrip()
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
        case_root = job_root / "case"
        poly_mesh = case_root / "constant" / "polyMesh"
        if not poly_mesh.is_dir():
            raise RuntimeError("OpenFOAM mesh is missing from constant/polyMesh")
        paraview_file = case_root / f"{job_id}.foam"
        paraview_file.write_text("", encoding="utf-8")
        time_directories = sorted(
            (
                path.name
                for path in case_root.iterdir()
                if path.is_dir() and _is_numeric_time(path.name)
            ),
            key=float,
        )
        mesh = parse_check_mesh(
            (job_root / "checkMesh.log").read_text(encoding="utf-8"),
            require_passed=False,
        )
        solver = asdict(
            parse_solver_log((job_root / "solver.log").read_text(encoding="utf-8"))
        )
        plan_path = case_root / "fluidScientist" / "plan.json"
        if plan_path.is_file():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            if plan.get("experiment_type") == "laminar_pipe":
                density = float(plan["case"]["density_kg_m3"])
                with contextlib.suppress(RuntimeError, OSError, ValueError, KeyError):
                    metrics = extract_surface_metrics(case_root, density_kg_m3=density)
                    solver.update(asdict(metrics))
        return {
            "job_id": job_id,
            "state": record.state.value,
            "mesh": asdict(mesh),
            "solver": solver,
            "observables": extract_case_observables(case_root),
            "case_manifest": record.case_manifest,
            "post_processing": {
                "case_path": f"jobs/{job_id}/case",
                "paraview_file": paraview_file.name,
                "time_directories": time_directories,
            },
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


def extract_case_observables(case_root: Path) -> dict[str, object]:
    """Extract the latest typed values from built-in function-object files."""

    observables: dict[str, object] = {}
    coefficient_file = case_root / "postProcessing" / "forceCoeffs" / "0" / "forceCoeffs.dat"
    coefficient_values = _latest_numeric_row(coefficient_file)
    if coefficient_values is not None and len(coefficient_values) >= 4:
        observables.update(
            {
                "moment_coefficient": coefficient_values[1],
                "drag_coefficient": coefficient_values[2],
                "lift_coefficient": coefficient_values[3],
            }
        )

    probe_root = case_root / "postProcessing" / "velocityProbes" / "0"
    velocity_line = _latest_data_line(probe_root / "U")
    if velocity_line is not None:
        vectors = [
            [float(component) for component in match.split()]
            for match in re.findall(r"\(([^()]+)\)", velocity_line)
        ]
        if vectors and all(len(vector) == 3 for vector in vectors):
            observables["velocity_probes"] = vectors
    pressure_values = _latest_numeric_row(probe_root / "p")
    if pressure_values is not None and len(pressure_values) >= 2:
        observables["pressure_probes"] = pressure_values[1:]
    return observables


def _latest_numeric_row(path: Path) -> list[float] | None:
    line = _latest_data_line(path)
    if line is None:
        return None
    return [
        float(value)
        for value in re.findall(r"[-+]?(?:\d*\.?\d+)(?:[eE][-+]?\d+)?", line)
    ]


def _latest_data_line(path: Path) -> str | None:
    if not path.is_file():
        return None
    latest: tuple[float, str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        first = stripped.split(maxsplit=1)[0]
        try:
            time_value = float(first)
        except ValueError:
            continue
        if latest is None or time_value > latest[0]:
            latest = (time_value, stripped)
    return None if latest is None else latest[1]


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


def _is_numeric_time(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


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
