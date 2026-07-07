"""Typed application configuration with explicit real-integration requirements."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from fluid_scientist.compat import StrEnum


class AppMode(StrEnum):
    FAKE = "fake"
    REAL = "real"


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenAISettings(ConfigModel):
    api_key: SecretStr | None = None
    planner_model: str = "gpt-5.5"
    extractor_model: str = "gpt-5.4-mini"
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)


class ProviderSettings(ConfigModel):
    """Ephemeral configuration for a selected experiment-plan provider."""

    model_config = ConfigDict(extra="forbid", strict=True)

    provider: Literal["openai", "glm", "deepseek"]
    api_key: SecretStr
    model: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)

    @field_validator("api_key")
    @classmethod
    def require_nonempty_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must not be empty")
        return value


class DatabaseSettings(ConfigModel):
    url: str = "sqlite:///fluid_scientist.db"


class NodeSettings(ConfigModel):
    host: str | None = None
    username: str | None = None
    port: int = Field(default=22, ge=1, le=65_535)
    identity_file: str | None = None
    known_hosts_file: str | None = None


class SlurmSettings(ConfigModel):
    partition: str | None = None
    account: str | None = None
    qos: str | None = None
    poll_interval_seconds: float = Field(default=10.0, gt=0, le=300)
    poll_timeout_seconds: float = Field(default=86_400.0, gt=0)


class OpenFOAMSettings(ConfigModel):
    module_name: str | None = None
    version: str | None = None
    shared_root: str | None = None


class WorkstationSettings(ConfigModel):
    hosts: tuple[str, ...] = ()
    username: str | None = None
    port: int = Field(default=22, ge=1, le=65_535)
    identity_file: str | None = None
    known_hosts_file: str | None = None


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLUID_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_mode: AppMode = AppMode.FAKE
    research_workflow_v2: bool = True  # Feature flag for new workflow
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    data_node: NodeSettings = Field(default_factory=NodeSettings)
    login_node: NodeSettings = Field(default_factory=NodeSettings)
    slurm: SlurmSettings = Field(default_factory=SlurmSettings)
    openfoam: OpenFOAMSettings = Field(default_factory=OpenFOAMSettings)
    workstation: WorkstationSettings = Field(default_factory=WorkstationSettings)

    @model_validator(mode="after")
    def require_real_integrations(self) -> "AppSettings":
        if self.app_mode == AppMode.FAKE:
            return self
        missing = []
        if self.openai.api_key is None:
            missing.append("openai.api_key")
        hpc = {
            "data_node.host": self.data_node.host,
            "data_node.username": self.data_node.username,
            "login_node.host": self.login_node.host,
            "login_node.username": self.login_node.username,
            "slurm.partition": self.slurm.partition,
            "openfoam.module_name": self.openfoam.module_name,
            "openfoam.shared_root": self.openfoam.shared_root,
        }
        workstation = {
            "workstation.hosts": self.workstation.hosts,
            "workstation.username": self.workstation.username,
            "workstation.known_hosts_file": self.workstation.known_hosts_file,
        }
        hpc_ready = all(value not in {None, ""} for value in hpc.values())
        workstation_ready = all(value not in {None, "", ()} for value in workstation.values())
        if not hpc_ready and not workstation_ready:
            missing.append("one complete execution platform (workstation or HPC)")
        if missing:
            raise ValueError("real mode requires: " + ", ".join(missing))
        return self
