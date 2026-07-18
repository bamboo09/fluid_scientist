"""LLM client wrapper with tracing and explicit error handling.

:class:`ModelClient` adapts the existing
:class:`fluid_scientist.llm.client.LLMClient` to the model-runtime
contract: every call is traced via :class:`TraceRecorder`, every outcome
is returned as a :class:`ModelInvocationResult`, and failures are
surfaced explicitly with ``fallback_used=False``.

A critical guarantee: if the underlying :class:`LLMClient` reports that
it fell back to a mock/template response (``record.fallback_used`` is
true) or otherwise failed, :class:`ModelClient` treats that as a hard
failure.  It never silently accepts a fallback result - the platform
prohibits silent degradation to regex, templates or defaults.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Callable

from fluid_scientist.llm.client import LLMClient
from fluid_scientist.model_runtime.errors import (
    ModelInvocationError,
    ModelInvocationResult,
)
from fluid_scientist.model_runtime.models import ModelConfig, ModelRole
from fluid_scientist.model_runtime.registry import ModelRegistry
from fluid_scientist.model_runtime.structured_output import StructuredOutputValidator
from fluid_scientist.model_runtime.tracing import ModelTrace, TraceRecorder

__all__ = ["ModelClient"]

#: Factory type: build an :class:`LLMClient` from a :class:`ModelConfig`.
LLMClientFactory = Callable[[ModelConfig], "LLMClient"]


class ModelClient:
    """Traceable, no-silent-fallback wrapper around :class:`LLMClient`.

    A :class:`ModelClient` resolves the model for a given role from a
    :class:`ModelRegistry`, delegates the actual call to an
    :class:`LLMClient`, records a :class:`ModelTrace` for every attempt,
    and returns a :class:`ModelInvocationResult`.

    By default a real :class:`LLMClient` is constructed per role from the
    role's :class:`ModelConfig` (resolving the API key from the env var
    named by ``api_key_env``).  For testing, an ``llm_client_factory`` can
    be supplied to inject a deterministic double.
    """

    def __init__(
        self,
        registry: ModelRegistry | None = None,
        recorder: TraceRecorder | None = None,
        *,
        validator: StructuredOutputValidator | None = None,
        llm_client_factory: LLMClientFactory | None = None,
    ) -> None:
        # Use explicit ``is not None`` checks: TraceRecorder defines
        # ``__len__``, so an empty recorder is falsy and ``recorder or
        # TraceRecorder()`` would silently substitute a fresh recorder.
        self._registry = registry if registry is not None else ModelRegistry()
        self._recorder = recorder if recorder is not None else TraceRecorder()
        self._validator = validator if validator is not None else StructuredOutputValidator()
        self._llm_client_factory = llm_client_factory
        self._llm_clients: dict[ModelRole, LLMClient] = {}

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------
    @property
    def registry(self) -> ModelRegistry:
        return self._registry

    @property
    def recorder(self) -> TraceRecorder:
        return self._recorder

    @property
    def validator(self) -> StructuredOutputValidator:
        return self._validator

    # ------------------------------------------------------------------
    # LLM client resolution
    # ------------------------------------------------------------------
    def _get_llm_client(self, config: ModelConfig) -> LLMClient:
        """Return (creating if necessary) the LLM client for *config*."""
        cached = self._llm_clients.get(config.role)
        if cached is not None:
            return cached

        if self._llm_client_factory is not None:
            client = self._llm_client_factory(config)
        else:
            api_key: str | None = None
            if config.api_key_env:
                api_key = os.environ.get(config.api_key_env)
            client = LLMClient(
                provider=config.provider,
                model_name=config.model_name,
                api_key=api_key,
                base_url=config.base_url,
                timeout_seconds=config.timeout_seconds,
            )
        self._llm_clients[config.role] = client
        return client

    # ------------------------------------------------------------------
    # Invocation
    # ------------------------------------------------------------------
    def invoke(
        self,
        role: ModelRole,
        system_prompt: str,
        user_message: str,
        output_schema: dict | None = None,
        session_id: str = "",
    ) -> ModelInvocationResult:
        """Invoke the model bound to *role* and return a traced result.

        The result is always a :class:`ModelInvocationResult`: on success
        it carries the parsed value and a :class:`ModelTrace`; on failure
        it carries an explicit :class:`ModelInvocationError` with
        ``fallback_used=False``.  The method never raises for ordinary
        model failures and never silently falls back.
        """
        request_id = f"req_{uuid.uuid4().hex[:16]}"
        started = time.perf_counter()
        system_prompt_sha = ModelTrace.hash_system_prompt(system_prompt)

        # Resolve the role's configuration.
        try:
            config = self._registry.get(role)
        except KeyError:
            return self._fail(
                ModelInvocationError(
                    code="MODEL_UNAVAILABLE",
                    provider="unknown",
                    configured_model="unknown",
                    request_id=request_id,
                    retryable=False,
                    message=f"no model registered for role {role!r}",
                ),
                role=str(role),
                provider="unknown",
                configured_model="unknown",
                request_id=request_id,
                system_prompt_sha=system_prompt_sha,
                started=started,
            )

        llm = self._get_llm_client(config)

        # Attempt the underlying call.
        try:
            parsed, record = llm.call(
                purpose=str(role),
                prompt_name=str(role),
                system_prompt=system_prompt,
                user_message=user_message,
                output_schema=output_schema,
                session_id=session_id,
            )
        except BaseException as exc:  # noqa: BLE001 - surface every failure
            code = "MODEL_TIMEOUT" if _looks_like_timeout(exc) else "MODEL_UNAVAILABLE"
            return self._fail(
                ModelInvocationError(
                    code=code,
                    provider=config.provider,
                    configured_model=config.model_name,
                    request_id=request_id,
                    retryable=code == "MODEL_TIMEOUT",
                    message=f"underlying LLM call raised {type(exc).__name__}: {exc}",
                ),
                role=str(role),
                provider=config.provider,
                configured_model=config.model_name,
                actual_model=config.model_name,
                request_id=request_id,
                system_prompt_sha=system_prompt_sha,
                started=started,
                config=config,
            )

        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        success = bool(getattr(record, "success", False))
        underlying_fallback = bool(getattr(record, "fallback_used", False))
        raw_output = getattr(record, "raw_output", None)
        parsed_output = getattr(record, "parsed_output", None)
        # The trace's ``provider``/``configured_model`` describe the
        # configured target; ``actual_model_from_response`` captures what
        # was actually returned by the underlying client.
        actual_model = getattr(record, "model_name", config.model_name)
        provider = config.provider

        # Reject any silent fallback or reported failure outright.
        if underlying_fallback or not success:
            return self._fail(
                ModelInvocationError(
                    code="MODEL_UNAVAILABLE",
                    provider=provider,
                    configured_model=config.model_name,
                    actual_model=actual_model,
                    request_id=request_id,
                    retryable=False,
                    message=(
                        "underlying LLM client reported a failure or used a "
                        "fallback; silent fallback is prohibited"
                    ),
                ),
                role=str(role),
                provider=provider,
                configured_model=config.model_name,
                actual_model=actual_model,
                request_id=request_id,
                system_prompt_sha=system_prompt_sha,
                started=started,
                latency_ms=latency_ms,
                config=config,
            )

        # Structured-output validation (no silent defaulting).
        if output_schema is not None:
            value, verror = self._validate_output(
                raw_output=raw_output,
                parsed_output=parsed_output,
                schema=output_schema,
            )
            if verror is not None:
                # Enrich the validator's error with real provider/model context.
                error = ModelInvocationError(
                    code=verror.code,
                    provider=provider,
                    configured_model=config.model_name,
                    actual_model=actual_model,
                    request_id=request_id,
                    retryable=False,
                    message=verror.message,
                )
                return self._fail(
                    error,
                    role=str(role),
                    provider=provider,
                    configured_model=config.model_name,
                    actual_model=actual_model,
                    request_id=request_id,
                    system_prompt_sha=system_prompt_sha,
                    started=started,
                    latency_ms=latency_ms,
                    config=config,
                )
        else:
            value = parsed_output if isinstance(parsed_output, dict) else parsed

        # Success.
        trace = self._build_trace(
            role=str(role),
            provider=provider,
            configured_model=config.model_name,
            actual_model=actual_model,
            request_id=request_id,
            system_prompt_sha=system_prompt_sha,
            latency_ms=latency_ms,
            config=config,
            fallback_used=False,
        )
        self._recorder.record(trace)
        return ModelInvocationResult.ok(value, trace=trace, request_id=request_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _validate_output(
        self,
        *,
        raw_output: Any,
        parsed_output: Any,
        schema: dict,
    ) -> tuple[dict | None, ModelInvocationError | None]:
        """Parse/validate the model output, returning ``(value, error)``."""
        if isinstance(raw_output, str) and raw_output.strip():
            return self._validator.parse(raw_output, schema)
        if isinstance(parsed_output, dict):
            if self._validator.validate(parsed_output, schema):
                return parsed_output, None
            return None, ModelInvocationError(
                code="MODEL_SCHEMA_MISMATCH",
                provider="unknown",
                configured_model="unknown",
                message="model output does not conform to the expected schema",
            )
        return None, ModelInvocationError(
            code="MODEL_OUTPUT_INVALID",
            provider="unknown",
            configured_model="unknown",
            message="model returned no parseable output",
        )

    def _build_trace(
        self,
        *,
        role: str,
        provider: str,
        configured_model: str,
        request_id: str,
        system_prompt_sha: str,
        started: float | None = None,
        config: ModelConfig | None = None,
        actual_model: str | None = None,
        latency_ms: float | None = None,
        fallback_used: bool = False,
    ) -> ModelTrace:
        if latency_ms is None:
            base = started if started is not None else time.perf_counter()
            latency_ms = round((time.perf_counter() - base) * 1000, 3)
        return ModelTrace(
            role=role,
            provider=provider,
            configured_model=configured_model,
            actual_model_from_response=actual_model,
            reasoning_effort=config.reasoning_effort if config else None,
            temperature=config.temperature if config else None,
            max_output_tokens=config.max_output_tokens if config else None,
            structured_output_enabled=config.structured_output_enabled if config else False,
            tool_calling_enabled=config.tool_calling_enabled if config else False,
            system_prompt_sha256=system_prompt_sha,
            conversation_turn_count=1,
            current_spec_included=False,
            skill_ids=[],
            request_id=request_id,
            latency_ms=latency_ms,
            fallback_used=fallback_used,
        )

    def _fail(
        self,
        error: ModelInvocationError,
        *,
        role: str,
        provider: str,
        configured_model: str,
        request_id: str,
        system_prompt_sha: str,
        started: float,
        config: ModelConfig | None = None,
        actual_model: str | None = None,
        latency_ms: float | None = None,
    ) -> ModelInvocationResult:
        """Record a trace for a failed invocation and return a failed result."""
        trace = self._build_trace(
            role=role,
            provider=provider,
            configured_model=configured_model,
            request_id=request_id,
            system_prompt_sha=system_prompt_sha,
            started=started,
            config=config,
            actual_model=actual_model,
            latency_ms=latency_ms,
            fallback_used=False,
        )
        self._recorder.record(trace)
        return ModelInvocationResult.fail(error, trace=trace)


def _looks_like_timeout(exc: BaseException) -> bool:
    """Heuristically classify *exc* as a timeout-related error."""
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "timed out" in str(exc).lower()
