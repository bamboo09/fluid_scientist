"""Close ordinary missing design values with traceable defaults."""

from __future__ import annotations

from fluid_scientist.workbench.experiment_design_synthesizer import (
    DesignField,
    ExperimentDesign,
)


class DesignClosureEngine:
    """Fill a synthesized design into a draftable experiment specification."""

    def close(self, design: ExperimentDesign) -> ExperimentDesign:
        closed = design.model_copy(deep=True)
        re_field = closed.dimensionless_parameters.get("Re")
        re_value = float(re_field.value) if re_field and re_field.value else 3900.0
        closed.dimensionless_parameters["Re"] = DesignField(
            value=re_value,
            source=re_field.source if re_field else "TEMPLATE_DEFAULT",
            reason=re_field.reason if re_field else "Default turbulent benchmark Re.",
            confidence=re_field.confidence if re_field else 0.65,
        )
        closed.dimensionless_parameters.setdefault(
            "Co",
            DesignField(
                value=0.5,
                source="SYSTEM_SELECTED",
                reason="Courant limit selected for stable transient integration.",
                confidence=0.9,
            ),
        )
        closed.dimensionless_parameters.setdefault(
            "target_y_plus",
            DesignField(
                value=1.0 if re_value >= 3000 else 30.0,
                source="SYSTEM_SELECTED",
                reason="Near-wall target selected from turbulence model.",
                confidence=0.85,
            ),
        )
        closed.material_properties.setdefault(
            "nu",
            DesignField(
                value=1.0 / re_value,
                unit="m2/s",
                source="SYSTEM_DERIVED",
                reason="nu = U_ref * D / Re with U_ref=1 and D=1.",
                confidence=0.95,
            ),
        )
        closed.solver = closed.solver or {
            "name": "pimpleFoam",
            "source": "SYSTEM_SELECTED",
            "reason": "Transient incompressible single-phase flow.",
        }
        closed.turbulence_model = closed.turbulence_model or {
            "model": "LES" if re_value >= 3000 else "laminar",
            "source": "SYSTEM_SELECTED",
            "reason": "Selected from Reynolds number and transient research goals.",
        }
        closed.numerical_schemes = closed.numerical_schemes or {
            "time": "backward",
            "gradient": "Gauss linear",
            "divergence": "bounded Gauss linearUpwind",
            "laplacian": "Gauss linear corrected",
            "source": "TEMPLATE_DEFAULT",
        }
        closed.pressure_velocity_coupling = closed.pressure_velocity_coupling or {
            "algorithm": "PIMPLE",
            "n_outer_correctors": 2,
            "source": "SYSTEM_SELECTED",
        }
        closed.mesh_strategy = closed.mesh_strategy or {
            "base_resolution": "domain-dependent structured/hexa mesh",
            "near_body_refinement": True,
            "target_cells": 500000,
            "reference_area": "D^2",
            "source": "SYSTEM_SELECTED",
        }
        closed.near_wall_strategy = closed.near_wall_strategy or {
            "target_y_plus": 1.0 if closed.turbulence_model.get("model") == "LES" else 30.0,
            "wall_layers": 12,
            "source": "SYSTEM_SELECTED",
        }
        closed.time_control = closed.time_control or {
            "delta_t": 0.002,
            "max_co": 0.5,
            "end_time": 200.0,
            "flow_through_time": 20.0,
            "statistical_cycles": 100,
            "source": "SYSTEM_SELECTED",
        }
        closed.sampling_strategy = closed.sampling_strategy or {
            "start_time": 50.0,
            "sample_interval": 0.01,
            "sampling_frequency": 100.0,
            "minimum_flow_through_times": 100,
            "source": "SYSTEM_SELECTED",
        }
        closed.output_control = closed.output_control or {
            "fields": ["U", "p", "vorticity"],
            "write_interval": 100,
            "source": "SYSTEM_SELECTED",
        }
        closed.post_processing = closed.post_processing or {
            "vortex_identification": "Q_criterion",
            "statistics": ["mean", "rms", "spectra"],
            "source": "SYSTEM_SELECTED",
        }
        closed.compute_resources = closed.compute_resources or {
            "parallel_ranks": 8,
            "memory_gb": 32,
            "estimated_runtime": "medium",
            "source": "SYSTEM_SELECTED",
        }
        return closed


__all__ = ["DesignClosureEngine"]
