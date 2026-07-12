from __future__ import annotations

import pytest

from fluid_scientist.execution.ssh import ProcessResult
from fluid_scientist.workstations.bootstrap import (
    BootstrapRequest,
    WorkstationBootstrapService,
)
from fluid_scientist.workstations.models import KnownHostStatus, PlatformStatus
from fluid_scientist.workstations.profile_store import WorkstationProfileStore


class BootstrapRunner:
    def __init__(
        self,
        *,
        host_key_status: KnownHostStatus = KnownHostStatus.KNOWN,
        auth_ok: bool = True,
    ) -> None:
        self.host_key_status = host_key_status
        self.auth_ok = auth_ok
        self.registered: dict[str, tuple[str, str, int]] = {}
        self.remote_commands: list[str] = []

    def register_target(self, alias: str, *, hostname: str, username: str, port: int) -> None:
        self.registered[alias] = (hostname, username, port)

    def check_ssh_installed(self) -> bool:
        return True

    def resolve_host(self, host_alias: str) -> dict:
        host, user, port = self.registered.get(host_alias, (host_alias, "researcher", 22))
        return {"hostname": host, "user": user, "port": port}

    def check_ssh_agent(self) -> dict:
        return {"available": True, "has_identities": True}

    def get_host_key_status(self, host_alias: str) -> KnownHostStatus:
        return self.host_key_status

    def get_host_fingerprint(self, host_alias: str) -> str:
        return "SHA256:test"

    def confirm_host_key(self, host_alias: str) -> bool:
        self.host_key_status = KnownHostStatus.KNOWN
        return True

    def test_authentication(self, host_alias: str, *, timeout: float) -> bool:
        return self.auth_ok

    def run_remote(self, host_alias: str, command: str, *, timeout: float) -> ProcessResult:
        self.remote_commands.append(command)
        if "foamRun" in command:
            return ProcessResult(
                0,
                "/opt/OpenFOAM/bin/foamRun\n"
                "/opt/OpenFOAM/bin/blockMesh\n"
                "/opt/OpenFOAM/bin/checkMesh\n"
                "/opt/OpenFOAM/bin/postProcess\n"
                "/opt/OpenFOAM/bin/decomposePar\n"
                "/opt/OpenFOAM/OpenFOAM-13\n13\n",
                "",
            )
        if "sbatch" in command:
            return ProcessResult(0, "/usr/bin/sbatch\n/usr/bin/squeue\n/usr/bin/srun\n", "")
        if "__FS_CPU__" in command:
            return ProcessResult(
                0,
                "__FS_CPU__\n64\n__FS_MEM__\n135291463680\n__FS_OS__\nLinux\n"
                "__FS_ARCH__\nx86_64\n__FS_HOST__\nhpc\n__FS_DISK__\n5368709120\n",
                "",
            )
        if "READY::" in command or "fluid_scientist/runs" in command:
            return ProcessResult(0, "READY::home_created::/home/researcher/fluid_scientist/runs::5368709120\n", "")
        return ProcessResult(0, "", "")


class EmptyDiscovery:
    def discover(self) -> list:
        return []


def test_no_ssh_config_and_no_profiles_returns_minimal_bootstrap(tmp_path):
    store = WorkstationProfileStore(str(tmp_path / "workstations.db"))
    service = WorkstationBootstrapService(
        runner=BootstrapRunner(),
        store=store,
        discovery=EmptyDiscovery(),
    )

    status = service.status()

    assert status.status == "NEEDS_MINIMAL_BOOTSTRAP"
    assert status.discovered_candidates == 0
    assert status.saved_profiles == 0
    assert status.default_profile_id is None


def test_bootstrap_request_rejects_secret_fields():
    with pytest.raises(ValueError):
        BootstrapRequest(
            host="hpc",
            username="researcher",
            port=22,
            private_key_path="C:/secret/key",
        )


def test_bootstrap_saves_ready_default_profile(tmp_path):
    runner = BootstrapRunner()
    store = WorkstationProfileStore(str(tmp_path / "workstations.db"))
    service = WorkstationBootstrapService(
        runner=runner,
        store=store,
        discovery=EmptyDiscovery(),
    )

    result = service.bootstrap(
        BootstrapRequest(
            host="hpc-login",
            username="researcher",
            port=2222,
            display_name="OpenFOAM Workstation",
        )
    )

    assert result.error_code is None
    assert result.status == PlatformStatus.READY.value
    assert runner.registered["hpc-login"] == ("hpc-login", "researcher", 2222)
    default = store.get_default()
    assert default is not None
    assert default.display_name == "OpenFOAM Workstation"
    assert default.connection_method == "SSH_AGENT"
    assert default.openfoam_available is True
    assert default.remote_base_dir == "/home/researcher/fluid_scientist/runs"


def test_bootstrap_unknown_host_key_requires_confirmation(tmp_path):
    store = WorkstationProfileStore(str(tmp_path / "workstations.db"))
    service = WorkstationBootstrapService(
        runner=BootstrapRunner(host_key_status=KnownHostStatus.UNKNOWN),
        store=store,
        discovery=EmptyDiscovery(),
    )

    result = service.bootstrap(BootstrapRequest(host="hpc", username="researcher"))

    assert result.error_code == "HOST_KEY_CONFIRMATION_REQUIRED"
    assert store.get_default() is None
