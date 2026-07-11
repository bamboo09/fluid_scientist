"""Remote environment probes for workstation auto-connect.

Each probe executes a *fixed* detection script (a system-owned constant;
no user input is ever interpolated) inside a remote login shell via
``bash -lc '<script>'``.  Probes never modify remote dotfiles
(``.bashrc``/``.profile``) and never install software.  The only remote
side effect is the creation of Fluid Scientist's own run directory
(``~/fluid_scientist/runs`` or ``$SCRATCH/fluid_scientist/runs``) when
selecting a remote workspace.

The probe result types are defined in
:mod:`fluid_scientist.workstations.models`.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from typing import TYPE_CHECKING

from fluid_scientist.workstations.models import (
    OpenFOAMActivationMethod,
    OpenFOAMProbeResult,
    RemoteWorkspaceResult,
    ResourceProbeResult,
    SchedulerProbeResult,
    SchedulerType,
)

if TYPE_CHECKING:  # pragma: no cover - import only for type checkers
    from fluid_scientist.workstations.ssh_runner import SSHCommandRunner


# ---------------------------------------------------------------------------
# Fixed detection scripts (system constants; never derived from user input)
# ---------------------------------------------------------------------------
#
# The OpenFOAM and scheduler scripts are the exact, verbatim scripts mandated
# by the workstation specification.  The resource and workspace scripts are
# likewise fixed system strings that combine the mandated commands with
# sentinel markers (``__FS_*__``) so the output can be parsed reliably
# without any user-supplied text.

_OPENFOAM_LOGIN_SCRIPT = (
    "command -v foamRun && command -v blockMesh && command -v checkMesh "
    "&& command -v postProcess && command -v decomposePar; "
    "printenv WM_PROJECT_DIR; "
    "printenv WM_PROJECT_VERSION; "
    "foamVersion 2>/dev/null || true"
)

_OPENFOAM_MODULES_SCRIPT = (
    "command -v module && module -t avail openfoam 2>&1 | head -20"
)

_SCHEDULER_SCRIPT = (
    "command -v sbatch; "
    "command -v squeue; "
    "command -v srun; "
    "command -v qsub; "
    "command -v qstat"
)

_RESOURCE_SCRIPT = (
    "echo __FS_CPU__; nproc; "
    "echo __FS_MEM__; "
    "__fs_mem=$(free -b 2>/dev/null | awk '/Mem:/{print $2}'); "
    "if [ -n \"$__fs_mem\" ]; then echo \"$__fs_mem\"; "
    "else head -1 /proc/meminfo; fi; "
    "echo __FS_OS__; uname -s; "
    "echo __FS_ARCH__; uname -m; "
    "echo __FS_HOST__; hostname; "
    "echo __FS_DISK__; "
    "__fs_disk=$(df -B1 . 2>/dev/null | tail -1 | awk '{print $4}'); "
    "[ -n \"$__fs_disk\" ] && echo \"$__fs_disk\" || echo 0"
)

# Remote workspace selection.  Only Fluid Scientist's own ``fluid_scientist/
# runs`` directory is ever created.  ``$SCRATCH`` and ``$HOME`` are remote
# environment variables expanded by the remote shell; no local user input is
# interpolated.  Output is ``READY::<reason>::<path>::<free_bytes>`` or
# ``FAILED``.
_WORKSPACE_SCRIPT = (
    "min=1073741824; chosen=''; reason=''; scratch=\"$SCRATCH\"; "
    "if [ -n \"$scratch\" ] && [ -d \"$scratch\" ] && [ -w \"$scratch\" ]; then "
    "  free=$(df -B1 \"$scratch\" 2>/dev/null | tail -1 | awk '{print $4}'); "
    "  if [ -n \"$free\" ] && [ \"$free\" -ge \"$min\" ] 2>/dev/null; then "
    "    target=\"$scratch/fluid_scientist/runs\"; mkdir -p \"$target\" 2>/dev/null; "
    "    if [ -d \"$target\" ] && [ -w \"$target\" ]; then chosen=\"$target\"; reason='scratch'; fi; "
    "  fi; "
    "fi; "
    "if [ -z \"$chosen\" ]; then "
    "  target=\"$HOME/fluid_scientist/runs\"; "
    "  if [ -d \"$target\" ] && [ -w \"$target\" ]; then chosen=\"$target\"; reason='home_existing'; fi; "
    "fi; "
    "if [ -z \"$chosen\" ]; then "
    "  target=\"$HOME/fluid_scientist/runs\"; mkdir -p \"$target\" 2>/dev/null; "
    "  if [ -d \"$target\" ] && [ -w \"$target\" ]; then chosen=\"$target\"; reason='home_created'; fi; "
    "fi; "
    "if [ -n \"$chosen\" ]; then "
    "  free=$(df -B1 \"$chosen\" 2>/dev/null | tail -1 | awk '{print $4}'); "
    "  [ -z \"$free\" ] && free=0; "
    "  printf 'READY::%s::%s::%s\\n' \"$reason\" \"$chosen\" \"$free\"; "
    "else printf 'FAILED\\n'; fi"
)


# Per-probe timeouts (seconds).
_OPENFOAM_TIMEOUT = 25.0
_MODULES_TIMEOUT = 15.0
_SCHEDULER_TIMEOUT = 12.0
_RESOURCE_TIMEOUT = 12.0
_WORKSPACE_TIMEOUT = 20.0

# Minimum free space (bytes) required for a remote workspace candidate.
_MIN_WORKSPACE_BYTES = 1 << 30  # 1 GiB

_BINARY_NAMES = ("foamRun", "blockMesh", "checkMesh", "postProcess", "decomposePar")
_SCHEDULER_BINARIES = ("sbatch", "squeue", "srun", "qsub", "qstat")

_VERSION_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,31}$")
_FOAM_VERSION_FROM_BANNER = re.compile(
    r"(?:OpenFOAM[-_ ](?:v(?:ersion)?[-_ ])?|version\s+)(v?\d+(?:\.\d+)*)",
    re.IGNORECASE,
)
_SENTINEL_RE = re.compile(r"^__FS_[A-Z]+__$")


def _login_command(script: str) -> str:
    """Wrap a fixed script for execution inside a remote login shell."""
    return "bash -lc " + shlex.quote(script)


def _run(runner: "SSHCommandRunner", host_alias: str, script: str, timeout: float) -> str:
    """Execute *script* remotely and return its stdout (``""`` on failure).

    The runner never raises for ordinary SSH/timeout errors (it returns a
    ``ProcessResult`` with a non-zero return code), but we guard against any
    unexpected exception so a probe always yields a result.
    """
    try:
        result = runner.run_remote(
            host_alias, _login_command(script), timeout=timeout
        )
    except Exception:
        return ""
    return getattr(result, "stdout", "") or ""


# ---------------------------------------------------------------------------
# OpenFOAMEnvironmentProbe
# ---------------------------------------------------------------------------


class OpenFOAMEnvironmentProbe:
    """Detect OpenFOAM in the remote login shell, falling back to modules.

    The probe first runs the fixed login-shell detection script.  If OpenFOAM
    is not already active, it probes environment modules.  The activation
    method is reported as :attr:`OpenFOAMActivationMethod.ALREADY_ACTIVE`
    (available in the login shell) or
    :attr:`OpenFOAMActivationMethod.ENVIRONMENT_MODULE` (available via
    ``module load``).  Remote dotfiles are never modified and no software is
    installed.
    """

    def probe(
        self, host_alias: str, runner: "SSHCommandRunner"
    ) -> OpenFOAMProbeResult:
        stdout = _run(runner, host_alias, _OPENFOAM_LOGIN_SCRIPT, _OPENFOAM_TIMEOUT)
        commands, wm_project_dir, version = _parse_openfoam_output(stdout)
        available = bool(commands.get("foamRun"))

        activation_method: OpenFOAMActivationMethod | None = None
        activation_reference: str | None = None

        if available:
            activation_method = OpenFOAMActivationMethod.ALREADY_ACTIVE
            activation_reference = wm_project_dir or "login-shell"
        else:
            modules = _parse_modules_output(
                _run(runner, host_alias, _OPENFOAM_MODULES_SCRIPT, _MODULES_TIMEOUT)
            )
            if modules:
                activation_method = OpenFOAMActivationMethod.ENVIRONMENT_MODULE
                activation_reference = ", ".join(modules)

        return OpenFOAMProbeResult(
            available=available,
            version=version,
            distribution=None,
            wm_project_dir=wm_project_dir,
            activation_method=activation_method,
            activation_reference=activation_reference,
            commands=commands,
        )


def _parse_openfoam_output(
    stdout: str,
) -> tuple[dict[str, bool], str | None, str | None]:
    """Parse the combined output of the fixed OpenFOAM detection script.

    Returns ``(commands, wm_project_dir, version)`` where *commands* maps
    each tracked binary name to whether its path was emitted by the
    ``command -v`` chain.
    """
    lines = [ln for ln in stdout.splitlines() if ln.strip() != ""]
    idx = 0
    commands: dict[str, bool] = {name: False for name in _BINARY_NAMES}

    # Leading lines: absolute paths emitted by the `command -v` chain.
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("/") and posixpath.basename(line) in _BINARY_NAMES:
            commands[posixpath.basename(line)] = True
            idx += 1
        else:
            break

    # Optional WM_PROJECT_DIR (an absolute path, e.g. /opt/OpenFOAM/OpenFOAM-13).
    wm_project_dir: str | None = None
    if idx < len(lines) and lines[idx].startswith("/"):
        wm_project_dir = lines[idx]
        idx += 1

    # Optional WM_PROJECT_VERSION (a short single token, no spaces/slashes).
    version: str | None = None
    if idx < len(lines) and "/" not in lines[idx] and _VERSION_TOKEN.match(lines[idx]):
        version = _clean_version(lines[idx])
        idx += 1

    # Remaining lines are the `foamVersion` banner; mine it for a version.
    if version is None:
        banner = "\n".join(lines[idx:])
        match = _FOAM_VERSION_FROM_BANNER.search(banner)
        if match:
            version = _clean_version(match.group(1))

    return commands, wm_project_dir, version


def _clean_version(token: str) -> str:
    """Normalise a version token (strip ``OpenFOAM-`` style prefixes)."""
    match = re.match(r"(?i)openfoam[-_]?v?(v?\d[\w.\-]*)", token)
    if match:
        return match.group(1)
    return token


def _parse_modules_output(stdout: str) -> list[str]:
    """Extract OpenFOAM module names from ``module -t avail openfoam`` output."""
    modules: list[str] = []
    for line in stdout.splitlines():
        name = line.strip()
        if not name or name.startswith("--") or name.startswith("/"):
            continue
        if "openfoam" in name.lower():
            modules.append(name)
    return modules


# ---------------------------------------------------------------------------
# SchedulerProbe
# ---------------------------------------------------------------------------


class SchedulerProbe:
    """Detect Slurm/PBS scheduler commands on the remote host."""

    def probe(
        self, host_alias: str, runner: "SSHCommandRunner"
    ) -> SchedulerProbeResult:
        stdout = _run(runner, host_alias, _SCHEDULER_SCRIPT, _SCHEDULER_TIMEOUT)
        commands = _parse_scheduler_output(stdout)
        has_slurm = any(commands.get(name) for name in ("sbatch", "squeue", "srun"))
        has_pbs = any(commands.get(name) for name in ("qsub", "qstat"))
        if has_slurm:
            scheduler = SchedulerType.SLURM
        elif has_pbs:
            scheduler = SchedulerType.PBS
        else:
            scheduler = SchedulerType.NONE
        return SchedulerProbeResult(scheduler=scheduler, commands=commands)


def _parse_scheduler_output(stdout: str) -> dict[str, bool]:
    """Map each scheduler binary name to whether it was found on the remote."""
    commands: dict[str, bool] = {name: False for name in _SCHEDULER_BINARIES}
    for line in stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        base = posixpath.basename(name) if name.startswith("/") else name
        if base in commands:
            commands[base] = True
    return commands


# ---------------------------------------------------------------------------
# ResourceProbe
# ---------------------------------------------------------------------------


class ResourceProbe:
    """Collect CPU, memory, OS, architecture, hostname, and disk info."""

    def probe(
        self, host_alias: str, runner: "SSHCommandRunner"
    ) -> ResourceProbeResult:
        stdout = _run(runner, host_alias, _RESOURCE_SCRIPT, _RESOURCE_TIMEOUT)
        sections = _parse_sections(stdout)
        cpu_count = _first_int(sections.get("__FS_CPU__", []))
        memory_bytes = _parse_memory(sections.get("__FS_MEM__", []))
        os_name = _first(sections.get("__FS_OS__", []))
        architecture = _first(sections.get("__FS_ARCH__", []))
        hostname = _first(sections.get("__FS_HOST__", []))
        disk = _first_int(sections.get("__FS_DISK__", []))
        return ResourceProbeResult(
            hostname=hostname or None,
            os=os_name or None,
            architecture=architecture or None,
            cpu_count=cpu_count or None,
            memory_bytes=memory_bytes or None,
            disk_available_bytes=disk or None,
        )


def _parse_sections(stdout: str) -> dict[str, list[str]]:
    """Split sentinel-delimited command output into named sections."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in stdout.splitlines():
        if _SENTINEL_RE.match(line):
            current = line
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return sections


