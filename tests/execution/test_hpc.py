from fluid_scientist.execution.hpc import (
    OpenFOAMCommand,
    RemoteRelativePath,
    SafeSlurmValue,
    SlurmResources,
    render_sbatch,
    sacct_argv,
    sbatch_argv,
    scancel_argv,
    squeue_argv,
)


def test_sbatch_renderer_uses_only_typed_values_and_fixed_commands() -> None:
    script = render_sbatch(
        job_name=SafeSlurmValue("bend-pilot-001"),
        partition=SafeSlurmValue("compute"),
        module_name=SafeSlurmValue("openfoam-v2312"),
        case_dir=RemoteRelativePath("projects/demo/bend-fine"),
        resources=SlurmResources(cpus=8, memory_gb=16, walltime_min=60),
        commands=(OpenFOAMCommand.CHECK_MESH, OpenFOAMCommand.SIMPLE_FOAM),
    )

    assert "#SBATCH --job-name=bend-pilot-001" in script
    assert "#SBATCH --cpus-per-task=8" in script
    assert "module load openfoam-v2312" in script
    assert "checkMesh" in script
    assert "simpleFoam" in script


def test_slurm_commands_are_argv_not_shell_strings() -> None:
    assert sbatch_argv(RemoteRelativePath("jobs/bend.sbatch")) == (
        "sbatch",
        "jobs/bend.sbatch",
    )
    assert sacct_argv(SafeSlurmValue("12345")) == (
        "sacct",
        "--jobs",
        "12345",
        "--parsable2",
        "--noheader",
    )
    assert squeue_argv(SafeSlurmValue("12345")) == (
        "squeue",
        "--jobs",
        "12345",
        "--noheader",
    )
    assert scancel_argv(SafeSlurmValue("12345")) == ("scancel", "12345")
