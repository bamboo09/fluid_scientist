"""OpenAI Responses API provider using strict Pydantic structured outputs."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Any, Literal, TypeVar

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.domain.models import (
    AnalysisResult,
    EvidenceLinkedClaim,
    EvidencePackage,
    ResearchReport,
    ResearchSpec,
    ValidationResult,
)
from fluid_scientist.experiment_planning import providers as plan_providers
from fluid_scientist.experiment_planning.models import ExperimentPlan
from fluid_scientist.ports import SimulationResult
from fluid_scientist.settings import OpenAISettings, ProviderSettings

OutputT = TypeVar("OutputT", bound=BaseModel)


class ProviderOutputError(RuntimeError):
    """Raised when the API does not return the requested structured output."""


class ProviderRequestError(RuntimeError):
    """Raised after transient API failures exhaust the configured retries."""


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClaimBatch(StrictOutput):
    claims: tuple[EvidenceLinkedClaim, ...] = Field(min_length=1)


class ReviewDecision(StrictOutput):
    approved: bool
    reason: str = Field(min_length=1)


class CustomOpenFOAMPlan(StrictOutput):
    geometry: str = Field(min_length=10)
    boundary_conditions: tuple[str, ...] = Field(min_length=2)
    mesh_strategy: str = Field(min_length=10)
    run_strategy: str = Field(min_length=10)


class ExperimentDesign(StrictOutput):
    experiment_name: str = Field(min_length=1, max_length=80)
    experiment_type: Literal["laminar_pipe", "custom_openfoam"]
    objective: str = Field(min_length=10)
    assumptions: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=10)
    requested_outputs: tuple[str, ...] = Field(min_length=1)
    case: LaminarPipeCase | None = None
    custom_case: CustomOpenFOAMPlan | None = None

    @model_validator(mode="after")
    def require_matching_case_payload(self) -> "ExperimentDesign":
        if self.experiment_type == "laminar_pipe" and self.case is None:
            raise ValueError("laminar_pipe design requires case")
        if self.experiment_type == "custom_openfoam" and self.custom_case is None:
            raise ValueError("custom_openfoam design requires custom_case")
        if self.experiment_type == "laminar_pipe" and self.custom_case is not None:
            raise ValueError("laminar_pipe design cannot include custom_case")
        if self.experiment_type == "custom_openfoam" and self.case is not None:
            raise ValueError("custom_openfoam design cannot include pipe case")
        return self


class OpenAIPlanResponse(StrictOutput):
    """Responses API envelope for the provider-neutral root model."""

    plan: ExperimentPlan


class OpenAIPlanProvider(plan_providers._PlanProviderSupport):
    """OpenAI Responses adapter that returns provider-neutral plans."""

    def __init__(self, settings: ProviderSettings, *, client: Any | None = None) -> None:
        if settings.provider != "openai":
            raise ValueError("OpenAI plan adapter requires the openai provider")
        if client is None:
            client = OpenAI(
                api_key=settings.api_key.get_secret_value(),
                timeout=settings.timeout_seconds,
                max_retries=0,
            )
        self._init_plan_provider_support(settings)
        self._client = client

    def __repr__(self) -> str:
        return f"OpenAIPlanProvider(model={self._settings.model!r})"

    def design_experiment(
        self,
        question: str,
        *,
        capabilities: tuple[str, ...],
        progress: Callable[[str], None] | None = None,
    ) -> ExperimentPlan:
        self._begin_request()
        for attempt in range(self._settings.max_retries + 1):
            if progress is not None:
                progress("model_planning")
            request_id: str | None = None
            try:
                raw_response = self._client.responses.with_raw_response.parse(
                    model=self._settings.model,
                    instructions=(
                        "Act as a fluid-mechanics experiment designer. Return a strict typed "
                        "experiment plan using SI units. Select only an experiment_type listed "
                        "in capabilities. Do not generate shell commands or remote paths, and "
                        "do not invent unsupported solver capabilities."
                    ),
                    input=self._json(
                        {"question": question, "capabilities": list(capabilities)}
                    ),
                    text_format=OpenAIPlanResponse,
                    store=False,
                    timeout=self._settings.timeout_seconds,
                )
                request_id = self._publish_request_id(raw_response)
                response = raw_response.parse()
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    self._last_request_id.set(request_id)
                    raise self._error(
                        plan_providers.ProviderEmptyOutputError,
                        "provider returned no structured output",
                        request_id=request_id,
                    )
                if not isinstance(parsed, OpenAIPlanResponse):
                    self._last_request_id.set(request_id)
                    raise self._error(
                        plan_providers.ProviderSchemaError,
                        "provider structured output failed strict plan schema validation",
                        request_id=request_id,
                    )
                plan = parsed.plan
                self._validate_capability(
                    plan, capabilities, request_id=request_id
                )
                self._last_request_id.set(request_id)
                return plan
            except plan_providers.PlanProviderError:
                raise
            except AuthenticationError as error:
                self._publish_error_id(error)
                raise self._error(
                    plan_providers.ProviderAuthenticationError,
                    "provider authentication failed",
                    request_id=self.last_request_id,
                ) from None
            except NotFoundError as error:
                self._publish_error_id(error)
                raise self._error(
                    plan_providers.ProviderModelNotFoundError,
                    "provider model was not found",
                    request_id=self.last_request_id,
                ) from None
            except APIResponseValidationError as error:
                request_id = self._request_id(error) or request_id
                self._last_request_id.set(request_id)
                raise self._error(
                    plan_providers.ProviderSchemaError,
                    "provider structured output failed strict plan schema validation",
                    request_id=request_id,
                ) from None
            except APIStatusError as error:
                self._publish_error_id(error)
                if (
                    error.status_code in (408, 409, 429) or error.status_code >= 500
                ) and attempt < self._settings.max_retries:
                    continue
                raise self._error(
                    plan_providers.ProviderRequestError,
                    "provider rejected the request with an API status error",
                    request_id=self.last_request_id,
                ) from None
            except ValidationError:
                self._last_request_id.set(request_id)
                raise self._error(
                    plan_providers.ProviderSchemaError,
                    "provider structured output failed strict plan schema validation",
                    request_id=request_id,
                ) from None
            except (TimeoutError, APITimeoutError) as error:
                if attempt == self._settings.max_retries:
                    self._publish_error_id(error)
                    raise self._error(
                        plan_providers.ProviderRequestError,
                        "provider request failed after timeout retries",
                        request_id=self.last_request_id,
                    ) from None
            except (ConnectionError, APIConnectionError) as error:
                if attempt == self._settings.max_retries:
                    self._publish_error_id(error)
                    raise self._error(
                        plan_providers.ProviderRequestError,
                        "provider request failed after connection retries",
                        request_id=self.last_request_id,
                    ) from None
        raise AssertionError("provider retry loop terminated unexpectedly")

    def _publish_error_id(self, error: Exception) -> None:
        self._publish_request_id(error)

    @staticmethod
    def _json(payload: Any) -> str:
        import json

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class OpenAIResponsesProvider:
    def __init__(self, settings: OpenAISettings, *, client: Any | None = None) -> None:
        if client is None:
            if settings.api_key is None:
                raise ValueError("OpenAI api_key is required")
            client = OpenAI(
                api_key=settings.api_key.get_secret_value(),
                timeout=settings.timeout_seconds,
                max_retries=0,
            )
        self._settings = settings
        self._client = client
        self.last_request_id: str | None = None
        self.last_review_reason: str | None = None

    def __repr__(self) -> str:
        return (
            "OpenAIResponsesProvider("
            f"planner_model={self._settings.planner_model!r}, "
            f"extractor_model={self._settings.extractor_model!r})"
        )

    def interpret(self, question: str) -> ResearchSpec:
        return self._parse(
            model=self._settings.planner_model,
            text_format=ResearchSpec,
            instructions=(
                "Convert the fluid-mechanics request into ResearchSpec. Use SI units. "
                "Do not silently invent material conditions that change the conclusion."
            ),
            input_text=question,
        )

    def design_experiment(
        self, question: str, *, capabilities: tuple[str, ...]
    ) -> ExperimentDesign:
        return self._parse(
            model=self._settings.planner_model,
            text_format=ExperimentDesign,
            instructions=(
                "Act as a fluid-mechanics experiment designer. Select only an experiment type "
                "listed in capabilities. Produce SI parameters that satisfy the typed schema and "
                "state assumptions, requested outputs, and scientific rationale. Do not invent "
                "unsupported solver capabilities or bypass approval gates. Use laminar_pipe only "
                "for the built-in analytical pipe template; route cylinder, bend, external-flow, "
                "transient, and other geometries to custom_openfoam with explicit case guidance."
            ),
            input_text=self._json(
                {"question": question, "capabilities": list(capabilities)}
            ),
        )

    def analyze(
        self,
        analysis: AnalysisResult,
        evidence: EvidencePackage,
        simulations: tuple[SimulationResult, ...],
    ) -> tuple[EvidenceLinkedClaim, ...]:
        payload = {
            "analysis": analysis.model_dump(mode="json"),
            "evidence": evidence.model_dump(mode="json"),
            "simulations": [asdict(item) for item in simulations],
        }
        batch = self._parse(
            model=self._settings.planner_model,
            text_format=ClaimBatch,
            instructions=(
                "Act as Results Analyst. Explain only deterministic results supplied in input. "
                "Every claim must cite analysis, simulation, or literature evidence IDs and use "
                "the correct evidence level. Never calculate replacement values."
            ),
            input_text=self._json(payload),
        )
        return batch.claims

    def review(self, report: ResearchReport, validation: ValidationResult) -> bool:
        decision = self._parse(
            model=self._settings.planner_model,
            text_format=ReviewDecision,
            instructions=(
                "Act as an independent scientific reviewer. Approve only when conclusions are "
                "traceable, scoped to the tested range, and consistent with validation."
            ),
            input_text=self._json(
                {
                    "report": report.model_dump(mode="json"),
                    "validation": validation.model_dump(mode="json"),
                }
            ),
        )
        self.last_review_reason = decision.reason
        return decision.approved

    def _parse(
        self,
        *,
        model: str,
        text_format: type[OutputT],
        instructions: str,
        input_text: str,
    ) -> OutputT:
        last_error: Exception | None = None
        for _attempt in range(self._settings.max_retries + 1):
            try:
                response = self._client.responses.parse(
                    model=model,
                    instructions=instructions,
                    input=input_text,
                    text_format=text_format,
                    store=False,
                    timeout=self._settings.timeout_seconds,
                )
                self.last_request_id = getattr(response, "_request_id", None)
                parsed = response.output_parsed
                if parsed is None:
                    raise ProviderOutputError("OpenAI response contained no structured output")
                return parsed
            except ProviderOutputError:
                raise
            except (TimeoutError, APITimeoutError, APIConnectionError) as error:
                last_error = error
        raise ProviderRequestError("OpenAI request failed after configured retries") from last_error

    @staticmethod
    def _json(payload: Any) -> str:
        import json

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
