from fluid_scientist.measurement.goal_metric_compiler import GoalMetricCompiler
from fluid_scientist.workbench.experiment_design_synthesizer import (
    AnalysisGoal,
    ExperimentDesign,
)


def test_goal_metric_compiler_outputs_layered_metrics() -> None:
    design = ExperimentDesign(
        research_objective="wake and force spectra",
        analysis_goals=[
            AnalysisGoal(
                goal_id="wake_deflection",
                description="wake deflection",
                target_quantities=["wake_center_offset"],
            ),
            AnalysisGoal(
                goal_id="force_spectrum",
                description="drag/lift spectra",
                target_quantities=["strouhal"],
            ),
        ],
    )

    layers = GoalMetricCompiler().compile(design)

    scientific_ids = {m["metric_id"] for m in layers["scientific"]}
    assert "wake_center_offset" in scientific_ids
    assert "strouhal" in scientific_ids
    assert "residual_convergence" in {m["metric_id"] for m in layers["credibility"]}
    assert layers["comparison"]
    assert layers["optional_diagnostics"]


def test_goal_metric_compiler_covers_required_goal_metric_contract() -> None:
    design = ExperimentDesign(
        research_objective="wake deflection, spanwise reversal, wall vortex structure, force spectrum",
        analysis_goals=[
            AnalysisGoal(goal_id="wake_deflection", description="wake deflection"),
            AnalysisGoal(goal_id="spanwise_reversal", description="spanwise reversal"),
            AnalysisGoal(goal_id="wall_vortex_structure", description="wall vortex structure"),
            AnalysisGoal(goal_id="force_spectrum", description="drag/lift force spectrum"),
        ],
    )

    ids = {m["metric_id"] for m in GoalMetricCompiler().compile(design)["scientific"]}

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
    }.issubset(ids)
