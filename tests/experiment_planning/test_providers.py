import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, Lock
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    RateLimitError,
)
from pydantic import SecretStr, ValidationError

from fluid_scientist.adapters.openai_provider import OpenAIPlanProvider
from fluid_scientist.experiment_planning.models import ExperimentPlan
from fluid_scientist.experiment_planning.providers import (
    ExperimentDesigner,
    OpenAICompatiblePlanProvider,
    ProviderAuthenticationError,
    ProviderEmptyOutputError,
    ProviderMalformedOutputError,
    ProviderModelNotFoundError,
    ProviderOutputError,
    ProviderRequestError,
    ProviderSchemaError,
    create_plan_provider,
)
from fluid_scientist.settings import ProviderSettings


def valid_pipe_plan() -> dict[str, object]:
    return {
        "experiment_type": "laminar_pipe",
        "experiment_name": "Pipe pressure-loss benchmark",
        "objective": "Measure pressure loss in fully developed laminar pipe flow.",
        "rationale": "This case provides an analytical benchmark for solver verification.",
        "assumptions": ["Steady incompressible Newtonian flow"],
        "limitations": ["The result applies only below the laminar transition"],
        "requested_outputs": ["pressure_drop", "mass_imbalance"],
        "convergence_targets": {
            "residual_tolerance": 1e-6,
            "mass_imbalance_percent": 0.1,
        },
        "case": {
            "diameter_m": 0.02,
            "length_m": 2.0,
            "mean_velocity_m_s": 0.08,
            "kinematic_viscosity_m2_s": 1e-6,
            "density_kg_m3": 998.2,
            "axial_cells": 80,
            "radial_cells": 10,
        },
        "parameter_sweeps": [],
    }


class FakeCompletions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, FakeResponse):
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=outcome.content))]
            )
            if outcome.request_id is not None:
                response._request_id = outcome.request_id
            return response
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))],
            _request_id="req-plan-123",
        )


class FakeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.completions = FakeCompletions(outcomes)
        self.chat = SimpleNamespace(completions=self.completions)


@dataclass(frozen=True)
class FakeRawParseOutcome:
    outcome: object
    request_id: str | None


class FakeRawResponse:
    def __init__(self, outcome: object, request_id: str | None) -> None:
        self.outcome = outcome
        self.request_id = request_id

    def parse(self) -> object:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return SimpleNamespace(output_parsed=self.outcome)


class FakeRawResponses:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, FakeRawParseOutcome):
            return FakeRawResponse(outcome.outcome, outcome.request_id)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeRawResponse(outcome, "req-openai-123")


class FakeResponsesClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.parsed_responses = FakeRawResponses(outcomes)
        self.responses = SimpleNamespace(with_raw_response=self.parsed_responses)


class ConcurrentRawResponses:
    def __init__(self) -> None:
        self.barrier = Barrier(2, timeout=1)
        self.state_lock = Lock()
        self.active = 0
        self.max_active = 0

    def parse(self, **kwargs: object) -> object:
        input_text = kwargs["input"]
        assert isinstance(input_text, str)
        request_id = "req-openai-alpha" if "alpha" in input_text else "req-openai-beta"
        with self.state_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.barrier.wait()
            return FakeRawResponse(openai_envelope(), request_id)
        finally:
            with self.state_lock:
                self.active -= 1


class ConcurrentResponsesClient:
    def __init__(self) -> None:
        self.raw_responses = ConcurrentRawResponses()
        self.responses = SimpleNamespace(with_raw_response=self.raw_responses)


class ConcurrentCompletions:
    def __init__(self) -> None:
        self.barrier = Barrier(2, timeout=1)
        self.state_lock = Lock()
        self.active = 0
        self.max_active = 0

    def create(self, **kwargs: object) -> object:
        messages = kwargs["messages"]
        assert isinstance(messages, list)
        user_message = messages[-1]["content"]
        request_id = "req-alpha" if "alpha" in user_message else "req-beta"
        with self.state_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.barrier.wait()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(valid_pipe_plan()))
                    )
                ],
                _request_id=request_id,
            )
        finally:
            with self.state_lock:
                self.active -= 1


class ConcurrentClient:
    def __init__(self) -> None:
        self.completions = ConcurrentCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


@dataclass(frozen=True)
class FakeResponse:
    content: object
    request_id: str | None


