"""Safe provider adapters for model-authored OpenFOAM case manifests."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any, Protocol, runtime_checkable

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
)
from pydantic import ValidationError

from fluid_scientist.case_generation.models import GeneratedCaseDraft
from fluid_scientist.experiment_planning.models import CustomExperimentPlan, ExperimentPlan
from fluid_scientist.settings import ProviderSettings

PROVIDER_BASE_URLS = {
    "glm": "https://open.bigmodel.cn/api/paas/v4/",
    "deepseek": "https://api.deepseek.com",
}


@runtime_checkable
class CaseBuilder(Protocol):
    """Contract for a separately configured model that proposes case files."""

    @property
    def provider_name(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    def generate_case(
        self,
        custom_plan: CustomExperimentPlan | ExperimentPlan,
        *,
        capabilities: tuple[str, ...],
        progress: Callable[[str], None] | None = None,
    ) -> GeneratedCaseDraft: ...


class CaseBuilderProviderError(RuntimeError):
    """Base class whose message contains only safe provider attribution."""

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


class CaseBuilderAuthenticationError(CaseBuilderProviderError):
    """The provider rejected the in-memory credential."""


class CaseBuilderModelNotFoundError(CaseBuilderProviderError):
    """The configured model ID was not found."""


class CaseBuilderRequestError(CaseBuilderProviderError):
    """A provider request failed or exhausted bounded retries."""


class CaseBuilderOutputError(CaseBuilderProviderError):
    """The provider response was not an acceptable generated-case draft."""


class CaseBuilderEmptyOutputError(CaseBuilderOutputError):
    """The provider returned no output."""


class CaseBuilderMalformedOutputError(CaseBuilderOutputError):
    """The provider returned malformed JSON or non-text compatible output."""


class CaseBuilderSchemaError(CaseBuilderOutputError):
    """The provider output failed strict generated-case schema validation."""

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


class _CaseBuilderSupport:
    """Shared validation, prompt, error, and request-context mechanics."""

    def _init_case_builder_support(self, settings: ProviderSettings) -> None:
        self._settings = settings
        self._last_request_id: ContextVar[str | None] = ContextVar(
            f"case_builder_request_id_{id(self)}", default=None
        )

    @property
    def provider_name(self) -> str:
        return self._settings.provider

    @property
    def model_name(self) -> str:
        return self._settings.model

    @property
    def last_request_id(self) -> str | None:
        return self._last_request_id.get()

    def _begin_request(self) -> None:
        self._last_request_id.set(None)

    def _publish_request_id(self, value: Any) -> str | None:
        request_id = self._request_id(value)
        self._last_request_id.set(request_id)
        return request_id

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

    def _error(
        self,
        error_type: type[CaseBuilderProviderError],
        message: str,
        *,
        request_id: str | None,
    ) -> CaseBuilderProviderError:
        return error_type(
            message,
            provider=self.provider_name,
            model=self.model_name,
            request_id=request_id,
        )

    @staticmethod
    def _custom_plan(
        plan: CustomExperimentPlan | ExperimentPlan,
    ) -> CustomExperimentPlan:
        candidate = plan.root if isinstance(plan, ExperimentPlan) else plan
        if not isinstance(candidate, CustomExperimentPlan):
            raise ValueError("Case Builder accepts only a custom_openfoam experiment plan")
        return candidate

    @staticmethod
    def _validate_capabilities(capabilities: tuple[str, ...]) -> None:
        if type(capabilities) is not tuple or not capabilities:
            raise ValueError("capabilities must be a non-empty tuple of strings")
        if any(type(item) is not str or not item.strip() for item in capabilities):
            raise ValueError("capabilities must be a non-empty tuple of strings")
        if "OpenFOAM-13" not in capabilities:
            raise ValueError("Case Builder requires the OpenFOAM-13 capability")

    def _prompt(
        self,
        plan: CustomExperimentPlan,
        capabilities: tuple[str, ...],
        *,
        validation_feedback: tuple[str, ...] = (),
    ) -> tuple[str, str]:
        schema = json.dumps(
            GeneratedCaseDraft.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        instructions = (
            "Act as a constrained OpenFOAM Case Builder for OpenFOAM Foundation 13. "
            "Return exactly one GeneratedCaseDraft JSON object matching the strict JSON Schema. "
            "Use only solver incompressibleFluid and preprocessing ordered as blockMesh then "
            "checkMesh. Files may exist only below 0/, constant/, system/, or fluidScientist/. "
            "Include 0/U, 0/p, constant/physicalProperties, system/controlDict, "
            "system/fvSchemes, system/fvSolution, and system/blockMeshDict. Every OpenFOAM "
            "dictionary or field must have a complete FoamFile header with version 2.0, format "
            "ascii, the correct class, and an object matching its basename. Parameterize scalar "
            "values only with the exact placeholder grammar {{ lower_snake_case }} and declare "
            "every placeholder in parameters with bounded defaults and regression values. "
            "Never return shell commands, scripts, executable files, command strings, dynamic "
            "code, #codeStream, systemCall, #include or #includeEtc directives, shared libs, "
            "custom libraries, remote paths, absolute paths, network fetches, archives, binary "
            "or base64 payloads, credentials, API keys, environment variables, or extra fields. "
            f"Strict GeneratedCaseDraft JSON Schema: {schema}"
        )
        payload: dict[str, object] = {
            "custom_plan": plan.model_dump(mode="json"),
            "capabilities": list(capabilities),
            "foundation_version": 13,
            "supported_solver": "incompressibleFluid",
            "supported_preprocessing": ["blockMesh", "checkMesh"],
            "requested_outputs": list(plan.requested_outputs),
        }
        if validation_feedback:
            payload["schema_correction"] = {
                "instruction": "Correct only these sanitized schema issues; return the full draft.",
                "issues": list(validation_feedback),
            }
        return instructions, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _issues(error: ValidationError) -> tuple[str, ...]:
        return tuple(
            (
                f"{'.'.join(str(part) for part in item['loc']) or '<root>'}: "
                f"{item['msg']} [{item['type']}]"
            )[:240]
            for item in error.errors(include_input=False)[:12]
        )

    @staticmethod
    def _is_transient_status(error: APIStatusError) -> bool:
        return error.status_code in (408, 409, 429) or error.status_code >= 500


class OpenAICompatibleCaseBuilder(_CaseBuilderSupport):
    """GLM/DeepSeek Chat Completions adapter with local strict validation."""

    def __init__(self, settings: ProviderSettings, *, client: Any | None = None) -> None:
        if settings.provider not in PROVIDER_BASE_URLS:
            raise ValueError("OpenAI-compatible Case Builder supports only GLM and DeepSeek")
        self._init_case_builder_support(settings)
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
            "OpenAICompatibleCaseBuilder("
            f"provider={self.provider_name!r}, model={self.model_name!r})"
        )

    def generate_case(
        self,
        custom_plan: CustomExperimentPlan | ExperimentPlan,
        *,
        capabilities: tuple[str, ...],
        progress: Callable[[str], None] | None = None,
    ) -> GeneratedCaseDraft:
        self._begin_request()
        plan = self._custom_plan(custom_plan)
        self._validate_capabilities(capabilities)
        feedback: tuple[str, ...] = ()
        for attempt in range(self._settings.max_retries + 1):
            if progress is not None:
                progress("case_model")
            instructions, input_text = self._prompt(
                plan, capabilities, validation_feedback=feedback
            )
            request_id: str | None = None
            try:
                response = self._client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": input_text + "\nRespond with JSON only."},
                    ],
                    response_format={"type": "json_object"},
                    stream=False,
                    timeout=self._settings.timeout_seconds,
                )
                request_id = self._request_id(response)
                content = self._content(response)
                if content is not None and not isinstance(content, str):
                    self._last_request_id.set(request_id)
                    raise self._error(
                        CaseBuilderMalformedOutputError,
                        "provider returned non-text content instead of JSON",
                        request_id=request_id,
                    )
                if content is None or not content.strip():
                    if attempt < self._settings.max_retries:
                        continue
                    self._last_request_id.set(request_id)
                    raise self._error(
                        CaseBuilderEmptyOutputError,
                        "provider returned empty generated-case JSON",
                        request_id=request_id,
                    )
                try:
                    draft = GeneratedCaseDraft.model_validate_json(content)
                except ValidationError as error:
                    self._last_request_id.set(request_id)
                    if any(item["type"] == "json_invalid" for item in error.errors()):
                        raise self._error(
                            CaseBuilderMalformedOutputError,
                            "provider returned malformed JSON",
                            request_id=request_id,
                        ) from None
                    raise CaseBuilderSchemaError(
                        "provider JSON failed strict generated-case schema validation",
                        provider=self.provider_name,
                        model=self.model_name,
                        request_id=request_id,
                        issues=self._issues(error),
                    ) from None
                self._last_request_id.set(request_id)
                return draft
            except CaseBuilderSchemaError as error:
                if attempt == self._settings.max_retries:
                    raise
                feedback = error.issues
                if progress is not None:
                    progress("schema_correction")
            except CaseBuilderProviderError:
                raise
            except AuthenticationError as error:
                request_id = self._publish_request_id(error)
                raise self._error(
                    CaseBuilderAuthenticationError,
                    "provider authentication failed",
                    request_id=request_id,
                ) from None
            except NotFoundError as error:
                request_id = self._publish_request_id(error)
                raise self._error(
                    CaseBuilderModelNotFoundError,
                    "provider model was not found",
                    request_id=request_id,
                ) from None
            except APIStatusError as error:
                request_id = self._publish_request_id(error)
                if self._is_transient_status(error) and attempt < self._settings.max_retries:
                    continue
                raise self._error(
                    CaseBuilderRequestError,
                    "provider rejected the Case Builder request with an API status error",
                    request_id=request_id,
                ) from None
            except (TimeoutError, APITimeoutError) as error:
                if attempt == self._settings.max_retries:
                    request_id = self._publish_request_id(error)
                    raise self._error(
                        CaseBuilderRequestError,
                        "provider Case Builder request failed after timeout retries",
                        request_id=request_id,
                    ) from None
            except (ConnectionError, APIConnectionError) as error:
                if attempt == self._settings.max_retries:
                    request_id = self._publish_request_id(error)
                    raise self._error(
                        CaseBuilderRequestError,
                        "provider Case Builder request failed after connection retries",
                        request_id=request_id,
                    ) from None
        raise AssertionError("Case Builder retry loop terminated unexpectedly")

    @staticmethod
    def _content(response: Any) -> object:
        try:
            return response.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            return None


class OpenAINativeCaseBuilder(_CaseBuilderSupport):
    """OpenAI Responses structured-output Case Builder adapter."""

    def __init__(self, settings: ProviderSettings, *, client: Any | None = None) -> None:
        if settings.provider != "openai":
            raise ValueError("OpenAI native Case Builder requires the openai provider")
        self._init_case_builder_support(settings)
        self._client = client or OpenAI(
            api_key=settings.api_key.get_secret_value(),
            timeout=settings.timeout_seconds,
            max_retries=0,
        )

    def __repr__(self) -> str:
        return f"OpenAINativeCaseBuilder(model={self.model_name!r})"

    def generate_case(
        self,
        custom_plan: CustomExperimentPlan | ExperimentPlan,
        *,
        capabilities: tuple[str, ...],
        progress: Callable[[str], None] | None = None,
    ) -> GeneratedCaseDraft:
        self._begin_request()
        plan = self._custom_plan(custom_plan)
        self._validate_capabilities(capabilities)
        feedback: tuple[str, ...] = ()
        for attempt in range(self._settings.max_retries + 1):
            if progress is not None:
                progress("case_model")
            instructions, input_text = self._prompt(
                plan, capabilities, validation_feedback=feedback
            )
            request_id: str | None = None
            try:
                raw_response = self._client.responses.with_raw_response.parse(
                    model=self.model_name,
                    instructions=instructions,
                    input=input_text,
                    text_format=GeneratedCaseDraft,
                    store=False,
                    timeout=self._settings.timeout_seconds,
                )
                request_id = self._publish_request_id(raw_response)
                response = raw_response.parse()
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    if attempt < self._settings.max_retries:
                        continue
                    raise self._error(
                        CaseBuilderEmptyOutputError,
                        "provider returned no structured generated-case output",
                        request_id=request_id,
                    )
                if not isinstance(parsed, GeneratedCaseDraft):
                    raise CaseBuilderSchemaError(
                        "provider structured output failed generated-case schema validation",
                        provider=self.provider_name,
                        model=self.model_name,
                        request_id=request_id,
                        issues=("<root>: expected GeneratedCaseDraft [model_type]",),
                    )
                self._last_request_id.set(request_id)
                return parsed
            except CaseBuilderSchemaError as error:
                self._last_request_id.set(error.request_id)
                if attempt == self._settings.max_retries:
                    raise
                feedback = error.issues
                if progress is not None:
                    progress("schema_correction")
            except CaseBuilderProviderError:
                raise
            except AuthenticationError as error:
                request_id = self._publish_request_id(error)
                raise self._error(
                    CaseBuilderAuthenticationError,
                    "provider authentication failed",
                    request_id=request_id,
                ) from None
            except NotFoundError as error:
                request_id = self._publish_request_id(error)
                raise self._error(
                    CaseBuilderModelNotFoundError,
                    "provider model was not found",
                    request_id=request_id,
                ) from None
            except (APIResponseValidationError, ValidationError) as error:
                request_id = self._request_id(error) or request_id
                self._last_request_id.set(request_id)
                issues = self._issues(error) if isinstance(error, ValidationError) else (
                    "<root>: provider structured response was invalid [response_validation]",
                )
                schema_error = CaseBuilderSchemaError(
                    "provider structured output failed generated-case schema validation",
                    provider=self.provider_name,
                    model=self.model_name,
                    request_id=request_id,
                    issues=issues,
                )
                if attempt == self._settings.max_retries:
                    raise schema_error from None
                feedback = issues
                if progress is not None:
                    progress("schema_correction")
            except APIStatusError as error:
                request_id = self._publish_request_id(error)
                if self._is_transient_status(error) and attempt < self._settings.max_retries:
                    continue
                raise self._error(
                    CaseBuilderRequestError,
                    "provider rejected the Case Builder request with an API status error",
                    request_id=request_id,
                ) from None
            except (TimeoutError, APITimeoutError) as error:
                if attempt == self._settings.max_retries:
                    request_id = self._publish_request_id(error)
                    raise self._error(
                        CaseBuilderRequestError,
                        "provider Case Builder request failed after timeout retries",
                        request_id=request_id,
                    ) from None
            except (ConnectionError, APIConnectionError) as error:
                if attempt == self._settings.max_retries:
                    request_id = self._publish_request_id(error)
                    raise self._error(
                        CaseBuilderRequestError,
                        "provider Case Builder request failed after connection retries",
                        request_id=request_id,
                    ) from None
        raise AssertionError("Case Builder retry loop terminated unexpectedly")


def create_case_builder(
    settings: ProviderSettings, *, client: Any | None = None
) -> CaseBuilder:
    """Create a Case Builder from ephemeral credential-bearing settings."""

    if settings.provider == "openai":
        return OpenAINativeCaseBuilder(settings, client=client)
    return OpenAICompatibleCaseBuilder(settings, client=client)


__all__ = [
    "CaseBuilder",
    "CaseBuilderAuthenticationError",
    "CaseBuilderEmptyOutputError",
    "CaseBuilderMalformedOutputError",
    "CaseBuilderModelNotFoundError",
    "CaseBuilderOutputError",
    "CaseBuilderProviderError",
    "CaseBuilderRequestError",
    "CaseBuilderSchemaError",
    "OpenAICompatibleCaseBuilder",
    "OpenAINativeCaseBuilder",
    "create_case_builder",
]
