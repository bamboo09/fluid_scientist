"""Model runtime infrastructure for the CFD simulation platform.

This package implements the model runtime layer that sits between the
agent pipeline and the underlying LLM providers.  Its defining rules
are:

* **No silent fallback.**  Model failures surface as explicit
  :class:`ModelInvocationError` instances with ``fallback_used`` forced
  to ``False``; the platform never degrades to regex, templates or
  defaults behind the caller's back.
* **Full provenance.**  Every invocation is recorded as a
  :class:`ModelTrace` via :class:`TraceRecorder` (API keys are never
  stored).
* **Capability gating.**  A model may only serve as
  :attr:`ModelRole.PRIMARY_REASONER` after passing
  :class:`ModelAdmissionThresholds`.

The public API is intentionally small; import from
``fluid_scientist.model_runtime`` rather than from submodules.
"""
from __future__ import annotations

from fluid_scientist.model_runtime.capability_eval import (
    CapabilityEvalResult,
    ModelAdmissionThresholds,
    evaluate_model,
)
from fluid_scientist.model_runtime.client import ModelClient
from fluid_scientist.model_runtime.errors import (
    ErrorCode,
    ModelInvocationError,
    ModelInvocationResult,
)
from fluid_scientist.model_runtime.models import (
    ModelConfig,
    ModelHealthStatus,
    ModelRole,
)
from fluid_scientist.model_runtime.registry import ModelRegistry
from fluid_scientist.model_runtime.structured_output import StructuredOutputValidator
from fluid_scientist.model_runtime.tracing import ModelTrace, TraceRecorder

__all__ = [
    "CapabilityEvalResult",
    "ErrorCode",
    "ModelAdmissionThresholds",
    "ModelClient",
    "ModelConfig",
    "ModelHealthStatus",
    "ModelInvocationError",
    "ModelInvocationResult",
    "ModelRegistry",
    "ModelRole",
    "ModelTrace",
    "StructuredOutputValidator",
    "TraceRecorder",
    "evaluate_model",
]