def settings(
    provider: str = "glm", *, max_retries: int = 2, api_key: str = "super-secret"
) -> ProviderSettings:
    return ProviderSettings(
        provider=provider,
        api_key=SecretStr(api_key),
        model="user/chosen-model:latest",
        max_retries=max_retries,
        timeout_seconds=17.5,
    )


def openai_envelope() -> object:
    from fluid_scientist.adapters.openai_provider import OpenAIPlanResponse

    return OpenAIPlanResponse(plan=ExperimentPlan.model_validate(valid_pipe_plan()))


def openai_plan_validation_error() -> ValidationError:
    try:
        openai_envelope().__class__.model_validate({"plan": {"unexpected": "secret"}})
    except ValidationError as error:
        return error
    raise AssertionError("invalid plan unexpectedly passed validation")


@pytest.mark.parametrize(
    ("provider", "expected_type"),
    [
        ("openai", OpenAIPlanProvider),
        ("glm", OpenAICompatiblePlanProvider),
        ("deepseek", OpenAICompatiblePlanProvider),
    ],
)
def test_plan_provider_factory_selects_configured_adapter(
    provider: str, expected_type: type[object]
) -> None:
    client: object
    if provider == "openai":
        client = FakeResponsesClient([openai_envelope()])
    else:
        client = FakeClient([json.dumps(valid_pipe_plan())])

    adapter = create_plan_provider(settings(provider), client=client)

    assert isinstance(adapter, expected_type)


def test_openai_plan_provider_uses_native_raw_structured_parse() -> None:
    from pydantic import BaseModel

    client = FakeResponsesClient([openai_envelope()])
    adapter = OpenAIPlanProvider(settings("openai"), client=client)

    result = adapter.design_experiment(
        "Validate pressure loss", capabilities=("laminar_pipe", "custom_openfoam")
    )

    assert result.root.experiment_type == "laminar_pipe"
    assert adapter.last_request_id == "req-openai-123"
    call = client.parsed_responses.calls[0]
    assert call["model"] == "user/chosen-model:latest"
    assert isinstance(call["text_format"], type)
    assert issubclass(call["text_format"], BaseModel)
    assert "plan" in call["text_format"].model_fields
    assert call["timeout"] == 17.5
    assert call["store"] is False
    assert "laminar_pipe" in str(call["input"])
    assert "custom_openfoam" in str(call["input"])
    assert "remote paths" in str(call["instructions"])
    assert "shell" in str(call["instructions"])


def test_openai_plan_provider_rejects_capability_mismatch_locally() -> None:
    client = FakeResponsesClient([openai_envelope()])
    adapter = OpenAIPlanProvider(settings("openai"), client=client)

    with pytest.raises(ProviderOutputError, match="capabilities") as caught:
        adapter.design_experiment(
            "Validate pressure loss", capabilities=("cylinder_flow",)
        )

    assert len(client.parsed_responses.calls) == 1
    assert caught.value.request_id == "req-openai-123"


def test_openai_plan_provider_retries_timeout_with_exact_bound() -> None:
    client = FakeResponsesClient([TimeoutError("secret timeout"), openai_envelope()])
    adapter = OpenAIPlanProvider(
        settings("openai", max_retries=1, api_key="never-print-this"), client=client
    )

    result = adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert result.root.experiment_type == "laminar_pipe"
    assert len(client.parsed_responses.calls) == 2
    assert "never-print-this" not in repr(adapter)


def test_openai_plan_provider_missing_output_is_non_retryable() -> None:
    client = FakeResponsesClient([None, openai_envelope()])
    adapter = OpenAIPlanProvider(settings("openai"), client=client)

    with pytest.raises(ProviderEmptyOutputError, match="structured output"):
        adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert len(client.parsed_responses.calls) == 1


def test_openai_plan_provider_status_error_is_sanitized_and_not_retried() -> None:
    failure = BadRequestError(
        "raw body includes never-print-this",
        response=httpx.Response(
            400,
            headers={"x-request-id": "req-openai-error"},
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        ),
        body={"error": {"message": "never-print-this"}},
    )
    client = FakeResponsesClient([failure, openai_envelope()])
    adapter = OpenAIPlanProvider(
        settings("openai", api_key="never-print-this"), client=client
    )

    with pytest.raises(ProviderRequestError, match="status") as caught:
        adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert len(client.parsed_responses.calls) == 1
    assert caught.value.request_id == "req-openai-error"
    assert "never-print-this" not in str(caught.value)
    assert "never-print-this" not in repr(caught.value)


