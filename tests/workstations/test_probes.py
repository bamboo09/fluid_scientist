"""Tests for remote environment probes: OpenFOAM, scheduler, resource, and workspace."""

from __future__ import annotations

from fluid_scientist.execution.ssh import ProcessResult
from fluid_scientist.workstations.models import (
    OpenFOAMActivationMethod,
    SchedulerType,
)
from fluid_scientist.workstations.probes import (
    OpenFOAMEnvironmentProbe,
    RemoteWorkspaceProbe,
    ResourceProbe,
    SchedulerProbe,
)

# ---------------------------------------------------------------------------
# Mock SSH runner for probe tests
# ---------------------------------------------------------------------------


class MockProbeRunner:
    """Mock SSHCommandRunner that returns canned stdout per command.

    The probe helper ``_run`` calls ``runner.run_remote(host_alias,
    command, timeout=timeout)`` and returns ``result.stdout``.  This mock
    inspects the *command* string and returns the matching canned output.
    """

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        # Each key is a substring to match inside the command; the first
        # match wins.
        self._responses: list[tuple[str, str]] = []
        if responses:
            for key, value in responses.items():
                self._responses.append((key, value))

    def run_remote(self, host_alias: str, command: str, *, timeout: float) -> ProcessResult:
        for key, stdout in self._responses:
            if key in command:
                return ProcessResult(0, stdout, "")
        return ProcessResult(0, "", "")


# ---------------------------------------------------------------------------
# OpenFOAMEnvironmentProbe
# ---------------------------------------------------------------------------


class TestOpenFOAMProbe:
    def test_detects_already_active_openfoam(self):
        """When foamRun and all binaries are on PATH, OpenFOAM is already active."""
        stdout = (
            "/usr/lib/openfoam/openfoam-13/bin/foamRun\n"
            "/usr/lib/openfoam/openfoam-13/bin/blockMesh\n"
            "/usr/lib/openfoam/openfoam-13/bin/checkMesh\n"
            "/usr/lib/openfoam/openfoam-13/bin/postProcess\n"
            "/usr/lib/openfoam/openfoam-13/bin/decomposePar\n"
            "/opt/OpenFOAM/OpenFOAM-13\n"
            "13\n"
        )
        runner = MockProbeRunner({"foamRun": stdout})
        result = OpenFOAMEnvironmentProbe().probe("hpc", runner)

        assert result.available is True
        assert result.version == "13"
        assert result.wm_project_dir == "/opt/OpenFOAM/OpenFOAM-13"
        assert result.activation_method == OpenFOAMActivationMethod.ALREADY_ACTIVE
        assert result.commands["foamRun"] is True
        assert result.commands["blockMesh"] is True
        assert result.commands["checkMesh"] is True
        assert result.commands["postProcess"] is True
        assert result.commands["decomposePar"] is True

    def test_detects_openfoam_via_modules(self):
        """When not active in login shell but available via module load."""
        login_stdout = ""  # no binaries found in login shell
        modules_stdout = (
            "openfoam-13\n"
            "openfoam-v2406\n"
        )
        runner = MockProbeRunner({
            "foamRun": login_stdout,
            "module -t avail": modules_stdout,
        })
        result = OpenFOAMEnvironmentProbe().probe("hpc", runner)

        assert result.available is False
        assert result.activation_method == OpenFOAMActivationMethod.ENVIRONMENT_MODULE
        assert "openfoam-13" in (result.activation_reference or "")
        assert "openfoam-v2406" in (result.activation_reference or "")

    def test_openfoam_not_found(self):
        """When neither login shell nor modules have OpenFOAM."""
        runner = MockProbeRunner({
            "foamRun": "",
            "module -t avail": "",
        })
        result = OpenFOAMEnvironmentProbe().probe("hpc", runner)

        assert result.available is False
        assert result.activation_method is None
        assert result.activation_reference is None
        assert result.version is None

    def test_version_extracted_from_banner(self):
        """Version is mined from the foamVersion banner if WM_PROJECT_VERSION is absent."""
        stdout = (
            "/usr/bin/foamRun\n"
            "/usr/bin/blockMesh\n"
            "/usr/bin/checkMesh\n"
            "/usr/bin/postProcess\n"
            "/usr/bin/decomposePar\n"
            "/opt/OpenFOAM/OpenFOAM-13\n"
            "\n"
            "OpenFOAM version v2406\n"
        )
        runner = MockProbeRunner({"foamRun": stdout})
        result = OpenFOAMEnvironmentProbe().probe("hpc", runner)

        assert result.available is True
        # WM_PROJECT_VERSION line is empty, so version comes from banner
        assert result.version == "v2406"

    def test_partial_binaries_still_not_available(self):
        """foamRun must be present for OpenFOAM to be considered available."""
        stdout = (
            "/usr/bin/blockMesh\n"
            "/usr/bin/checkMesh\n"
        )
        runner = MockProbeRunner({
            "foamRun": stdout,
            "module -t avail": "",
        })
        result = OpenFOAMEnvironmentProbe().probe("hpc", runner)

        assert result.available is False
        assert result.commands["foamRun"] is False
        assert result.commands["blockMesh"] is True


# ---------------------------------------------------------------------------
# SchedulerProbe
# ---------------------------------------------------------------------------


