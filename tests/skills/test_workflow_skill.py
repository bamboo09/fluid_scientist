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
    assert "checkMesh" in reference
    assert "foamRun -solver incompressibleFluid" in reference
    assert "browser" in reference
    assert "API key" in reference