def sdk_status_error(status_code: int, request_id: str) -> Exception:
    response = httpx.Response(
        status_code,
        headers={"x-request-id": request_id},
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )
    if status_code == 429:
        return RateLimitError("raw-rate-limit-secret", response=response, body=None)
    if status_code >= 500:
        return InternalServerError("raw-server-secret", response=response, body=None)
    return APIStatusError("raw-status-secret", response=response, body=None)


@pytest.mark.parametrize("status_code", [408, 409, 429, 500])
def test_openai_plan_provider_retries_transient_status_with_exact_bound(
    status_code: int,
) -> None:
    client = FakeResponsesClient(
        [sdk_status_error(status_code, f"req-transient-{attempt}") for attempt in range(3)]
    )
    adapter = OpenAIPlanProvider(settings("openai", max_retries=2), client=client)

    with pytest.raises(ProviderRequestError) as caught:
        adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert len(client.parsed_responses.calls) == 3
    assert caught.value.request_id == "req-transient-2"
    assert adapter.last_request_id == "req-transient-2"


def test_openai_plan_provider_preserves_final_transient_status_request_id() -> None:
    client = FakeResponsesClient(
        [sdk_status_error(500, "req-server-1"), sdk_status_error(500, "req-server-2")]
    )
    adapter = OpenAIPlanProvider(settings("openai", max_retries=1), client=client)

    with pytest.raises(ProviderRequestError) as caught:
        adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert len(client.parsed_responses.calls) == 2
    assert caught.value.request_id == "req-server-2"
    assert adapter.last_request_id == "req-server-2"
    assert "raw-server-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [(401, AuthenticationError), (404, NotFoundError)],
)
def test_openai_auth_and_not_found_are_not_retried(
    status_code: int, error_type: type[Exception]
) -> None:
    response = httpx.Response(
        status_code,
        headers={"x-request-id": "req-non-retry"},
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )
    failure = error_type("raw-key-or-model-secret", response=response, body=None)
    client = FakeResponsesClient([failure, openai_envelope()])
    adapter = OpenAIPlanProvider(
        settings("openai", max_retries=2, api_key="never-print-this"), client=client
    )

    with pytest.raises((ProviderAuthenticationError, ProviderModelNotFoundError)) as caught:
        adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert len(client.parsed_responses.calls) == 1
    assert caught.value.request_id == "req-non-retry"
    assert "raw-key-or-model-secret" not in str(caught.value)
    assert "never-print-this" not in str(caught.value)


def test_openai_response_validation_error_is_typed_sanitized_and_keeps_id() -> None:
    response = httpx.Response(
        200,
        headers={"x-request-id": "req-validation"},
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )
    failure = APIResponseValidationError(
        response,
        {"raw": "never-print-this"},
        message="schema body has never-print-this",
    )
    client = FakeResponsesClient(
        [FakeRawParseOutcome(failure, "req-validation"), openai_envelope()]
    )
    adapter = OpenAIPlanProvider(
        settings("openai", api_key="never-print-this"), client=client
    )

    with pytest.raises(ProviderSchemaError, match="schema") as caught:
        adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert len(client.parsed_responses.calls) == 1
    assert caught.value.request_id == "req-validation"
    assert adapter.last_request_id == "req-validation"
    assert "never-print-this" not in str(caught.value)
    assert "never-print-this" not in repr(caught.value)


def test_openai_pydantic_post_parser_error_keeps_raw_response_id() -> None:
    client = FakeResponsesClient(
        [FakeRawParseOutcome(openai_plan_validation_error(), "req-post-parser")]
    )
    adapter = OpenAIPlanProvider(settings("openai", api_key="never-print-this"), client=client)

    with pytest.raises(ProviderSchemaError, match="schema") as caught:
        adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert caught.value.request_id == "req-post-parser"
    assert adapter.last_request_id == "req-post-parser"
    assert "secret" not in str(caught.value)
    assert "never-print-this" not in repr(caught.value)


