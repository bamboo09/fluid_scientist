from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fluid_scientist.domain.models import (
    CaseManifest,
    EvidenceLinkedClaim,
    FluidSpec,
    GeometrySpec,
    ResearchSpec,
)


def valid_research_spec() -> ResearchSpec:
    return ResearchSpec(
        question="How do curvature and Reynolds number affect bend pressure loss?",
        geometry=GeometrySpec(type="bend_90", diameter_m=0.2, curvature_ratio=2.0),
        fluid=FluidSpec(),
        responses=("pressure_drop", "secondary_flow_intensity"),
    )


def valid_case_manifest() -> CaseManifest:
    return CaseManifest(
        case_id="BEND_RE50000_CR2_001",
        project_id="project-1",
        version=1,
        template_id="openfoam-bend-v1",
        template_git_commit="abc1234",
        solver="simpleFoam",
        software_version="OpenFOAM-v2312",
        artifact_digest="sha256:" + "a" * 64,
        geometry=valid_research_spec().geometry,
        physics={"reynolds_number": 50_000.0},
        resources={"cpus": 8, "memory_gb": 16, "walltime_min": 60},
        expected_outputs=("pressure_drop", "mass_balance"),
        created_at=datetime(2026, 6, 29, tzinfo=UTC),
    )


def test_research_spec_rejects_nonpositive_diameter() -> None:
    with pytest.raises(ValidationError):
        ResearchSpec(
            question="invalid diameter must be rejected",
            geometry=GeometrySpec(type="bend_90", diameter_m=0),
            fluid=FluidSpec(),
        )


def test_research_spec_rejects_unknown_fields() -> None:
    payload = valid_research_spec().model_dump()
    payload["silent_guess"] = True

    with pytest.raises(ValidationError, match="Extra inputs"):
        ResearchSpec.model_validate(payload)


def test_case_manifest_is_frozen() -> None:
    case = valid_case_manifest()

    with pytest.raises(ValidationError, match="frozen"):
        case.solver = "other"  # type: ignore[misc]


def test_evidence_linked_claim_requires_evidence() -> None:
    with pytest.raises(ValidationError):
        EvidenceLinkedClaim(
            text="Pressure loss increases with Reynolds number.",
            evidence_ids=(),
            level="statistical_inference",
        )
