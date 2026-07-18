"""Test: no silent fallback on model failures.

This test reproduces the known issue where model failures (invalid JSON,
schema mismatch, timeouts) were silently swallowed and the system fell
back to regex/template/default responses.  The new ``model_runtime``
module ensures that:

* :class:`ModelInvocationError` is always raised/returned with
  ``fallback_used=False``.
* Attempting to construct an error with ``fallback_used=True`` raises
  :class:`ValueError`.
* :class:`StructuredOutputValidator` rejects invalid JSON and schema
  mismatches with explicit errors.
* :class:`ModelClient` surfaces all failures as
  :class:`ModelInvocationResult` with ``fallback_used=False`` — never
  silently accepting a degraded response.

Verifies:
* ModelInvocationError.fallback_used is always False.
* Constructing with fallback_used=True raises ValueError.
* StructuredOutputValidator rejects invalid JSON.
* StructuredOutputValidator rejects schema mismatch.
* ModelClient returns failure results (not exceptions) for:
  a. Invalid JSON output
  b. Schema mismatch
  c. Model timeout
* All failure results have fallback_used=False.
"""
from __future__ import annotations

import pytest

from fluid_scientist.model_runtime import (
    ModelClient,
    ModelConfig,
    ModelInvocationError,
    ModelInvocationResult,
    ModelRole,
    ModelRegistry,
    StructuredOutputValidator,
)
from fluid_scientist.model_runtime.tracing import TraceRecorder

from .conftest import make_study_spec


# ---------------------------------------------------------------------------
# Mock LLM client for testing ModelClient failures
# ---------------------------------------------------------------------------

class _MockRecord:
    """Minimal stand-in for LLMCallRecord with the attributes ModelClient
    reads via ``getattr``."""

    def __init__(
        self,
        *,
        success: bool = True,
        fallback_used: bool = False,
        raw_output: str | None = None,
        parsed_output: dict | None = None,
        model_name: str = "mock-model",
    ) -> None:
        self.success = success
        self.fallback_used = fallback_used
        self.raw_output = raw_output
        self.parsed_output = parsed_output or {}
        self.model_name = model_name


class _MockLLMClient:
    """Mock LLM client whose ``.call()`` behaviour is controlled by the
    *scenario* parameter.

    scenario="ok"        — returns valid output
    scenario="invalid_json" — returns non-JSON raw_output
    scenario="schema_mismatch" — returns JSON that fails schema
    scenario="timeout"   — raises TimeoutError
    scenario="fallback"  — returns record with fallback_used=True
    """

    def __init__(self, scenario: str = "ok") -> None:
        self._scenario = scenario

    def call(self, **kwargs) -> tuple[dict, _MockRecord]:
        if self._scenario == "timeout":
            raise TimeoutError("model call timed out after 5 seconds")

        if self._scenario == "invalid_json":
            record = _MockRecord(
                success=True,
                fallback_used=False,
                raw_output="this is definitely not valid JSON {{{",
                parsed_output={"status": "error"},
            )
            return {"status": "error"}, record

        if self._scenario == "schema_mismatch":
            record = _MockRecord(
                success=True,
                fallback_used=False,
                raw_output='{"foo": "bar"}',
                parsed_output={"foo": "bar"},
            )
            return {"foo": "bar"}, record

        if self._scenario == "fallback":
            record = _MockRecord(
                success=True,
                fallback_used=True,
                raw_output='{"result": "fallback"}',
                parsed_output={"result": "fallback"},
            )
            return {"result": "fallback"}, record

        # Default: valid output
        record = _MockRecord(
            success=True,
            fallback_used=False,
            raw_output='{"answer": 42}',
            parsed_output={"answer": 42},
        )
        return {"answer": 42}, record


def _make_model_client(scenario: str = "ok") -> ModelClient:
    """Build a ModelClient with a mock LLM client for the given scenario."""
    registry = ModelRegistry()
    config = ModelConfig(
        role=ModelRole.FAST_ASSISTANT,
        provider="mock",
        model_name="mock-model",
        timeout_seconds=5.0,
    )
    registry.register(ModelRole.FAST_ASSISTANT, config)

    mock_llm = _MockLLMClient(scenario=scenario)
    factory = lambda cfg: mock_llm  # noqa: E731

    return ModelClient(
        registry=registry,
        recorder=TraceRecorder(),
        llm_client_factory=factory,
    )


