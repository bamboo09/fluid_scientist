"""Typed application configuration with explicit real-integration requirements."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FLUID_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_mode: AppMode = AppMode.FAKE
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    data_node: NodeSettings = Field(default_factory=NodeSettings)
    login_node: NodeSettings = Field(default_factory=NodeSettings)
    slurm: SlurmSettings = Field(default_factory=SlurmSettings)
    openfoam: OpenFOAMSettings = Field(default_factory=OpenFOAMSettings)

    @model_validator(mode="after")
    def require_real_integrations(self) -> "AppSettings":
        if self.app_mode == AppMode.FAKE:
            return self
        required = {
            "openai.api_key": self.openai.api_key,
            "data_node.host": self.data_node.host,
            "data_node.username": self.data_node.username,
            "login_node.host": self.login_node.host,
            "login_node.username": self.login_node.username,
            "slurm.partition": self.slurm.partition,
            "openfoam.module_name": self.openfoam.module_name,
            "openfoam.shared_root": self.openfoam.shared_root,
        }
        missing = [name for name, value in required.items() if value in {None, ""}]
        if missing:
            raise ValueError("real mode requires: " + ", ".join(missing))
        return self
