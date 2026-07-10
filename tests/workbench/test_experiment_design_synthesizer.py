from fluid_scientist.study_decomposition.models import StudyIntent
from fluid_scientist.workbench.experiment_design_synthesizer import (
    ExperimentDesignSynthesizer,
)


def test_synthesizer_builds_complete_pipe_design_with_user_free_slip() -> None:
    study = StudyIntent(
        study_id="s1",
        title="pipe",
        raw_text="new pipe research Re=3900 with top free slip; analyze wake deflection and wall vortex structure",
        study_type="pipe",
        research_objective="new pipe research Re=3900 with top free slip; analyze wake deflection and wall vortex structure",
        geometry={"type": "pipe"},
    )

    design = ExperimentDesignSynthesizer().synthesize(study)

    assert design.geometry["type"] == "pipe"
    assert design.dimensionless_parameters["Re"].value == 3900.0
    assert design.dimensionless_parameters["Re"].source == "USER_SPECIFIED"
    assert design.boundary_conditions["top"]["type"] == "free_slip"
    assert design.boundary_conditions["top"]["source"] == "USER_SPECIFIED"
    assert design.boundary_facts["top"]["type"] == "free_slip"
    assert {"wake_deflection", "wall_vortex_structure"}.issubset(set(design.target_phenomena))
