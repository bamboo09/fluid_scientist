"""One-click auto-connect CLI for workstation discovery and provisioning.

Usage::

    python -m fluid_scientist.workstations auto-connect
    python -m fluid_scientist.workstations auto-connect --json

The command discovers SSH-config host aliases, probes each candidate's
connection and environment (OpenFOAM, scheduler, remote workspace), saves
healthy candidates as :class:`WorkstationProfile` records, and
auto-selects a default when a single ready candidate is found.

Security constraints
--------------------
* No private keys, passwords, passphrases, or identity-file paths are ever
  requested from the user or written to logs.
* The user is only ever asked to confirm a host-key fingerprint or pick a
  workstation by display name — never to enter a path or credential.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from fluid_scientist.workstations.connection import WorkstationConnectionService
from fluid_scientist.workstations.bootstrap import (
    BootstrapRequest,
    WorkstationBootstrapService,
)
from fluid_scientist.workstations.discovery import WorkstationDiscoveryService
from fluid_scientist.workstations.models import (
    PlatformStatus,
    ProbeResult,
    WorkstationCandidate,
    WorkstationErrorCode,
    WorkstationProfile,
)
from fluid_scientist.workstations.profile_store import WorkstationProfileStore
from fluid_scientist.workstations.ssh_runner import SSHCommandRunner


# ---------------------------------------------------------------------------
# Service singletons (module-level so they can be monkey-patched in tests)
# ---------------------------------------------------------------------------

_runner = SSHCommandRunner()
_store = WorkstationProfileStore()
_discovery = WorkstationDiscoveryService(runner=_runner, profile_store=_store)
_connection = WorkstationConnectionService(runner=_runner, store=_store)
_bootstrap = WorkstationBootstrapService(
    runner=_runner,
    store=_store,
    discovery=_discovery,
    connection=_connection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile_is_ready(profile: WorkstationProfile) -> bool:
    """Return ``True`` if *profile* has ``PlatformStatus.READY``."""
    return profile.platform_status == PlatformStatus.READY


def _profile_summary(profile: WorkstationProfile) -> dict[str, Any]:
    """Build the JSON-serialisable summary dict for *profile*."""
    return {
        "status": profile.platform_status.value,
        "profile_id": profile.profile_id,
        "host_alias": profile.host_alias,
        "openfoam_version": profile.openfoam_version,
        "scheduler": profile.scheduler.value if profile.scheduler else "NONE",
        "remote_base_dir": profile.remote_base_dir,
        "is_default": profile.is_default,
    }


def _probe_needs_host_key_confirmation(probe_result: ProbeResult) -> bool:
    """Check whether *probe_result* indicates an unknown host key."""
    return (
        probe_result.error_code
        == WorkstationErrorCode.HOST_KEY_CONFIRMATION_REQUIRED.value
    )


def _confirm_host_key_interactive(
    candidate: WorkstationCandidate,
    probe_result: ProbeResult,
    *,
    input_fn=input,
    output_fn=print,
) -> bool:
    """Prompt the user to confirm an unknown host key.

    Displays the fingerprint extracted from the error message (never the
    full key material).  Returns ``True`` if the user confirms and the key
    was successfully added to ``known_hosts``.
    """
    host_alias = candidate.host_alias
    output_fn(f"\n  Host key for '{host_alias}' is not in known_hosts.")

    # Extract fingerprint from the error message if present.
    fingerprint: str | None = None
    if probe_result.error_message:
        msg = probe_result.error_message
        if "fingerprint:" in msg:
            fingerprint = msg.split("fingerprint:", 1)[1].split(";")[0].strip()
    if fingerprint:
        output_fn(f"  Fingerprint: {fingerprint}")
    output_fn(f"  Do you trust this host? [y/N] ", end="")

    try:
        answer = input_fn("").strip().lower()
    except (EOFError, KeyboardInterrupt):
        output_fn()
        return False

    if answer not in ("y", "yes"):
        output_fn(f"  Skipping '{host_alias}' — host key not confirmed.")
        return False

    try:
        confirmed = _runner.confirm_host_key(host_alias)
    except Exception:
        confirmed = False

    if not confirmed:
        output_fn(f"  Failed to add host key for '{host_alias}'.")
        return False

    output_fn(f"  Host key for '{host_alias}' confirmed.")
    return True


def _select_default_interactive(
    ready_profiles: list[WorkstationProfile],
    *,
    input_fn=input,
    output_fn=print,
) -> WorkstationProfile | None:
    """Prompt the user to pick one of *ready_profiles* as the default.

    Only display names are shown — no paths, keys, or credentials.
    """
    if not ready_profiles:
        return None
    if len(ready_profiles) == 1:
        return ready_profiles[0]

    output_fn("\n  Multiple ready workstations found:")
    for idx, profile in enumerate(ready_profiles, start=1):
        output_fn(f"    [{idx}] {profile.display_name}")

    output_fn(f"\n  Select a default [1-{len(ready_profiles)}]: ", end="")
    try:
        choice_raw = input_fn("").strip()
    except (EOFError, KeyboardInterrupt):
        output_fn()
        return None

    try:
        choice = int(choice_raw)
    except ValueError:
        output_fn("  Invalid selection — no default set.")
        return None

    if not (1 <= choice <= len(ready_profiles)):
        output_fn("  Selection out of range — no default set.")
        return None

    return ready_profiles[choice - 1]


# ---------------------------------------------------------------------------
# Main auto-connect flow
# ---------------------------------------------------------------------------


def auto_connect(
    *,
    output_json: bool = False,
    input_fn=input,
    output_fn=print,
) -> int:
    """Auto-discover, probe, and save workstation profiles.

    Returns 0 on success (at least one ready profile), 1 otherwise.
    """
    result: dict[str, Any] = {}

    # 1. Detect SSH
    if output_json is False:
        output_fn("Checking SSH client...")
    try:
        ssh_installed = _runner.check_ssh_installed()
    except Exception:
        ssh_installed = False

    if not ssh_installed:
        result = {
            "status": "SSH_NOT_INSTALLED",
            "error_code": WorkstationErrorCode.SSH_NOT_INSTALLED.value,
            "error_message": "ssh client is not installed or not on PATH",
        }
        if output_json:
            print(json.dumps(result, indent=2))
        else:
            output_fn("Error: SSH client is not installed.")
        return 1

    # 2. Discover host aliases
    if output_json is False:
        output_fn("Discovering SSH config hosts...")
    candidates = _discovery.discover()

    if not candidates:
        result = {
            "status": "NO_CANDIDATES",
            "error_code": "NO_CANDIDATES",
            "error_message": "no workstation candidates found in SSH config",
        }
        if output_json:
            print(json.dumps(result, indent=2))
        else:
            output_fn("No workstation candidates found in ~/.ssh/config.")
        return 1

    if output_json is False:
        output_fn(f"Found {len(candidates)} candidate(s):")
        for c in candidates:
            output_fn(f"  - {c.display_name} ({c.host_alias})")

    # 3-7. Probe each candidate, handle host-key confirmation, save healthy ones
    saved_profiles: list[WorkstationProfile] = []
    if output_json is False:
        output_fn("\nProbing candidates...")

    for candidate in candidates:
        host_alias = candidate.host_alias
        if output_json is False:
            output_fn(f"\n  Probing '{host_alias}'...")

        probe_result = _connection.probe(candidate)

        # 11. Unknown host key — pause and request confirmation
        if _probe_needs_host_key_confirmation(probe_result):
            if output_json is False:
                confirmed = _confirm_host_key_interactive(
                    candidate,
                    probe_result,
                    input_fn=input_fn,
                    output_fn=output_fn,
                )
                if confirmed:
                    # Re-probe after confirming the host key
                    probe_result = _connection.probe(candidate)
                else:
                    continue
            else:
                # In JSON mode we cannot interact — skip this candidate
                continue

        if probe_result.error_code:
            if output_json is False:
                output_fn(
                    f"  {host_alias}: FAILED — "
                    f"{probe_result.error_code}: {probe_result.error_message}"
                )
            continue

        if not probe_result.ssh_connected:
            if output_json is False:
                output_fn(f"  {host_alias}: SSH connection failed.")
            continue

        # Report probe results
        openfoam = probe_result.openfoam
        scheduler = probe_result.scheduler
        workspace = probe_result.remote_workspace

        if output_json is False:
            of_status = (
                f"OpenFOAM {openfoam.version}" if openfoam and openfoam.available
                else "OpenFOAM not found"
            )
            sched_status = (
                scheduler.scheduler.value if scheduler else "NONE"
            )
            ws_status = (
                workspace.remote_base_dir
                if workspace and workspace.writable
                else "no writable workspace"
            )
            output_fn(f"  {host_alias}: connected, {of_status}, "
                       f"scheduler={sched_status}, workspace={ws_status}")

        # 8. Save healthy profiles
        try:
            profile = _connection.save_profile(candidate, probe_result)
        except Exception as error:
            if output_json is False:
                output_fn(f"  {host_alias}: failed to save profile — {error}")
            continue

        saved_profiles.append(profile)
        if output_json is False:
            output_fn(f"  {host_alias}: saved as '{profile.profile_id}' "
                       f"(status={profile.platform_status.value})")

    # 9-10. Default selection
    ready_profiles = [p for p in saved_profiles if _profile_is_ready(p)]

    if not ready_profiles:
        # Fall back to any saved profile (even degraded)
        if saved_profiles:
            result = _profile_summary(saved_profiles[0])
            result["status"] = saved_profiles[0].platform_status.value
        else:
            result = {
                "status": "NO_HEALTHY_CANDIDATES",
                "error_code": "NO_HEALTHY_CANDIDATES",
                "error_message": "no candidates passed all probes",
            }
        if output_json:
            print(json.dumps(result, indent=2))
        else:
            output_fn("\nNo ready workstation profiles were saved.")
        return 1

    # Check if a default already exists among ready profiles
    default_profile: WorkstationProfile | None = None
    existing_default = _store.get_default()
    if existing_default:
        for p in ready_profiles:
            if p.profile_id == existing_default.profile_id:
                default_profile = existing_default
                break

    if default_profile is None:
        if len(ready_profiles) == 1:
            # 9. Auto-select single ready candidate
            default_profile = ready_profiles[0]
            try:
                _store.set_default(default_profile.profile_id)
            except Exception:
                pass
            if output_json is False:
                output_fn(
                    f"\nAuto-selected '{default_profile.display_name}' "
                    f"as default."
                )
        else:
            # 10. Prompt user to choose (only by name)
            if output_json is False:
                default_profile = _select_default_interactive(
                    ready_profiles,
                    input_fn=input_fn,
                    output_fn=output_fn,
                )
                if default_profile is not None:
                    try:
                        _store.set_default(default_profile.profile_id)
                    except Exception:
                        pass
                    output_fn(
                        f"\nSet '{default_profile.display_name}' as default."
                    )
            else:
                # In JSON mode, pick the first ready profile
                default_profile = ready_profiles[0]
                try:
                    _store.set_default(default_profile.profile_id)
                except Exception:
                    pass

    if default_profile is None:
        # User cancelled selection
        result = _profile_summary(ready_profiles[0])
        result["is_default"] = False
        if output_json:
            print(json.dumps(result, indent=2))
        else:
            output_fn("\nNo default workstation selected.")
        return 0

    # Refresh the profile to reflect is_default state
    refreshed = _store.get(default_profile.profile_id)
    if refreshed is not None:
        default_profile = refreshed

    result = _profile_summary(default_profile)

    if output_json:
        print(json.dumps(result, indent=2))
    else:
        output_fn(f"\nDefault workstation: {default_profile.display_name}")
        output_fn(f"  Status:          {result['status']}")
        output_fn(f"  Host alias:      {result['host_alias']}")
        output_fn(f"  OpenFOAM:        {result['openfoam_version'] or 'not detected'}")
        output_fn(f"  Scheduler:       {result['scheduler']}")
        output_fn(f"  Remote base dir: {result['remote_base_dir'] or 'N/A'}")
        output_fn(f"  Profile ID:      {result['profile_id']}")
        output_fn(f"  Is default:      {result['is_default']}")

    return 0


def bootstrap(
    *,
    host: str | None = None,
    username: str | None = None,
    port: int = 22,
    name: str | None = None,
    output_json: bool = False,
    input_fn=input,
    output_fn=print,
) -> int:
    """Bootstrap a workstation profile from minimal non-secret metadata."""
    if host is None and not output_json:
        output_fn("Host address or SSH host alias: ", end="")
        host = input_fn("").strip()
    if username is None and not output_json:
        output_fn("SSH username: ", end="")
        username = input_fn("").strip()
    if not port and not output_json:
        output_fn("SSH port [22]: ", end="")
        raw = input_fn("").strip()
        port = int(raw) if raw else 22
    if name is None and not output_json:
        output_fn("Workstation name (optional): ", end="")
        raw_name = input_fn("").strip()
        name = raw_name or None

    if not host or not username:
        result = {
            "status": "FAILED",
            "error_code": "INVALID_BOOTSTRAP_REQUEST",
            "error_message": "host and username are required",
        }
        if output_json:
            print(json.dumps(result, indent=2))
        else:
            output_fn("Error: host and username are required.")
        return 1

    try:
        request = BootstrapRequest(
            display_name=name,
            host=host,
            username=username,
            port=port,
        )
    except Exception as error:
        result = {
            "status": "FAILED",
            "error_code": "INVALID_BOOTSTRAP_REQUEST",
            "error_message": str(error),
        }
        if output_json:
            print(json.dumps(result, indent=2))
        else:
            output_fn(f"Error: {error}")
        return 1

    result = _bootstrap.bootstrap(request).model_dump()
    if output_json:
        print(json.dumps(result, indent=2, default=str))
    elif result.get("error_code"):
        output_fn(f"Bootstrap failed: {result['error_code']} - {result.get('error_message') or ''}")
    else:
        profile = result.get("profile") or {}
        output_fn(f"Saved workstation: {profile.get('display_name')}")
        output_fn(f"  Status: {result.get('status')}")
        output_fn(f"  Profile ID: {profile.get('profile_id')}")
        output_fn(f"  OpenFOAM: {profile.get('openfoam_version') or 'not detected'}")
    return 0 if not result.get("error_code") else 1


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).
    """
    parser = argparse.ArgumentParser(
        prog="fluid-scientist workstation",
        description="Workstation auto-discovery and connection management.",
    )
    subparsers = parser.add_subparsers(dest="command")

    ac = subparsers.add_parser(
        "auto-connect",
        help="Auto-discover and connect to workstations",
    )
    ac.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human-readable text",
    )
    bs = subparsers.add_parser(
        "bootstrap",
        help="Create a workstation profile from host/user/port only",
    )
    bs.add_argument("--host", help="Host address or SSH host alias")
    bs.add_argument("--username", help="SSH username")
    bs.add_argument("--port", type=int, default=22, help="SSH port")
    bs.add_argument("--name", help="Display name")
    bs.add_argument(
        "--json",
        action="store_true",
        help="Output machine-readable JSON instead of human-readable text",
    )

    args = parser.parse_args(argv)

    if args.command == "auto-connect":
        return auto_connect(output_json=args.json)
    if args.command == "bootstrap":
        return bootstrap(
            host=args.host,
            username=args.username,
            port=args.port,
            name=args.name,
            output_json=args.json,
        )

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