# ---------------------------------------------------------------------------
# Tests: ModelInvocationError
# ---------------------------------------------------------------------------

class TestModelInvocationErrorFallback:
    """Verify ModelInvocationError.fallback_used is always False."""

    def test_fallback_used_always_false(self) -> None:
        """Constructing an error normally yields fallback_used=False."""
        error = ModelInvocationError(
            code="MODEL_OUTPUT_INVALID",
            provider="openai",
            configured_model="gpt-4",
            message="invalid output",
        )
        assert error.fallback_used is False

    def test_fallback_used_true_raises_value_error(self) -> None:
        """Attempting fallback_used=True raises ValueError."""
        with pytest.raises(ValueError, match="fallback_used must be False"):
            ModelInvocationError(
                code="MODEL_OUTPUT_INVALID",
                provider="openai",
                configured_model="gpt-4",
                fallback_used=True,
                message="should not be allowed",
            )

    def test_all_error_codes_have_fallback_false(self) -> None:
        """Every error code produces fallback_used=False."""
        for code in [
            "MODEL_UNAVAILABLE",
            "MODEL_TIMEOUT",
            "MODEL_OUTPUT_INVALID",
            "MODEL_SCHEMA_MISMATCH",
            "MODEL_CAPABILITY_INSUFFICIENT",
            "SKILL_LOAD_FAILED",
        ]:
            error = ModelInvocationError(
                code=code,  # type: ignore[arg-type]
                provider="test",
                configured_model="test-model",
            )
            assert error.fallback_used is False, (
                f"fallback_used should be False for code={code}"
            )


# ---------------------------------------------------------------------------
# Tests: ModelInvocationResult
# ---------------------------------------------------------------------------

class TestModelInvocationResultFallback:
    """Verify ModelInvocationResult.fail() always has fallback_used=False."""

    def test_fail_result_has_fallback_false(self) -> None:
        """A failed result's error has fallback_used=False."""
        error = ModelInvocationError(
            code="MODEL_TIMEOUT",
            provider="test",
            configured_model="test-model",
        )
        result = ModelInvocationResult.fail(error)
        assert result.success is False
        assert result.error is not None
        assert result.error.fallback_used is False

    def test_ok_result_has_no_error(self) -> None:
        """A successful result has no error."""
        result = ModelInvocationResult.ok({"answer": 42})
        assert result.success is True
        assert result.error is None
        assert result.value == {"answer": 42}


# ---------------------------------------------------------------------------
# Tests: StructuredOutputValidator
# ---------------------------------------------------------------------------

class TestStructuredOutputValidator:
    """Verify StructuredOutputValidator rejects invalid output."""

    def test_rejects_invalid_json(self) -> None:
        """Invalid JSON returns an error, not a silent default."""
        validator = StructuredOutputValidator()
        schema = {"type": "object", "required": ["answer"]}
        parsed, error = validator.parse("this is not json", schema)
        assert parsed is None, "Invalid JSON should not produce a parsed value"
        assert error is not None, "Invalid JSON should produce an error"
        assert error.code == "MODEL_OUTPUT_INVALID"
        assert error.fallback_used is False

    def test_rejects_empty_response(self) -> None:
        """Empty response returns an error."""
        validator = StructuredOutputValidator()
        schema = {"type": "object", "required": ["answer"]}
        parsed, error = validator.parse("", schema)
        assert parsed is None
        assert error is not None
        assert error.code == "MODEL_OUTPUT_INVALID"

    def test_rejects_schema_mismatch(self) -> None:
        """Schema mismatch returns an error, not a silent default."""
        validator = StructuredOutputValidator()
        schema = {"type": "object", "required": ["answer"]}
        parsed, error = validator.parse('{"foo": "bar"}', schema)
        assert parsed is None, "Schema mismatch should not produce a value"
        assert error is not None, "Schema mismatch should produce an error"
        assert error.code == "MODEL_SCHEMA_MISMATCH"
        assert error.fallback_used is False

    def test_accepts_valid_output(self) -> None:
        """Valid JSON matching the schema is accepted."""
        validator = StructuredOutputValidator()
        schema = {"type": "object", "required": ["answer"]}
        parsed, error = validator.parse('{"answer": 42}', schema)
        assert error is None, f"Valid output should not produce an error: {error}"
        assert parsed == {"answer": 42}

    def test_strips_code_fence_before_parsing(self) -> None:
        """The validator strips ```json ... ``` fences before parsing."""
        validator = StructuredOutputValidator()
        schema = {"type": "object", "required": ["answer"]}
        raw = '```json\n{"answer": 42}\n```'
        parsed, error = validator.parse(raw, schema)
        assert error is None
        assert parsed == {"answer": 42}


