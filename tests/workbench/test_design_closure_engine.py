from fluid_scientist.study_decomposition.models import StudyIntent
from fluid_scientist.workbench.design_closure_engine import DesignClosureEngine
from fluid_scientist.workbench.experiment_design_synthesizer import (
    ExperimentDesignSynthesizer,
)


def test_closure_fills_ordinary_missing_values_from_re() -> None:
    study = StudyIntent(
        study_id="s1",
        title="pipe",
        raw_text="Re=3900 pipe flow",
        study_type="pipe",
        research_objective="Re=3900 pipe flow",
        geometry={"type": "pipe"},
    )
    design = ExperimentDesignSynthesizer().synthesize(study)

    closed = DesignClosureEngine().close(design)

    assert closed.material_properties["D"].value == 1.0
    assert closed.material_properties["U_ref"].value == 1.0
    assert closed.material_properties["rho"].value == 1.0
    assert closed.material_properties["nu"].value == 1 / 3900
    assert closed.dimensionless_parameters["Co"].value == 0.5
    assert closed.dimensionless_parameters["target_y_plus"].value == 1.0
    assert closed.solver["name"] == "pimpleFoam"
    assert closed.mesh_strategy["reference_area"] == "D^2"
    assert closed.time_control["flow_through_time"] == 20.0
    assert closed.time_control["statistical_cycles"] == 100
    assert closed.sampling_strategy["sampling_frequency"] == 100.0
    assert closed.output_control["fields"]
