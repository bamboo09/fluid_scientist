import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
)
from pydantic import SecretStr, ValidationError

from fluid_scientist.case_generation.models import GeneratedCaseDraft
from fluid_scientist.case_generation.providers import (
    CaseBuilder,
    CaseBuilderAuthenticationError,
    CaseBuilderEmptyOutputError,
    CaseBuilderMalformedOutputError,
    CaseBuilderModelNotFoundError,
    CaseBuilderRequestError,
    CaseBuilderSchemaError,
    OpenAICompatibleCaseBuilder,
    OpenAINativeCaseBuilder,
    create_case_builder,
)
from fluid_scientist.experiment_planning.models import ExperimentPlan
from fluid_scientist.settings import ProviderSettings


def valid_draft_payload() -> dict[str, object]:
    return {
        "experiment_name": "Backward-facing step study",
        "objective": "Resolve reattachment length for a laminar backward-facing step flow.",
        "solver": "incompressibleFluid",
        "preprocessing": ["blockMesh", "checkMesh"],
        "parameters": [],
        "files": [
            {"path": "0/U", "content": "FoamFile { class volVectorField; }"},
            {"path": "0/p", "content": "FoamFile { class volScalarField; }"},
            {"path": "constant/physicalProperties", "content": "nu 1e-5;"},
            {"path": "system/controlDict", "content": "solver incompressibleFluid;"},
            {"path": "system/fvSchemes", "content": "ddtSchemes { default steadyState; }"},
            {"path": "system/fvSolution", "content": "solvers {}"},
            {"path": "system/blockMeshDict", "content": "vertices ();"},
        ],
        "requested_outputs": ["reattachment_length", "residuals"],
        "assumptions": ["Two-dimensional incompressible flow"],
        "limitations": ["Pilot resolution is not grid independent"],
    }


def custom_plan(label: str = "step") -> ExperimentPlan:
    return ExperimentPlan.model_validate(
        {
            "experiment_type": "custom_openfoam",
            "experiment_name": f"Custom {label} experiment",
            "objective": "Resolve a bounded unsupported laminar flow experiment.",
            "rationale": "A generated candidate case is needed because no template exists.",
            "assumptions": ["Incompressible Newtonian flow"],
            "limitations": ["A short pilot does not establish grid independence"],
            "requested_outputs": ["reattachment_length"],
            "convergence_targets": {
                "residual_tolerance": 1e-6,
                "mass_imbalance_percent": 0.1,
            },
            "case": {
                "geometry": "A two-dimensional backward-facing step in a channel.",
                "boundary_conditions": ["Uniform inlet velocity", "No-slip walls"],
                "mesh_strategy": "Structured blockMesh refinement near the step corner.",
                "run_strategy": "Run a short steady incompressible pilot and inspect residuals.",
            },
        }
    )


def builtin_plan() -> ExperimentPlan:
    return ExperimentPlan.model_validate(
        {
            "experiment_type": "lid_driven_cavity",
            "experiment_name": "Cavity",
            "objective": "Compare the centerline velocity profile in a square cavity.",
            "rationale": "The template provides a deterministic verification experiment.",
            "assumptions": ["Incompressible Newtonian flow"],
            "limitations": ["Two-dimensional approximation"],
            "requested_outputs": ["velocity_probes"],
            "convergence_targets": {
                "residual_tolerance": 1e-6,
                "mass_imbalance_percent": 0.1,
            },
            "case": {
                "side_length_m": 1.0,
                "lid_velocity_m_s": 1.0,
                "kinematic_viscosity_m2_s": 0.01,
                "density_kg_m3": 1.0,
                "cells_per_side": 32,
                "end_time_s": 5.0,
            },
            "parameter_sweeps": [],
        }
    )


def settings(provider: str = "glm", *, retries: int = 1) -> ProviderSettings:
    return ProviderSettings(
        provider=provider,
        model="selected/model:latest",
        api_key=SecretStr("never-print-this"),
        max_retries=retries,
        timeout_seconds=12.5,
    )


