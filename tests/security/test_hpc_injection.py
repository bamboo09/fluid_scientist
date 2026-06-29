import pytest

from fluid_scientist.execution.hpc import (
    RemoteRelativePath,
    SafeSlurmValue,
    UnsafeValueError,
)


@pytest.mark.parametrize(
    "value",
    ["job;rm-rf", "../outside", "$(curl-bad)", "name\n#SBATCH --uid=0", "white space"],
)
def test_slurm_values_reject_control_and_shell_syntax(value: str) -> None:
    with pytest.raises(UnsafeValueError):
        SafeSlurmValue(value)


@pytest.mark.parametrize(
    "value",
    ["../outside", "/absolute/path", "projects/../../secret", "projects/demo;rm"],
)
def test_remote_paths_cannot_escape_project_root(value: str) -> None:
    with pytest.raises(UnsafeValueError):
        RemoteRelativePath(value)
