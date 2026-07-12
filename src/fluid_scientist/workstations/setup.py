"""Automated workstation setup: password → key deployment → probe → save.

:class:`WorkstationSetupService` orchestrates the complete one-click setup
flow that takes a host IP, username, and password, then:

1. Tests password-based SSH connectivity (via paramiko).
2. Generates an SSH key pair if none exists for this host.
3. Deploys the public key to the remote ``authorized_keys``.
4. Adds the host key to local ``known_hosts`` via ``ssh-keyscan``.
5. Creates or updates ``~/.ssh/config`` with a Host entry.
6. Runs the full environment probe (OpenFOAM, scheduler, resources, workspace).
7. Persists the result as a :class:`WorkstationProfile`.

No passwords or private keys are persisted.  The password is used only
in-memory for the initial connection and key deployment.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fluid_scientist.workstations.models import (
    ConnectionStatus,
    CredentialSource,
    PlatformStatus,
    ProbeResult,
    SchedulerType,
    WorkstationCandidate,
    WorkstationErrorCode,
    WorkstationProfile,
)
from fluid_scientist.workstations.connection import WorkstationConnectionService
from fluid_scientist.workstations.profile_store import WorkstationProfileStore
from fluid_scientist.workstations.ssh_runner import SSHCommandRunner

logger = logging.getLogger(__name__)

# Default SSH key used by the fluid_scientist platform.
_DEFAULT_KEY_NAME = "fluid_scientist_ed25519"
_DEFAULT_PORT = 22
_SSH_CONNECT_TIMEOUT = 15
_KEY_DEPLOY_TIMEOUT = 15

# Regex for validating IPv4 addresses.
_IPV4_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")

# Regex for validating hostnames (RFC 1123, simplified).
_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_host(host: str) -> bool:
    """Return True if *host* is a valid IPv4 address or hostname."""
    if not host or len(host) > 255:
        return False
    if _IPV4_RE.match(host):
        return all(0 <= int(octet) <= 255 for octet in host.split("."))
    return bool(_HOSTNAME_RE.match(host))


def _is_valid_username(username: str) -> bool:
    """Return True if *username* is a valid POSIX username."""
    if not username or len(username) > 32:
        return False
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", username))


class SetupStepResult:
    """Result of a single setup step."""

    def __init__(
        self,
        step: str,
        success: bool,
        message: str = "",
        error_code: str | None = None,
    ) -> None:
        self.step = step
        self.success = success
        self.message = message
        self.error_code = error_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "success": self.success,
            "message": self.message,
            "error_code": self.error_code,
        }


class WorkstationSetupService:
    """One-click workstation setup service.

    Takes host credentials (IP, username, password) and performs the full
    setup pipeline: password connect → key deployment → SSH config →
    host key trust → probe → save.

    Args:
        runner: Optional :class:`SSHCommandRunner` (a default is created).
        store: Optional :class:`WorkstationProfileStore` (a default is
            created, backed by ``~/.fluid_scientist/workstations.db``).
        connection: Optional :class:`WorkstationConnectionService`.
        ssh_dir: Optional path to the ``~/.ssh`` directory.
    """

    def __init__(
        self,
        *,
        runner: SSHCommandRunner | None = None,
        store: WorkstationProfileStore | None = None,
        connection: WorkstationConnectionService | None = None,
        ssh_dir: Path | None = None,
    ) -> None:
        self._runner = runner or SSHCommandRunner()
        self._store = store or WorkstationProfileStore()
        self._connection = connection or WorkstationConnectionService(
            runner=self._runner, store=self._store
        )
        self._ssh_dir = ssh_dir or (Path.home() / ".ssh")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def setup(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = _DEFAULT_PORT,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Run the full one-click setup flow.

        Returns a dict with:
        * ``steps``: list of step results.
        * ``profile``: the saved :class:`WorkstationProfile` (or None).
        * ``probe_result``: the probe result (or None).
        * ``error_code`` / ``error_message``: on failure.
        """
        steps: list[SetupStepResult] = []
        now = _utcnow_iso()

        def _fail(step: str, message: str, error_code: str) -> dict[str, Any]:
            steps.append(SetupStepResult(step, False, message, error_code))
            return {
                "steps": [s.to_dict() for s in steps],
                "profile": None,
                "probe_result": None,
                "error_code": error_code,
                "error_message": message,
                "setup_at": now,
            }

        # --- Step 0: Validate inputs ---
        if not _is_valid_host(host):
            return _fail("validate", f"invalid host: {host}", "INVALID_HOST")
        if not _is_valid_username(username):
            return _fail("validate", f"invalid username: {username}", "INVALID_USERNAME")
        if not password:
            return _fail("validate", "password is required", "PASSWORD_REQUIRED")

        steps.append(SetupStepResult("validate", True, f"host={host} user={username} port={port}"))

        # --- Step 1: Test password-based SSH connectivity ---
        try:
            import paramiko
        except ImportError:
            return _fail(
                "password_connect",
                "paramiko is not installed; cannot use password authentication",
                "PARAMIKO_NOT_INSTALLED",
            )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=username,
                password=password,
                timeout=_SSH_CONNECT_TIMEOUT,
                allow_agent=False,
                look_for_keys=False,
            )
        except Exception as exc:
            return _fail(
                "password_connect",
                f"password authentication failed: {exc}",
                WorkstationErrorCode.AUTHENTICATION_FAILED.value,
            )

        steps.append(SetupStepResult("password_connect", True, "connected via password"))

        # --- Step 2: Ensure SSH key pair exists ---
        key_path = self._ssh_dir / _DEFAULT_KEY_NAME
        pub_key_path = self._ssh_dir / f"{_DEFAULT_KEY_NAME}.pub"

        if not key_path.is_file():
            try:
                self._ssh_dir.mkdir(parents=True, exist_ok=True)
                key = paramiko.Ed25519Key.generate()
                key.write_private_key_file(str(key_path))
                with open(pub_key_path, "w", encoding="utf-8") as f:
                    f.write(f"{key.get_name()} {key.get_base64()} fluid-scientist-workstation\n")
                # Set restrictive permissions on private key (best-effort on Windows).
                try:
                    key_path.chmod(0o600)
                except OSError:
                    pass
                steps.append(SetupStepResult("generate_key", True, f"generated {_DEFAULT_KEY_NAME}"))
            except Exception as exc:
                client.close()
                return _fail("generate_key", f"failed to generate SSH key: {exc}", "KEY_GENERATION_FAILED")
        else:
            steps.append(SetupStepResult("generate_key", True, "key already exists"))

        # --- Step 3: Deploy public key to remote authorized_keys ---
        try:
            pub_key_content = pub_key_path.read_text(encoding="utf-8").strip()
            deploy_cmd = (
                "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
                f"grep -qF '{pub_key_content}' ~/.ssh/authorized_keys 2>/dev/null || "
                f"echo '{pub_key_content}' >> ~/.ssh/authorized_keys && "
                "chmod 600 ~/.ssh/authorized_keys && echo KEY_DEPLOYED_OK"
            )
            stdin, stdout, stderr = client.exec_command(deploy_cmd, timeout=_KEY_DEPLOY_TIMEOUT)
            deploy_output = stdout.read().decode().strip()
            if "KEY_DEPLOYED_OK" not in deploy_output:
                client.close()
                return _fail(
                    "deploy_key",
                    f"key deployment failed: {deploy_output}",
                    "KEY_DEPLOYMENT_FAILED",
                )
            steps.append(SetupStepResult("deploy_key", True, "public key deployed to authorized_keys"))
        except Exception as exc:
            try:
                client.close()
            except Exception:
                pass
            return _fail("deploy_key", f"key deployment error: {exc}", "KEY_DEPLOYMENT_FAILED")

        # --- Step 4: Write host key to known_hosts from paramiko ---
        # Extract the host key directly from the paramiko connection (more
        # reliable than ssh-keyscan on Windows).  The key was already
        # accepted by AutoAddPolicy during the password connection.
        try:
            self._write_host_key_to_known_hosts(client, host, port)
            steps.append(SetupStepResult("trust_host_key", True, "host key written to known_hosts"))
        except Exception as exc:
            steps.append(SetupStepResult("trust_host_key", True, f"host key write skipped: {exc}"))
        finally:
            try:
                client.close()
            except Exception:
                pass

        # --- Step 5: Create or update ~/.ssh/config ---
        try:
            self._update_ssh_config(host, username, port)
            steps.append(SetupStepResult("update_config", True, "SSH config updated"))
        except Exception as exc:
            return _fail("update_config", f"failed to update SSH config: {exc}", "CONFIG_UPDATE_FAILED")

        # --- Step 6: Run full environment probe ---
        try:
            candidate = WorkstationCandidate(
                candidate_id=f"setup:{host}",
                host_alias=host,
                display_name=display_name or host,
                resolved_host=host,
                resolved_user=username,
                resolved_port=port,
                credential_source=CredentialSource.SSH_CONFIG,
                known_host_status="KNOWN",
                connection_status=ConnectionStatus.UNTESTED,
                source="SSH_CONFIG",
            )
            probe_result = self._connection.probe(candidate)
            if probe_result.error_code:
                steps.append(SetupStepResult(
                    "probe", False,
                    probe_result.error_message or "probe failed",
                    probe_result.error_code,
                ))
                return {
                    "steps": [s.to_dict() for s in steps],
                    "profile": None,
                    "probe_result": probe_result.model_dump(),
                    "error_code": probe_result.error_code,
                    "error_message": probe_result.error_message,
                    "setup_at": now,
                }
            steps.append(SetupStepResult("probe", True, "environment probe completed"))
        except Exception as exc:
            return _fail("probe", f"probe error: {exc}", WorkstationErrorCode.REMOTE_COMMAND_FAILED.value)

        # --- Step 7: Save profile ---
        try:
            profile = self._connection.save_profile(
                candidate, probe_result, display_name=display_name
            )
            steps.append(SetupStepResult("save", True, f"profile saved: {profile.profile_id}"))
        except Exception as exc:
            return _fail("save", f"failed to save profile: {exc}", "PROFILE_SAVE_FAILED")

        return {
            "steps": [s.to_dict() for s in steps],
            "profile": profile.model_dump(),
            "probe_result": probe_result.model_dump(),
            "error_code": None,
            "error_message": None,
            "setup_at": now,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write_host_key_to_known_hosts(self, client: Any, host: str, port: int) -> None:
        """Write the remote host key to ``known_hosts``.

        Uses the host key already accepted by paramiko's
        :class:`paramiko.AutoAddPolicy` during the password connection.
        This is more reliable than ``ssh-keyscan`` on Windows.

        Writes in the OpenSSH ``known_hosts`` format:
        ``[host]:port <key-type> <base64-key>``
        """
        import base64

        known_hosts = self._ssh_dir / "known_hosts"
        self._ssh_dir.mkdir(parents=True, exist_ok=True)

        host_keys = client.get_host_keys()
        if host_keys is None:
            return

        # paramiko's HostKeys maps hostname -> dict[key_type -> PKey].
        key_hostname = f"[{host}]:{port}" if port != 22 else host

        lines_to_write: list[str] = []
        for hostname, key_dict in host_keys.items():
            if hostname != host and hostname != f"[{host}]:{port}":
                continue
            for key_type, pkey in key_dict.items():
                key_b64 = base64.b64encode(pkey.asbytes()).decode("ascii")
                lines_to_write.append(f"{key_hostname} {key_type} {key_b64}\n")

        if not lines_to_write:
            return

        existing = ""
        if known_hosts.is_file():
            existing = known_hosts.read_text(encoding="utf-8")

        with open(known_hosts, "a", encoding="utf-8") as f:
            for line in lines_to_write:
                if line not in existing:
                    f.write(line)

    def _update_ssh_config(self, host: str, username: str, port: int) -> None:
        """Create or update the SSH config entry for *host*.

        If a ``Host <host>`` block already exists, it is replaced.
        Otherwise a new block is appended.
        """
        config_path = self._ssh_dir / "config"
        self._ssh_dir.mkdir(parents=True, exist_ok=True)

        # Build the new Host block.
        identity_path = self._ssh_dir / _DEFAULT_KEY_NAME
        # Use ~ notation for portability.
        identity_line = f"IdentityFile ~/.ssh/{_DEFAULT_KEY_NAME}"
        new_block = (
            f"Host {host}\n"
            f"    HostName {host}\n"
            f"    User {username}\n"
            f"    Port {port}\n"
            f"    {identity_line}\n"
        )

        if not config_path.is_file():
            config_path.write_text(new_block, encoding="utf-8")
            return

        existing = config_path.read_text(encoding="utf-8")

        # Check if a Host block for this exact host already exists.
        # Match "Host <host>\n" at the start of a line.
        pattern = re.compile(
            r"^Host\s+" + re.escape(host) + r"\s*\n(?:(?!^Host\s).)*",
            re.MULTILINE | re.DOTALL,
        )
        if pattern.search(existing):
            # Replace existing block.
            updated = pattern.sub(new_block, existing)
        else:
            # Append new block.
            separator = "\n" if existing and not existing.endswith("\n") else ""
            updated = existing + separator + new_block

        config_path.write_text(updated, encoding="utf-8")
