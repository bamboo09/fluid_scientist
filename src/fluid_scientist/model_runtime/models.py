"""Model role definitions and configuration models.

Defines the static configuration surface for the model runtime:

* :class:`ModelRole` enumerates the distinct reasoning roles a model can
  play in the CFD agent pipeline.
* :class:`ModelConfig` captures the per-role provider/model settings used
  to build a concrete client.
* :class:`ModelHealthStatus` captures the result of a (non-network)
  health probe against a registered model.

All models use Pydantic v2 and forbid extra fields so misconfiguration
fails loudly rather than being silently ignored.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import StrEnum, UTC

__all__ = ["ModelRole", "ModelConfig", "ModelHealthStatus"]


class ModelRole(StrEnum):
    """The distinct reasoning roles a model can play in the pipeline.

    Each role maps to a different responsibility and may be backed by a
    different provider/model with different capability requirements.
    Only :attr:`PRIMARY_REASONER` is subject to admission thresholds
    before it may be registered.
    """

    PRIMARY_REASONER = "primary_reasoner"
    CRITIC = "critic"
    FAST_ASSISTANT = "fast_assistant"
    CODE_EXTENSION = "code_extension"


class ModelConfig(BaseModel):
    """Static configuration for a single model role.

    ``api_key_env`` holds the *name* of an environment variable that
    resolves to the API key, never the key itself, so configs remain
    safe to log and trace.
    """

    model_config = ConfigDict(extra="forbid")

    role: ModelRole
    provider: str
    model_name: str
    api_key_env: str | None = None
    base_url: str | None = None
    timeout_seconds: float = 120.0
    temperature: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    structured_output_enabled: bool = True
    tool_calling_enabled: bool = False


class ModelHealthStatus(BaseModel):
    """Outcome of a health probe against a registered model.

    A health probe in this runtime is a *metadata-level* check (the model
    is registered, and for :attr:`ModelRole.PRIMARY_REASONER` has passed
    capability admission); it intentionally performs no network calls so
    the runtime stays importable and testable without external APIs.
    """

    model_config = ConfigDict(extra="forbid")

    role: ModelRole
    provider: str
    configured_model: str
    actual_returned_model: str | None = None
    structured_output_support: bool = False
    reasoning_mode: str | None = None
    last_health_check: str = Field(
        default_factory=lambda: _now_iso()
    )
    capability_eval_version: str | None = None
    pass_fail: Literal["pass", "fail"] = "fail"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Uses :data:`fluid_scientist.compat.UTC` so the module stays
    Python-3.10 compatible.
    """
    from datetime import datetime

    return datetime.now(UTC).isoformat()
