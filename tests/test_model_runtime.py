"""Comprehensive tests for the ``fluid_scientist.model_runtime`` package.

These tests pin the platform's core invariants:

* model failures surface as explicit :class:`ModelInvocationError`
  (raised by the registry, returned by the client) and are never
  silently swallowed;
* ``fallback_used`` is always ``False`` in real mode;
* traces capture every required field and never leak API keys;
* structured-output validation rejects invalid JSON / schema mismatches
  without defaulting;
* capability admission thresholds gate the primary reasoner;
* the registry rejects an unqualified primary reasoner.

All tests run offline - no external API calls are made.  A deterministic
``FakeLLMClient`` is injected via ``llm_client_factory`` to exercise the
client's success and failure branches.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from fluid_scientist.model_runtime import (
    CapabilityEvalResult,
    ModelAdmissionThresholds,
    ModelClient,
    ModelConfig,
    ModelHealthStatus,
    ModelInvocationError,
    ModelInvocationResult,
    ModelRegistry,
    ModelRole,
    ModelTrace,
    StructuredOutputValidator,
    TraceRecorder,
    evaluate_model,
)
from fluid_scientist.model_runtime.errors import ErrorCode


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeRecord:
    """Minimal duck-typed stand-in for ``LLMCallRecord``."""

    success: bool = True
    fallback_used: bool = False
    raw_output: str | None = None
    parsed_output: dict | None = None
    model_name: str = "fake-model"
    provider: str = "fake"
    latency_ms: float = 1.0
    call_id: str = "fake_call"
    input_refs: list[str] = field(default_factory=list)


class FakeLLMClient:
    """Deterministic double for :class:`fluid_scientist.llm.client.LLMClient`.

    ``call`` mimics the real client's signature and return shape so the
    :class:`ModelClient` wrapper can be exercised without any network.
    """

    def __init__(
        self,
        *,
        response: dict | None = None,
        raw_output: str | None = None,
        raise_exc: BaseException | None = None,
        fallback: bool = False,
        success: bool = True,
        model_name: str = "fake-model",
        provider: str = "fake",
    ) -> None:
        self._response = response
        self._raw_output = raw_output
        self._raise_exc = raise_exc
        self._fallback = fallback
        self._success = success
        self._model_name = model_name
        self._provider = provider
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        purpose: str,
        prompt_name: str,
        system_prompt: str,
        user_message: str,
        output_schema: dict | None = None,
        session_id: str = "",
        input_refs: list[str] | None = None,
        prompt_version: str = "",
    ) -> tuple[dict, FakeRecord]:
        self.calls.append(
            {
                "purpose": purpose,
                "prompt_name": prompt_name,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "output_schema": output_schema,
                "session_id": session_id,
            }
        )
        if self._raise_exc is not None:
            raise self._raise_exc
        parsed = self._response if self._response is not None else {"status": "ok"}
        raw = self._raw_output if self._raw_output is not None else json.dumps(parsed)
        record = FakeRecord(
            success=self._success,
            fallback_used=self._fallback,
            raw_output=raw,
            parsed_output=parsed,
            model_name=self._model_name,
            provider=self._provider,
        )
        return parsed, record


def _factory_for(fake: FakeLLMClient):
    """Return an ``llm_client_factory`` that always returns *fake*."""
    return lambda config: fake


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def passing_eval(model_id: str = "strong-model") -> CapabilityEvalResult:
    return CapabilityEvalResult(
        model_id=model_id,
        structured_output_parse_rate=1.0,
        single_field_edit_accuracy=1.0,
        consecutive_8turn_retention=1.0,
        geometry_type_accuracy=1.0,
        unit_accuracy=1.0,
        conflict_recall=1.0,
        unknown_capability_recall=1.0,
        template_misuse_rate=0.0,
        fabricated_success_rate=0.0,
    )


def primary_config(provider: str = "openai", model_name: str = "gpt-test") -> ModelConfig:
    return ModelConfig(
        role=ModelRole.PRIMARY_REASONER,
        provider=provider,
        model_name=model_name,
        api_key_env="FAKE_API_KEY",
        reasoning_effort="high",
        temperature=0.2,
        max_output_tokens=4096,
        structured_output_enabled=True,
        tool_calling_enabled=True,
    )


PERSON_SCHEMA: dict = {
    "type": "object",
    "required": ["name", "age"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer", "minimum": 0},
    },
}


# ===========================================================================
# 1. ModelInvocationError - explicit, raisable, no silent fallback
# ===========================================================================


class TestModelInvocationError:
    def test_has_all_required_fields(self) -> None:
        err = ModelInvocationError(
            code="MODEL_TIMEOUT",
            provider="openai",
            configured_model="gpt-test",
            actual_model="gpt-test-2024",
            request_id="req_1",
            retryable=True,
            message="timed out",
        )
        assert err.code == "MODEL_TIMEOUT"
        assert err.provider == "openai"
        assert err.configured_model == "gpt-test"
        assert err.actual_model == "gpt-test-2024"
        assert err.request_id == "req_1"
        assert err.retryable is True
        assert err.fallback_used is False
        assert err.message == "timed out"
        assert err.to_dict()["fallback_used"] is False

    def test_fallback_used_defaults_to_false(self) -> None:
        err = ModelInvocationError(
            code="MODEL_UNAVAILABLE",
            provider="p",
            configured_model="m",
        )
        assert err.fallback_used is False

    def test_fallback_used_true_is_rejected_in_real_mode(self) -> None:
        # The platform prohibits silent fallback: constructing an error
        # that claims a fallback was used must fail loudly.
        with pytest.raises(ValueError, match="fallback_used must be False"):
            ModelInvocationError(
                code="MODEL_UNAVAILABLE",
                provider="p",
                configured_model="m",
                fallback_used=True,
            )

    def test_invalid_code_is_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ModelInvocationError(
                code="NOT_A_REAL_CODE",  # type: ignore[arg-type]
                provider="p",
                configured_model="m",
            )

    def test_error_is_raisable_and_catchable(self) -> None:
        with pytest.raises(ModelInvocationError) as excinfo:
            raise ModelInvocationError(
                code="SKILL_LOAD_FAILED",
                provider="p",
                configured_model="m",
                message="skill pack missing",
            )
        assert excinfo.value.code == "SKILL_LOAD_FAILED"
        assert "skill pack missing" in str(excinfo.value)

    def test_error_code_literal_is_closed_set(self) -> None:
        from typing import get_args

        allowed = set(get_args(ErrorCode))
        assert allowed == {
            "MODEL_UNAVAILABLE",
            "MODEL_TIMEOUT",
            "MODEL_OUTPUT_INVALID",
            "MODEL_SCHEMA_MISMATCH",
            "MODEL_CAPABILITY_INSUFFICIENT",
            "SKILL_LOAD_FAILED",
        }

    @pytest.mark.parametrize(
        "code",
        [
            "MODEL_UNAVAILABLE",
            "MODEL_TIMEOUT",
            "MODEL_OUTPUT_INVALID",
            "MODEL_SCHEMA_MISMATCH",
            "MODEL_CAPABILITY_INSUFFICIENT",
            "SKILL_LOAD_FAILED",
        ],
    )
    def test_every_failure_code_can_be_surfaced(self, code: str) -> None:
        # Every failure mode produces an explicit, non-swallowed error.
        err = ModelInvocationError(code=code, provider="p", configured_model="m")
        assert err.code == code
        assert err.fallback_used is False


# ===========================================================================
# 2. ModelInvocationResult - generic success/failure wrapper
# ===========================================================================


class TestModelInvocationResult:
    def test_ok_carries_value_and_no_error(self) -> None:
        result = ModelInvocationResult[dict].ok({"a": 1}, request_id="req_1")
        assert result.success is True
        assert result.value == {"a": 1}
        assert result.error is None
        assert result.request_id == "req_1"

    def test_fail_carries_error_with_fallback_false(self) -> None:
        err = ModelInvocationError(
            code="MODEL_TIMEOUT", provider="p", configured_model="m", request_id="req_2"
        )
        result = ModelInvocationResult[dict].fail(err)
        assert result.success is False
        assert result.value is None
        assert result.error is err
        assert result.error.fallback_used is False
        assert result.request_id == "req_2"

    def test_ok_without_value_is_invalid(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ModelInvocationResult(success=True, value=None, error=None)

    def test_fail_without_error_is_invalid(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ModelInvocationResult(success=False, value=None, error=None)

    def test_success_with_error_is_invalid(self) -> None:
        from pydantic import ValidationError

        err = ModelInvocationError(code="MODEL_TIMEOUT", provider="p", configured_model="m")
        with pytest.raises(ValidationError):
            ModelInvocationResult(success=True, value={"a": 1}, error=err)

    def test_to_dict_roundtrip(self) -> None:
        err = ModelInvocationError(
            code="MODEL_SCHEMA_MISMATCH",
            provider="p",
            configured_model="m",
            message="bad shape",
        )
        result = ModelInvocationResult[dict].fail(err)
        data = result.to_dict()
        assert data["success"] is False
        assert data["error"]["code"] == "MODEL_SCHEMA_MISMATCH"
        assert data["error"]["fallback_used"] is False
        assert data["value"] is None


# ===========================================================================
# 3. Tracing - all required fields recorded, no API keys
# ===========================================================================


REQUIRED_TRACE_FIELDS = {
    "trace_id",
    "role",
    "provider",
    "configured_model",
    "actual_model_from_response",
    "endpoint_type",
    "reasoning_effort",
    "temperature",
    "max_output_tokens",
    "structured_output_enabled",
    "tool_calling_enabled",
    "system_prompt_sha256",
    "conversation_turn_count",
    "current_spec_included",
    "skill_ids",
    "request_id",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "retry_count",
    "fallback_used",
    "timestamp",
}


class TestTracing:
    def test_model_trace_has_all_required_fields(self) -> None:
        trace = ModelTrace(
            role="primary_reasoner",
            provider="openai",
            configured_model="gpt-test",
            actual_model_from_response="gpt-test-2024",
            reasoning_effort="high",
            temperature=0.2,
            max_output_tokens=4096,
            structured_output_enabled=True,
            tool_calling_enabled=True,
            system_prompt_sha256=ModelTrace.hash_system_prompt("be the reasoner"),
            conversation_turn_count=3,
            current_spec_included=True,
            skill_ids=["fluid.geometry_reasoning", "fluid.solver_selection"],
            request_id="req_1",
            latency_ms=123.4,
            input_tokens=150,
            output_tokens=80,
            retry_count=0,
            fallback_used=False,
        )
        dumped = trace.model_dump()
        assert REQUIRED_TRACE_FIELDS.issubset(dumped.keys())
        assert dumped["fallback_used"] is False
        # No secret-like field exists on the schema.
        for secret in ("api_key", "api_key_env", "authorization", "token"):
            assert secret not in dumped

    def test_hash_system_prompt_is_deterministic_and_not_the_prompt(self) -> None:
        prompt = "You are a CFD reasoning assistant."
        h1 = ModelTrace.hash_system_prompt(prompt)
        h2 = ModelTrace.hash_system_prompt(prompt)
        assert h1 == h2
        assert len(h1) == 64  # sha256 hex
        assert prompt not in h1

    def test_trace_recorder_records_and_retrieves(self) -> None:
        rec = TraceRecorder()
        t1 = ModelTrace(role="critic", provider="p", configured_model="m", request_id="r1")
        t2 = ModelTrace(role="critic", provider="p", configured_model="m", request_id="r2")
        t3 = ModelTrace(role="primary_reasoner", provider="p", configured_model="m", request_id="r1")
        rec.record(t1)
        rec.record(t2)
        rec.record(t3)
        assert len(rec) == 3
        assert rec.latest() is t3
        assert len(rec.for_role("critic")) == 2
        assert len(rec.for_request("r1")) == 2
        assert rec.all() == [t1, t2, t3]

    def test_trace_recorder_export_json_has_no_api_keys(self) -> None:
        rec = TraceRecorder()
        rec.record(
            ModelTrace(
                role="primary_reasoner",
                provider="openai",
                configured_model="gpt-test",
                request_id="r1",
                system_prompt_sha256="abc",
            )
        )
        exported = rec.export_json()
        payload = json.loads(exported)
        assert isinstance(payload, list)
        assert len(payload) == 1
        text = exported.lower()
        # Even though the schema has no key field, ensure none slipped in.
        for secret in ("api_key", "apikey", "authorization", "secret", "token"):
            assert secret not in payload[0]
            # The provider/model names should not coincidentally contain these.
        assert "api_key" not in text.split('"configured_model"')[0]

    def test_trace_recorder_rejects_non_trace(self) -> None:
        rec = TraceRecorder()
        with pytest.raises(TypeError):
            rec.record("not a trace")  # type: ignore[arg-type]

    def test_trace_recorder_clear(self) -> None:
        rec = TraceRecorder()
        rec.record(ModelTrace(role="r", provider="p", configured_model="m"))
        rec.clear()
        assert len(rec) == 0


# ===========================================================================
# 4. Structured output validation - rejects invalid JSON / schema mismatch
# ===========================================================================


class TestStructuredOutput:
    def setup_method(self) -> None:
        self.v = StructuredOutputValidator()

    def test_validate_accepts_conforming_output(self) -> None:
        assert self.v.validate({"name": "cylinder", "age": 3}, PERSON_SCHEMA) is True

    def test_validate_rejects_missing_required_field(self) -> None:
        assert self.v.validate({"name": "cylinder"}, PERSON_SCHEMA) is False

    def test_validate_rejects_wrong_type(self) -> None:
        assert self.v.validate({"name": "cylinder", "age": "3"}, PERSON_SCHEMA) is False

    def test_validate_rejects_extra_fields_when_disallowed(self) -> None:
        assert (
            self.v.validate(
                {"name": "cylinder", "age": 3, "extra": 1}, PERSON_SCHEMA
            )
            is False
        )

    def test_validate_rejects_non_dict(self) -> None:
        assert self.v.validate([], PERSON_SCHEMA) is False  # type: ignore[arg-type]
        assert self.v.validate("nope", PERSON_SCHEMA) is False  # type: ignore[arg-type]

    def test_parse_valid_json_returns_dict(self) -> None:
        parsed, err = self.v.parse('{"name": "cylinder", "age": 3}', PERSON_SCHEMA)
        assert err is None
        assert parsed == {"name": "cylinder", "age": 3}

    def test_parse_rejects_invalid_json(self) -> None:
        parsed, err = self.v.parse("{not valid json", PERSON_SCHEMA)
        assert parsed is None
        assert err is not None
        assert err.code == "MODEL_OUTPUT_INVALID"

    def test_parse_rejects_empty_response(self) -> None:
        parsed, err = self.v.parse("   ", PERSON_SCHEMA)
        assert parsed is None
        assert err is not None
        assert err.code == "MODEL_OUTPUT_INVALID"

    def test_parse_rejects_non_object_root(self) -> None:
        parsed, err = self.v.parse("[1, 2, 3]", PERSON_SCHEMA)
        assert parsed is None
        assert err is not None
        assert err.code == "MODEL_SCHEMA_MISMATCH"

    def test_parse_rejects_schema_mismatch(self) -> None:
        parsed, err = self.v.parse('{"name": "cylinder"}', PERSON_SCHEMA)
        assert parsed is None
        assert err is not None
        assert err.code == "MODEL_SCHEMA_MISMATCH"

    def test_parse_strips_code_fence(self) -> None:
        raw = '```json\n{"name": "cylinder", "age": 3}\n```'
        parsed, err = self.v.parse(raw, PERSON_SCHEMA)
        assert err is None
        assert parsed == {"name": "cylinder", "age": 3}

    def test_parse_does_not_silently_default_missing_fields(self) -> None:
        # A response missing a required field must NOT be coerced to a
        # default-populated dict; it must be a hard failure.
        parsed, err = self.v.parse('{"name": "cylinder"}', PERSON_SCHEMA)
        assert parsed is None
        assert err is not None


# ===========================================================================
# 5. Capability evaluation thresholds
# ===========================================================================


class TestCapabilityEval:
    def test_passing_result_passes(self) -> None:
        assert evaluate_model(passing_eval()) is True

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("structured_output_parse_rate", 0.97),
            ("single_field_edit_accuracy", 0.94),
            ("consecutive_8turn_retention", 0.89),
            ("geometry_type_accuracy", 0.94),
            ("unit_accuracy", 0.97),
            ("conflict_recall", 0.89),
            ("unknown_capability_recall", 0.94),
            ("template_misuse_rate", 0.03),
        ],
    )
    def test_single_metric_below_threshold_fails(self, field: str, value: float) -> None:
        data = passing_eval().model_dump()
        data[field] = value
        data.pop("pass_fail")
        result = CapabilityEvalResult(**data)
        assert evaluate_model(result) is False

    def test_fabricated_success_rate_must_be_exactly_zero(self) -> None:
        data = passing_eval().model_dump()
        data["fabricated_success_rate"] = 0.001
        data.pop("pass_fail")
        result = CapabilityEvalResult(**data)
        assert evaluate_model(result) is False

    def test_boundary_values_pass(self) -> None:
        # Values exactly at the threshold must pass (>= and <=).
        result = CapabilityEvalResult(
            model_id="boundary",
            structured_output_parse_rate=0.98,
            single_field_edit_accuracy=0.95,
            consecutive_8turn_retention=0.90,
            geometry_type_accuracy=0.95,
            unit_accuracy=0.98,
            conflict_recall=0.90,
            unknown_capability_recall=0.95,
            template_misuse_rate=0.02,
            fabricated_success_rate=0.0,
        )
        assert evaluate_model(result) is True

    def test_thresholds_match_specification(self) -> None:
        t = ModelAdmissionThresholds
        assert t.structured_output_parse_rate == 0.98
        assert t.single_field_edit_accuracy == 0.95
        assert t.consecutive_8turn_retention == 0.90
        assert t.geometry_type_accuracy == 0.95
        assert t.unit_accuracy == 0.98
        assert t.conflict_recall == 0.90
        assert t.unknown_capability_recall == 0.95
        assert t.template_misuse_rate == 0.02
        assert t.fabricated_success_rate == 0.0

    def test_classmethod_evaluate_aliases_function(self) -> None:
        assert ModelAdmissionThresholds.evaluate(passing_eval()) is True


# ===========================================================================
# 6. Model registry - rejects unqualified primary reasoner
# ===========================================================================


class TestModelRegistry:
    def test_register_non_primary_without_eval(self) -> None:
        reg = ModelRegistry()
        config = ModelConfig(
            role=ModelRole.CRITIC, provider="openai", model_name="gpt-critic"
        )
        reg.register(ModelRole.CRITIC, config)
        assert reg.get(ModelRole.CRITIC) is config
        assert ModelRole.CRITIC in reg.list_roles()

    def test_register_primary_requires_eval(self) -> None:
        reg = ModelRegistry()
        with pytest.raises(ModelInvocationError) as excinfo:
            reg.register(ModelRole.PRIMARY_REASONER, primary_config())
        assert excinfo.value.code == "MODEL_CAPABILITY_INSUFFICIENT"
        assert excinfo.value.fallback_used is False
        assert ModelRole.PRIMARY_REASONER not in reg.list_roles()

    def test_register_primary_rejects_failing_eval(self) -> None:
        reg = ModelRegistry()
        failing = CapabilityEvalResult(
            model_id="weak-model",
            structured_output_parse_rate=0.50,  # below 0.98
            single_field_edit_accuracy=1.0,
            consecutive_8turn_retention=1.0,
            geometry_type_accuracy=1.0,
            unit_accuracy=1.0,
            conflict_recall=1.0,
            unknown_capability_recall=1.0,
            template_misuse_rate=0.0,
            fabricated_success_rate=0.0,
        )
        with pytest.raises(ModelInvocationError) as excinfo:
            reg.register(
                ModelRole.PRIMARY_REASONER, primary_config(), capability_eval=failing
            )
        assert excinfo.value.code == "MODEL_CAPABILITY_INSUFFICIENT"
        assert "failed capability admission thresholds" in (excinfo.value.message or "")
        assert reg.has(ModelRole.PRIMARY_REASONER) is False

    def test_register_primary_accepts_passing_eval(self) -> None:
        reg = ModelRegistry()
        reg.register(
            ModelRole.PRIMARY_REASONER,
            primary_config(),
            capability_eval=passing_eval(),
        )
        assert reg.has(ModelRole.PRIMARY_REASONER) is True
        assert reg.capability_eval(ModelRole.PRIMARY_REASONER) is not None

    def test_register_role_mismatch_raises(self) -> None:
        reg = ModelRegistry()
        config = ModelConfig(role=ModelRole.CRITIC, provider="p", model_name="m")
        with pytest.raises(ValueError):
            reg.register(ModelRole.FAST_ASSISTANT, config)

    def test_get_unregistered_raises_keyerror(self) -> None:
        reg = ModelRegistry()
        with pytest.raises(KeyError):
            reg.get(ModelRole.CODE_EXTENSION)

    def test_health_check_for_passing_primary(self) -> None:
        reg = ModelRegistry()
        reg.register(
            ModelRole.PRIMARY_REASONER,
            primary_config(model_name="gpt-strong"),
            capability_eval=passing_eval(model_id="gpt-strong"),
        )
        status = reg.health_check(ModelRole.PRIMARY_REASONER)
        assert isinstance(status, ModelHealthStatus)
        assert status.pass_fail == "pass"
        assert status.configured_model == "gpt-strong"
        assert status.structured_output_support is True
        assert status.reasoning_mode == "high"
        assert status.capability_eval_version == "cap-eval-v1"

    def test_health_check_for_non_primary(self) -> None:
        reg = ModelRegistry()
        reg.register(
            ModelRole.FAST_ASSISTANT,
            ModelConfig(role=ModelRole.FAST_ASSISTANT, provider="p", model_name="fast"),
        )
        status = reg.health_check(ModelRole.FAST_ASSISTANT)
        assert status.pass_fail == "pass"


# ===========================================================================
# 7. ModelClient - tracing, no silent fallback, explicit results
# ===========================================================================


class TestModelClient:
    def test_invoke_unregistered_role_returns_failure(self) -> None:
        client = ModelClient(ModelRegistry(), recorder := TraceRecorder())
        result = client.invoke(
            ModelRole.PRIMARY_REASONER, "sys", "hi", None, "sess-1"
        )
        assert result.success is False
        assert result.error is not None
        assert result.error.code == "MODEL_UNAVAILABLE"
        assert result.error.fallback_used is False
        # A trace is still recorded for the failed attempt.
        assert len(recorder) == 1
        assert recorder.latest().fallback_used is False

    def test_invoke_success_with_schema(self) -> None:
        fake = FakeLLMClient(
            response={"name": "cylinder", "age": 3},
            raw_output='{"name": "cylinder", "age": 3}',
            model_name="gpt-test",
            provider="openai",
        )
        reg = ModelRegistry()
        reg.register(ModelRole.CRITIC, ModelConfig(role=ModelRole.CRITIC, provider="openai", model_name="gpt-test"))
        client = ModelClient(reg, recorder := TraceRecorder(), llm_client_factory=_factory_for(fake))
        result = client.invoke(
            ModelRole.CRITIC, "be a critic", "review this", PERSON_SCHEMA, "sess-1"
        )
        assert result.success is True
        assert result.value == {"name": "cylinder", "age": 3}
        assert result.error is None
        assert result.trace is not None
        assert result.trace.role == "critic"
        assert result.trace.fallback_used is False
        assert result.trace.system_prompt_sha256 == ModelTrace.hash_system_prompt("be a critic")
        assert len(recorder) == 1

    def test_invoke_success_without_schema(self) -> None:
        fake = FakeLLMClient(response={"summary": "looks good"})
        reg = ModelRegistry()
        reg.register(ModelRole.FAST_ASSISTANT, ModelConfig(role=ModelRole.FAST_ASSISTANT, provider="openai", model_name="fast"))
        client = ModelClient(reg, llm_client_factory=_factory_for(fake))
        result = client.invoke(
            ModelRole.FAST_ASSISTANT, "sys", "summarize", None, "sess-1"
        )
        assert result.success is True
        assert result.value == {"summary": "looks good"}

    def test_invoke_rejects_underlying_fallback(self) -> None:
        # The underlying LLMClient (mock mode) reports fallback_used=True.
        # ModelClient must NOT silently accept it.
        fake = FakeLLMClient(fallback=True, success=True, response={"fallback": True})
        reg = ModelRegistry()
        reg.register(ModelRole.CRITIC, ModelConfig(role=ModelRole.CRITIC, provider="mock", model_name="mock-v1"))
        client = ModelClient(reg, recorder := TraceRecorder(), llm_client_factory=_factory_for(fake))
        result = client.invoke(ModelRole.CRITIC, "sys", "hi", None, "sess-1")
        assert result.success is False
        assert result.error is not None
        assert result.error.code == "MODEL_UNAVAILABLE"
        assert result.error.fallback_used is False
        assert recorder.latest().fallback_used is False

    def test_invoke_rejects_underlying_failure(self) -> None:
        fake = FakeLLMClient(success=False, fallback=False, response={"status": "error"})
        reg = ModelRegistry()
        reg.register(ModelRole.CRITIC, ModelConfig(role=ModelRole.CRITIC, provider="openai", model_name="gpt-test"))
        client = ModelClient(reg, llm_client_factory=_factory_for(fake))
        result = client.invoke(ModelRole.CRITIC, "sys", "hi", None, "sess-1")
        assert result.success is False
        assert result.error.code == "MODEL_UNAVAILABLE"

    def test_invoke_surfaces_timeout(self) -> None:
        fake = FakeLLMClient(raise_exc=TimeoutError("request timed out"))
        reg = ModelRegistry()
        reg.register(ModelRole.CRITIC, ModelConfig(role=ModelRole.CRITIC, provider="openai", model_name="gpt-test"))
        client = ModelClient(reg, llm_client_factory=_factory_for(fake))
        result = client.invoke(ModelRole.CRITIC, "sys", "hi", None, "sess-1")
        assert result.success is False
        assert result.error.code == "MODEL_TIMEOUT"
        assert result.error.retryable is True
        assert result.error.fallback_used is False

    def test_invoke_surfaces_generic_exception(self) -> None:
        fake = FakeLLMClient(raise_exc=RuntimeError("provider 500"))
        reg = ModelRegistry()
        reg.register(ModelRole.CRITIC, ModelConfig(role=ModelRole.CRITIC, provider="openai", model_name="gpt-test"))
        client = ModelClient(reg, llm_client_factory=_factory_for(fake))
        result = client.invoke(ModelRole.CRITIC, "sys", "hi", None, "sess-1")
        assert result.success is False
        assert result.error.code == "MODEL_UNAVAILABLE"
        assert result.error.retryable is False

    def test_invoke_schema_mismatch_is_failure(self) -> None:
        fake = FakeLLMClient(
            response={"name": "cylinder"},  # missing required "age"
            raw_output='{"name": "cylinder"}',
        )
        reg = ModelRegistry()
        reg.register(ModelRole.CRITIC, ModelConfig(role=ModelRole.CRITIC, provider="openai", model_name="gpt-test"))
        client = ModelClient(reg, llm_client_factory=_factory_for(fake))
        result = client.invoke(ModelRole.CRITIC, "sys", "hi", PERSON_SCHEMA, "sess-1")
        assert result.success is False
        assert result.error.code == "MODEL_SCHEMA_MISMATCH"
        assert result.error.fallback_used is False

    def test_invoke_invalid_json_output_is_failure(self) -> None:
        fake = FakeLLMClient(raw_output="not json at all", response={"name": "x"})
        reg = ModelRegistry()
        reg.register(ModelRole.CRITIC, ModelConfig(role=ModelRole.CRITIC, provider="openai", model_name="gpt-test"))
        client = ModelClient(reg, llm_client_factory=_factory_for(fake))
        result = client.invoke(ModelRole.CRITIC, "sys", "hi", PERSON_SCHEMA, "sess-1")
        assert result.success is False
        assert result.error.code == "MODEL_OUTPUT_INVALID"

    def test_invoke_with_real_mock_llm_is_rejected(self) -> None:
        # End-to-end: registering a mock provider (no factory) and invoking
        # must yield a failure because the mock LLMClient uses a fallback.
        reg = ModelRegistry()
        reg.register(ModelRole.CODE_EXTENSION, ModelConfig(role=ModelRole.CODE_EXTENSION, provider="mock", model_name="mock-v1"))
        client = ModelClient(reg, recorder := TraceRecorder())
        result = client.invoke(
            ModelRole.CODE_EXTENSION, "sys", "extend this", None, "sess-1"
        )
        assert result.success is False
        assert result.error.code == "MODEL_UNAVAILABLE"
        assert result.error.fallback_used is False
        assert recorder.latest().fallback_used is False

    def test_every_invocation_records_a_trace(self) -> None:
        fake = FakeLLMClient(response={"ok": True})
        reg = ModelRegistry()
        reg.register(ModelRole.FAST_ASSISTANT, ModelConfig(role=ModelRole.FAST_ASSISTANT, provider="openai", model_name="fast"))
        client = ModelClient(reg, recorder := TraceRecorder(), llm_client_factory=_factory_for(fake))
        client.invoke(ModelRole.FAST_ASSISTANT, "sys", "a", None, "s1")
        client.invoke(ModelRole.FAST_ASSISTANT, "sys", "b", None, "s2")
        assert len(recorder) == 2
        for trace in recorder.all():
            assert trace.fallback_used is False
            assert trace.role == "fast_assistant"
            assert trace.provider == "openai"
            assert trace.configured_model == "fast"

    def test_fallback_used_never_true_across_failure_modes(self) -> None:
        # Sweep every failure path and assert fallback_used stays False on
        # both the error and the recorded trace.
        reg = ModelRegistry()
        reg.register(ModelRole.CRITIC, ModelConfig(role=ModelRole.CRITIC, provider="openai", model_name="gpt-test"))

        scenarios = [
            FakeLLMClient(fallback=True, response={"x": 1}),
            FakeLLMClient(success=False, response={"x": 1}),
            FakeLLMClient(raise_exc=TimeoutError("t/o")),
            FakeLLMClient(raise_exc=RuntimeError("boom")),
            FakeLLMClient(raw_output="not json", response={"x": 1}),
        ]
        for fake in scenarios:
            recorder = TraceRecorder()
            client = ModelClient(reg, recorder=recorder, llm_client_factory=_factory_for(fake))
            result = client.invoke(ModelRole.CRITIC, "sys", "hi", PERSON_SCHEMA, "s")
            assert result.success is False, f"expected failure for {fake}"
            assert result.error is not None
            assert result.error.fallback_used is False, f"error fallback_used True for {fake}"
            assert recorder.latest().fallback_used is False, f"trace fallback_used True for {fake}"


# ===========================================================================
# 8. Models - roles, config, health status
# ===========================================================================


class TestModels:
    def test_model_role_has_four_members(self) -> None:
        assert {r.value for r in ModelRole} == {
            "primary_reasoner",
            "critic",
            "fast_assistant",
            "code_extension",
        }

    def test_model_config_defaults(self) -> None:
        cfg = ModelConfig(role=ModelRole.CRITIC, provider="openai", model_name="gpt-test")
        assert cfg.timeout_seconds == 120.0
        assert cfg.structured_output_enabled is True
        assert cfg.tool_calling_enabled is False
        assert cfg.api_key_env is None

    def test_model_config_forbids_extra_fields(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ModelConfig(
                role=ModelRole.CRITIC,
                provider="openai",
                model_name="gpt-test",
                unexpected="boom",  # type: ignore[call-arg]
            )

    def test_model_health_status_defaults(self) -> None:
        status = ModelHealthStatus(
            role=ModelRole.CRITIC, provider="openai", configured_model="gpt-test"
        )
        assert status.pass_fail == "fail"
        assert status.structured_output_support is False
        assert status.actual_returned_model is None


# ===========================================================================
# 9. Package surface
# ===========================================================================


class TestPackageSurface:
    def test_all_exports_resolve(self) -> None:
        import fluid_scientist.model_runtime as pkg

        for name in pkg.__all__:
            assert hasattr(pkg, name), f"{name} missing from package"

    def test_import_does_not_trigger_api_calls(self) -> None:
        # Re-importing must not perform any network I/O.
        import importlib

        mod = importlib.import_module("fluid_scientist.model_runtime")
        assert hasattr(mod, "ModelClient")