@dataclass(frozen=True)
class ResponseValue:
    content: object
    request_id: str | None = "req-compatible"


class Completions:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        value = outcome if isinstance(outcome, ResponseValue) else ResponseValue(outcome)
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=value.content))]
        )
        if value.request_id is not None:
            response._request_id = value.request_id
        return response


class CompatibleClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.completions = Completions(outcomes)
        self.chat = SimpleNamespace(completions=self.completions)


class RawResponse:
    def __init__(self, outcome: object, request_id: str | None = "req-native") -> None:
        self.outcome = outcome
        self.request_id = request_id

    def parse(self) -> object:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return SimpleNamespace(output_parsed=self.outcome)


class NativeResponses:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, RawResponse):
            return outcome
        return RawResponse(outcome)


class NativeClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.raw = NativeResponses(outcomes)
        self.responses = SimpleNamespace(with_raw_response=self.raw)


@pytest.mark.parametrize(
    ("provider", "adapter_type"),
    [
        ("openai", OpenAINativeCaseBuilder),
        ("glm", OpenAICompatibleCaseBuilder),
        ("deepseek", OpenAICompatibleCaseBuilder),
    ],
)
def test_factory_selects_all_supported_case_builders(
    provider: str, adapter_type: type[object]
) -> None:
    client = (
        NativeClient([GeneratedCaseDraft.model_validate(valid_draft_payload())])
        if provider == "openai"
        else CompatibleClient([json.dumps(valid_draft_payload())])
    )

    builder = create_case_builder(settings(provider), client=client)

    assert isinstance(builder, adapter_type)
    assert isinstance(builder, CaseBuilder)
    assert builder.provider_name == provider
    assert builder.model_name == "selected/model:latest"