def test_openai_pydantic_post_parser_error_without_id_clears_prior_id() -> None:
    client = FakeResponsesClient(
        [openai_envelope(), FakeRawParseOutcome(openai_plan_validation_error(), None)]
    )
    adapter = OpenAIPlanProvider(settings("openai", max_retries=0), client=client)

    adapter.design_experiment("First", capabilities=("laminar_pipe",))
    assert adapter.last_request_id == "req-openai-123"

    with pytest.raises(ProviderSchemaError) as caught:
        adapter.design_experiment("Second", capabilities=("laminar_pipe",))

    assert caught.value.request_id is None
    assert adapter.last_request_id is None


def test_openai_connection_exhaustion_is_bounded_and_sanitized() -> None:
    failures = [
        APIConnectionError(
            message="raw connection never-print-this",
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
        )
        for _ in range(2)
    ]
    client = FakeResponsesClient(failures)
    adapter = OpenAIPlanProvider(
        settings("openai", max_retries=1, api_key="never-print-this"), client=client
    )

    with pytest.raises(ProviderRequestError, match="connection") as caught:
        adapter.design_experiment("Validate", capabilities=("laminar_pipe",))

    assert len(client.parsed_responses.calls) == 2
    assert caught.value.request_id is None
    assert "never-print-this" not in str(caught.value)


def test_openai_concurrent_callers_have_context_local_request_ids() -> None:
    client = ConcurrentResponsesClient()
    adapter = OpenAIPlanProvider(settings("openai", max_retries=0), client=client)

    def design(question: str) -> str | None:
        adapter.design_experiment(question, capabilities=("laminar_pipe",))
        return adapter.last_request_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        alpha = executor.submit(design, "alpha")
        beta = executor.submit(design, "beta")

    assert {alpha.result(), beta.result()} == {"req-openai-alpha", "req-openai-beta"}
    assert client.raw_responses.max_active == 2


def test_openai_plan_provider_clears_request_id_between_calls() -> None:
    client = FakeResponsesClient([openai_envelope(), TimeoutError("no id")])
    adapter = OpenAIPlanProvider(settings("openai", max_retries=0), client=client)

    adapter.design_experiment("First", capabilities=("laminar_pipe",))
    assert adapter.last_request_id == "req-openai-123"

    with pytest.raises(ProviderRequestError):
        adapter.design_experiment("Second", capabilities=("laminar_pipe",))

    assert adapter.last_request_id is None


