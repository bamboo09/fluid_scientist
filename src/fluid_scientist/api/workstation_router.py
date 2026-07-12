"""FastAPI router for workstation discovery, probing, and profile management.

Exposes REST endpoints under ``/api/v5/workstations`` that wire together the
workstation discovery, connection-probe, and profile-persistence services.

No private keys, passwords, or identity-file paths are ever collected or
returned by any endpoint.  All SSH operations rely on the system SSH
configuration and ``ssh-agent``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, status

from fluid_scientist.workstations.bootstrap import (
    BootstrapRequest,
    WorkstationBootstrapService,
)
from fluid_scientist.workstations.connection import WorkstationConnectionService
from fluid_scientist.workstations.discovery import WorkstationDiscoveryService
from fluid_scientist.workstations.models import (
    WorkstationCandidate,
    WorkstationErrorCode,
)
from fluid_scientist.workstations.profile_store import WorkstationProfileStore
from fluid_scientist.workstations.ssh_runner import SSHCommandRunner

router = APIRouter(prefix="/api/v5/workstations", tags=["workstations"])

# ---------------------------------------------------------------------------
# Module-level singletons
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

_SSH_CONFIG_PATH = Path.home() / ".ssh" / "config"

# ---------------------------------------------------------------------------
# Error-code → HTTP-status mapping
# ---------------------------------------------------------------------------

_ERROR_STATUS_MAP: dict[str, int] = {
    WorkstationErrorCode.SSH_NOT_INSTALLED.value: status.HTTP_503_SERVICE_UNAVAILABLE,
    WorkstationErrorCode.SSH_CONFIG_NOT_FOUND.value: status.HTTP_404_NOT_FOUND,
    WorkstationErrorCode.HOST_KEY_CONFIRMATION_REQUIRED.value: (
        status.HTTP_422_UNPROCESSABLE_ENTITY
    ),
    WorkstationErrorCode.HOST_KEY_CHANGED.value: status.HTTP_409_CONFLICT,
    WorkstationErrorCode.AUTHENTICATION_FAILED.value: status.HTTP_401_UNAUTHORIZED,
    WorkstationErrorCode.CONNECTION_TIMEOUT.value: status.HTTP_504_GATEWAY_TIMEOUT,
    WorkstationErrorCode.REMOTE_COMMAND_FAILED.value: status.HTTP_502_BAD_GATEWAY,
    WorkstationErrorCode.OPENFOAM_NOT_FOUND.value: status.HTTP_422_UNPROCESSABLE_ENTITY,
    WorkstationErrorCode.REMOTE_DIRECTORY_NOT_WRITABLE.value: (
        status.HTTP_422_UNPROCESSABLE_ENTITY
    ),
    WorkstationErrorCode.SCHEDULER_PROBE_FAILED.value: (
        status.HTTP_422_UNPROCESSABLE_ENTITY
    ),
    WorkstationErrorCode.PROFILE_NOT_FOUND.value: status.HTTP_404_NOT_FOUND,
    WorkstationErrorCode.NO_USABLE_SYSTEM_SSH_IDENTITY.value: (
        status.HTTP_401_UNAUTHORIZED
    ),
    WorkstationErrorCode.INVALID_BOOTSTRAP_REQUEST.value: (
        status.HTTP_422_UNPROCESSABLE_ENTITY
    ),
}


def _raise_for_error(error_code: str | None, error_message: str | None) -> None:
    """Raise an :class:`HTTPException` with a structured error code.

    If *error_code* is ``None`` or empty the function is a no-op.
    """
    if not error_code:
        return
    http_status = _ERROR_STATUS_MAP.get(
        error_code, status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    raise HTTPException(
        status_code=http_status,
        detail={
            "error_code": error_code,
            "error_message": error_message or "",
        },
    )


def _find_candidate(candidate_id: str) -> WorkstationCandidate:
    """Find a workstation candidate by *candidate_id* via fresh discovery.

    Raises a 404 :class:`HTTPException` if no matching candidate is found.
    """
    try:
        candidates = _discovery.discover()
    except Exception as error:  # pragma: no cover - defensive guard
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_code": "DISCOVERY_FAILED",
                "error_message": str(error),
            },
        ) from error

    for candidate in candidates:
        if candidate.candidate_id == candidate_id:
            return candidate
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error_code": "CANDIDATE_NOT_FOUND",
            "error_message": f"no workstation candidate with id '{candidate_id}'",
        },
    )


# ---------------------------------------------------------------------------
# Endpoints — fixed paths first (must precede /{profile_id})
# ---------------------------------------------------------------------------


@router.get("/discover")
async def discover_workstations() -> dict:
    """Auto-discover SSH-config workstation candidates.

    Returns candidate metadata, SSH availability, config presence, and
    ssh-agent status.  On error the response includes ``error_code`` and
    ``error_message`` fields.
    """
    try:
        ssh_installed = _runner.check_ssh_installed()
    except Exception:
        ssh_installed = False

    ssh_config_found = _SSH_CONFIG_PATH.is_file()

    try:
        agent_info = _runner.check_ssh_agent()
        agent_available = bool(agent_info.get("available", False))
    except Exception:
        agent_available = False

    if not ssh_installed:
        return {
            "candidates": [],
            "ssh_installed": False,
            "ssh_config_found": ssh_config_found,
            "agent_available": agent_available,
            "error_code": WorkstationErrorCode.SSH_NOT_INSTALLED.value,
            "error_message": "ssh client is not installed or not on PATH",
        }

    if not ssh_config_found:
        return {
            "candidates": [],
            "ssh_installed": True,
            "ssh_config_found": False,
            "agent_available": agent_available,
            "error_code": WorkstationErrorCode.SSH_CONFIG_NOT_FOUND.value,
            "error_message": f"ssh config not found at {_SSH_CONFIG_PATH}",
        }

    candidates = _discovery.discover()
    return {
        "candidates": [c.model_dump() for c in candidates],
        "ssh_installed": True,
        "ssh_config_found": True,
        "agent_available": agent_available,
    }


@router.get("/default")
async def get_default_profile() -> dict:
    """Get the default workstation profile.

    Raises 404 if no default profile is set.
    """
    profile = _store.get_default()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": "NO_DEFAULT_PROFILE",
                "error_message": "no default workstation profile is set",
            },
        )
    return profile.model_dump()


@router.get("/bootstrap-status")
async def bootstrap_status() -> dict:
    """Return whether a minimal workstation bootstrap is required."""
    return _bootstrap.status().model_dump()


@router.post("/bootstrap")
async def bootstrap_workstation(request: BootstrapRequest) -> dict:
    """Bootstrap a profile from host/user/port only.

    The request model forbids secret/path/shell fields. Authentication and
    probing continue through the system SSH agent/config and existing
    workstation connection service.
    """
    result = _bootstrap.bootstrap(request)
    if result.error_code:
        _raise_for_error(result.error_code, result.error_message)
    return result.model_dump()


@router.get("")
async def list_profiles() -> dict:
    """List all saved workstation profiles."""
    profiles = _store.list_all()
    return {"profiles": [p.model_dump() for p in profiles]}


# ---------------------------------------------------------------------------
# Endpoints — candidate-scoped operations
# ---------------------------------------------------------------------------


@router.post("/{candidate_id}/probe")
async def probe_candidate(candidate_id: str) -> dict:
    """Execute connection and environment probing for a candidate.

    Runs SSH authentication, OpenFOAM, scheduler, resource, and workspace
    probes.  Returns the full :class:`ProbeResult` as a dict.
    """
    candidate = _find_candidate(candidate_id)
    probe_result = _connection.probe(candidate)
    if probe_result.error_code:
        _raise_for_error(probe_result.error_code, probe_result.error_message)
    return probe_result.model_dump()


@router.post("/{candidate_id}/confirm-host-key")
async def confirm_host_key(candidate_id: str) -> dict:
    """Confirm (trust) an unknown host fingerprint.

    Adds the host key to ``known_hosts`` via ``ssh-keyscan -H``.
    """
    candidate = _find_candidate(candidate_id)
    host_alias = candidate.host_alias
    try:
        confirmed = _runner.confirm_host_key(host_alias)
    except Exception:
        confirmed = False
    fingerprint: str | None = None
    if confirmed:
        try:
            fingerprint = _runner.get_host_fingerprint(host_alias)
        except Exception:
            fingerprint = None
    return {"confirmed": confirmed, "fingerprint": fingerprint}


@router.post("/{candidate_id}/save")
async def save_profile(candidate_id: str, body: dict = Body(default={})) -> dict:
    """Save a candidate as a :class:`WorkstationProfile`.

    The request *body* may contain a ``display_name`` field.  A fresh probe
    is performed before saving so the profile reflects the current remote
    state.  Only profiles whose SSH connection succeeds are persisted.
    """
    candidate = _find_candidate(candidate_id)
    display_name = body.get("display_name") if body else None

    probe_result = _connection.probe(candidate)
    if probe_result.error_code:
        _raise_for_error(probe_result.error_code, probe_result.error_message)

    profile = _connection.save_profile(
        candidate, probe_result, display_name=display_name
    )
    return profile.model_dump()


# ---------------------------------------------------------------------------
# Endpoints — profile-scoped operations (path-param routes last)
# ---------------------------------------------------------------------------


@router.get("/{profile_id}")
async def get_profile(profile_id: str) -> dict:
    """Get a single workstation profile by ID."""
    profile = _store.get(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": WorkstationErrorCode.PROFILE_NOT_FOUND.value,
                "error_message": f"no workstation profile with id '{profile_id}'",
            },
        )
    return profile.model_dump()


@router.post("/{profile_id}/test")
async def test_profile(profile_id: str) -> dict:
    """Re-test a previously saved profile's connection.

    Re-runs the full probe flow against the stored host alias.
    """
    probe_result = _connection.test_profile(profile_id)
    if probe_result.error_code:
        _raise_for_error(probe_result.error_code, probe_result.error_message)
    return probe_result.model_dump()


@router.post("/{profile_id}/set-default")
async def set_default(profile_id: str) -> dict:
    """Mark a profile as the default workstation."""
    profile = _store.get(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": WorkstationErrorCode.PROFILE_NOT_FOUND.value,
                "error_message": f"no workstation profile with id '{profile_id}'",
            },
        )
    try:
        _store.set_default(profile_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": WorkstationErrorCode.PROFILE_NOT_FOUND.value,
                "error_message": f"no workstation profile with id '{profile_id}'",
            },
        )
    return {"profile_id": profile_id, "is_default": True}


@router.delete("/{profile_id}")
async def delete_profile(profile_id: str) -> dict:
    """Delete a local workstation profile.

    Only the local database row is removed; ``~/.ssh/config``,
    ``known_hosts``, and remote files are never touched.
    """
    profile = _store.get(profile_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_code": WorkstationErrorCode.PROFILE_NOT_FOUND.value,
                "error_message": f"no workstation profile with id '{profile_id}'",
            },
        )
    _store.delete(profile_id)
    return {"profile_id": profile_id, "deleted": True}