# ---------------------------------------------------------------------------
# Tests: ModelClient — no silent fallback
# ---------------------------------------------------------------------------

class TestModelClientNoSilentFallback:
    """Verify ModelClient surfaces failures with fallback_used=False."""

    def test_invalid_json_returns_failure(self) -> None:
        """Model returning invalid JSON produces a failure result."""
        client = _make_model_client(scenario="invalid_json")
        schema = {"type": "object", "required": ["answer"]}

        result = client.invoke(
            role=ModelRole.FAST_ASSISTANT,
            system_prompt="test",
            user_message="test",
            output_schema=schema,
        )

        assert result.success is False, "Invalid JSON should produce a failure"
        assert result.error is not None, "Failure must carry an error"
        assert result.error.fallback_used is False, (
            "fallback_used must be False even for invalid JSON"
        )
        assert result.error.code in ("MODEL_OUTPUT_INVALID", "MODEL_SCHEMA_MISMATCH")

    def test_schema_mismatch_returns_failure(self) -> None:
        """Model returning schema-mismatched output produces a failure."""
        client = _make_model_client(scenario="schema_mismatch")
        schema = {"type": "object", "required": ["answer"]}

        result = client.invoke(
            role=ModelRole.FAST_ASSISTANT,
            system_prompt="test",
            user_message="test",
            output_schema=schema,
        )

        assert result.success is False
        assert result.error is not None
        assert result.error.fallback_used is False
        assert result.error.code == "MODEL_SCHEMA_MISMATCH"

    def test_timeout_returns_failure(self) -> None:
        """Model timeout produces a failure result (not a raised exception)."""
        client = _make_model_client(scenario="timeout")

        result = client.invoke(
            role=ModelRole.FAST_ASSISTANT,
            system_prompt="test",
            user_message="test",
        )

        assert result.success is False, "Timeout should produce a failure"
        assert result.error is not None
        assert result.error.fallback_used is False, (
            "fallback_used must be False even for timeout"
        )
        assert result.error.code == "MODEL_TIMEOUT"
        assert result.error.retryable is True, "Timeout should be retryable"

    def test_fallback_record_rejected(self) -> None:
        """A record with fallback_used=True is rejected as a failure."""
        client = _make_model_client(scenario="fallback")

        result = client.invoke(
            role=ModelRole.FAST_ASSISTANT,
            system_prompt="test",
            user_message="test",
        )

        assert result.success is False, (
            "Fallback record should be rejected, not silently accepted"
        )
        assert result.error is not None
        assert result.error.fallback_used is False

    def test_all_failures_have_trace(self) -> None:
        """Every failure result carries a ModelTrace for auditability."""
        for scenario in ("invalid_json", "schema_mismatch", "timeout", "fallback"):
            client = _make_model_client(scenario=scenario)
            result = client.invoke(
                role=ModelRole.FAST_ASSISTANT,
                system_prompt="test",
                user_message="test",
                output_schema={"type": "object", "required": ["answer"]} if scenario in ("invalid_json", "schema_mismatch") else None,
            )
            assert result.success is False
            assert result.trace is not None, (
                f"Failure for scenario='{scenario}' must carry a trace"
            )
            assert result.trace.fallback_used is False, (
                f"Trace fallback_used must be False for scenario='{scenario}'"
            )

    def test_valid_output_succeeds(self) -> None:
        """A valid model response succeeds (positive control)."""
        client = _make_model_client(scenario="ok")
        schema = {"type": "object", "required": ["answer"]}

        result = client.invoke(
            role=ModelRole.FAST_ASSISTANT,
            system_prompt="test",
            user_message="test",
            output_schema=schema,
        )

        assert result.success is True
        assert result.value is not None
        assert result.error is None
