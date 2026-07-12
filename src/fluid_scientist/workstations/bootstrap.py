"""Minimal workstation bootstrap without requiring an SSH config file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from fluid_scientist.workstations.connection import WorkstationConnectionService
from fluid_scientist.workstations.discovery import WorkstationDiscoveryService
from fluid_scientist.workstations.models import (
    CandidateSource,
    ConnectionStatus,
    CredentialSource,
    PlatformStatus,
    WorkstationCandidate,
    WorkstationErrorCode,
    WorkstationProfile,
)
from fluid_scientist.workstations.profile_store import WorkstationProfileStore
from fluid_scientist.workstations.ssh_runner import SSHCommandRunner, SafeHostAlias

BootstrapStatus = Literal[
    "READY",
    "HAS_SAVED_PROFILES",
    "HAS_DISCOVERED_CANDIDATES",
    "NEEDS_MINIMAL_BOOTSTRAP",
]

_SAFE_USERNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


class BootstrapStatusView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovered_candidates: int = 0
    saved_profiles: int = 0
    default_profile_id: str | None = None
    status: BootstrapStatus = "NEEDS_MINIMAL_BOOTSTRAP"


class BootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] = None
    host: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    port: int = Field(default=22, ge=1, le=65535)


class BootstrapResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    profile: dict | None = None
    probe: dict | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class WorkstationBootstrapService:
    """Create a default profile from minimal host/user metadata.

    The service intentionally accepts only non-sensitive connection metadata.
    Authentication is delegated to system OpenSSH and ssh-agent via the
    existing :class:`SSHCommandRunner` and :class:`WorkstationConnectionService`.
    """

    runner: SSHCommandRunner | None = None
    store: WorkstationProfileStore | None = None
    discovery: WorkstationDiscoveryService | None = None
    connection: WorkstationConnectionService | None = None

    def __post_init__(self) -> None:
        self.runner = self.runner or SSHCommandRunner()
        self.store = self.store or WorkstationProfileStore()
        self.discovery = self.discovery or WorkstationDiscoveryService(
            runner=self.runner,
            profile_store=self.store,
        )
        self.connection = self.connection or WorkstationConnectionService(
            runner=self.runner,
            store=self.store,
        )

    def status(self) -> BootstrapStatusView:
        profiles = self.store.list_all()
        default = self.store.get_default()
        try:
            candidates = self.discovery.discover()
        except Exception:
            candidates = []
        if default and default.platform_status == PlatformStatus.READY:
            status: BootstrapStatus = "READY"
        elif profiles:
            status = "HAS_SAVED_PROFILES"
        elif candidates:
            status = "HAS_DISCOVERED_CANDIDATES"
        else:
            status = "NEEDS_MINIMAL_BOOTSTRAP"
        return BootstrapStatusView(
            discovered_candidates=len(candidates),
            saved_profiles=len(profiles),
            default_profile_id=default.profile_id if default else None,
            status=status,
        )

    def bootstrap(self, request: BootstrapRequest) -> BootstrapResult:
        try:
            candidate = self._candidate_from_request(request)
        except ValueError as error:
            return BootstrapResult(
                status="FAILED",
                error_code=WorkstationErrorCode.INVALID_BOOTSTRAP_REQUEST.value,
                error_message=str(error),
            )

        probe = self.connection.probe(candidate)
        if probe.error_code:
            return BootstrapResult(
                status="FAILED",
                probe=probe.model_dump(),
                error_code=probe.error_code,
                error_message=probe.error_message,
            )

        profile = self.connection.save_profile(
            candidate,
            probe,
            display_name=request.display_name or f"{request.username}@{request.host}",
        )
        try:
            self.store.set_default(profile.profile_id)
            profile = self.store.get(profile.profile_id) or profile
        except Exception:
            pass

        return BootstrapResult(
            status=profile.platform_status.value,
            profile=profile.model_dump(),
            probe=probe.model_dump(),
        )

    def _candidate_from_request(self, request: BootstrapRequest) -> WorkstationCandidate:
        host = SafeHostAlias(request.host).value
        username = request.username
        if not _SAFE_USERNAME.fullmatch(username):
            raise ValueError("username must be a valid OpenSSH user name")
        alias = host
        self.runner.register_target(
            alias,
            hostname=host,
            username=username,
            port=request.port,
        )
        return WorkstationCandidate(
            candidate_id=f"bootstrap:{alias}:{username}:{request.port}",
            host_alias=alias,
            display_name=request.display_name or f"{username}@{host}",
            resolved_host=host,
            resolved_user=username,
            resolved_port=request.port,
            credential_source=CredentialSource.SSH_AGENT,
            connection_status=ConnectionStatus.UNTESTED,
            source=CandidateSource.BOOTSTRAP,
        )


__all__ = [
    "BootstrapRequest",
    "BootstrapResult",
    "BootstrapStatusView",
    "WorkstationBootstrapService",
]