class TestSchedulerProbe:
    def test_detects_slurm(self):
        stdout = (
            "/usr/bin/sbatch\n"
            "/usr/bin/squeue\n"
            "/usr/bin/srun\n"
        )
        runner = MockProbeRunner({"sbatch": stdout})
        result = SchedulerProbe().probe("hpc", runner)

        assert result.scheduler == SchedulerType.SLURM
        assert result.commands["sbatch"] is True
        assert result.commands["squeue"] is True
        assert result.commands["srun"] is True
        assert result.commands["qsub"] is False
        assert result.commands["qstat"] is False

    def test_detects_pbs(self):
        stdout = (
            "/usr/bin/qsub\n"
            "/usr/bin/qstat\n"
        )
        runner = MockProbeRunner({"sbatch": stdout})
        result = SchedulerProbe().probe("hpc", runner)

        assert result.scheduler == SchedulerType.PBS
        assert result.commands["qsub"] is True
        assert result.commands["qstat"] is True
        assert result.commands["sbatch"] is False

    def test_detects_no_scheduler(self):
        runner = MockProbeRunner({"sbatch": ""})
        result = SchedulerProbe().probe("hpc", runner)

        assert result.scheduler == SchedulerType.NONE
        assert all(v is False for v in result.commands.values())

    def test_slurm_takes_precedence_over_pbs(self):
        """When both Slurm and PBS commands exist, Slurm wins."""
        stdout = (
            "/usr/bin/sbatch\n"
            "/usr/bin/squeue\n"
            "/usr/bin/srun\n"
            "/usr/bin/qsub\n"
            "/usr/bin/qstat\n"
        )
        runner = MockProbeRunner({"sbatch": stdout})
        result = SchedulerProbe().probe("hpc", runner)

        assert result.scheduler == SchedulerType.SLURM


# ---------------------------------------------------------------------------
# ResourceProbe
# ---------------------------------------------------------------------------


class TestResourceProbe:
    def test_parses_all_resource_fields(self):
        stdout = (
            "__FS_CPU__\n"
            "64\n"
            "__FS_MEM__\n"
            "135291463680\n"
            "__FS_OS__\n"
            "Linux\n"
            "__FS_ARCH__\n"
            "x86_64\n"
            "__FS_HOST__\n"
            "hpc-node-01\n"
            "__FS_DISK__\n"
            "1073741824\n"
        )
        runner = MockProbeRunner({"__FS_CPU__": stdout})
        result = ResourceProbe().probe("hpc", runner)

        assert result.cpu_count == 64
        assert result.memory_bytes == 135291463680
        assert result.os == "Linux"
        assert result.architecture == "x86_64"
        assert result.hostname == "hpc-node-01"
        assert result.disk_available_bytes == 1073741824

    def test_parses_meminfo_format(self):
        """When free is unavailable, /proc/meminfo format is used."""
        stdout = (
            "__FS_CPU__\n"
            "32\n"
            "__FS_MEM__\n"
            "MemTotal:       65980944 kB\n"
            "__FS_OS__\n"
            "Linux\n"
            "__FS_ARCH__\n"
            "aarch64\n"
            "__FS_HOST__\n"
            "arm-node\n"
            "__FS_DISK__\n"
            "536870912\n"
        )
        runner = MockProbeRunner({"__FS_CPU__": stdout})
        result = ResourceProbe().probe("hpc", runner)

        assert result.cpu_count == 32
        assert result.memory_bytes == 65980944 * 1024
        assert result.architecture == "aarch64"

    def test_empty_output_returns_none_fields(self):
        runner = MockProbeRunner({})
        result = ResourceProbe().probe("hpc", runner)

        assert result.cpu_count is None
        assert result.memory_bytes is None
        assert result.os is None
        assert result.hostname is None


# ---------------------------------------------------------------------------
# RemoteWorkspaceProbe
# ---------------------------------------------------------------------------


class TestRemoteWorkspaceProbe:
    def test_selects_scratch_when_available(self):
        stdout = "READY::scratch::/scratch/user/fluid_scientist/runs::5368709120\n"
        runner = MockProbeRunner({"min=1073741824": stdout})
        result = RemoteWorkspaceProbe().probe("hpc", runner)

        assert result.writable is True
        assert result.remote_base_dir == "/scratch/user/fluid_scientist/runs"
        assert result.disk_available_bytes == 5368709120

    def test_falls_back_to_home_existing(self):
        stdout = "READY::home_existing::/home/user/fluid_scientist/runs::1073741824\n"
        runner = MockProbeRunner({"min=1073741824": stdout})
        result = RemoteWorkspaceProbe().probe("hpc", runner)

        assert result.writable is True
        assert result.remote_base_dir == "/home/user/fluid_scientist/runs"
        assert result.disk_available_bytes == 1073741824

    def test_falls_back_to_home_created(self):
        stdout = "READY::home_created::/home/user/fluid_scientist/runs::2147483648\n"
        runner = MockProbeRunner({"min=1073741824": stdout})
        result = RemoteWorkspaceProbe().probe("hpc", runner)

        assert result.writable is True
        assert result.remote_base_dir == "/home/user/fluid_scientist/runs"

    def test_returns_not_writable_on_failure(self):
        stdout = "FAILED\n"
        runner = MockProbeRunner({"min=1073741824": stdout})
        result = RemoteWorkspaceProbe().probe("hpc", runner)

        assert result.writable is False
        assert result.remote_base_dir == ""
        assert result.disk_available_bytes is None

    def test_returns_not_writable_on_empty_output(self):
        runner = MockProbeRunner({})
        result = RemoteWorkspaceProbe().probe("hpc", runner)

        assert result.writable is False
        assert result.remote_base_dir == ""

    def test_handles_malformed_ready_line(self):
        stdout = "READY::scratch\n"  # missing path and free bytes
        runner = MockProbeRunner({"min=1073741824": stdout})
        result = RemoteWorkspaceProbe().probe("hpc", runner)

        assert result.writable is False
