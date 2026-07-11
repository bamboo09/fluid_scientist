"""Pydantic data models for workstation discovery, probing, and profiling."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class KnownHostStatus(str, Enum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    CHANGED = "CHANGED"


class ConnectionStatus(str, Enum):
    UNTESTED = "UNTESTED"
    REACHABLE = "REACHABLE"
    UNREACHABLE = "UNREACHABLE"


class PlatformStatus(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class CredentialSource(str, Enum):
    SSH_CONFIG = "SSH_CONFIG"
    SSH_AGENT = "SSH_AGENT"
    SYSTEM = "SYSTEM"


class CandidateSource(str, Enum):
    SSH_CONFIG = "SSH_CONFIG"
    PROFILE = "PROFILE"
    HISTORY = "HISTORY"


class SchedulerType(str, Enum):
    NONE = "NONE"
    SLURM = "SLURM"
    PBS = "PBS"


class OpenFOAMActivationMethod(str, Enum):
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    LOGIN_SHELL = "LOGIN_SHELL"
    ENVIRONMENT_MODULE = "ENVIRONMENT_MODULE"


class WorkstationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    host_alias: str
    display_name: str
    resolved_host: str
    resolved_user: str
    resolved_port: int = 22
    proxy_jump: str | None = None
    credential_source: CredentialSource = CredentialSource.SSH_CONFIG
    known_host_status: KnownHostStatus = KnownHostStatus.UNKNOWN
    connection_status: ConnectionStatus = ConnectionStatus.UNTESTED
    source: CandidateSource = CandidateSource.SSH_CONFIG
    last_success_at: str | None = None


class OpenFOAMProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    version: str | None = None
    distribution: str | None = None
    wm_project_dir: str | None = None
    activation_method: OpenFOAMActivationMethod | None = None
    activation_reference: str | None = None
    commands: dict[str, bool] = Field(default_factory=dict)


class SchedulerProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduler: SchedulerType = SchedulerType.NONE
    commands: dict[str, bool] = Field(default_factory=dict)


class ResourceProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hostname: str | None = None
    os: str | None = None
    architecture: str | None = None
    cpu_count: int | None = None
    memory_bytes: int | None = None
    disk_available_bytes: int | None = None


class RemoteWorkspaceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    remote_base_dir: str
    writable: bool
    disk_available_bytes: int | None = None


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    host_alias: str
    ssh_connected: bool = False
    openfoam: OpenFOAMProbeResult | None = None
    scheduler: SchedulerProbeResult | None = None
    resources: ResourceProbeResult | None = None
    remote_workspace: RemoteWorkspaceResult | None = None
    error_code: str | None = None
    error_message: str | None = None
    probed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class WorkstationProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    display_name: str
    host_alias: str
    resolved_host: str
    detected_username: str
    detected_port: int = 22
    connection_method: str = "SSH_CONFIG"
    known_host_fingerprint: str | None = None
    scheduler: SchedulerType = SchedulerType.NONE
    openfoam_available: bool = False
    openfoam_version: str | None = None
    openfoam_activation_method: str | None = None
    openfoam_activation_reference: str | None = None
    remote_base_dir: str | None = None
    remote_os: str | None = None
    cpu_count: int | None = None
    memory_bytes: int | None = None
    disk_available_bytes: int | None = None
    connection_status: str = "UNTESTED"
    platform_status: PlatformStatus = PlatformStatus.UNAVAILABLE
    last_probe_at: str | None = None
    last_success_at: str | None = None
    is_default: bool = False


class WorkstationErrorCode(str, Enum):
    SSH_NOT_INSTALLED = "SSH_NOT_INSTALLED"
    SSH_CONFIG_NOT_FOUND = "SSH_CONFIG_NOT_FOUND"
    HOST_KEY_CONFIRMATION_REQUIRED = "HOST_KEY_CONFIRMATION_REQUIRED"
    HOST_KEY_CHANGED = "HOST_KEY_CHANGED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    CONNECTION_TIMEOUT = "CONNECTION_TIMEOUT"
    REMOTE_COMMAND_FAILED = "REMOTE_COMMAND_FAILED"
    OPENFOAM_NOT_FOUND = "OPENFOAM_NOT_FOUND"
    REMOTE_DIRECTORY_NOT_WRITABLE = "REMOTE_DIRECTORY_NOT_WRITABLE"
    SCHEDULER_PROBE_FAILED = "SCHEDULER_PROBE_FAILED"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    NO_USABLE_SYSTEM_SSH_IDENTITY = "NO_USABLE_SYSTEM_SSH_IDENTITY"
