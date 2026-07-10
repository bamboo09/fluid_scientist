"""Compile boundary-condition verification metrics."""

from __future__ import annotations

from typing import Any

from fluid_scientist.workbench.experiment_design_synthesizer import ExperimentDesign


class BoundaryVerificationCompiler:
    """Create verification metrics required by selected boundary conditions."""

    def compile(self, design: ExperimentDesign) -> list[dict[str, Any]]:
        metrics: list[dict[str, Any]] = []
        for patch, bc in design.boundary_conditions.items():
            bc_type = str(bc.get("type", "")).lower()
            if bc_type == "free_slip":
                metrics.append(
                    self._metric("free_slip_normal_velocity_error", patch, ["U"])
                )
            elif bc_type == "periodic":
                metrics.append(
                    self._metric("periodic_boundary_mismatch", patch, ["U", "p"])
                )
            elif bc_type in {"inlet_velocity", "velocity_inlet"}:
                metrics.append(self._metric("inlet_profile_error", patch, ["U"]))
            elif bc_type == "no_slip":
                metrics.append(self._metric("no_slip_wall_error", patch, ["U"]))
            elif bc_type in {"outlet_pressure", "pressure_outlet", "outlet_advective"}:
                metrics.append(self._metric("outlet_backflow_ratio", patch, ["U", "phi"]))
        metrics.append({
            "metric_id": "mass_conservation_error",
            "layer": "boundary_verification",
            "patch": "all",
            "required_fields": ["phi"],
            "method": "sum boundary fluxes",
        })
        return metrics

    @staticmethod
    def _metric(metric_id: str, patch: str, fields: list[str]) -> dict[str, Any]:
        return {
            "metric_id": metric_id,
            "layer": "boundary_verification",
            "patch": patch,
            "required_fields": fields,
            "method": metric_id.replace("_", " "),
        }


__all__ = ["BoundaryVerificationCompiler"]
