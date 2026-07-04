"""Provider adapters that return strict, provider-neutral experiment plans."""

import json
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Protocol, runtime_checkable

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
)
from pydantic import ValidationError

from fluid_scientist.experiment_planning.models import ExperimentPlan
from fluid_scientist.settings import ProviderSettings

PROVIDER_BASE_URLS = {
    "glm": "https://open.bigmodel.cn/api/paas/v4/",
    "deepseek": "https://api.deepseek.com",
}


@runtime_checkable
class ExperimentDesigner(Protocol):
    """Common contract for model-backed experiment planning."""

    def design_experiment(
        self,
        question: str,
        *,
        capabilities: tuple[str, ...],
        progress: Callable[[str], None] | None = None,
    ) -> ExperimentPlan: ...


class PlanProviderError(RuntimeError):
    """Base for safe, classified plan-provider failures."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str,
        request_id: str | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.request_id = request_id
        context = f"provider={provider!r}, model={model!r}"
        if request_id is not None:
            context += f", request_id={request_id!r}"
        super().__init__(f"{message} ({context})")


class ProviderAuthenticationError(PlanProviderError):
    """The provider rejected its in-memory credential."""


class ProviderModelNotFoundError(PlanProviderError):
    """The selected provider does not expose the requested model ID."""


class ProviderRequestError(PlanProviderError):
    """A status error occurred or transient request retries were exhausted."""


class ProviderOutputError(PlanProviderError):
    """The provider response could not be accepted as a plan."""


class ProviderEmptyOutputError(ProviderOutputError):
    """The provider returned no JSON content after bounded retries."""


class ProviderMalformedOutputError(ProviderOutputError):
    """The provider returned malformed JSON."""


class ProviderSchemaError(ProviderOutputError):
    """The provider JSON did not satisfy the strict plan schema."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str,
        request_id: str | None = None,
        issues: tuple[str, ...] = (),
    ) -> None:
        self.issues = issues
        super().__init__(
            message,
            provider=provider,
            model=model,
            request_id=request_id,
        )


