"""Typed HPC values that prevent free-form shell execution."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from fluid_scientist.compat import StrEnum


class UnsafeValueError(ValueError):
    """Raised before an unsafe value reaches SSH or Slurm."""


_SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")


@dataclass(frozen=True)
class SafeSlurmValue:
    value: str

    def __post_init__(self) -> None:
        if not _SAFE_VALUE.fullmatch(self.value):
            raise UnsafeValueError("Slurm value contains forbidden characters")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class RemoteRelativePath:
    value: str

    def __post_init__(self) -> None:
        path = PurePosixPath(self.value)
        if path.is_absolute() or not _SAFE_PATH.fullmatch(self.value):
            raise UnsafeValueError("remote path must be a safe relative POSIX path")
        if any(part in {".", ".."} for part in path.parts):
            raise UnsafeValueError("remote path cannot contain traversal components")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class SlurmResources:
    cpus: int
    memory_gb: int
    walltime_min: int

    def __post_init__(self) -> None:
        if not 1 <= self.cpus <= 4096:
            raise UnsafeValueError("cpus must be between 1 and 4096")
        if not 1 <= self.memory_gb <= 1_048_576:
            raise UnsafeValueError("memory_gb must be between 1 and 1048576")
        if not 1 <= self.walltime_min <= 43_200:
            raise UnsafeValueError("walltime_min must be between 1 and 43200")


class OpenFOAMCommand(StrEnum):
    BLOCK_MESH = "blockMesh"
    SNAPPY_HEX_MESH = "snappyHexMesh -overwrite"
    CHECK_MESH = "checkMesh"
    SIMPLE_FOAM = "simpleFoam"
    POST_PROCESS = "postProcess"


def render_sbatch(
    *,
    job_name: SafeSlurmValue,
    partition: SafeSlurmValue,
    module_name: SafeSlurmValue,
    case_dir: RemoteRelativePath,
    resources: SlurmResources,
    commands: tuple[OpenFOAMCommand, ...],
) -> str:
    if not commands:
        raise ValueError("at least one OpenFOAM command is required")
    hours, minutes = divmod(resources.walltime_min, 60)
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={partition}",
        f"#SBATCH --cpus-per-task={resources.cpus}",
        f"#SBATCH --mem={resources.memory_gb}G",
        f"#SBATCH --time={hours:02d}:{minutes:02d}:00",
        f"module load {module_name}",
        f'cd "$SCRATCH/{case_dir}"',
        *(command.value for command in commands),
    ]
    return "\n".join(lines) + "\n"


def sbatch_argv(script_path: RemoteRelativePath) -> tuple[str, ...]:
    return ("sbatch", str(script_path))


def squeue_argv(job_id: SafeSlurmValue) -> tuple[str, ...]:
    return ("squeue", "--jobs", str(job_id), "--noheader")


def sacct_argv(job_id: SafeSlurmValue) -> tuple[str, ...]:
    return ("sacct", "--jobs", str(job_id), "--parsable2", "--noheader")


def scancel_argv(job_id: SafeSlurmValue) -> tuple[str, ...]:
    return ("scancel", str(job_id))
