"""Pass 6: Observable Decomposer -- convert goals to structured observables.

The :class:`ObservableDecomposer` takes the extracted facts (which include
observables detected from user text) and the physics decomposition, and
produces a structured :class:`ObservableDecomposition` where each
observable is expanded with:

* ``required_fields`` -- the OpenFOAM fields needed for measurement.
* ``sampling`` -- where, when, and at what frequency to sample.
* ``analysis`` -- the post-processing method to apply.
* ``capability_status`` -- whether the capability is SUPPORTED,
  EXTENDABLE, or REQUIRES_NEW_PHYSICS.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.llm_pipeline.models import (
    ExtractedFact,
    ObservableDecomposition,
    PhysicsDecomposition,
)

# ---------------------------------------------------------------------------
# Observable definition catalog
# ---------------------------------------------------------------------------
# Each entry defines how to structure an observable of a given semantic type.
# ---------------------------------------------------------------------------

_OBSERVABLE_CATALOG: dict[str, dict[str, Any]] = {
    "drag_coefficient": {
        "required_fields": ["U", "p"],
        "sampling": {
            "type": "surface_integration",
            "target": "body_surface",
            "frequency": "every_time_step",
            "stage": "measurement",
        },
        "analysis": {
            "method": "force_integration",
            "formula": "Cd = 2 * F_drag / (rho * U_ref^2 * A_ref)",
            "output": "drag_coefficient_time_history",
        },
        "capability_status": "SUPPORTED",
    },
    "lift_coefficient": {
        "required_fields": ["U", "p"],
        "sampling": {
            "type": "surface_integration",
            "target": "body_surface",
            "frequency": "every_time_step",
            "stage": "measurement",
        },
        "analysis": {
            "method": "force_integration",
            "formula": "Cl = 2 * F_lift / (rho * U_ref^2 * A_ref)",
            "output": "lift_coefficient_time_history",
        },
        "capability_status": "SUPPORTED",
    },
    "frequency_spectrum": {
        "required_fields": ["U"],
        "sampling": {
            "type": "point_probe",
            "target": "wake_region",
            "frequency": "every_time_step",
            "stage": "measurement",
        },
        "analysis": {
            "method": "fast_fourier_transform",
            "parameters": {
                "window": "hann",
                "overlap": 0.5,
                "output": "power_spectral_density",
            },
            "output": "frequency_spectrum",
        },
        "capability_status": "SUPPORTED",
    },
    "vortex_shedding": {
        "required_fields": ["U"],
        "sampling": {
            "type": "point_probe_array",
            "target": "wake_region",
            "frequency": "every_time_step",
            "stage": "measurement",
        },
        "analysis": {
            "method": "vortex_identification",
            "parameters": {
                "criterion": "q_criterion",
                "threshold": "auto",
            },
            "output": "strouhal_number_and_shedding_pattern",
        },
        "capability_status": "SUPPORTED",
    },
    "wake_analysis": {
        "required_fields": ["U"],
        "sampling": {
            "type": "plane_slice",
            "target": "downstream_cross_section",
            "frequency": "periodic",
            "interval": "flow_through_time / 10",
            "stage": "measurement",
        },
        "analysis": {
            "method": "wake_profile_extraction",
            "parameters": {
                "extract": ["velocity_profile", "turbulence_intensity"],
            },
            "output": "wake_deficit_profile",
        },
        "capability_status": "SUPPORTED",
    },
    "wall_heat_flux": {
        "required_fields": ["T", "U"],
        "sampling": {
            "type": "surface_integration",
            "target": "heated_wall",
            "frequency": "every_time_step",
            "stage": "measurement",
        },
        "analysis": {
            "method": "heat_flux_integration",
            "formula": "q = -k * dT/dn",
            "output": "wall_heat_flux_distribution",
        },
        "capability_status": "SUPPORTED",
    },
    "wall_shear_stress": {
        "required_fields": ["U"],
        "sampling": {
            "type": "surface_field",
            "target": "wall_surface",
            "frequency": "every_time_step",
            "stage": "measurement",
        },
        "analysis": {
            "method": "wall_shear_extraction",
            "formula": "tau_w = mu * dU/dn_at_wall",
            "output": "wall_shear_stress_distribution",
        },
        "capability_status": "SUPPORTED",
    },
    "pressure_coefficient": {
        "required_fields": ["p"],
        "sampling": {
            "type": "surface_field",
            "target": "body_surface",
            "frequency": "every_time_step",
            "stage": "measurement",
        },
        "analysis": {
            "method": "pressure_normalization",
            "formula": "Cp = (p - p_inf) / (0.5 * rho * U_ref^2)",
            "output": "pressure_coefficient_distribution",
        },
        "capability_status": "SUPPORTED",
    },
    "nusselt_number": {
        "required_fields": ["T", "U"],
        "sampling": {
            "type": "surface_integration",
            "target": "heated_wall",
            "frequency": "every_time_step",
            "stage": "measurement",
        },
        "analysis": {
            "method": "nusselt_calculation",
            "formula": "Nu = h * L_ref / k",
            "output": "nusselt_number_distribution",
        },
        "capability_status": "SUPPORTED",
    },
}


class ObservableDecomposer:
    """Convert scientific goals to structured observables.

    For each observable detected in the extracted facts, the decomposer
    looks up the observable's semantic type in the catalog and produces
    a structured definition with required fields, sampling info, analysis
    method, and capability status.

    The physics decomposition is consulted to adjust capability status
    (e.g. if heat transfer is not enabled but a thermal observable is
    requested, the status becomes ``REQUIRES_NEW_PHYSICS``).
    """

    def decompose(
        self,
        facts: list[ExtractedFact],
        physics: PhysicsDecomposition,
    ) -> ObservableDecomposition:
        """Decompose observables from facts and physics.

        Args:
            facts: The list of facts extracted in Pass 1.
            physics: The physics decomposition from Pass 5.

        Returns:
            An :class:`ObservableDecomposition` with structured
            observable definitions.
        """
        observables: list[dict[str, Any]] = []
        seen_types: set[str] = set()

        for fact in facts:
            if fact.category != "observable":
                continue
            obs_type = str(fact.value) if fact.value else ""
            if not obs_type or obs_type in seen_types:
                continue

            catalog_entry = _OBSERVABLE_CATALOG.get(obs_type)
            if catalog_entry is None:
                # Unknown observable -- mark as requiring new physics.
                observables.append({
                    "id": f"OBS_{len(observables) + 1}",
                    "semantic_type": obs_type,
                    "target_region": "",
                    "required_fields": [],
                    "sampling": {},
                    "analysis": {},
                    "capability_status": "REQUIRES_NEW_PHYSICS",
                    "source_fact_id": fact.fact_id,
                })
                seen_types.add(obs_type)
                continue

            # Determine capability status adjusted for physics.
            capability_status = self._adjust_capability_status(
                obs_type, catalog_entry["capability_status"], physics
            )

            observables.append({
                "id": f"OBS_{len(observables) + 1}",
                "semantic_type": obs_type,
                "target_region": self._determine_target_region(obs_type, fact),
                "required_fields": list(catalog_entry["required_fields"]),
                "sampling": dict(catalog_entry["sampling"]),
                "analysis": dict(catalog_entry["analysis"]),
                "capability_status": capability_status,
                "source_fact_id": fact.fact_id,
            })
            seen_types.add(obs_type)

        return ObservableDecomposition(observables=observables)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _adjust_capability_status(
        self,
        obs_type: str,
        base_status: str,
        physics: PhysicsDecomposition,
    ) -> str:
        """Adjust capability status based on physics configuration.

        Some observables require specific physics to be enabled.  If the
        physics is not active, the status is downgraded.
        """
        # Thermal observables require heat transfer.
        thermal_observables = {"wall_heat_flux", "nusselt_number"}
        if obs_type in thermal_observables and not physics.heat_transfer:
            return "REQUIRES_NEW_PHYSICS"

        # Transient observables require transient mode.
        transient_observables = {"frequency_spectrum", "vortex_shedding"}
        if obs_type in transient_observables and physics.time_mode == "steady":
            return "REQUIRES_NEW_PHYSICS"

        # If physics involves multiphase but observable is single-phase,
        # mark as EXTENDABLE.
        if physics.multiphase and base_status == "SUPPORTED":
            return "EXTENDABLE"

        return base_status

    def _determine_target_region(
        self, obs_type: str, fact: ExtractedFact
    ) -> str:
        """Determine the target region for an observable."""
        # Check if the fact's source_location mentions a specific region.
        if fact.source_location:
            return "domain"
        # Default regions based on observable type.
        region_map: dict[str, str] = {
            "drag_coefficient": "body_surface",
            "lift_coefficient": "body_surface",
            "frequency_spectrum": "wake_region",
            "vortex_shedding": "wake_region",
            "wake_analysis": "downstream_cross_section",
            "wall_heat_flux": "heated_wall",
            "wall_shear_stress": "wall_surface",
            "pressure_coefficient": "body_surface",
            "nusselt_number": "heated_wall",
        }
        return region_map.get(obs_type, "domain")


__all__ = ["ObservableDecomposer"]
