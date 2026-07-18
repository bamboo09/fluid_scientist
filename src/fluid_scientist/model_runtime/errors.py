"""Explicit error model for model runtime invocations.

A core invariant of the platform is that model failures are *never*
silently swallowed or papered over with regex/template/default
fallbacks.  Every failure surfaces as an explicit
:class:`ModelInvocationError` whose ``fallback_used`` is forced to
``False`` in real mode, and every invocation returns a
:class:`ModelInvocationResult` that makes success/failure and the
associated :class:`~fluid_scientist.model_runtime.tracing.ModelTrace`
unambiguous.

``ModelInvocationError`` is both a structured error record (with
pydantic-validated fields via :class:`pydantic.TypeAdapter`) and a
raisable :class:`Exception`, so it can be surfaced either as a returned
result (by :class:`~fluid_scientist.model_runtime.client.ModelClient`)
or raised directly (by
:class:`~fluid_scientist.model_runtime.registry.ModelRegistry` when a
model is rejected at admission).
"""
from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from fluid_scientist.model_runtime.tracing import ModelTrace

__all__ = [
    "ModelInvocationError",
    "ModelInvocationResult",
    "ErrorCode",
]

#: The closed set of error codes a model invocation can fail with.
ErrorCode = Literal[
    "MODEL_UNAVAILABLE",
    "MODEL_TIMEOUT",
    "MODEL_OUTPUT_INVALID",
    "MODEL_SCHEMA_MISMATCH",
    "MODEL_CAPABILITY_INSUFFICIENT",
    "SKILL_LOAD_FAILED",
]

# Pydantic adapter used to validate the ``code`` Literal at construction
# time.  Re-used across instances for cheapness.
_CODE_ADAPTER: TypeAdapter[ErrorCode] = TypeAdapter(ErrorCode)


class ModelInvocationError(Exception):
    """Structured, raisable error for a failed model invocation.

    The required fields mirror the failure dimensions the platform cares
    about: *what* kind of failure (``code``), *where* it happened
    (``provider`` / ``configured_model`` / ``actual_model``), *which*
    request it was (``request_id``), whether it is worth retrying, and
    whether a fallback was used.

    ``fallback_used`` is **always** ``False`` in real mode.  Attempting
    to construct an error with ``fallback_used=True`` raises
    :class:`ValueError` - the platform prohibits silent fallback to
    regex, templates or defaults.
    """

    __slots__ = (
        "code",
        "provider",
        "configured_model",
        "actual_model",
        "request_id",
        "retryable",
        "fallback_used",
        "message",
    )

    def __init__(
        self,
        *,
        code: ErrorCode,
        provider: str,
        configured_model: str,
        actual_model: str | None = None,
        request_id: str | None = None,
        retryable: bool = False,
        fallback_used: bool = False,
        message: str | None = None,
    ) -> None:
        # Validate the closed set of codes via pydantic (Literal enforcement).
        validated_code: ErrorCode = _CODE_ADAPTER.validate_python(code)

        # Hard invariant: in real mode the platform never silently falls
        # back.  ``fallback_used`` exists for explicitness/audit but must
        # always be False.
        if fallback_used:
            raise ValueError(
                "fallback_used must be False in real mode; silent fallback "
                "to regex/templates/defaults is prohibited"
            )

        self.code = validated_code
        self.provider = provider
        self.configured_model = configured_model
        self.actual_model = actual_model
        self.request_id = request_id
        self.retryable = retryable
        self.fallback_used = False
        self.message = message

        super().__init__(message or validated_code)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation (safe to log/trace)."""
        return {
            "code": self.code,
            "provider": self.provider,
            "configured_model": self.configured_model,
            "actual_model": self.actual_model,
            "request_id": self.request_id,
            "retryable": self.retryable,
            "fallback_used": self.fallback_used,
            "message": self.message,
        }

    def __repr__(self) -> str:
        return (
            f"ModelInvocationError(code={self.code!r}, provider={self.provider!r}, "
            f"configured_model={self.configured_model!r})"
        )


T = TypeVar("T")


class ModelInvocationResult(BaseModel, Generic[T]):
    """Generic wrapper around a model invocation outcome.

    On success ``value`` is populated and ``error`` is ``None``; on
    failure ``error`` is populated (always with ``fallback_used=False``)
    and ``value`` is ``None``.  ``trace`` carries the
    :class:`~fluid_scientist.model_runtime.tracing.ModelTrace` recorded
    for the call, so every result is fully auditable.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    value: T | None = None
    error: ModelInvocationError | None = None
    trace: ModelTrace | None = None
    request_id: str | None = None

    @model_validator(mode="after")
    def _check_success_failure_consistency(self) -> "ModelInvocationResult[T]":
        if self.success:
            if self.value is None:
                raise ValueError("a successful result must carry a value")
            if self.error is not None:
                raise ValueError("a successful result must not carry an error")
        else:
            if self.error is None:
                raise ValueError("a failed result must carry an error")
            # Enforce the real-mode invariant on the carried error too.
            if self.error.fallback_used:
                raise ValueError(
                    "fallback_used must be False in real mode; silent fallback "
                    "is prohibited"
                )
        return self

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def ok(
        cls,
        value: T,
        *,
        trace: ModelTrace | None = None,
        request_id: str | None = None,
    ) -> "ModelInvocationResult[T]":
        """Build a successful result carrying *value*."""
        return cls(
            success=True,
            value=value,
            error=None,
            trace=trace,
            request_id=request_id,
        )

    @classmethod
    def fail(
        cls,
        error: ModelInvocationError,
        *,
        trace: ModelTrace | None = None,
    ) -> "ModelInvocationResult[T]":
        """Build a failed result carrying *error* (``fallback_used=False``)."""
        return cls(
            success=False,
            value=None,
            error=error,
            trace=trace,
            request_id=error.request_id,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict representation of the result."""
        return {
            "success": self.success,
            "value": self.value,
            "error": self.error.to_dict() if self.error is not None else None,
            "trace": self.trace.model_dump(mode="json") if self.trace is not None else None,
            "request_id": self.request_id,
        }


# Rebuild so the forward references to ``ModelTrace`` / ``ModelInvocationError``
# resolve against this module's namespace.
ModelInvocationResult.model_rebuild()
