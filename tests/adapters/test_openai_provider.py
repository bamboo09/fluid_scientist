from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from fluid_scientist.adapters.openai_provider import (
    CustomOpenFOAMPlan,
    ExperimentDesign,
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


def test_experiment_designer_returns_typed_openfoam_plan() -> None:
    from fluid_scientist.adapters.openfoam import LaminarPipeCase

    design = ExperimentDesign(
        experiment_name="Laminar Pipe Reynolds Sweep",
        experiment_type="laminar_pipe",
        objective="Measure pressure loss while remaining in the laminar regime.",
        assumptions=("Single-phase incompressible flow",),
        rationale="Start with an analytical benchmark before extending the study.",
        requested_outputs=("pressure_drop_pa", "mass_imbalance_percent"),
        case=LaminarPipeCase(
            diameter_m=0.02,
            length_m=2.0,
            mean_velocity_m_s=0.08,
            kinematic_viscosity_m2_s=1e-6,
        ),
    )
    responses = FakeResponses([design])
    provider = OpenAIResponsesProvider(settings(), client=SimpleNamespace(responses=responses))

    result = provider.design_experiment(
        "Design a trustworthy laminar pipe pressure-loss experiment.",
        capabilities=("OpenFOAM-13", "laminar_pipe"),
    )

    assert result == design
    assert responses.calls[0]["text_format"] is ExperimentDesign
    assert "OpenFOAM-13" in responses.calls[0]["input"]


def test_experiment_designer_can_route_non_pipe_request_to_custom_openfoam() -> None:
    design = ExperimentDesign(
        experiment_name="Cylinder Wake Re 100",
        experiment_type="custom_openfoam",
        objective="Resolve transient vortex shedding behind a circular cylinder.",
        assumptions=("Two-dimensional incompressible laminar flow",),
        rationale="A transient custom case is required because the pipe template is unsuitable.",
        requested_outputs=("drag_coefficient", "lift_coefficient", "Strouhal_number"),
        custom_case=CustomOpenFOAMPlan(
            geometry="Circular cylinder in a rectangular far-field domain",
            boundary_conditions=("uniform inlet", "no-slip cylinder", "pressure outlet"),
            mesh_strategy="Refine the cylinder wall and near wake; verify y+ and mesh sensitivity.",
            run_strategy="Transient incompressible run covering at least ten shedding periods.",
        ),
    )

    assert design.case is None
    assert design.custom_case is not None
    assert design.experiment_type == "custom_openfoam"


def test_experiment_design_rejects_type_without_matching_payload() -> None:
    with pytest.raises(ValueError, match="custom_case"):
        ExperimentDesign(
            experiment_name="Cylinder Wake",
            experiment_type="custom_openfoam",
            objective="Resolve transient vortex shedding behind a circular cylinder.",
            assumptions=("Two-dimensional incompressible laminar flow",),
            rationale="The built-in pipe case cannot represent external flow.",
            requested_outputs=("drag_coefficient",),
        )


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
