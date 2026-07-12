"""One-click workstation probe flow and profile management.

:class:`WorkstationConnectionService` orchestrates the full connection and
environment probe for a remote host and persists the outcome as a
:class:`WorkstationProfile`.

Connection-state separation
---------------------------
The overall platform status is derived from three independent signals:

* ``SSH_CONNECTED``         — ``ssh`` authentication succeeded.
* ``OPENFOAM_DETECTED``     — OpenFOAM is available on the remote host.
* ``REMOTE_WORKSPACE_READY`` — a writable remote base directory exists.

Only when all three pass is the platform marked :attr:`PlatformStatus.READY`.
An SSH connection that succeeds while OpenFOAM is missing (or the workspace
is not writable) yields :attr:`PlatformStatus.DEGRADED`.  An authentication
failure yields :attr:`WorkstationErrorCode.NO_USABLE_SYSTEM_SSH_IDENTITY`.

No private keys, passwords, passphrases, or raw credentials are stored; the
profile only references the SSH host alias and records environment facts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fluid_scientist.workstations.models import (
    ConnectionStatus,
    CredentialSource,
    KnownHostStatus,
    PlatformStatus,
    ProbeResult,
    SchedulerType,
    WorkstationCandidate,
    WorkstationErrorCode,
    WorkstationProfile,
)
from fluid_scientist.workstations.probes import (
    OpenFOAMEnvironmentProbe,
    RemoteWorkspaceProbe,
    ResourceProbe,
    SchedulerProbe,
)
from fluid_scientist.workstations.profile_store import WorkstationProfileStore
from fluid_scientist.workstations.ssh_runner import SSHCommandRunner

# BatchMode authentication test timeout (seconds).
_AUTH_TIMEOUT = 15.0


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _enum_value(member: object) -> str:
    """Return the string value of a ``str`` enum member (or ``str(member)``)."""
    if hasattr(member, "value"):
        return str(member.value)
    return str(member)


class WorkstationConnectionService:
    """Orchestrate remote probing and persist workstation profiles.

    Args:
        runner: Optional :class:`SSHCommandRunner` (a default is created).
        store: Optional :class:`WorkstationProfileStore` (a default is
            created, backed by ``~/.fluid_scientist/workstations.db``).
    """

    def __init__(
        self,
        runner: SSHCommandRunner | None = None,
        store: WorkstationProfileStore | None = None,
    ) -> None:
        self._runner = runner or SSHCommandRunner()
        self._store = store or WorkstationProfileStore()

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe(self, candidate: WorkstationCandidate) -> ProbeResult:
        """Run the full connection and environment probe for *candidate*.

        Steps:
          1. Verify the ``ssh`` client is installed.
          2. Resolve SSH parameters (``ssh -G``).
          3. Check the host key against ``known_hosts``.
          4. Test authentication in ``BatchMode``.
          5. Run the OpenFOAM, scheduler, resource, and workspace probes.
          6. Assemble a :class:`ProbeResult` with an error code on failure.
        """
        host_alias = candidate.host_alias
        candidate_id = candidate.candidate_id
        self._register_candidate_target(candidate)

        def _result(
            *,
            ssh_connected: bool = False,
            openfoam=None,
            scheduler=None,
            resources=None,
            remote_workspace=None,
            error_code: str | None = None,
            error_message: str | None = None,
        ) -> ProbeResult:
            return ProbeResult(
                candidate_id=candidate_id,
                host_alias=host_alias,
                ssh_connected=ssh_connected,
                openfoam=openfoam,
                scheduler=scheduler,
                resources=resources,
                remote_workspace=remote_workspace,
                error_code=error_code,
                error_message=error_message,
                probed_at=_utcnow_iso(),
            )

        # 1. ssh installed?
        try:
            ssh_installed = self._runner.check_ssh_installed()
        except Exception:
            ssh_installed = False
        if not ssh_installed:
            return _result(
                error_code=WorkstationErrorCode.SSH_NOT_INSTALLED.value,
                error_message="ssh client is not installed or not on PATH",
            )

        # 2. resolve host (ssh -G).  A candidate discovered earlier may already
        #    carry resolved parameters, so only fail when neither source helps.
        try:
            resolved = self._runner.resolve_host(host_alias) or {}
        except Exception:
            resolved = {}
        if not resolved and not getattr(candidate, "resolved_host", ""):
            return _result(
                error_code=WorkstationErrorCode.SSH_CONFIG_NOT_FOUND.value,
                error_message=(
                    f"could not resolve SSH parameters for '{host_alias}'"
                ),
            )

        # 3. host key status.
        try:
            host_key_status = self._runner.get_host_key_status(host_alias)
        except Exception:
            host_key_status = KnownHostStatus.UNKNOWN
        if host_key_status == KnownHostStatus.CHANGED:
            return _result(
                error_code=WorkstationErrorCode.HOST_KEY_CHANGED.value,
                error_message=(
                    f"host key for '{host_alias}' has changed; possible MITM"
                ),
            )
        if host_key_status == KnownHostStatus.UNKNOWN:
            fingerprint = self._safe_fingerprint(host_alias)
            message = f"host key for '{host_alias}' is not in known_hosts"
            if fingerprint:
                message += f"; fingerprint: {fingerprint}"
            message += " — call confirm_host_key() to trust it"
            return _result(
                error_code=WorkstationErrorCode.HOST_KEY_CONFIRMATION_REQUIRED.value,
                error_message=message,
            )

        # 4. BatchMode authentication test.
        try:
            auth_ok = self._runner.test_authentication(
                host_alias, timeout=_AUTH_TIMEOUT
            )
        except Exception:
            auth_ok = False
        if not auth_ok:
            return _result(
                error_code=WorkstationErrorCode.NO_USABLE_SYSTEM_SSH_IDENTITY.value,
                error_message=(
                    f"SSH authentication failed for '{host_alias}' "
                    "(no usable system SSH identity)"
                ),
            )

        # 5-9. environment probes.  Each probe is resilient and returns a
        #      result even on remote-command failure, but we guard against any
        #      unexpected exception so one bad probe cannot abort the flow.
        openfoam = self._safe_probe(OpenFOAMEnvironmentProbe, host_alias)
        scheduler = self._safe_probe(SchedulerProbe, host_alias)
        resources = self._safe_probe(ResourceProbe, host_alias)
        remote_workspace = self._safe_probe(RemoteWorkspaceProbe, host_alias)

        # 10. If remote execution is broken (we cannot even read the hostname),
        #      flag it explicitly.
        if resources is None or not getattr(resources, "hostname", None):
            return _result(
                ssh_connected=True,
                openfoam=openfoam,
                scheduler=scheduler,
                resources=resources,
                remote_workspace=remote_workspace,
                error_code=WorkstationErrorCode.REMOTE_COMMAND_FAILED.value,
                error_message=(
                    "authenticated but remote command execution failed"
                ),
            )

        # 11. Success (possibly degraded — OpenFOAM/workspace may still be
        #      missing; that is reflected via the profile's platform status).
        return _result(
            ssh_connected=True,
            openfoam=openfoam,
            scheduler=scheduler,
            resources=resources,
            remote_workspace=remote_workspace,
        )

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    def save_profile(
        self,
        candidate: WorkstationCandidate,
        probe_result: ProbeResult,
        display_name: str | None = None,
    ) -> WorkstationProfile:
        """Persist *probe_result* as a :class:`WorkstationProfile`.

        The first profile saved becomes the default.  No credentials are
        stored: the profile only records the SSH host alias and environment
        facts.
        """
        host_alias = candidate.host_alias
        resolved_host = getattr(candidate, "resolved_host", "") or ""
        detected_username = getattr(candidate, "resolved_user", "") or ""
        detected_port = getattr(candidate, "resolved_port", 22) or 22
        credential_source = getattr(
            candidate, "credential_source", CredentialSource.SSH_CONFIG
        )
        connection_method = _enum_value(credential_source)

        fingerprint = self._safe_fingerprint(host_alias)

        openfoam = probe_result.openfoam
        scheduler = probe_result.scheduler
        resources = probe_result.resources
        workspace = probe_result.remote_workspace

        openfoam_available = bool(openfoam.available) if openfoam else False
        openfoam_version = openfoam.version if openfoam else None
        activation = (
            openfoam.activation_method
            if openfoam and openfoam.activation_method
            else None
        )
        openfoam_activation_method = _enum_value(activation) if activation else None
        openfoam_activation_reference = (
            openfoam.activation_reference if openfoam else None
        )

        scheduler_type = (
            scheduler.scheduler if scheduler else SchedulerType.NONE
        )
        workspace_ready = bool(workspace.writable) if workspace else False
        remote_base_dir = (
            workspace.remote_base_dir if workspace and workspace.writable else None
        )
        remote_os = resources.os if resources else None
        cpu_count = resources.cpu_count if resources else None
        memory_bytes = resources.memory_bytes if resources else None
        if workspace and workspace.writable:
            disk = workspace.disk_available_bytes
        elif resources:
            disk = resources.disk_available_bytes
        else:
            disk = None

        ssh_connected = bool(probe_result.ssh_connected)

        # Connection-state separation → platform status.
        if ssh_connected and openfoam_available and workspace_ready:
            platform_status = PlatformStatus.READY
        elif ssh_connected:
            platform_status = PlatformStatus.DEGRADED
        else:
            platform_status = PlatformStatus.UNAVAILABLE

        connection_status = (
            ConnectionStatus.REACHABLE
            if ssh_connected
            else ConnectionStatus.UNREACHABLE
        )

        is_first = self._store.get_default() is None
        now = _utcnow_iso()

        profile = WorkstationProfile(
            profile_id=f"ws_{uuid.uuid4().hex[:16]}",
            display_name=(
                display_name
                or getattr(candidate, "display_name", "")
                or host_alias
            ),
            host_alias=host_alias,
            resolved_host=resolved_host,
            detected_username=detected_username,
            detected_port=detected_port,
            connection_method=connection_method,
            known_host_fingerprint=fingerprint,
            scheduler=scheduler_type,
            openfoam_available=openfoam_available,
            openfoam_version=openfoam_version,
            openfoam_activation_method=openfoam_activation_method,
            openfoam_activation_reference=openfoam_activation_reference,
            remote_base_dir=remote_base_dir,
            remote_os=remote_os,
            cpu_count=cpu_count,
            memory_bytes=memory_bytes,
            disk_available_bytes=disk,
            connection_status=_enum_value(connection_status),
            platform_status=platform_status,
            last_probe_at=probe_result.probed_at,
            last_success_at=now if ssh_connected else None,
            is_default=is_first,
        )

        self._store.save(profile)
        if is_first:
            try:
                self._store.set_default(profile.profile_id)
            except Exception:
                # The save already recorded is_default=1; stay silent.
                pass
        return profile

    def test_profile(self, profile_id: str) -> ProbeResult:
        """Re-probe a previously saved profile by ID."""
        profile = self._store.get(profile_id)
        if profile is None:
            return ProbeResult(
                candidate_id=profile_id,
                host_alias="",
                ssh_connected=False,
                error_code=WorkstationErrorCode.PROFILE_NOT_FOUND.value,
                error_message=f"no workstation profile with id '{profile_id}'",
                probed_at=_utcnow_iso(),
            )
        candidate = self._candidate_from_profile(profile)
        return self.probe(candidate)

    def confirm_host_key(self, host_alias: str) -> bool:
        """Trust an unknown host by adding its key to ``known_hosts``."""
        try:
            return bool(self._runner.confirm_host_key(host_alias))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _safe_probe(self, probe_cls, host_alias: str):
        """Instantiate *probe_cls* and run it, returning ``None`` on error."""
        try:
            return probe_cls().probe(host_alias, self._runner)
        except Exception:
            return None

    def _safe_fingerprint(self, host_alias: str) -> str | None:
        """Fetch a host fingerprint, tolerating any runner failure."""
        try:
            return self._runner.get_host_fingerprint(host_alias)
        except Exception:
            return None

    def _register_candidate_target(self, candidate: WorkstationCandidate) -> None:
        """Register direct host/user/port metadata with the runner when present.

        This enables minimal bootstrap profiles that are not present in
        ``~/.ssh/config`` while still using the same system OpenSSH runner.
        """
        register = getattr(self._runner, "register_target", None)
        if not callable(register):
            return
        host = getattr(candidate, "resolved_host", "") or candidate.host_alias
        user = getattr(candidate, "resolved_user", "") or ""
        port = getattr(candidate, "resolved_port", 22) or 22
        if not host or not user:
            return
        try:
            register(candidate.host_alias, hostname=host, username=user, port=port)
        except ValueError:
            return

    @staticmethod
    def _candidate_from_profile(
        profile: WorkstationProfile,
    ) -> WorkstationCandidate:
        """Reconstruct a :class:`WorkstationCandidate` from a saved profile."""
        try:
            credential_source = CredentialSource(profile.connection_method)
        except ValueError:
            credential_source = CredentialSource.SSH_AGENT
        return WorkstationCandidate(
            candidate_id=profile.profile_id,
            host_alias=profile.host_alias,
            display_name=profile.display_name,
            resolved_host=profile.resolved_host,
            resolved_user=profile.detected_username,
            resolved_port=profile.detected_port,
            credential_source=credential_source,
        )


__all__ = ["WorkstationConnectionService"]