def _first(lines: list[str]) -> str:
    for line in lines:
        text = line.strip()
        if text:
            return text
    return ""


def _first_int(lines: list[str], default: int = 0) -> int:
    text = _first(lines)
    try:
        return int(text)
    except (ValueError, TypeError):
        return default


def _parse_memory(lines: list[str]) -> int:
    """Parse memory in bytes from ``free`` output or ``/proc/meminfo``."""
    text = _first(lines)
    if not text:
        return 0
    if text.lower().startswith("memtotal:"):
        match = re.search(r"(\d+)\s*kB", text, re.IGNORECASE)
        if match:
            return int(match.group(1)) * 1024
        return 0
    try:
        return int(text)
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# RemoteWorkspaceProbe
# ---------------------------------------------------------------------------


class RemoteWorkspaceProbe:
    """Select a writable remote base directory for Fluid Scientist runs.

    Selection order (handled by the fixed remote script):

      1. ``$SCRATCH`` — writable with at least
         :data:`_MIN_WORKSPACE_BYTES` of free space.
      2. An existing ``~/fluid_scientist/runs`` directory.
      3. A freshly created ``~/fluid_scientist/runs`` directory.

    Only Fluid Scientist's own ``fluid_scientist/runs`` directories are ever
    created; no other remote paths are touched.
    """

    def probe(
        self, host_alias: str, runner: "SSHCommandRunner"
    ) -> RemoteWorkspaceResult:
        stdout = _run(runner, host_alias, _WORKSPACE_SCRIPT, _WORKSPACE_TIMEOUT)
        line = _first(stdout.splitlines())
        if line.startswith("READY::"):
            parts = line.split("::")
            # READY :: reason :: path :: free_bytes
            if len(parts) >= 4:
                base_dir = parts[2]
                try:
                    free = int(parts[3])
                except ValueError:
                    free = 0
                return RemoteWorkspaceResult(
                    remote_base_dir=base_dir,
                    writable=True,
                    disk_available_bytes=free,
                )
        return RemoteWorkspaceResult(
            remote_base_dir="",
            writable=False,
            disk_available_bytes=None,
        )


__all__ = [
    "OpenFOAMEnvironmentProbe",
    "SchedulerProbe",
    "ResourceProbe",
    "RemoteWorkspaceProbe",
]
