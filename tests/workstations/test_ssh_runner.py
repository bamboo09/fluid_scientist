"""Tests for SSH command runner: host-alias validation, subprocess safety, and host resolution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fluid_scientist.execution.ssh import ProcessResult
from fluid_scientist.workstations.models import KnownHostStatus
from fluid_scientist.workstations.ssh_runner import (
    SafeHostAlias,
    SSHCommandRunner,
    SubprocessCommandRunner,
)

# ---------------------------------------------------------------------------
# SafeHostAlias
# ---------------------------------------------------------------------------


class TestSafeHostAlias:
    def test_accepts_valid_alias(self):
        SafeHostAlias("my-server")
        SafeHostAlias("host.example.com")
        SafeHostAlias("a-b-c_123")

    def test_value_attribute_preserved(self):
        alias = SafeHostAlias("cluster-node-1")
        assert alias.value == "cluster-node-1"

    def test_str_returns_value(self):
        alias = SafeHostAlias("hpc.example.org")
        assert str(alias) == "hpc.example.org"

    @pytest.mark.parametrize(
        "bad",
        [
            "host; rm -rf /",
            "host | cat /etc/passwd",
            "host $(whoami)",
            "host name with spaces",
            "host\nnewline",
            "",
            "*",
            "*.example.com",
            "host$var",
        ],
    )
    def test_rejects_injection(self, bad):
        with pytest.raises(ValueError):
            SafeHostAlias(bad)

    def test_rejects_too_long_alias(self):
        with pytest.raises(ValueError):
            SafeHostAlias("a" * 129)

    def test_accepts_max_length_alias(self):
        alias = SafeHostAlias("a" * 128)
        assert alias.value == "a" * 128

    def test_rejects_backtick(self):
        with pytest.raises(ValueError):
            SafeHostAlias("host`whoami`")

    def test_rejects_parentheses(self):
        with pytest.raises(ValueError):
            SafeHostAlias("host(cmd)")

    def test_frozen_dataclass(self):
        alias = SafeHostAlias("my-host")
        with pytest.raises(AttributeError):
            alias.value = "other-host"


# ---------------------------------------------------------------------------
# SubprocessCommandRunner
# ---------------------------------------------------------------------------


class TestSubprocessRunner:
    def test_uses_argv_not_shell(self, monkeypatch):
        """Verify subprocess.run is called with a list, not shell=True."""
        calls: list[tuple[tuple, dict]] = []
        original_run = subprocess.run

        def mock_run(*args, **kwargs):
            calls.append((args, kwargs))
            return original_run(
                [sys.executable, "-c", "print('ok')"],
                capture_output=True, text=True, timeout=5,
            )

        monkeypatch.setattr("subprocess.run", mock_run)
        runner = SubprocessCommandRunner()
        runner.run((sys.executable, "-c", "print('test')"), timeout=5)
        assert len(calls) == 1
        _args, kwargs = calls[0]
        assert not kwargs.get("shell", False), "shell=True must never be used"

    def test_returns_process_result(self):
        runner = SubprocessCommandRunner()
        result = runner.run((sys.executable, "-c", "print('hello')"), timeout=5)
        assert isinstance(result, ProcessResult)
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_passes_input_text(self, monkeypatch):
        captured: dict = {}
        original_run = subprocess.run

        def mock_run(*args, **kwargs):
            captured.update(kwargs)
            return original_run(
                [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
                capture_output=True, text=True, timeout=5,
                input=kwargs.get("input"),
            )

        monkeypatch.setattr("subprocess.run", mock_run)
        runner = SubprocessCommandRunner()
        runner.run(
            (sys.executable, "-c", "import sys; print(sys.stdin.read())"),
            timeout=5,
            input_text="secret-data",
        )
        assert captured["input"] == "secret-data"


# ---------------------------------------------------------------------------
# SSHCommandRunner — resolve_host
# ---------------------------------------------------------------------------


class _StaticRunner:
    """Minimal CommandRunner that returns a canned ProcessResult."""

    def __init__(self, result: ProcessResult) -> None:
        self._result = result

    def run(self, argv, *, timeout, input_text=None):
        return self._result


class TestResolveHost:
    def test_resolve_host_excludes_identity_file(self):
        """ssh -G output must not include identityfile in the result."""
        stdout = (
            "hostname server.example.com\n"
            "user myuser\n"
            "port 22\n"
            "identityfile /home/user/.ssh/id_rsa\n"
            "proxyjump none\n"
            "identityagent none\n"
        )
        runner = SSHCommandRunner(runner=_StaticRunner(ProcessResult(0, stdout, "")))
        result = runner.resolve_host("test-host")
        assert "hostname" in result
        assert "identityfile" not in result
        assert "user" in result

    def test_resolve_host_parses_all_fields(self):
        stdout = (
            "hostname 10.0.0.5\n"
            "user researcher\n"
            "port 2222\n"
            "proxyjump bastion.example.com\n"
            "identityagent /tmp/ssh-agent.sock\n"
        )
        runner = SSHCommandRunner(runner=_StaticRunner(ProcessResult(0, stdout, "")))
        result = runner.resolve_host("hpc")
        assert result["hostname"] == "10.0.0.5"
        assert result["user"] == "researcher"
        assert result["port"] == 2222
        assert result["proxyjump"] == "bastion.example.com"
        assert result["identityagent"] == "/tmp/ssh-agent.sock"

    def test_resolve_host_proxyjump_none(self):
        stdout = "hostname host\nuser u\nport 22\nproxyjump none\n"
        runner = SSHCommandRunner(runner=_StaticRunner(ProcessResult(0, stdout, "")))
        result = runner.resolve_host("h")
        assert result["proxyjump"] is None

    def test_resolve_host_returns_empty_on_failure(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(1, "", "error"))
        )
        result = runner.resolve_host("bad-host")
        assert result == {}

    def test_resolve_host_rejects_invalid_alias(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(0, "", ""))
        )
        with pytest.raises(ValueError):
            runner.resolve_host("host; rm -rf /")

    def test_resolve_host_truncates_output(self):
        """Output longer than max_output_chars is truncated."""
        long_stdout = "hostname h\n" + "x" * 20_000
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(0, long_stdout, "")),
            max_output_chars=100,
        )
        result = runner.resolve_host("h")
        # hostname should still be parsed before truncation kicks in
        assert result.get("hostname") == "h"


# ---------------------------------------------------------------------------
# SSHCommandRunner — check_ssh_installed / check_ssh_agent
# ---------------------------------------------------------------------------


class TestSSHInstalled:
    def test_installed_returns_true(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(0, "OpenSSH_9.0", ""))
        )
        assert runner.check_ssh_installed() is True

    def test_not_installed_returns_false(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(127, "", "not found"))
        )
        assert runner.check_ssh_installed() is False


class TestSSHAgent:
    def test_agent_with_identities(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(0, "SHA256:abc...", ""))
        )
        info = runner.check_ssh_agent()
        assert info["available"] is True
        assert info["has_identities"] is True

    def test_agent_without_identities(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(1, "The agent has no identities.", ""))
        )
        info = runner.check_ssh_agent()
        assert info["available"] is True
        assert info["has_identities"] is False

    def test_agent_not_available(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(2, "", "Could not open a connection"))
        )
        info = runner.check_ssh_agent()
        assert info["available"] is False
        assert info["has_identities"] is False


# ---------------------------------------------------------------------------
# SSHCommandRunner — run_remote / get_host_key_status / test_authentication
# ---------------------------------------------------------------------------


class TestRunRemote:
    def test_run_remote_uses_batch_mode(self):
        """run_remote must include BatchMode=yes in the argv."""
        captured: list[tuple] = []

        class CapturingRunner:
            def run(self, argv, *, timeout, input_text=None):
                captured.append(argv)
                return ProcessResult(0, "ok", "")

        runner = SSHCommandRunner(runner=CapturingRunner())
        runner.run_remote("my-host", "uname -a", timeout=10)
        assert len(captured) == 1
        argv = captured[0]
        assert "ssh" in argv
        assert "BatchMode=yes" in argv
        assert "my-host" in argv
        assert "uname -a" in argv

    def test_run_remote_rejects_injection(self):
        runner = SSHCommandRunner(runner=_StaticRunner(ProcessResult(0, "", "")))
        with pytest.raises(ValueError):
            runner.run_remote("host; rm -rf /", "ls", timeout=5)

    def test_run_remote_returns_timeout_result(self):
        class TimeoutRunner:
            def run(self, argv, *, timeout, input_text=None):
                raise subprocess.TimeoutExpired(cmd=list(argv), timeout=timeout)

        runner = SSHCommandRunner(runner=TimeoutRunner())
        result = runner.run_remote("my-host", "true", timeout=1)
        assert result.returncode == 124
        assert "timeout" in result.stderr


class TestHostKeyStatus:
    def test_known_host(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(0, "# Host my-host found\nmy-host ssh-rsa AAAA", ""))
        )
        assert runner.get_host_key_status("my-host") == KnownHostStatus.KNOWN

    def test_unknown_host(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(1, "", "not found"))
        )
        assert runner.get_host_key_status("unknown-host") == KnownHostStatus.UNKNOWN

    def test_empty_output_means_unknown(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(0, "", ""))
        )
        assert runner.get_host_key_status("blank-host") == KnownHostStatus.UNKNOWN


class TestAuthentication:
    def test_auth_success(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(0, "", ""))
        )
        assert runner.test_authentication("my-host", timeout=10) is True

    def test_auth_failure(self):
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(255, "", "Permission denied"))
        )
        assert runner.test_authentication("my-host", timeout=10) is False

    def test_auth_uses_strict_host_key_checking(self):
        captured: list[tuple] = []

        class CapturingRunner:
            def run(self, argv, *, timeout, input_text=None):
                captured.append(argv)
                return ProcessResult(0, "", "")

        runner = SSHCommandRunner(runner=CapturingRunner())
        runner.test_authentication("my-host", timeout=10)
        argv = captured[0]
        assert "StrictHostKeyChecking=yes" in argv
        assert "BatchMode=yes" in argv


class TestOutputTruncation:
    def test_truncates_long_stdout(self):
        long_output = "x" * 20_000
        runner = SSHCommandRunner(
            runner=_StaticRunner(ProcessResult(0, long_output, "")),
            max_output_chars=500,
        )
        result = runner.run_remote("my-host", "cat bigfile", timeout=5)
        assert len(result.stdout) <= 500


# ---------------------------------------------------------------------------
# SSHCommandRunner — _detect_identity_files
# ---------------------------------------------------------------------------


class TestDetectIdentityFiles:
    """Tests for SSH private key auto-detection in ~/.ssh/."""

    def test_detects_standard_key_names(self, tmp_path):
        """Standard key names (id_rsa, id_ed25519, etc.) are detected."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "id_rsa").write_text("dummy")
        (ssh_dir / "id_ed25519").write_text("dummy")
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        runner = SSHCommandRunner(known_hosts_file=ssh_dir / "known_hosts")
        identities = runner._detect_identity_files()
        names = [Path(p).name for p in identities]
        assert "id_rsa" in names
        assert "id_ed25519" in names

    def test_detects_non_standard_key_name(self, tmp_path):
        """Non-standard key names like fluid_scientist_ed25519 are detected."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "fluid_scientist_ed25519").write_text("dummy")
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        runner = SSHCommandRunner(known_hosts_file=ssh_dir / "known_hosts")
        identities = runner._detect_identity_files()
        names = [Path(p).name for p in identities]
        assert "fluid_scientist_ed25519" in names

    def test_excludes_public_keys(self, tmp_path):
        """Files ending in .pub are excluded."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "id_ed25519").write_text("dummy")
        (ssh_dir / "id_ed25519.pub").write_text("ssh-ed25519 AAAA")
        (ssh_dir / "fluid_scientist_ed25519").write_text("dummy")
        (ssh_dir / "fluid_scientist_ed25519.pub").write_text("ssh-ed25519 AAAA")
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        runner = SSHCommandRunner(known_hosts_file=ssh_dir / "known_hosts")
        identities = runner._detect_identity_files()
        names = [Path(p).name for p in identities]
        assert "id_ed25519" in names
        assert "fluid_scientist_ed25519" in names
        assert not any(n.endswith(".pub") for n in names)

    def test_excludes_scripts_and_config(self, tmp_path):
        """Scripts and config files are excluded."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "id_ed25519").write_text("dummy")
        (ssh_dir / "fix_key.ps1").write_text("Write-Host hello")
        (ssh_dir / "deploy.sh").write_text("echo hello")
        (ssh_dir / "config").write_text("Host *")
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        runner = SSHCommandRunner(known_hosts_file=ssh_dir / "known_hosts")
        identities = runner._detect_identity_files()
        names = [Path(p).name for p in identities]
        assert "id_ed25519" in names
        assert "fix_key.ps1" not in names
        assert "deploy.sh" not in names
        assert "config" not in names

    def test_returns_empty_when_ssh_dir_missing(self, tmp_path):
        """Returns empty list when ~/.ssh/ doesn't exist."""
        runner = SSHCommandRunner(known_hosts_file=tmp_path / "nonexistent" / "known_hosts")
        assert runner._detect_identity_files() == []

    def test_returns_empty_when_no_keys(self, tmp_path):
        """Returns empty list when ~/.ssh/ has no key files."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        (ssh_dir / "config").write_text("Host *")
        runner = SSHCommandRunner(known_hosts_file=ssh_dir / "known_hosts")
        assert runner._detect_identity_files() == []

    def test_caps_at_max_identity_files(self, tmp_path):
        """Stops after _MAX_IDENTITY_FILES keys."""
        from fluid_scientist.workstations.ssh_runner import _MAX_IDENTITY_FILES
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        for i in range(_MAX_IDENTITY_FILES + 3):
            (ssh_dir / f"key_{i}_ed25519").write_text("dummy")
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        runner = SSHCommandRunner(known_hosts_file=ssh_dir / "known_hosts")
        identities = runner._detect_identity_files()
        assert len(identities) == _MAX_IDENTITY_FILES

    def test_identity_args_returns_i_flag_pairs(self, tmp_path):
        """_identity_args returns -i path -i path ... tuples."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "fluid_scientist_ed25519").write_text("dummy")
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        runner = SSHCommandRunner(known_hosts_file=ssh_dir / "known_hosts")
        args = runner._identity_args()
        assert len(args) == 2  # -i + path
        assert args[0] == "-i"
        assert "fluid_scientist_ed25519" in args[1]

    def test_test_authentication_includes_identity_args(self, tmp_path):
        """test_authentication passes -i for non-standard keys."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "fluid_scientist_ed25519").write_text("dummy")
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        captured: list = []

        class CapturingRunner:
            def run(self, argv, *, timeout, input_text=None):
                captured.append(argv)
                return ProcessResult(0, "", "")

        runner = SSHCommandRunner(
            runner=CapturingRunner(),
            known_hosts_file=ssh_dir / "known_hosts",
        )
        runner.test_authentication("my-host", timeout=10)
        argv = captured[0]
        assert "-i" in argv
        idx = argv.index("-i")
        assert "fluid_scientist_ed25519" in argv[idx + 1]

    def test_run_remote_includes_identity_args(self, tmp_path):
        """run_remote passes -i for non-standard keys."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "fluid_scientist_ed25519").write_text("dummy")
        (ssh_dir / "known_hosts").write_text("host ssh-rsa AAAA")
        captured: list = []

        class CapturingRunner:
            def run(self, argv, *, timeout, input_text=None):
                captured.append(argv)
                return ProcessResult(0, "ok", "")

        runner = SSHCommandRunner(
            runner=CapturingRunner(),
            known_hosts_file=ssh_dir / "known_hosts",
        )
        runner.run_remote("my-host", "uname", timeout=10)
        argv = captured[0]
        assert "-i" in argv
        idx = argv.index("-i")
        assert "fluid_scientist_ed25519" in argv[idx + 1]
