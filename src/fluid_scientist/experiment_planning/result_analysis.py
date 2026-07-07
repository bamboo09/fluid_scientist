"""Provider-neutral, evidence-bound interpretation of deterministic CFD results."""

import json
from typing import Any, Literal, Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    NotFoundError,
    OpenAI,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from fluid_scientist.experiment_planning.providers import PROVIDER_BASE_URLS
from fluid_scientist.settings import ProviderSettings


class AnalysisClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2_000)
    level: Literal[
        "direct_observation",
        "statistical_inference",
        "model_extrapolation",
        "unverified_hypothesis",
    ]
    evidence_keys: tuple[str, ...] = Field(min_length=1)


class ExperimentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    executive_summary: str = Field(min_length=1, max_length=4_000)
    claims: tuple[AnalysisClaim, ...] = Field(min_length=1, max_length=30)
    credibility_assessment: tuple[str, ...] = Field(min_length=1, max_length=20)
    limitations: tuple[str, ...] = Field(min_length=1, max_length=20)
    recommended_next_steps: tuple[str, ...] = Field(min_length=1, max_length=20)


class ResultAnalyst(Protocol):
    def analyze(
        self,
        summary: dict[str, object],
        *,
        evidence_keys: tuple[str, ...],
    ) -> ExperimentAnalysis: ...


class AnalysisProviderError(RuntimeError):
    """Safe result-analysis provider failure."""


class AnalysisEvidenceError(AnalysisProviderError):
    """A model claim cites a value absent from deterministic input."""


class OpenAICompatibleResultAnalyst:
    """JSON-mode result analyst shared by OpenAI, GLM, and DeepSeek."""

    def __init__(self, settings: ProviderSettings, *, client: Any | None = None) -> None:
        self._settings = settings
        self.base_url = PROVIDER_BASE_URLS.get(settings.provider)
        if client is None:
            options: dict[str, object] = {
                "api_key": settings.api_key.get_secret_value(),
                "timeout": settings.timeout_seconds,
                "max_retries": 0,
            }
            if self.base_url is not None:
                options["base_url"] = self.base_url
            client = OpenAI(**options)
        elif self.base_url is not None:
            client.base_url = self.base_url
        self._client = client

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleResultAnalyst("
            f"provider={self._settings.provider!r}, model={self._settings.model!r})"
        )

    def analyze(
        self,
        summary: dict[str, object],
        *,
        evidence_keys: tuple[str, ...],
    ) -> ExperimentAnalysis:
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = self._client.chat.completions.create(
                    model=self._settings.model,
                    messages=self._messages(summary, evidence_keys),
                    response_format={"type": "json_object"},
                    stream=False,
                    timeout=self._settings.timeout_seconds,
                )
                content = response.choices[0].message.content
                if not isinstance(content, str) or not content.strip():
                    if attempt < self._settings.max_retries:
                        continue
                    raise AnalysisProviderError("provider returned empty analysis JSON")
                try:
                    analysis = ExperimentAnalysis.model_validate_json(content)
                except ValidationError as error:
                    raise AnalysisProviderError(
                        "provider returned an invalid analysis schema"
                    ) from error
                unavailable = {
                    key
                    for claim in analysis.claims
                    for key in claim.evidence_keys
                    if key not in evidence_keys
                }
                if unavailable:
                    raise AnalysisEvidenceError(
                        "analysis cited unavailable evidence keys: "
                        + ", ".join(sorted(unavailable))
                    )
                return analysis
            except AuthenticationError as error:
                raise AnalysisProviderError("provider authentication failed") from error
            except NotFoundError as error:
                raise AnalysisProviderError("provider model was not found") from error
            except APIStatusError as error:
                raise AnalysisProviderError("provider rejected analysis request") from error
            except (TimeoutError, APITimeoutError, ConnectionError, APIConnectionError) as error:
                if attempt == self._settings.max_retries:
                    raise AnalysisProviderError(
                        "provider analysis request exhausted transient retries"
                    ) from error
        raise AssertionError("analysis retry loop terminated unexpectedly")

    @staticmethod
    def _messages(
        summary: dict[str, object], evidence_keys: tuple[str, ...]
    ) -> list[dict[str, str]]:
        schema = json.dumps(
            ExperimentAnalysis.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system = (
            "Act as a CFD Results Analyst. Interpret only the deterministic JSON supplied "
            "by the application. Never invent, alter, or recompute numeric values. Every "
            "claim must cite one or more exact evidence_keys from the allow-list. Explicitly "
            "separate direct observations from inference, extrapolation, and hypotheses. "
            f"Return exactly one JSON object matching this schema: {schema}"
        )
        user = json.dumps(
            {"deterministic_summary": summary, "evidence_keys": evidence_keys},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]


def create_result_analyst(
    settings: ProviderSettings, *, client: Any | None = None
) -> ResultAnalyst:
    return OpenAICompatibleResultAnalyst(settings, client=client)
