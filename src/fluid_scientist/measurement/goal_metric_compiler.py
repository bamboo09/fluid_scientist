"""Compile analysis goals into layered metric plans."""

from __future__ import annotations

from typing import Any

from fluid_scientist.workbench.experiment_design_synthesizer import AnalysisGoal, ExperimentDesign


class GoalMetricCompiler:
    """Translate analysis goals into scientific and credibility metrics."""

    _GOAL_METRICS: dict[str, list[dict[str, Any]]] = {
        "wake_deflection": [
            {"metric_id": "wake_center_offset", "layer": "scientific", "required_fields": ["U"], "method": "sample wake center offset"},
            {"metric_id": "wake_deflection_angle", "layer": "scientific", "required_fields": ["U"], "method": "fit wake centerline slope"},
        ],
        "spanwise_reversal": [
            {"metric_id": "sign_change_rate", "layer": "scientific", "required_fields": ["U"], "method": "detect spanwise sign reversals"},
            {"metric_id": "phase_difference", "layer": "scientific", "required_fields": ["U"], "method": "phase lag between spanwise probes"},
            {"metric_id": "spanwise_correlation", "layer": "scientific", "required_fields": ["U"], "method": "two-point spanwise correlation"},
        ],
        "force_spectrum": [
            {"metric_id": "force_mean", "layer": "scientific", "required_fields": ["U", "p"], "function_object": "forceCoeffs"},
            {"metric_id": "force_rms", "layer": "scientific", "required_fields": ["U", "p"], "function_object": "forceCoeffs"},
            {"metric_id": "force_psd", "layer": "scientific", "required_fields": ["U", "p"], "method": "PSD of force coefficients"},
            {"metric_id": "dominant_frequency", "layer": "scientific", "required_fields": ["U", "p"], "method": "dominant peak of force PSD"},
            {"metric_id": "strouhal", "layer": "scientific", "required_fields": ["U", "p"], "method": "St = fD/U"},
        ],
        "wall_vortex_structure": [
            {"metric_id": "Q", "layer": "scientific", "required_fields": ["U"], "method": "Q criterion vortex identification"},
            {"metric_id": "lambda2", "layer": "scientific", "required_fields": ["U"], "method": "vortex identification"},
            {"metric_id": "wall_vorticity", "layer": "scientific", "required_fields": ["U"], "method": "near-wall vorticity extraction"},
            {"metric_id": "wall_shear_stress", "layer": "scientific", "required_fields": ["U"], "method": "wall shear stress field"},
        ],
        "baseline_flow_characterization": [
            {"metric_id": "velocity_profile", "layer": "scientific", "required_fields": ["U"], "method": "profile sampling"},
            {"metric_id": "pressure_drop", "layer": "scientific", "required_fields": ["p"], "method": "surface averages"},
        ],
    }

    def compile(self, design: ExperimentDesign) -> dict[str, list[dict[str, Any]]]:
        scientific: list[dict[str, Any]] = []
        seen: set[str] = set()
        for goal in design.analysis_goals:
            for metric in self._metrics_for_goal(goal):
                metric_id = metric["metric_id"]
                if metric_id not in seen:
                    scientific.append({**metric, "goal_id": goal.goal_id})
                    seen.add(metric_id)
        credibility = [
            {"metric_id": "residual_convergence", "layer": "numerical_credibility", "required_fields": ["residuals"]},
            {"metric_id": "mass_conservation_error", "layer": "numerical_credibility", "required_fields": ["phi"]},
            {"metric_id": "courant_number_max", "layer": "numerical_credibility", "required_fields": ["Co"]},
            {"metric_id": "statistical_stationarity", "layer": "numerical_credibility", "required_fields": ["U"]},
        ]
        comparison = [
            {"metric_id": "reynolds_number", "layer": "comparison", "required_fields": []},
            {"metric_id": "strouhal", "layer": "comparison", "required_fields": []},
        ]
        optional = [
            {"metric_id": "field_snapshot_diagnostics", "layer": "optional_diagnostics", "required_fields": ["U", "p"]},
        ]
        return {
            "scientific": scientific,
            "credibility": credibility,
            "comparison": comparison,
            "optional_diagnostics": optional,
        }

    def _metrics_for_goal(self, goal: AnalysisGoal) -> list[dict[str, Any]]:
        return self._GOAL_METRICS.get(goal.goal_id, self._GOAL_METRICS["baseline_flow_characterization"])


__all__ = ["GoalMetricCompiler"]
