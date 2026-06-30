from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from fluid_scientist.adapters.openai_provider import (
    OpenAIResponsesProvider,
    ProviderOutputError,
)
from fluid_scientist.domain.models import (
    AnalysisResult,
    EvidenceItem,
    EvidencePackage,
    FluidSpec,
    GeometrySpec,
    ResearchReport,
    ResearchSpec,
    ValidationResult,
)
from fluid_scientist.settings import OpenAISettings


class FakeResponses:
    def __init__(self, outputs, failures: int = 0) -> None:
        self.outputs = list(outputs)
        self.failures = failures
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise TimeoutError("temporary timeout")
        return SimpleNamespace(output_parsed=self.outputs.pop(0), _request_id="req-123")


def settings() -> OpenAISettings:
    return OpenAISettings(
        api_key=SecretStr("not-a-real-key"),
        planner_model="planner-model",
        extractor_model="extractor-model",
        max_retries=2,
    )


def spec() -> ResearchSpec:
    return ResearchSpec(
        question="How does bend curvature affect pressure loss?",
        geometry=GeometrySpec(type="bend_90", diameter_m=0.2, curvature_ratio=2.0),
        fluid=FluidSpec(),
    )


def evidence() -> EvidencePackage:
    return EvidencePackage(
        query="bend pressure loss",
        items=(
            EvidenceItem(
                evidence_id="paper:1:p4",
                source_id="paper:1",
                locator="page 4",
                excerpt="Pressure loss increases in the tested range.",
                confidence=0.9,
                reviewed=True,
            ),
        ),
    )


def test_interpret_uses_structured_responses_parse() -> None:
    responses = FakeResponses([spec()])
    provider = OpenAIResponsesProvider(settings(), client=SimpleNamespace(responses=responses))

    result = provider.interpret("How does bend curvature affect pressure loss?")

    assert result == spec()
    assert responses.calls[0]["model"] == "planner-model"
    assert responses.calls[0]["text_format"] is ResearchSpec
    assert provider.last_request_id == "req-123"


def test_results_analyst_returns_evidence_linked_claims() -> None:
    from fluid_scientist.adapters.openai_provider import ClaimBatch
    from fluid_scientist.domain.models import EvidenceLinkedClaim

    batch = ClaimBatch(
        claims=(
            EvidenceLinkedClaim(
                text="Pressure loss increased within the tested range.",
                evidence_ids=("analysis:mean", "paper:1:p4"),
                level="statistical_inference",
            ),
        )
    )
    responses = FakeResponses([batch])
    provider = OpenAIResponsesProvider(settings(), client=SimpleNamespace(responses=responses))
    analysis = AnalysisResult(
        project_id="project-1", sample_count=3, metrics={"mean": 10.0}
    )

    claims = provider.analyze(analysis, evidence(), ())

    assert claims == batch.claims
    assert responses.calls[0]["text_format"] is ClaimBatch


def test_reviewer_returns_structured_decision() -> None:
    from fluid_scientist.adapters.openai_provider import ReviewDecision
    from fluid_scientist.domain.models import EvidenceLinkedClaim

    responses = FakeResponses([ReviewDecision(approved=True, reason="Evidence is traceable.")])
    provider = OpenAIResponsesProvider(settings(), client=SimpleNamespace(responses=responses))
    report = ResearchReport(
        project_id="project-1",
        title="Report",
        scope="Tested range only",
        claims=(
            EvidenceLinkedClaim(
                text="Observed result",
                evidence_ids=("analysis:mean",),
                level="direct_observation",
            ),
        ),
    )
    validation = ValidationResult(
        case_id="case-1",
        iterative_convergence=1,
        mass_imbalance_percent=0.01,
        mass_conservation_passed=True,
    )

    assert provider.review(report, validation) is True


def test_timeout_is_retried_but_secret_is_never_represented() -> None:
    responses = FakeResponses([spec()], failures=1)
    provider = OpenAIResponsesProvider(settings(), client=SimpleNamespace(responses=responses))

    assert provider.interpret("How does bend curvature affect pressure loss?") == spec()
    assert len(responses.calls) == 2
    assert "not-a-real-key" not in repr(provider)


def test_missing_parsed_output_is_rejected() -> None:
    responses = FakeResponses([None])
    provider = OpenAIResponsesProvider(settings(), client=SimpleNamespace(responses=responses))

    with pytest.raises(ProviderOutputError, match="structured output"):
        provider.interpret("How does bend curvature affect pressure loss?")
