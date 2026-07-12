"""OpenFOAM validation runners for local and workstation-backed checks."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.workstations.connection import WorkstationConnectionService
from fluid_scientist.workstations.models import PlatformStatus, WorkstationProfile
from fluid_scientist.workstations.profile_store import WorkstationProfileStore
from fluid_scientist.workstations.ssh_runner import SSHCommandRunner

ValidationFailureCode = Literal[
    "LOCAL_OPENFOAM_NOT_FOUND",
    "WORKSTATION_PROFILE_REQUIRED",
    "WORKSTATION_NOT_READY",
    "REMOTE_UPLOAD_FAILED",
    "OPENFOAM_COMMAND_FAILED",
]


class OpenFOAMValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_dir: str
    commands: list[str] = Field(default_factory=lambda: ["blockMesh", "checkMesh"])
    expected_outputs: list[str] = Field(default_factory=list)
    solver: str | None = None
    fixture_id: str = ""
    generated_config_hash: str = ""


class OpenFOAMValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner: Literal["local", "remote", "none"]
    passed: bool = False
    error_code: ValidationFailureCode | None = None
    error_message: str = ""
    profile_id: str | None = None
    openfoam_version: str | None = None
    artifact_hash: str = ""
    commands: list[str] = Field(default_factory=list)
    exit_codes: list[int] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    actual_outputs: list[str] = Field(default_factory=list)
    remote_case_dir: str | None = None


class OpenFOAMValidationRunner(Protocol):
    def validate(self, request: OpenFOAMValidationRequest) -> OpenFOAMValidationReport: ...


class TypedCommandBuilder:
    """Build allowlisted OpenFOAM commands for a case directory."""

    _BASE_ALLOWED = {
        "blockMesh",
        "checkMesh",
        "postProcess",
        "foamRun",
        "decomposePar",
        "reconstructPar",
    }
    _APPROVED_SOLVERS = {"icoFoam", "pisoFoam", "pimpleFoam", "simpleFoam", "foamRun"}

    def __init__(self, *, remote: bool = False) -> None:
        self.remote = remote

    def build(self, command: str, *, case_dir: str, solver: str | None = None) -> list[str] | str:
        if command == "solver":
            if not solver or solver not in self._APPROVED_SOLVERS:
                raise ValueError("solver command requires an approved solver")
            executable = solver
        elif command in self._BASE_ALLOWED:
            executable = command
        else:
            raise ValueError(f"OpenFOAM command is not allowlisted: {command}")

        if self.remote:
            return f"cd {_sq(case_dir)} && {executable}"
        return [executable, "-case", case_dir]


class LocalOpenFOAMValidationRunner:
    def __init__(self, *, command_builder: TypedCommandBuilder | None = None) -> None:
        self._builder = command_builder or TypedCommandBuilder(remote=False)

    def validate(self, request: OpenFOAMValidationRequest) -> OpenFOAMValidationReport:
        case_dir = str(Path(request.case_dir).resolve())
        missing = [
            cmd for cmd in _expanded_commands(request)
            if cmd != "solver" and shutil.which(cmd) is None
        ]
        if missing:
            return OpenFOAMValidationReport(
                runner="local",
                passed=False,
                error_code="LOCAL_OPENFOAM_NOT_FOUND",
                error_message=f"OpenFOAM command not found: {missing[0]}",
                artifact_hash=_hash_case(Path(case_dir)),
                expected_outputs=request.expected_outputs,
            )

        commands: list[str] = []
        exit_codes: list[int] = []
        logs: list[str] = []
        for command in _expanded_commands(request):
            argv = self._builder.build(command, case_dir=case_dir, solver=request.solver)
            assert isinstance(argv, list)
            completed = subprocess.run(
                argv,
                cwd=case_dir,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            commands.append(" ".join(argv))
            exit_codes.append(completed.returncode)
            logs.append((completed.stdout + "\n" + completed.stderr)[-4000:])
            if completed.returncode != 0:
                return OpenFOAMValidationReport(
                    runner="local",
                    passed=False,
                    error_code="OPENFOAM_COMMAND_FAILED",
                    error_message=f"{command} failed with exit code {completed.returncode}",
                    artifact_hash=_hash_case(Path(case_dir)),
                    commands=commands,
                    exit_codes=exit_codes,
                    logs=logs,
                    expected_outputs=request.expected_outputs,
                )
        return OpenFOAMValidationReport(
            runner="local",
            passed=True,
            artifact_hash=_hash_case(Path(case_dir)),
            commands=commands,
            exit_codes=exit_codes,
            logs=logs,
            expected_outputs=request.expected_outputs,
            actual_outputs=_collect_outputs(Path(case_dir), request.expected_outputs),
        )


class RemoteOpenFOAMValidationRunner:
    def __init__(
        self,
        *,
        store: WorkstationProfileStore | None = None,
        connection: WorkstationConnectionService | None = None,
        runner: SSHCommandRunner | None = None,
        command_builder: TypedCommandBuilder | None = None,
    ) -> None:
        self._runner = runner or SSHCommandRunner()
        self._store = store or WorkstationProfileStore()
        self._connection = connection or WorkstationConnectionService(
            runner=self._runner,
            store=self._store,
        )
        self._builder = command_builder or TypedCommandBuilder(remote=True)

    def validate(self, request: OpenFOAMValidationRequest) -> OpenFOAMValidationReport:
        profile = self._store.get_default()
        if profile is None:
            return OpenFOAMValidationReport(
                runner="none",
                passed=False,
                error_code="WORKSTATION_PROFILE_REQUIRED",
                error_message="No default WorkstationProfile is configured.",
                artifact_hash=_hash_case(Path(request.case_dir)),
                expected_outputs=request.expected_outputs,
            )
        if profile.platform_status != PlatformStatus.READY:
            return OpenFOAMValidationReport(
                runner="remote",
                passed=False,
                profile_id=profile.profile_id,
                openfoam_version=profile.openfoam_version,
                error_code="WORKSTATION_NOT_READY",
                error_message="Default WorkstationProfile is not READY.",
                artifact_hash=_hash_case(Path(request.case_dir)),
                expected_outputs=request.expected_outputs,
            )

        probe = self._connection.test_profile(profile.profile_id)
        if probe.error_code or not probe.openfoam or not probe.openfoam.available:
            return OpenFOAMValidationReport(
                runner="remote",
                passed=False,
                profile_id=profile.profile_id,
                openfoam_version=profile.openfoam_version,
                error_code="WORKSTATION_NOT_READY",
                error_message=probe.error_message or "OpenFOAM is not available on the default workstation.",
                artifact_hash=_hash_case(Path(request.case_dir)),
                expected_outputs=request.expected_outputs,
            )

        remote_base = (
            probe.remote_workspace.remote_base_dir
            if probe.remote_workspace and probe.remote_workspace.writable
            else profile.remote_base_dir
        )
        if not remote_base:
            return OpenFOAMValidationReport(
                runner="remote",
                passed=False,
                profile_id=profile.profile_id,
                openfoam_version=profile.openfoam_version,
                error_code="WORKSTATION_NOT_READY",
                error_message="Default workstation has no ready remote base directory.",
                artifact_hash=_hash_case(Path(request.case_dir)),
                expected_outputs=request.expected_outputs,
            )

        remote_case_dir = f"{remote_base.rstrip('/')}/validation/{uuid.uuid4().hex}"
        mkdir = self._runner.run_remote(
            profile.host_alias,
            f"mkdir -p {_sq(remote_case_dir)}",
            timeout=30,
        )
        if mkdir.returncode != 0:
            return self._failed_upload(request, profile, remote_case_dir, mkdir.stderr)

        copy = getattr(self._runner, "copy_tree_to_remote", None)
        if not callable(copy) or not copy(profile.host_alias, Path(request.case_dir), remote_case_dir):
            return self._failed_upload(request, profile, remote_case_dir, "case upload failed")

        commands: list[str] = []
        exit_codes: list[int] = []
        logs: list[str] = []
        for command in _expanded_commands(request):
            remote_command = self._builder.build(
                command,
                case_dir=remote_case_dir,
                solver=request.solver,
            )
            assert isinstance(remote_command, str)
            result = self._runner.run_remote(profile.host_alias, remote_command, timeout=180)
            commands.append(command)
            exit_codes.append(result.returncode)
            logs.append((result.stdout + "\n" + result.stderr)[-4000:])
            if result.returncode != 0:
                self._cleanup(profile, remote_case_dir)
                return OpenFOAMValidationReport(
                    runner="remote",
                    passed=False,
                    profile_id=profile.profile_id,
                    openfoam_version=profile.openfoam_version,
                    remote_case_dir=remote_case_dir,
                    error_code="OPENFOAM_COMMAND_FAILED",
                    error_message=f"{command} failed with exit code {result.returncode}",
                    artifact_hash=_hash_case(Path(request.case_dir)),
                    commands=commands,
                    exit_codes=exit_codes,
                    logs=logs,
                    expected_outputs=request.expected_outputs,
                )

        self._cleanup(profile, remote_case_dir)
        return OpenFOAMValidationReport(
            runner="remote",
            passed=True,
            profile_id=profile.profile_id,
            openfoam_version=profile.openfoam_version,
            remote_case_dir=remote_case_dir,
            artifact_hash=_hash_case(Path(request.case_dir)),
            commands=commands,
            exit_codes=exit_codes,
            logs=logs,
            expected_outputs=request.expected_outputs,
            actual_outputs=request.expected_outputs,
        )

    def _failed_upload(
        self,
        request: OpenFOAMValidationRequest,
        profile: WorkstationProfile,
        remote_case_dir: str,
        message: str,
    ) -> OpenFOAMValidationReport:
        return OpenFOAMValidationReport(
            runner="remote",
            passed=False,
            profile_id=profile.profile_id,
            openfoam_version=profile.openfoam_version,
            remote_case_dir=remote_case_dir,
            error_code="REMOTE_UPLOAD_FAILED",
            error_message=message,
            artifact_hash=_hash_case(Path(request.case_dir)),
            expected_outputs=request.expected_outputs,
        )

    def _cleanup(self, profile: WorkstationProfile, remote_case_dir: str) -> None:
        self._runner.run_remote(
            profile.host_alias,
            f"rm -rf {_sq(remote_case_dir)}",
            timeout=30,
        )


def _expanded_commands(request: OpenFOAMValidationRequest) -> list[str]:
    commands = list(request.commands)
    if request.solver and "solver" not in commands:
        commands.append("solver")
    return commands


def _hash_case(case_dir: Path) -> str:
    h = hashlib.sha256()
    if case_dir.exists():
        for path in sorted(p for p in case_dir.rglob("*") if p.is_file()):
            h.update(str(path.relative_to(case_dir)).replace("\\", "/").encode("utf-8"))
            h.update(path.read_bytes())
    return f"sha256:{h.hexdigest()}"


def _collect_outputs(case_dir: Path, expected: list[str]) -> list[str]:
    found: list[str] = []
    for rel in expected:
        if (case_dir / rel).exists():
            found.append(rel)
    return found


def _sq(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def write_validation_report(path: Path, report: OpenFOAMValidationReport) -> str:
    payload = report.model_dump()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
