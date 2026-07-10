from fluid_scientist.draft.apply_executor import ApplyProposalExecutor
from fluid_scientist.draft.change_agent import DraftChangeAgent
from fluid_scientist.draft.draft_generator import DraftGenerator
from fluid_scientist.measurement.boundary_verification_compiler import (
    BoundaryVerificationCompiler,
)
from fluid_scientist.measurement.goal_metric_compiler import GoalMetricCompiler
from fluid_scientist.study_decomposition.models import StudyIntent
from fluid_scientist.workbench.design_closure_engine import DesignClosureEngine
from fluid_scientist.workbench.experiment_design_synthesizer import (
    ExperimentDesignSynthesizer,
)


def _complete_study() -> StudyIntent:
    study = StudyIntent(
        study_id="s1",
        title="pipe",
        raw_text=(
            "pipe flow Re=3900 spanwise length 4D; analyze wake deflection, "
            "spanwise reversal, wall vortex structure and drag lift force spectrum"
        ),
        study_type="pipe",
        research_objective=(
            "pipe flow Re=3900 spanwise length 4D; analyze wake deflection, "
            "spanwise reversal, wall vortex structure and drag lift force spectrum"
        ),
        geometry={"type": "pipe"},
    )
    design = DesignClosureEngine().close(ExperimentDesignSynthesizer().synthesize(study))
    layers = GoalMetricCompiler().compile(design)
    boundary = BoundaryVerificationCompiler().compile(design)
    design.scientific_metrics = layers["scientific"]
    design.boundary_verification_metrics = boundary
    design.credibility_metrics = layers["credibility"]
    return study.model_copy(
        update={
            "experiment_design": design.model_dump(),
            "target_phenomena": design.target_phenomena,
            "boundary_facts": design.boundary_facts,
            "scientific_metrics": layers["scientific"],
            "boundary_verification_metrics": boundary,
            "credibility_metrics": layers["credibility"],
            "comparison_metrics": layers["comparison"],
            "optional_diagnostics": layers["optional_diagnostics"],
        }
    )


def _ordinary_missing_count(draft) -> int:
    fields = (draft.capability_preview or {}).get("fields", {})
    ordinary = ("solver", "mesh", "requested_outputs")
    return sum(1 for name in ordinary if fields.get(name, {}).get("value_status") == "MISSING_REQUIRED")


def test_complete_draft_has_no_ordinary_missing_values() -> None:
    draft = DraftGenerator().generate(_complete_study(), {"session_id": "session_1"})

    assert draft.solver["name"] == "pimpleFoam"
    assert draft.mesh["strategy"]["reference_area"] == "D^2"
    assert draft.numerics["time_control"]["flow_through_time"] == 20.0
    assert draft.numerics["time_control"]["statistical_cycles"] == 100
    assert draft.measurement_plan["sampling_strategy"]["sampling_frequency"] == 100.0
    assert _ordinary_missing_count(draft) == 0

    scientific_ids = {m["metric_id"] for m in draft.measurement_plan["scientific_metrics"]}
    assert {
        "wake_center_offset",
        "wake_deflection_angle",
        "sign_change_rate",
        "phase_difference",
        "spanwise_correlation",
        "Q",
        "lambda2",
        "wall_vorticity",
        "wall_shear_stress",
        "force_mean",
        "force_rms",
        "force_psd",
        "dominant_frequency",
        "strouhal",
    }.issubset(scientific_ids)

    boundary_ids = {m["metric_id"] for m in draft.measurement_plan["boundary_verification_metrics"]}
    assert {"inlet_profile_error", "no_slip_wall_error", "outlet_backflow_ratio", "mass_conservation_error"}.issubset(boundary_ids)


def test_draft_modification_preserves_unrelated_fields() -> None:
    draft = DraftGenerator().generate(_complete_study(), {"session_id": "session_1"})
    original_solver = dict(draft.solver)
    original_mesh = dict(draft.mesh)

    proposal = DraftChangeAgent().generate(
        draft,
        "change spanwise length from 4D to 6D and change Q criterion to lambda2",
    )

    assert len(proposal.changes) == 2
    new_draft, result = ApplyProposalExecutor().apply(draft, proposal)
    assert result.valid
    assert new_draft.physical_system["computational_domain"]["spanwise_length"] == "6D"
    assert any(o.get("metric_id") == "lambda2" for o in new_draft.requested_outputs)
    assert new_draft.solver == original_solver
    assert new_draft.mesh == original_mesh
