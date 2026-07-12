"""Tests for WorkstationConnectionService: full probe flow and profile management.

Covers connection-state separation (SSH_CONNECTED / OPENFOAM_DETECTED / READY),
error-code mapping for various failure scenarios, profile persistence, and
host-key confirmation.
"""

from __future__ import annotations

from fluid_scientist.execution.ssh import ProcessResult
from fluid_scientist.workstations.connection import WorkstationConnectionService
from fluid_scientist.workstations.models import (
    CandidateSource,
    ConnectionStatus,
    CredentialSource,
    KnownHostStatus,
    PlatformStatus,
    SchedulerType,
    WorkstationCandidate,
    WorkstationErrorCode,
)
from fluid_scientist.workstations.profile_store import WorkstationProfileStore

# ---------------------------------------------------------------------------
# Mock SSH runner for connection tests
# ---------------------------------------------------------------------------


# Canned remote outputs for a fully-READY host.
_OPENFOAM_ACTIVE_STDOUT = (
    "/usr/lib/openfoam/openfoam-13/bin/foamRun\n"
    "/usr/lib/openfoam/openfoam-13/bin/blockMesh\n"
    "/usr/lib/openfoam/openfoam-13/bin/checkMesh\n"
    "/usr/lib/openfoam/openfoam-13/bin/postProcess\n"
    "/usr/lib/openfoam/openfoam-13/bin/decomposePar\n"
    "/opt/OpenFOAM/OpenFOAM-13\n"
    "13\n"
)
_OPENFOAM_MODULES_STDOUT = ""
_SLURM_STDOUT = "/usr/bin/sbatch\n/usr/bin/squeue\n/usr/bin/srun\n"
_RESOURCE_STDOUT = (
    "__FS_CPU__\n64\n"
    "__FS_MEM__\n135291463680\n"
    "__FS_OS__\nLinux\n"
    "__FS_ARCH__\nx86_64\n"
    "__FS_HOST__\nhpc-node-01\n"
    "__FS_DISK__\n5368709120\n"
)
_WORKSPACE_READY_STDOUT = (
    "READY::scratch::/scratch/researcher/fluid_scientist/runs::5368709120\n"
)


class MockConnectionRunner:
    """Configurable mock that simulates SSHCommandRunner for connection tests.

    Each aspect of the SSH lifecycle (installed check, host resolution,
    host-key status, authentication, remote commands) can be independently
    configured to simulate different scenarios.
    """

    def __init__(
        self,
        *,
        ssh_installed: bool = True,
        resolve: dict | None = None,
        host_key_status: KnownHostStatus = KnownHostStatus.KNOWN,
        fingerprint: str | None = "SHA256:abc123",
        auth_ok: bool = True,
        openfoam_stdout: str = _OPENFOAM_ACTIVE_STDOUT,
        modules_stdout: str = _OPENFOAM_MODULES_STDOUT,
        scheduler_stdout: str = _SLURM_STDOUT,
        resource_stdout: str = _RESOURCE_STDOUT,
        workspace_stdout: str = _WORKSPACE_READY_STDOUT,
        confirm_ok: bool = True,
    ) -> None:
        self._ssh_installed = ssh_installed
        self._resolve = resolve or {
            "hostname": "hpc.example.com",
            "user": "researcher",
            "port": 22,
        }
        self._host_key_status = host_key_status
        self._fingerprint = fingerprint
        self._auth_ok = auth_ok
        self._openfoam_stdout = openfoam_stdout
        self._modules_stdout = modules_stdout
        self._scheduler_stdout = scheduler_stdout
        self._resource_stdout = resource_stdout
        self._workspace_stdout = workspace_stdout
        self._confirm_ok = confirm_ok

    def check_ssh_installed(self) -> bool:
        return self._ssh_installed

    def resolve_host(self, host_alias: str) -> dict:
        return dict(self._resolve)

    def check_ssh_agent(self) -> dict:
        return {"available": True, "has_identities": True}

    def get_host_key_status(self, host_alias: str) -> KnownHostStatus:
        return self._host_key_status

    def get_host_fingerprint(self, host_alias: str) -> str | None:
        return self._fingerprint

    def confirm_host_key(self, host_alias: str) -> bool:
        return self._confirm_ok

    def test_authentication(self, host_alias: str, *, timeout: float) -> bool:
        return self._auth_ok

    def run_remote(
        self, host_alias: str, command: str, *, timeout: float
    ) -> ProcessResult:
        if "foamRun" in command:
            return ProcessResult(0, self._openfoam_stdout, "")
        if "module -t avail" in command:
            return ProcessResult(0, self._modules_stdout, "")
        if "sbatch" in command:
            return ProcessResult(0, self._scheduler_stdout, "")
        if "__FS_CPU__" in command:
            return ProcessResult(0, self._resource_stdout, "")
        if "min=1073741824" in command:
            return ProcessResult(0, self._workspace_stdout, "")
        return ProcessResult(0, "", "")


