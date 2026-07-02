import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from fluid_scientist.experiment_planning.result_analysis import (
    AnalysisEvidenceError,
    OpenAICompatibleResultAnalyst,
)
from fluid_scientist.settings import ProviderSettings


class FakeCompletions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            _request_id="analysis-request-1",
        )


class FakeClient:
    def __init__(self, content: str) -> None:
        self.completions = FakeCompletions(content)
        self.chat = SimpleNamespace(completions=self.completions)


def settings(provider: str = "glm") -> ProviderSettings:
    return ProviderSettings(
        provider=provider,
        model="chosen-model",
        api_key=SecretStr("secret"),
        max_retries=0,
    )


def valid_analysis(evidence_key: str = "mesh.cells") -> dict[str, object]:
    return {
        "title": "Cylinder smoke analysis",
        "executive_summary": "The solver completed and the mesh passed validation.",
        "claims": [
            {
                "text": "The generated mesh contains 2688 cells.",
                "level": "direct_observation",
                "evidence_keys": [evidence_key],
            }
        ],
        "credibility_assessment": ["This is a startup smoke run, not a converged study."],
        "limitations": ["The simulated duration is too short for shedding statistics."],
        "recommended_next_steps": ["Extend the run for multiple shedding cycles."],
    }


def test_result_analyst_returns_strict_evidence_linked_analysis() -> None:
    client = FakeClient(json.dumps(valid_analysis()))
    analyst = OpenAICompatibleResultAnalyst(settings(), client=client)

    result = analyst.analyze(
        {"mesh": {"cells": 2688}, "solver": {"completed": True}},
        evidence_keys=("mesh.cells", "solver.completed"),
    )

    assert result.claims[0].evidence_keys == ("mesh.cells",)
    assert client.completions.calls[0]["response_format"] == {"type": "json_object"}
    assert "secret" not in repr(analyst)


def test_result_analyst_rejects_claims_with_unavailable_evidence_keys() -> None:
    analyst = OpenAICompatibleResultAnalyst(
        settings(), client=FakeClient(json.dumps(valid_analysis("invented.metric")))
    )

    with pytest.raises(AnalysisEvidenceError, match="unavailable evidence"):
        analyst.analyze(
            {"mesh": {"cells": 2688}},
            evidence_keys=("mesh.cells",),
        )