def test_compatible_builder_requests_only_a_strict_safe_file_manifest() -> None:
    client = CompatibleClient([json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(), client=client)

    draft = builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert draft.solver == "incompressibleFluid"
    call = client.completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["stream"] is False
    assert call["timeout"] == 12.5
    prompt = json.dumps(call["messages"], ensure_ascii=False)
    for required in (
        "OpenFOAM Foundation 13",
        "incompressibleFluid",
        "blockMesh",
        "checkMesh",
        "0/",
        "constant/",
        "system/",
        "fluidScientist/",
        "FoamFile",
        "{{ lower_snake_case }}",
        "shell",
        "scripts",
        "dynamic code",
        "#include",
        "remote paths",
        "archive",
        "binary",
        "credentials",
        "reattachment_length",
    ):
        assert required in prompt
    assert "never-print-this" not in prompt


def test_schema_failure_gets_one_sanitized_correction_with_exact_progress() -> None:
    invalid = valid_draft_payload() | {"command": "raw-rejected-value"}
    client = CompatibleClient([json.dumps(invalid), json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=client)
    stages: list[str] = []

    result = builder.generate_case(
        custom_plan(), capabilities=("OpenFOAM-13",), progress=stages.append
    )

    assert result.files
    assert stages == ["case_model", "schema_correction", "case_model"]
    retry = json.dumps(client.completions.calls[1]["messages"])
    assert "command" in retry
    assert "raw-rejected-value" not in retry


@pytest.mark.parametrize("content", ["not-json", {"files": []}, ["not", "text"]])
def test_malformed_or_nontext_compatible_output_is_not_retried(content: object) -> None:
    client = CompatibleClient([content, json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=client)

    with pytest.raises(CaseBuilderMalformedOutputError):
        builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert len(client.completions.calls) == 1


def test_empty_compatible_output_has_bounded_retries() -> None:
    client = CompatibleClient([" ", None])
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=client)

    with pytest.raises(CaseBuilderEmptyOutputError) as caught:
        builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert len(client.completions.calls) == 2
    assert caught.value.request_id == "req-compatible"


@pytest.mark.parametrize(
    ("failure", "error_type"),
    [
        (
            AuthenticationError(
                "raw key",
                response=httpx.Response(401, request=httpx.Request("POST", "https://x")),
                body=None,
            ),
            CaseBuilderAuthenticationError,
        ),
        (
            NotFoundError(
                "raw model",
                response=httpx.Response(404, request=httpx.Request("POST", "https://x")),
                body=None,
            ),
            CaseBuilderModelNotFoundError,
        ),
    ],
)
def test_authentication_and_model_errors_are_terminal_and_safe(
    failure: Exception, error_type: type[Exception]
) -> None:
    client = CompatibleClient([failure, json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=client)

    with pytest.raises(error_type) as caught:
        builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert len(client.completions.calls) == 1
    assert "raw" not in str(caught.value)
    assert "never-print-this" not in repr(builder)


@pytest.mark.parametrize("failure", [TimeoutError("raw"), ConnectionError("raw")])
def test_transient_compatible_failures_retry_with_exact_bound(failure: Exception) -> None:
    client = CompatibleClient([failure, json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=client)
    stages: list[str] = []

    builder.generate_case(
        custom_plan(), capabilities=("OpenFOAM-13",), progress=stages.append
    )

    assert len(client.completions.calls) == 2
    assert stages == ["case_model", "case_model"]


def test_transient_status_is_bounded_and_preserves_final_request_id() -> None:
    failures = []
    for request_id in ("req-first", "req-final"):
        failures.append(
            InternalServerError(
                "raw body",
                response=httpx.Response(
                    500,
                    headers={"x-request-id": request_id},
                    request=httpx.Request("POST", "https://x"),
                ),
                body=None,
            )
        )
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=CompatibleClient(failures))

    with pytest.raises(CaseBuilderRequestError) as caught:
        builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert caught.value.request_id == "req-final"
    assert builder.last_request_id == "req-final"
    assert "raw body" not in str(caught.value)


def test_native_builder_uses_structured_responses_and_preserves_request_id() -> None:
    client = NativeClient([RawResponse(GeneratedCaseDraft.model_validate(valid_draft_payload()))])
    builder = OpenAINativeCaseBuilder(settings("openai"), client=client)

    result = builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert result.files
    assert builder.last_request_id == "req-native"
    call = client.raw.calls[0]
    assert call["text_format"] is GeneratedCaseDraft
    assert call["store"] is False
    assert "never-print-this" not in str(call)


def validation_error() -> ValidationError:
    try:
        GeneratedCaseDraft.model_validate(valid_draft_payload() | {"command": "raw-secret"})
    except ValidationError as error:
        return error
    raise AssertionError("invalid draft unexpectedly validated")


@pytest.mark.parametrize(
    "schema_failure",
    [
        validation_error(),
        APIResponseValidationError(
            httpx.Response(200, request=httpx.Request("POST", "https://x")),
            {"raw": "secret"},
            message="raw secret",
        ),
    ],
)
def test_native_schema_failures_retry_consistently(schema_failure: Exception) -> None:
    client = NativeClient(
        [
            RawResponse(schema_failure, "req-schema"),
            GeneratedCaseDraft.model_validate(valid_draft_payload()),
        ]
    )
    builder = OpenAINativeCaseBuilder(settings("openai", retries=1), client=client)
    stages: list[str] = []

    result = builder.generate_case(
        custom_plan(), capabilities=("OpenFOAM-13",), progress=stages.append
    )

    assert result.files
    assert stages == ["case_model", "schema_correction", "case_model"]
    assert len(client.raw.calls) == 2
    assert "raw-secret" not in str(client.raw.calls[1])


def test_native_empty_output_has_bounded_retries() -> None:
    client = NativeClient(
        [RawResponse(None, "req-empty-first"), RawResponse(None, "req-empty-final")]
    )
    builder = OpenAINativeCaseBuilder(settings("openai", retries=1), client=client)
    stages: list[str] = []

    with pytest.raises(CaseBuilderEmptyOutputError) as caught:
        builder.generate_case(
            custom_plan(), capabilities=("OpenFOAM-13",), progress=stages.append
        )

    assert len(client.raw.calls) == 2
    assert stages == ["case_model", "case_model"]
    assert caught.value.request_id == "req-empty-final"


def test_native_wrong_structured_output_gets_bounded_schema_correction() -> None:
    wrong = SimpleNamespace(raw="secret")
    client = NativeClient(
        [RawResponse(wrong, "req-schema-first"), RawResponse(wrong, "req-schema-final")]
    )
    builder = OpenAINativeCaseBuilder(settings("openai", retries=1), client=client)
    stages: list[str] = []

    with pytest.raises(CaseBuilderSchemaError) as caught:
        builder.generate_case(
            custom_plan(), capabilities=("OpenFOAM-13",), progress=stages.append
        )

    assert len(client.raw.calls) == 2
    assert stages == ["case_model", "schema_correction", "case_model"]
    assert caught.value.request_id == "req-schema-final"
    assert "secret" not in str(client.raw.calls[1])


def test_builtin_plan_and_incompatible_target_are_rejected_before_network() -> None:
    client = CompatibleClient([json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(), client=client)

    with pytest.raises(ValueError, match="custom_openfoam"):
        builder.generate_case(builtin_plan(), capabilities=("OpenFOAM-13",))
    with pytest.raises(ValueError, match="OpenFOAM-13"):
        builder.generate_case(custom_plan(), capabilities=("GPU-only",))

    assert client.completions.calls == []


def test_provider_base_urls_are_fixed_and_cannot_be_overridden() -> None:
    for provider, expected in (
        ("glm", "https://open.bigmodel.cn/api/paas/v4/"),
        ("deepseek", "https://api.deepseek.com"),
    ):
        client = CompatibleClient([json.dumps(valid_draft_payload())])
        builder = OpenAICompatibleCaseBuilder(settings(provider), client=client)
        assert builder.base_url == expected
        assert client.base_url == expected


class ConcurrentCompletions:
    def __init__(self) -> None:
        self.barrier = Barrier(2, timeout=2)

    def create(self, **kwargs: object) -> object:
        messages = kwargs["messages"]
        encoded = json.dumps(messages)
        request_id = "req-alpha" if "alpha" in encoded else "req-beta"
        self.barrier.wait()
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(valid_draft_payload())))],
            _request_id=request_id,
        )