@pytest.mark.parametrize(
    ("provider", "expected_base_url"),
    [
        ("glm", "https://open.bigmodel.cn/api/paas/v4/"),
        ("deepseek", "https://api.deepseek.com"),
    ],
)
def test_provider_requests_and_validates_json_plan(
    provider: str, expected_base_url: str
) -> None:
    client = FakeClient([json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings(provider), client=client)

    result = adapter.design_experiment(
        "Validate pressure loss", capabilities=("laminar_pipe", "custom_openfoam")
    )

    assert isinstance(adapter, ExperimentDesigner)
    assert result.root.experiment_type == "laminar_pipe"
    assert adapter.base_url == expected_base_url
    assert client.base_url == expected_base_url
    assert adapter.last_request_id == "req-plan-123"
    call = client.completions.calls[0]
    assert call["model"] == "user/chosen-model:latest"
    assert call["response_format"] == {"type": "json_object"}
    assert call["stream"] is False
    assert call["timeout"] == 17.5
    prompt = json.dumps(call["messages"])
    assert "JSON" in prompt
    assert "laminar_pipe" in prompt
    assert "custom_openfoam" in prompt
    assert "remote path" in prompt
    assert "shell" in prompt


@pytest.mark.parametrize("provider", ["openai", "other"])
def test_compatible_adapter_rejects_unsupported_provider(provider: str) -> None:
    if provider == "other":
        with pytest.raises(ValidationError):
            settings(provider)
        return

    with pytest.raises(ValueError, match="GLM and DeepSeek"):
        OpenAICompatiblePlanProvider(settings(provider), client=FakeClient([]))


def test_provider_settings_are_strict_and_do_not_accept_base_url() -> None:
    with pytest.raises(ValidationError):
        ProviderSettings(
            provider="glm",
            api_key=SecretStr("key"),
            model="",
            max_retries=2,
            timeout_seconds=10,
        )
    with pytest.raises(ValidationError):
        ProviderSettings(
            provider="glm",
            api_key=SecretStr("key"),
            model="m" * 129,
            max_retries=2,
            timeout_seconds=10,
        )
    with pytest.raises(ValidationError):
        ProviderSettings(
            provider="glm",
            api_key=SecretStr("key"),
            model="model",
            max_retries=6,
            timeout_seconds=10,
        )
    with pytest.raises(ValidationError):
        ProviderSettings(
            provider="glm",
            api_key=SecretStr("key"),
            model="model",
            max_retries=2,
            timeout_seconds=0,
        )
    with pytest.raises(ValidationError, match="base_url"):
        ProviderSettings(
            provider="glm",
            api_key=SecretStr("key"),
            model="model",
            max_retries=2,
            timeout_seconds=10,
            base_url="https://attacker.invalid",
        )


@pytest.mark.parametrize("api_key", ["", "   ", "\t\n"])
def test_provider_settings_reject_empty_api_key(api_key: str) -> None:
    with pytest.raises(ValidationError):
        settings(api_key=api_key)


def test_provider_settings_trim_model_and_reject_padded_provider() -> None:
    configured = ProviderSettings(
        provider="glm",
        api_key=SecretStr("key"),
        model="  chosen-model  ",
    )

    assert configured.model == "chosen-model"
    with pytest.raises(ValidationError):
        ProviderSettings(
            provider=" glm ",
            api_key=SecretStr("key"),
            model="chosen-model",
        )


def test_empty_output_retries_and_then_succeeds() -> None:
    client = FakeClient(["  ", json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings("deepseek"), client=client)

    result = adapter.design_experiment("Validate pressure loss", capabilities=("laminar_pipe",))

    assert result.root.experiment_type == "laminar_pipe"
    assert len(client.completions.calls) == 2


def test_empty_output_final_failure_is_explicit_and_safe() -> None:
    client = FakeClient([None, ""])
    adapter = OpenAICompatiblePlanProvider(
        settings("deepseek", max_retries=1), client=client
    )

    with pytest.raises(ProviderEmptyOutputError, match="empty") as caught:
        adapter.design_experiment("Validate pressure loss", capabilities=("laminar_pipe",))

    assert len(client.completions.calls) == 2
    assert caught.value.request_id == "req-plan-123"
    assert "super-secret" not in str(caught.value)
    assert "super-secret" not in repr(caught.value)
    assert "super-secret" not in repr(adapter)
    assert "deepseek" in repr(adapter)
    assert "user/chosen-model:latest" in repr(adapter)


@pytest.mark.parametrize("failure", [TimeoutError("slow"), ConnectionError("offline")])
def test_transient_request_failure_is_retried(failure: Exception) -> None:
    client = FakeClient([failure, json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings(max_retries=1), client=client)

    result = adapter.design_experiment("Validate pressure loss", capabilities=("laminar_pipe",))

    assert result.root.experiment_type == "laminar_pipe"
    assert len(client.completions.calls) == 2


@pytest.mark.parametrize(
    "failure",
    [
        APITimeoutError(httpx.Request("POST", "https://provider.invalid")),
        APIConnectionError(
            request=httpx.Request("POST", "https://provider.invalid")
        ),
    ],
)
def test_sdk_transient_request_failure_is_retried(failure: Exception) -> None:
    client = FakeClient([failure, json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings(max_retries=1), client=client)

    result = adapter.design_experiment(
        "Validate pressure loss", capabilities=("laminar_pipe",)
    )

    assert result.root.experiment_type == "laminar_pipe"
    assert len(client.completions.calls) == 2


def test_timeout_exhaustion_raises_typed_safe_request_error() -> None:
    client = FakeClient([TimeoutError("secret-free timeout"), TimeoutError("again")])
    adapter = OpenAICompatiblePlanProvider(
        settings(max_retries=1, api_key="never-print-this"), client=client
    )

    with pytest.raises(ProviderRequestError, match="timeout") as caught:
        adapter.design_experiment("Validate pressure loss", capabilities=("laminar_pipe",))

    assert len(client.completions.calls) == 2
    assert "never-print-this" not in str(caught.value)
    assert "never-print-this" not in repr(caught.value)


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (
            AuthenticationError(
                "bad key",
                response=httpx.Response(
                    401, request=httpx.Request("POST", "https://provider.invalid")
                ),
                body=None,
            ),
            ProviderAuthenticationError,
        ),
        (
            NotFoundError(
                "model missing",
                response=httpx.Response(
                    404,
                    headers={"x-request-id": "req-error-404"},
                    request=httpx.Request("POST", "https://provider.invalid"),
                ),
                body={"error": {"code": "model_not_found"}},
            ),
            ProviderModelNotFoundError,
        ),
    ],
)
def test_authentication_and_model_errors_are_typed_and_not_retried(
    failure: Exception, error_type: type[Exception]
) -> None:
    client = FakeClient([failure, json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings(), client=client)

    with pytest.raises(error_type) as caught:
        adapter.design_experiment("Validate pressure loss", capabilities=("laminar_pipe",))

    assert len(client.completions.calls) == 1
    assert "super-secret" not in str(caught.value)


def test_generic_sdk_status_error_is_typed_sanitized_and_not_retried() -> None:
    failure = BadRequestError(
        "raw body includes never-print-this",
        response=httpx.Response(
            400,
            headers={"x-request-id": "req-error-400"},
            request=httpx.Request("POST", "https://provider.invalid"),
        ),
        body={"error": {"message": "never-print-this"}},
    )
    client = FakeClient([failure, json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(
        settings(max_retries=2, api_key="never-print-this"), client=client
    )

    with pytest.raises(ProviderRequestError, match="status") as caught:
        adapter.design_experiment(
            "Validate pressure loss", capabilities=("laminar_pipe",)
        )

    assert len(client.completions.calls) == 1
    assert caught.value.request_id == "req-error-400"
    assert "never-print-this" not in str(caught.value)
    assert "never-print-this" not in repr(caught.value)


def test_failure_without_request_id_clears_previous_success_id() -> None:
    timeout = APITimeoutError(httpx.Request("POST", "https://provider.invalid"))
    client = FakeClient(
        [
            FakeResponse(json.dumps(valid_pipe_plan()), "req-success"),
            timeout,
        ]
    )
    adapter = OpenAICompatiblePlanProvider(settings(max_retries=0), client=client)

    adapter.design_experiment("First call", capabilities=("laminar_pipe",))
    assert adapter.last_request_id == "req-success"

    with pytest.raises(ProviderRequestError):
        adapter.design_experiment("Second call", capabilities=("laminar_pipe",))

    assert adapter.last_request_id is None


def test_concurrent_callers_receive_their_own_request_ids_without_serialization() -> None:
    client = ConcurrentClient()
    adapter = OpenAICompatiblePlanProvider(settings(max_retries=0), client=client)

    def design(question: str) -> str | None:
        adapter.design_experiment(question, capabilities=("laminar_pipe",))
        return adapter.last_request_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        alpha = executor.submit(design, "alpha")
        beta = executor.submit(design, "beta")

    assert {alpha.result(), beta.result()} == {"req-alpha", "req-beta"}
    assert client.completions.max_active == 2


def test_plan_experiment_type_must_be_in_capabilities() -> None:
    client = FakeClient([json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings(), client=client)

    with pytest.raises(ProviderOutputError, match="capabilities") as caught:
        adapter.design_experiment(
            "Validate pressure loss",
            capabilities=("cylinder_flow", "OpenFOAM-13", "workstation_openfoam"),
        )

    assert len(client.completions.calls) == 1
    assert caught.value.request_id == "req-plan-123"
    assert "super-secret" not in str(caught.value)


def test_malformed_json_is_not_retried() -> None:
    client = FakeClient(["not JSON", json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings(), client=client)

    with pytest.raises(ProviderMalformedOutputError, match="malformed JSON"):
        adapter.design_experiment("Validate pressure loss", capabilities=("laminar_pipe",))

    assert len(client.completions.calls) == 1


def test_non_text_content_is_malformed_and_not_retried() -> None:
    client = FakeClient([valid_pipe_plan(), json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings(), client=client)

    with pytest.raises(ProviderMalformedOutputError, match="text content"):
        adapter.design_experiment("Validate pressure loss", capabilities=("laminar_pipe",))

    assert len(client.completions.calls) == 1


def test_schema_invalid_json_is_not_retried() -> None:
    invalid = valid_pipe_plan() | {"unexpected": "field"}
    client = FakeClient([json.dumps(invalid), json.dumps(valid_pipe_plan())])
    adapter = OpenAICompatiblePlanProvider(settings(), client=client)

    with pytest.raises(ProviderSchemaError, match="schema"):
        adapter.design_experiment("Validate pressure loss", capabilities=("laminar_pipe",))

    assert len(client.completions.calls) == 1
