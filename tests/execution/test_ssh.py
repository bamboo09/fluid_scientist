from pathlib import Path

import pytest

from fluid_scientist.execution.ssh import (
    ProcessResult,
    RemoteArg,
    RemoteExecutionError,
    RemoteProgram,
    SSHTransport,
)
from fluid_scientist.settings import NodeSettings


class RecordingRunner:
    def __init__(self, result: ProcessResult) -> None:
        self.result = result
        self.calls = []

    def run(self, argv: tuple[str, ...], *, timeout: float) -> ProcessResult:
        self.calls.append((argv, timeout))
        return self.result


def node(tmp_path: Path) -> NodeSettings:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("example.invalid ssh-ed25519 AAAA\n", encoding="utf-8")
    return NodeSettings(
        host="login.example",
        username="researcher",
        known_hosts_file=str(known_hosts),
        identity_file=str(tmp_path / "id_ed25519"),
    )


def test_ssh_enforces_known_hosts_and_typed_remote_argv(tmp_path) -> None:
    runner = RecordingRunner(ProcessResult(returncode=0, stdout="123\n", stderr=""))
    transport = SSHTransport(node(tmp_path), runner=runner)

    result = transport.execute(
        RemoteProgram.SBATCH,
        (RemoteArg("jobs/pipe.sbatch"),),
        timeout=30,
    )

    argv, timeout = runner.calls[0]
    assert result.stdout == "123\n"
    assert timeout == 30
    assert "StrictHostKeyChecking=yes" in argv
    assert "StrictHostKeyChecking=no" not in argv
    assert argv[-3:] == ("--", "sbatch", "jobs/pipe.sbatch")


def test_ssh_rejects_missing_known_hosts() -> None:
    with pytest.raises(ValueError, match="known_hosts"):
        SSHTransport(NodeSettings(host="login.example", username="researcher"))


def test_remote_arg_rejects_shell_and_traversal() -> None:
    for value in ("../secret", "job;rm", "$(curl bad)", "name\nwhoami"):
        with pytest.raises(ValueError):
            RemoteArg(value)


def test_nonzero_remote_exit_is_classified(tmp_path) -> None:
    runner = RecordingRunner(ProcessResult(returncode=1, stdout="", stderr="denied"))
    transport = SSHTransport(node(tmp_path), runner=runner)

    with pytest.raises(RemoteExecutionError, match="denied"):
        transport.execute(RemoteProgram.SQUEUE, (RemoteArg("123"),), timeout=10)


def test_workstation_worker_uses_home_relative_install_path(tmp_path) -> None:
    runner = RecordingRunner(ProcessResult(returncode=0, stdout="{}", stderr=""))
    transport = SSHTransport(node(tmp_path), runner=runner)

    transport.execute(
        RemoteProgram.FLUID_WORKER,
        (RemoteArg("doctor"), RemoteArg("--json")),
        timeout=15,
    )

    argv, _ = runner.calls[0]
    assert argv[-4:] == ("--", ".local/bin/fluid-worker", "doctor", "--json")


def test_upload_uses_fixed_incoming_directory_and_strict_host_key(tmp_path) -> None:
    runner = RecordingRunner(ProcessResult(returncode=0, stdout="", stderr=""))
    transport = SSHTransport(node(tmp_path), runner=runner)
    archive = tmp_path / "case.tar.gz"
    archive.write_bytes(b"payload")

    transport.upload_incoming(archive, "job-001.tar.gz", timeout=30)

    mkdir_argv, _ = runner.calls[0]
    scp_argv, _ = runner.calls[1]
    assert mkdir_argv[-4:] == (
        "--",
        "mkdir",
        "-p",
        ".local/share/fluid-scientist/incoming",
    )
    assert scp_argv[0] == "scp"
    assert "StrictHostKeyChecking=yes" in scp_argv
    assert scp_argv[-1].endswith(":.local/share/fluid-scientist/incoming/job-001.tar.gz")


def test_upload_rejects_remote_name_traversal(tmp_path) -> None:
    transport = SSHTransport(
        node(tmp_path),
        runner=RecordingRunner(ProcessResult(returncode=0, stdout="", stderr="")),
    )
    archive = tmp_path / "case.tar.gz"
    archive.write_bytes(b"payload")

    with pytest.raises(ValueError):
        transport.upload_incoming(archive, "../escape.tar.gz", timeout=30)
