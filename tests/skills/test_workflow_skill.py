from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_workflow_skill_preserves_custom_openfoam_safety_and_result_contracts() -> None:
    skill = (ROOT / "skills/fluid-research-workflow/SKILL.md").read_text(encoding="utf-8")
    reference = (ROOT / "skills/fluid-research-workflow/references/workflow.md").read_text(
        encoding="utf-8"
    )

    assert "custom OpenFOAM" in skill
    assert "double validation" in reference
    assert "submit-custom" in reference
    assert "blockMesh" in reference
    assert "optional `mirrorMesh`" in reference
    assert "checkMesh" in reference
    assert "foamRun -solver incompressibleFluid" in reference
    assert "browser" in reference
    assert "API key" in reference


def test_workflow_skill_governs_provider_neutral_planning_and_compilation() -> None:
    skill = (ROOT / "skills/fluid-research-workflow/SKILL.md").read_text(encoding="utf-8")
    reference = (ROOT / "skills/fluid-research-workflow/references/workflow.md").read_text(
        encoding="utf-8"
    )
    combined = skill + reference

    assert "provider-neutral" in combined
    assert all(provider in combined for provider in ("OpenAI", "GLM", "DeepSeek"))
    assert "local schema validation" in combined
    assert "deterministic compiler" in combined
    assert "plan version" in combined
    assert "archive digest" in combined
    assert "Gate 2" in combined
    assert "model-generated commands" in combined
    assert all(
        experiment_type in combined
        for experiment_type in (
            "laminar_pipe",
            "cylinder_flow",
            "lid_driven_cavity",
            "custom_openfoam",
        )
    )
    assert "evidence keys" in combined
    assert "force coefficients" in combined
    assert "velocity and pressure probes" in combined
    assert "never alter deterministic values" in combined
