"""Tests for SSH command runner: host-alias validation, subprocess safety, and host resolution."""

from __future__ import annotations

import subprocess
import sys

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
