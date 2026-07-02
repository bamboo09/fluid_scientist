import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, Lock
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
)
from pydantic import SecretStr, ValidationError

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