def _make_candidate(
    host_alias: str = "hpc",
    resolved_host: str = "hpc.example.com",
) -> WorkstationCandidate:
    return WorkstationCandidate(
        candidate_id=f"ssh-config:{host_alias}",
        host_alias=host_alias,
        display_name=host_alias,
        resolved_host=resolved_host,
        resolved_user="researcher",
        resolved_port=22,
        credential_source=CredentialSource.SSH_CONFIG,
        known_host_status=KnownHostStatus.KNOWN,
        connection_status=ConnectionStatus.UNTESTED,
        source=CandidateSource.SSH_CONFIG,
    )


# ---------------------------------------------------------------------------
# Probe: all-success → READY
# ---------------------------------------------------------------------------


class TestProbeReady:
    def test_full_success_returns_no_error(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.error_code is None
        assert result.ssh_connected is True
        assert result.openfoam is not None
        assert result.openfoam.available is True
        assert result.scheduler is not None
        assert result.scheduler.scheduler == SchedulerType.SLURM
        assert result.resources is not None
        assert result.resources.hostname == "hpc-node-01"
        assert result.remote_workspace is not None
        assert result.remote_workspace.writable is True

    def test_save_profile_ready(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        candidate = _make_candidate()
        probe_result = svc.probe(candidate)
        profile = svc.save_profile(candidate, probe_result, display_name="My HPC")

        assert profile.platform_status == PlatformStatus.READY
        assert profile.openfoam_available is True
        assert profile.scheduler == SchedulerType.SLURM
        assert profile.connection_status == "REACHABLE"
        assert profile.display_name == "My HPC"
        assert profile.is_default is True  # first profile becomes default

    def test_save_profile_sets_default_for_first(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        candidate = _make_candidate("hpc-1")
        probe_result = svc.probe(candidate)
        profile1 = svc.save_profile(candidate, probe_result)

        assert profile1.is_default is True
        assert store.get_default().profile_id == profile1.profile_id

    def test_save_profile_second_does_not_steal_default(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        c1 = _make_candidate("hpc-1")
        p1 = svc.save_profile(c1, svc.probe(c1))

        c2 = _make_candidate("hpc-2")
        p2 = svc.save_profile(c2, svc.probe(c2))

        assert p1.is_default is True
        assert p2.is_default is False
        assert store.get_default().profile_id == p1.profile_id


# ---------------------------------------------------------------------------
# Probe: SSH connected but no OpenFOAM → DEGRADED
# ---------------------------------------------------------------------------


class TestProbeDegraded:
    def test_ssh_ok_no_openfoam_yields_degraded(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(
                openfoam_stdout="",
                modules_stdout="",
            ),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.error_code is None
        assert result.ssh_connected is True
        assert result.openfoam is not None
        assert result.openfoam.available is False

    def test_save_profile_degraded(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(
                openfoam_stdout="",
                modules_stdout="",
            ),
            store=store,
        )
        candidate = _make_candidate()
        probe_result = svc.probe(candidate)
        profile = svc.save_profile(candidate, probe_result)

        assert profile.platform_status == PlatformStatus.DEGRADED
        assert profile.openfoam_available is False
        assert profile.connection_status == "REACHABLE"

    def test_ssh_ok_workspace_not_writable_yields_degraded(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(
                workspace_stdout="FAILED\n",
            ),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.error_code is None
        assert result.ssh_connected is True
        assert result.remote_workspace is not None
        assert result.remote_workspace.writable is False

        candidate = _make_candidate()
        profile = svc.save_profile(candidate, result)
        assert profile.platform_status == PlatformStatus.DEGRADED


# ---------------------------------------------------------------------------
# Probe: authentication failure → NO_USABLE_SYSTEM_SSH_IDENTITY
# ---------------------------------------------------------------------------


class TestProbeAuthFailure:
    def test_auth_failure_returns_error_code(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(auth_ok=False),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.ssh_connected is False
        assert result.error_code == WorkstationErrorCode.NO_USABLE_SYSTEM_SSH_IDENTITY.value
        assert "authentication" in (result.error_message or "").lower()

    def test_auth_failure_no_openfoam_probed(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(auth_ok=False),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.openfoam is None
        assert result.scheduler is None
        assert result.resources is None


# ---------------------------------------------------------------------------
# Probe: unknown host key → HOST_KEY_CONFIRMATION_REQUIRED
# ---------------------------------------------------------------------------


class TestProbeHostKeyUnknown:
    def test_unknown_host_key_returns_error_code(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(
                host_key_status=KnownHostStatus.UNKNOWN,
            ),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.ssh_connected is False
        assert result.error_code == WorkstationErrorCode.HOST_KEY_CONFIRMATION_REQUIRED.value
        assert "known_hosts" in (result.error_message or "")

    def test_unknown_host_key_includes_fingerprint(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(
                host_key_status=KnownHostStatus.UNKNOWN,
                fingerprint="SHA256:xyz789",
            ),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert "SHA256:xyz789" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Probe: host key changed → HOST_KEY_CHANGED
# ---------------------------------------------------------------------------


class TestProbeHostKeyChanged:
    def test_changed_host_key_returns_error_code(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(
                host_key_status=KnownHostStatus.CHANGED,
            ),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.error_code == WorkstationErrorCode.HOST_KEY_CHANGED.value
        assert result.ssh_connected is False


# ---------------------------------------------------------------------------
# Probe: SSH not installed → SSH_NOT_INSTALLED
# ---------------------------------------------------------------------------


class TestProbeSSHNotInstalled:
    def test_ssh_not_installed_returns_error_code(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(ssh_installed=False),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.error_code == WorkstationErrorCode.SSH_NOT_INSTALLED.value
        assert result.ssh_connected is False


# ---------------------------------------------------------------------------
# Probe: remote command failure → REMOTE_COMMAND_FAILED
# ---------------------------------------------------------------------------


class TestProbeRemoteCommandFailed:
    def test_no_hostname_returns_remote_command_failed(self, tmp_path):
        """When SSH auth succeeds but remote commands return empty, the
        error is REMOTE_COMMAND_FAILED."""
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(
                resource_stdout="",
                openfoam_stdout="",
                scheduler_stdout="",
                workspace_stdout="",
            ),
            store=store,
        )
        result = svc.probe(_make_candidate())

        assert result.ssh_connected is True
        assert result.error_code == WorkstationErrorCode.REMOTE_COMMAND_FAILED.value


# ---------------------------------------------------------------------------
# Profile management
# ---------------------------------------------------------------------------


class TestSaveProfile:
    def test_profile_has_no_sensitive_fields(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        candidate = _make_candidate()
        probe_result = svc.probe(candidate)
        profile = svc.save_profile(candidate, probe_result)

        data = profile.model_dump()
        for key in ("private_key", "private_key_path", "password", "passphrase", "raw_credential"):
            assert key not in data

    def test_profile_records_environment_facts(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        candidate = _make_candidate()
        probe_result = svc.probe(candidate)
        profile = svc.save_profile(candidate, probe_result)

        assert profile.host_alias == "hpc"
        assert profile.resolved_host == "hpc.example.com"
        assert profile.detected_username == "researcher"
        assert profile.detected_port == 22
        assert profile.openfoam_version == "13"
        assert profile.scheduler == SchedulerType.SLURM
        assert profile.remote_base_dir == "/scratch/researcher/fluid_scientist/runs"
        assert profile.remote_os == "Linux"
        assert profile.cpu_count == 64
        assert profile.known_host_fingerprint == "SHA256:abc123"

    def test_display_name_defaults_to_candidate(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        candidate = _make_candidate()
        probe_result = svc.probe(candidate)
        profile = svc.save_profile(candidate, probe_result, display_name=None)

        assert profile.display_name == "hpc"


class TestTestProfile:
    def test_test_profile_reprobes(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        candidate = _make_candidate()
        probe_result = svc.probe(candidate)
        profile = svc.save_profile(candidate, probe_result)

        reprobe = svc.test_profile(profile.profile_id)
        assert reprobe.error_code is None
        assert reprobe.ssh_connected is True

    def test_test_profile_missing_returns_error(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        result = svc.test_profile("nonexistent")
        assert result.error_code == WorkstationErrorCode.PROFILE_NOT_FOUND.value


class TestConfirmHostKey:
    def test_confirm_host_key_delegates_to_runner(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(confirm_ok=True),
            store=store,
        )
        assert svc.confirm_host_key("hpc") is True

    def test_confirm_host_key_failure_returns_false(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(confirm_ok=False),
            store=store,
        )
        assert svc.confirm_host_key("hpc") is False


# ---------------------------------------------------------------------------
# Connection-state separation
# ---------------------------------------------------------------------------


class TestConnectionStateSeparation:
    """Verify that SSH_CONNECTED, OPENFOAM_DETECTED, and READY are
    independently reflected in the profile's platform_status."""

    def test_all_three_pass_yields_ready(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(),
            store=store,
        )
        candidate = _make_candidate()
        profile = svc.save_profile(candidate, svc.probe(candidate))
        assert profile.platform_status == PlatformStatus.READY

    def test_ssh_ok_openfoam_missing_yields_degraded(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(openfoam_stdout="", modules_stdout=""),
            store=store,
        )
        candidate = _make_candidate()
        profile = svc.save_profile(candidate, svc.probe(candidate))
        assert profile.platform_status == PlatformStatus.DEGRADED

    def test_ssh_ok_workspace_missing_yields_degraded(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(workspace_stdout="FAILED\n"),
            store=store,
        )
        candidate = _make_candidate()
        profile = svc.save_profile(candidate, svc.probe(candidate))
        assert profile.platform_status == PlatformStatus.DEGRADED

    def test_ssh_fail_yields_unavailable(self, tmp_path):
        store = WorkstationProfileStore(db_path=str(tmp_path / "test.db"))
        svc = WorkstationConnectionService(
            runner=MockConnectionRunner(auth_ok=False),
            store=store,
        )
        candidate = _make_candidate()
        result = svc.probe(candidate)
        # Cannot save_profile with a failed probe in the API (it would raise),
        # but we can verify the probe result directly.
        assert result.ssh_connected is False
