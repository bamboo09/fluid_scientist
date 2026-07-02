"""Provider adapters that return strict, provider-neutral experiment plans."""

import json
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
        self, question: str, *, capabilities: tuple[str, ...]
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


class OpenAICompatiblePlanProvider:
    """GLM/DeepSeek Chat Completions adapter with local strict validation."""

    def __init__(self, settings: ProviderSettings, *, client: Any | None = None) -> None:
        if settings.provider not in PROVIDER_BASE_URLS:
            raise ValueError("OpenAI-compatible adapter supports only GLM and DeepSeek")
        self._settings = settings
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
        self._last_request_id: ContextVar[str | None] = ContextVar(
            f"plan_provider_request_id_{id(self)}", default=None
        )

    @property
    def provider_name(self) -> str:
        return self._settings.provider

    @property
    def last_request_id(self) -> str | None:
        """Return the terminal request ID published in the current context."""

        return self._last_request_id.get()

    def __repr__(self) -> str:
        return (
            "OpenAICompatiblePlanProvider("
            f"provider={self._settings.provider!r}, model={self._settings.model!r})"
        )

    def design_experiment(
        self, question: str, *, capabilities: tuple[str, ...]
    ) -> ExperimentPlan:
        self._last_request_id.set(None)
        return self._design_experiment(question, capabilities=capabilities)

    def _design_experiment(
        self, question: str, *, capabilities: tuple[str, ...]
    ) -> ExperimentPlan:
        for attempt in range(self._settings.max_retries + 1):
            request_id: str | None = None
            try:
                response = self._client.chat.completions.create(
                    model=self._settings.model,
                    messages=self._messages(question, capabilities),
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
                if plan.root.experiment_type not in capabilities:
                    self._last_request_id.set(request_id)
                    raise self._error(
                        ProviderOutputError,
                        "provider selected an experiment type outside supplied capabilities",
                        request_id=request_id,
                    )
                self._last_request_id.set(request_id)
                return plan
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

    def _validate_content(
        self, content: str, *, request_id: str | None
    ) -> ExperimentPlan:
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
            raise self._error(
                ProviderSchemaError,
                "provider JSON failed strict plan schema validation",
                request_id=request_id,
            ) from None

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
    def _content(response: Any) -> object:
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            return None
        return content

    @staticmethod
    def _request_id(value: Any) -> str | None:
        request_id = getattr(value, "_request_id", None) or getattr(
            value, "request_id", None
        )
        return request_id if isinstance(request_id, str) else None

    @staticmethod
    def _messages(
        question: str, capabilities: tuple[str, ...]
    ) -> list[dict[str, str]]:
        schema = json.dumps(
            ExperimentPlan.model_json_schema(), ensure_ascii=False, separators=(",", ":")
        )
        capability_json = json.dumps(
            list(capabilities), ensure_ascii=False, separators=(",", ":")
        )
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
