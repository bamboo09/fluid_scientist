from pathlib import Path

import pytest

from fluid_scientist.services.skill_candidates import (
    PublishBlocked,
    SkillCandidateExtractor,
    SkillPublisher,
)


def sensitive_event() -> dict[str, object]:
    return {
        "pattern": "reduce time step after divergence",
        "host": "login.internal",
        "path": "/home/alice/case",
        "secret": "sk-live-secret-value",
        "source_refs": ["audit:event-17", "case:bend-fine:v2"],
    }


def test_candidate_redacts_environment_details() -> None:
    candidate = SkillCandidateExtractor().extract(sensitive_event())
    serialized = candidate.model_dump_json()

    assert "login.internal" not in serialized
    assert "/home/alice" not in serialized
    assert "sk-live-secret-value" not in serialized
    assert "audit:event-17" in serialized


def test_candidate_cannot_publish_without_red_green_evidence() -> None:
    candidate = SkillCandidateExtractor().extract(sensitive_event())

    with pytest.raises(PublishBlocked, match="RED and GREEN"):
        SkillPublisher().publish(candidate)


def test_candidate_requires_human_approval_after_tests() -> None:
    candidate = SkillCandidateExtractor().extract(sensitive_event())
    candidate.record_red("submits batch before Pilot")
    candidate.record_green("blocks batch until Pilot passes")

    with pytest.raises(PublishBlocked, match="approval"):
        SkillPublisher().publish(candidate)

    candidate.approve("researcher")
    published = SkillPublisher().publish(candidate)
    assert published.state == "PUBLISHED"


def test_repository_skill_encodes_credibility_gates() -> None:
    skill_path = Path("skills/fluid-research-workflow/SKILL.md")
    text = skill_path.read_text(encoding="utf-8")

    assert text.startswith("---\nname: fluid-research-workflow\n")
    assert "description: Use when" in text
    for required in (
        "ResearchSpec",
        "Pilot",
        "deterministic validation",
        "evidence-linked claims",
        "human approval",
    ):
        assert required in text
    assert "TBD" not in text


def test_repository_skill_covers_safe_openfoam13_workstation_execution() -> None:
    skill = Path("skills/fluid-research-workflow/SKILL.md").read_text(encoding="utf-8")
    reference = Path("skills/fluid-research-workflow/references/workflow.md").read_text(
        encoding="utf-8"
    )
    combined = skill + reference

    for required in (
        "OpenFOAM Foundation 13",
        "fluid-worker",
        "host fingerprint",
        "workstation",
        "kinematic pressure",
        "volumetric flow",
    ):
        assert required in combined
    assert "10.129.177.241" not in combined
    assert "192.168.1.102" not in combined
    assert "username: ls" not in combined
