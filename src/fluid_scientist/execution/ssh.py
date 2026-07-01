"""Safe OpenSSH transport with strict host-key checking and typed remote arguments."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from fluid_scientist.compat import StrEnum
from fluid_scientist.settings import NodeSettings


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    def run(self, argv: tuple[str, ...], *, timeout: float) -> ProcessResult: ...


class SubprocessRunner:
    def run(self, argv: tuple[str, ...], *, timeout: float) -> ProcessResult:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


class RemoteExecutionError(RuntimeError):
    pass


class RemoteProgram(StrEnum):
    SBATCH = "sbatch"
    SQUEUE = "squeue"
    SACCT = "sacct"
    SCANCEL = "scancel"
    SHA256SUM = "sha256sum"
    MKDIR = "mkdir"
    FLUID_WORKER = ".local/bin/fluid-worker"


_REMOTE_ARG = re.compile(r"^[A-Za-z0-9_./:=+,%\-]+$")
_HOST_OR_USER = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class RemoteArg:
    value: str

    def __post_init__(self) -> None:
        if not _REMOTE_ARG.fullmatch(self.value):
            raise ValueError("remote argument contains forbidden characters")
        if any(part == ".." for part in PurePosixPath(self.value).parts):
            raise ValueError("remote argument cannot contain path traversal")

    def __str__(self) -> str:
        return self.value


class SSHTransport:
    def __init__(self, node: NodeSettings, *, runner: ProcessRunner | None = None) -> None:
        if not node.host or not _HOST_OR_USER.fullmatch(node.host):
            raise ValueError("safe SSH host is required")
        if not node.username or not _HOST_OR_USER.fullmatch(node.username):
            raise ValueError("safe SSH username is required")
        if not node.known_hosts_file:
            raise ValueError("known_hosts_file is required for strict host verification")
        if not Path(node.known_hosts_file).is_file():
            raise ValueError("known_hosts_file does not exist")
        self._node = node
        self._runner = runner or SubprocessRunner()

    def execute(
        self,
        program: RemoteProgram,
        args: tuple[RemoteArg, ...],
        *,
        timeout: float,
    ) -> ProcessResult:
        argv = self._base_argv() + ("--", program.value, *(str(arg) for arg in args))
        result = self._runner.run(argv, timeout=timeout)
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit code {result.returncode}"
            raise RemoteExecutionError(f"remote {program.value} failed: {detail}")
        return result

    def _base_argv(self) -> tuple[str, ...]:
        argv = (
            "ssh",
            "-p",
            str(self._node.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self._node.known_hosts_file}",
        )
        if self._node.identity_file:
            argv += ("-i", self._node.identity_file)
        return argv + (f"{self._node.username}@{self._node.host}",)