class _PlanProviderSupport:
    """Shared safe error, request-context, and capability mechanics."""

    def _init_plan_provider_support(self, settings: ProviderSettings) -> None:
        self._settings = settings
        self._last_request_id: ContextVar[str | None] = ContextVar(
            f"plan_provider_request_id_{id(self)}", default=None
        )

    @property
    def provider_name(self) -> str:
        return self._settings.provider

    @property
    def model_name(self) -> str:
        return self._settings.model

    @property
    def last_request_id(self) -> str | None:
        """Return the terminal request ID published in the current context."""

        return self._last_request_id.get()

    def _begin_request(self) -> None:
        self._last_request_id.set(None)

    def _publish_request_id(self, value: Any) -> str | None:
        request_id = self._request_id(value)
        self._last_request_id.set(request_id)
        return request_id

    def _validate_capability(
        self,
        plan: ExperimentPlan,
        capabilities: tuple[str, ...],
        *,
        request_id: str | None,
    ) -> None:
        if plan.root.experiment_type not in capabilities:
            self._last_request_id.set(request_id)
            raise self._error(
                ProviderOutputError,
                "provider selected an experiment type outside supplied capabilities",
                request_id=request_id,
            )

    def _error(
        self,
        error_type: type[PlanProviderError],
        message: str,
        *,
        request_id: str | None,
    ) -> PlanProviderError:
        return error_type(
            message,
            provider=self._settings.provider,
            model=self._settings.model,
            request_id=request_id,
        )

    @staticmethod
    def _request_id(value: Any) -> str | None:
        request_id = getattr(value, "_request_id", None) or getattr(value, "request_id", None)
        if isinstance(request_id, str):
            return request_id
        response = getattr(value, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            header_id = headers.get("x-request-id")
            if isinstance(header_id, str):
                return header_id
        return None


class OpenAICompatiblePlanProvider(_PlanProviderSupport):
    """GLM/DeepSeek Chat Completions adapter with local strict validation."""

    def __init__(self, settings: ProviderSettings, *, client: Any | None = None) -> None:
        if settings.provider not in PROVIDER_BASE_URLS:
            raise ValueError("OpenAI-compatible adapter supports only GLM and DeepSeek")
        self._init_plan_provider_support(settings)
        self.base_url = PROVIDER_BASE_URLS[settings.provider]
        if client is None:
            client = OpenAI(
                api_key=settings.api_key.get_secret_value(),
                base_url=self.base_url,
                timeout=settings.timeout_seconds,
                max_retries=0,
            )
        else:
            client.base_url = self.base_url
        self._client = client

    def __repr__(self) -> str:
        return (
            "OpenAICompatiblePlanProvider("
            f"provider={self._settings.provider!r}, model={self._settings.model!r})"
        )

    def design_experiment(
        self,
        question: str,
        *,
        capabilities: tuple[str, ...],
        progress: Callable[[str], None] | None = None,
    ) -> ExperimentPlan:
        self._begin_request()
        return self._design_experiment(question, capabilities=capabilities, progress=progress)

    def _design_experiment(
        self,
        question: str,
        *,
        capabilities: tuple[str, ...],
        progress: Callable[[str], None] | None,
    ) -> ExperimentPlan:
        validation_feedback: tuple[str, ...] = ()
        for attempt in range(self._settings.max_retries + 1):
            if progress is not None:
                progress("model_planning")
            request_id: str | None = None
            try:
                response = self._client.chat.completions.create(
                    model=self._settings.model,
                    messages=self._messages(
                        question,
                        capabilities,
                        validation_feedback=validation_feedback,
                    ),
                    response_format={"type": "json_object"},
                    stream=False,
                    timeout=self._settings.timeout_seconds,
                )
                request_id = self._request_id(response)
                content = self._content(response)
                if content is not None and not isinstance(content, str):
                    self._last_request_id.set(request_id)
                    raise self._error(
                        ProviderMalformedOutputError,
                        "provider returned non-text content instead of JSON",
                        request_id=request_id,
                    )
                if content is None or not content.strip():
                    error = self._error(
                        ProviderEmptyOutputError,
                        "provider returned empty JSON content",
                        request_id=request_id,
                    )
                    if attempt < self._settings.max_retries:
                        continue
                    self._last_request_id.set(request_id)
                    raise error
                plan = self._validate_content(content, request_id=request_id)
                self._validate_capability(plan, capabilities, request_id=request_id)
                self._last_request_id.set(request_id)
                return plan
            except ProviderSchemaError as error:
                if attempt == self._settings.max_retries:
                    raise
                validation_feedback = error.issues
                if progress is not None:
                    progress("schema_correction")
            except AuthenticationError as error:
                request_id = self._request_id(error)
                self._last_request_id.set(request_id)
                raise self._error(
                    ProviderAuthenticationError,
                    "provider authentication failed",
                    request_id=request_id,
                ) from None
            except NotFoundError as error:
                request_id = self._request_id(error)
                self._last_request_id.set(request_id)
                raise self._error(
                    ProviderModelNotFoundError,
                    "provider model was not found",
                    request_id=request_id,
                ) from None
            except APIStatusError as error:
                request_id = self._request_id(error)
                self._last_request_id.set(request_id)
                raise self._error(
                    ProviderRequestError,
                    "provider rejected the request with an API status error",
                    request_id=request_id,
                ) from None
            except (TimeoutError, APITimeoutError) as error:
                if attempt == self._settings.max_retries:
                    request_id = self._request_id(error)
                    self._last_request_id.set(request_id)
                    raise self._error(
                        ProviderRequestError,
                        "provider request failed after timeout retries",
                        request_id=request_id,
                    ) from None
            except (ConnectionError, APIConnectionError) as error:
                if attempt == self._settings.max_retries:
                    request_id = self._request_id(error)
                    self._last_request_id.set(request_id)
                    raise self._error(
                        ProviderRequestError,
                        "provider request failed after connection retries",
                        request_id=request_id,
                    ) from None
        raise AssertionError("provider retry loop terminated unexpectedly")

    def _validate_content(self, content: str, *, request_id: str | None) -> ExperimentPlan:
        try:
            return ExperimentPlan.model_validate_json(content)
        except ValidationError as error:
            self._last_request_id.set(request_id)
            if any(item["type"] == "json_invalid" for item in error.errors()):
                raise self._error(
                    ProviderMalformedOutputError,
                    "provider returned malformed JSON",
                    request_id=request_id,
                ) from None
            issues = tuple(
                f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: "
                f"{item['msg']} [{item['type']}]"[:240]
                for item in error.errors()[:12]
            )
            raise ProviderSchemaError(
                "provider JSON failed strict plan schema validation",
                provider=self._settings.provider,
                model=self._settings.model,
                request_id=request_id,
                issues=issues,
            ) from None

    @staticmethod
    def _content(response: Any) -> object:
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            return None
        return content

    @staticmethod
    def _messages(
        question: str,
        capabilities: tuple[str, ...],
        *,
        validation_feedback: tuple[str, ...] = (),
    ) -> list[dict[str, str]]:
        schema = json.dumps(
            ExperimentPlan.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        capability_json = json.dumps(list(capabilities), ensure_ascii=False, separators=(",", ":"))
        system = (
            "Act as a fluid-mechanics experiment designer. Return exactly one JSON object "
            "matching the strict JSON Schema below. Select only an experiment_type present "
            "in the supplied capabilities and use SI units. Do not generate shell commands "
            "or remote paths. Do not add fields outside the schema. "
            f"Strict plan JSON Schema: {schema}"
        )
        user = (
            f"Capabilities: {capability_json}\n"
            f"Research question: {question}\n"
            "Respond with JSON only."
        )
        if validation_feedback:
            feedback_json = json.dumps(
                list(validation_feedback), ensure_ascii=False, separators=(",", ":")
            )
            user += (
                "\nYour previous response was rejected by strict schema validation. "
                f"Correct these issues and return the complete plan: {feedback_json}"
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]


def create_plan_provider(
    settings: ProviderSettings, *, client: Any | None = None
) -> ExperimentDesigner:
    """Create the plan adapter selected by ephemeral provider settings."""

    if settings.provider == "openai":
        from fluid_scientist.adapters.openai_provider import OpenAIPlanProvider

        return OpenAIPlanProvider(settings, client=client)
    return OpenAICompatiblePlanProvider(settings, client=client)
