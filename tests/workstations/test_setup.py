"""Tests for the automated workstation setup service."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fluid_scientist.workstations.models import (
    ConnectionStatus,
    CredentialSource,
    KnownHostStatus,
    ProbeResult,
    WorkstationCandidate,
    WorkstationProfile,
)
from fluid_scientist.workstations.setup import (
    WorkstationSetupService,
    _is_valid_host,
    _is_valid_username,
)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_ipv4(self):
        assert _is_valid_host("10.129.177.241") is True
        assert _is_valid_host("192.168.1.1") is True
        assert _is_valid_host("0.0.0.0") is True
        assert _is_valid_host("255.255.255.255") is True

    def test_invalid_ipv4(self):
        assert _is_valid_host("256.1.1.1") is False
        assert _is_valid_host("10.999.1.1") is False
        assert _is_valid_host("") is False

    def test_valid_hostname(self):
        assert _is_valid_host("cluster.example.com") is True
        assert _is_valid_host("hpc-node-01") is True
        assert _is_valid_host("localhost") is True

    def test_invalid_hostname(self):
        assert _is_valid_host("host with spaces") is False
        assert _is_valid_host("host;rm -rf /") is False
        assert _is_valid_host("-leading-dash") is False

    def test_valid_username(self):
        assert _is_valid_username("root") is True
        assert _is_valid_username("baoxu") is True
        assert _is_valid_username("user_name") is True
        assert _is_valid_username("user-1") is True
        assert _is_valid_username("_user") is True

    def test_invalid_username(self):
        assert _is_valid_username("") is False
        assert _is_valid_username("1user") is False
        assert _is_valid_username("user name") is False
        assert _is_valid_username("user;rm") is False
        assert _is_valid_username("a" * 33) is False


# ---------------------------------------------------------------------------
# SSH config update
# ---------------------------------------------------------------------------


class TestUpdateSshConfig:
    def test_creates_new_config(self, tmp_path):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        service = WorkstationSetupService(ssh_dir=ssh_dir)
        service._update_ssh_config("10.0.0.1", "myuser", 22)
        config = (ssh_dir / "config").read_text()
        assert "Host 10.0.0.1" in config
        assert "HostName 10.0.0.1" in config
        assert "User myuser" in config
        assert "Port 22" in config
        assert "IdentityFile ~/.ssh/fluid_scientist_ed25519" in config

    def test_appends_to_existing_config(self, tmp_path):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "config").write_text("Host existing-host\n    HostName 1.2.3.4\n    User old\n")
        service = WorkstationSetupService(ssh_dir=ssh_dir)
        service._update_ssh_config("10.0.0.2", "newuser", 2222)
        config = (ssh_dir / "config").read_text()
        assert "Host existing-host" in config
        assert "Host 10.0.0.2" in config
        assert "User newuser" in config
        assert "Port 2222" in config

    def test_replaces_existing_host_block(self, tmp_path):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "config").write_text(
            "Host 10.0.0.1\n    HostName 10.0.0.1\n    User olduser\n    Port 22\n"
        )
        service = WorkstationSetupService(ssh_dir=ssh_dir)
        service._update_ssh_config("10.0.0.1", "newuser", 2222)
        config = (ssh_dir / "config").read_text()
        assert "User newuser" in config
        assert "Port 2222" in config
        assert "olduser" not in config

    def test_creates_ssh_dir_if_missing(self, tmp_path):
        ssh_dir = tmp_path / ".ssh"
        service = WorkstationSetupService(ssh_dir=ssh_dir)
        service._update_ssh_config("10.0.0.1", "user", 22)
        assert (ssh_dir / "config").is_file()


# ---------------------------------------------------------------------------
# Full setup flow (with mocked paramiko and connection service)
# ---------------------------------------------------------------------------


class TestSetupFlow:
    """Tests for the full setup flow with mocked dependencies."""

    def _make_service(self, tmp_path, *, probe_result=None, profile=None):
        """Create a WorkstationSetupService with mocked dependencies."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        # Pre-create key files so setup skips generation.
        (ssh_dir / "fluid_scientist_ed25519").write_text("dummy-key")
        (ssh_dir / "fluid_scientist_ed25519.pub").write_text(
            "ssh-ed25519 AAAA dummy@host"
        )

        runner = MagicMock()
        runner.confirm_host_key.return_value = True

        store = MagicMock()

        connection = MagicMock()
        if probe_result is None:
            probe_result = ProbeResult(
                candidate_id="test",
                host_alias="10.0.0.1",
                ssh_connected=True,
                error_code=None,
            )
        connection.probe.return_value = probe_result

        if profile is None:
            profile = WorkstationProfile(
                profile_id="ws_test123",
                display_name="test-host",
                host_alias="10.0.0.1",
                resolved_host="10.0.0.1",
                detected_username="testuser",
                detected_port=22,
                connection_status="REACHABLE",
                platform_status="DEGRADED",
            )
        connection.save_profile.return_value = profile

        return WorkstationSetupService(
            runner=runner, store=store, connection=connection, ssh_dir=ssh_dir
        )

    @patch("paramiko.SSHClient")
    def test_successful_setup(self, mock_ssh_class, tmp_path):
        """Full setup succeeds when all steps pass."""
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.return_value = None
        mock_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(read=lambda: b"KEY_DEPLOYED_OK"),
            MagicMock(read=lambda: b""),
        )

        service = self._make_service(tmp_path)
        result = service.setup("10.0.0.1", "testuser", "password123")

        assert result["error_code"] is None
        assert result["profile"] is not None
        assert result["profile"]["host_alias"] == "10.0.0.1"
        assert len(result["steps"]) >= 7
        assert all(s["success"] for s in result["steps"])

    def test_invalid_host_returns_error(self, tmp_path):
        service = self._make_service(tmp_path)
        result = service.setup("not a host", "user", "pass")
        assert result["error_code"] == "INVALID_HOST"
        assert result["profile"] is None

    def test_invalid_username_returns_error(self, tmp_path):
        service = self._make_service(tmp_path)
        result = service.setup("10.0.0.1", "1invalid", "pass")
        assert result["error_code"] == "INVALID_USERNAME"
        assert result["profile"] is None

    def test_empty_password_returns_error(self, tmp_path):
        service = self._make_service(tmp_path)
        result = service.setup("10.0.0.1", "user", "")
        assert result["error_code"] == "PASSWORD_REQUIRED"
        assert result["profile"] is None

    @patch("paramiko.SSHClient")
    def test_password_auth_failure_returns_error(self, mock_ssh_class, tmp_path):
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.side_effect = Exception("Auth failed")

        service = self._make_service(tmp_path)
        result = service.setup("10.0.0.1", "user", "wrongpass")

        assert result["error_code"] == "AUTHENTICATION_FAILED"
        assert result["profile"] is None

    @patch("paramiko.SSHClient")
    def test_key_deployment_failure_returns_error(self, mock_ssh_class, tmp_path):
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.return_value = None
        mock_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(read=lambda: b"DEPLOY_FAILED"),
            MagicMock(read=lambda: b"error"),
        )

        service = self._make_service(tmp_path)
        result = service.setup("10.0.0.1", "user", "pass")

        assert result["error_code"] == "KEY_DEPLOYMENT_FAILED"
        assert result["profile"] is None

    @patch("paramiko.SSHClient")
    def test_probe_failure_returns_probe_result(self, mock_ssh_class, tmp_path):
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.return_value = None
        mock_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(read=lambda: b"KEY_DEPLOYED_OK"),
            MagicMock(read=lambda: b""),
        )

        failed_probe = ProbeResult(
            candidate_id="test",
            host_alias="10.0.0.1",
            ssh_connected=False,
            error_code="NO_USABLE_SYSTEM_SSH_IDENTITY",
            error_message="auth failed",
        )
        service = self._make_service(tmp_path, probe_result=failed_probe)
        result = service.setup("10.0.0.1", "user", "pass")

        assert result["error_code"] == "NO_USABLE_SYSTEM_SSH_IDENTITY"
        assert result["profile"] is None
        assert result["probe_result"] is not None

    @patch("paramiko.SSHClient")
    def test_setup_creates_ssh_config(self, mock_ssh_class, tmp_path):
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.return_value = None
        mock_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(read=lambda: b"KEY_DEPLOYED_OK"),
            MagicMock(read=lambda: b""),
        )

        service = self._make_service(tmp_path)
        result = service.setup("10.0.0.1", "myuser", "pass")

        config_path = service._ssh_dir / "config"
        assert config_path.is_file()
        config = config_path.read_text()
        assert "Host 10.0.0.1" in config
        assert "User myuser" in config

    @patch("paramiko.SSHClient")
    def test_setup_with_display_name(self, mock_ssh_class, tmp_path):
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.return_value = None
        mock_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(read=lambda: b"KEY_DEPLOYED_OK"),
            MagicMock(read=lambda: b""),
        )

        service = self._make_service(tmp_path)
        # Override save_profile to reflect the passed display_name.
        def mock_save(candidate, probe_result, display_name=None):
            return WorkstationProfile(
                profile_id="ws_test123",
                display_name=display_name or candidate.host_alias,
                host_alias=candidate.host_alias,
                resolved_host=candidate.resolved_host,
                detected_username=candidate.resolved_user,
                detected_port=candidate.resolved_port,
                connection_status="REACHABLE",
                platform_status="DEGRADED",
            )
        service._connection.save_profile.side_effect = mock_save

        result = service.setup("10.0.0.1", "user", "pass", display_name="my-cluster")

        assert result["error_code"] is None
        assert result["profile"]["display_name"] == "my-cluster"

    @patch("paramiko.SSHClient")
    def test_setup_with_custom_port(self, mock_ssh_class, tmp_path):
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.return_value = None
        mock_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(read=lambda: b"KEY_DEPLOYED_OK"),
            MagicMock(read=lambda: b""),
        )

        service = self._make_service(tmp_path)
        result = service.setup("10.0.0.1", "user", "pass", port=2222)

        assert result["error_code"] is None
        config_path = service._ssh_dir / "config"
        config = config_path.read_text()
        assert "Port 2222" in config

    @patch("paramiko.SSHClient")
    def test_no_password_leak_in_result(self, mock_ssh_class, tmp_path):
        """Ensure password is never in the result dict."""
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client
        mock_client.connect.return_value = None
        mock_client.exec_command.return_value = (
            MagicMock(),
            MagicMock(read=lambda: b"KEY_DEPLOYED_OK"),
            MagicMock(read=lambda: b""),
        )

        service = self._make_service(tmp_path)
        result = service.setup("10.0.0.1", "user", "secret_password_123")

        result_str = str(result)
        assert "secret_password_123" not in result_str