def test_request_ids_are_context_local_for_concurrent_callers() -> None:
    completions = ConcurrentCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    builder = OpenAICompatibleCaseBuilder(settings(retries=0), client=client)

    def generate(label: str) -> str | None:
        builder.generate_case(custom_plan(label), capabilities=("OpenFOAM-13",))
        return builder.last_request_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        alpha = pool.submit(generate, "alpha")
        beta = pool.submit(generate, "beta")
        ids = {alpha.result(), beta.result()}

    assert ids == {"req-alpha", "req-beta"}


def test_bad_status_is_safe_and_terminal() -> None:
    failure = BadRequestError(
        "raw response secret",
        response=httpx.Response(
            400,
            headers={"x-request-id": "req-bad"},
            request=httpx.Request("POST", "https://x"),
        ),
        body={"raw": "secret"},
    )
    client = CompatibleClient([failure, json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=client)

    with pytest.raises(CaseBuilderRequestError) as caught:
        builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert len(client.completions.calls) == 1
    assert caught.value.request_id == "req-bad"
    assert "secret" not in str(caught.value)


def test_sdk_timeout_is_bounded() -> None:
    request = httpx.Request("POST", "https://x")
    client = CompatibleClient([APITimeoutError(request), APITimeoutError(request)])
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=client)

    with pytest.raises(CaseBuilderRequestError, match="timeout"):
        builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert len(client.completions.calls) == 2


def test_sdk_connection_is_bounded() -> None:
    request = httpx.Request("POST", "https://x")
    client = CompatibleClient(
        [APIConnectionError(request=request), APIConnectionError(request=request)]
    )
    builder = OpenAICompatibleCaseBuilder(settings(retries=1), client=client)

    with pytest.raises(CaseBuilderRequestError, match="connection"):
        builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))

    assert len(client.completions.calls) == 2
