"""Derived parameter computation engine.

Computes derived parameters (mean_velocity, reynolds_number, etc.) from
their source parameters when all required inputs are available.
"""

from __future__ import annotations

import logging
import math

from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ParameterSource,
    ParameterSourceInfo,
    ParameterSpec,
    ParameterStatus,
)

logger = logging.getLogger(__name__)


def compute_derived_parameters(spec: ExperimentSpec) -> ExperimentSpec:
    """Compute all derivable parameters from their source parameters.

    Only fills in parameters that are currently None or have source type UNKNOWN.
    Does NOT overwrite user-confirmed or already-computed values.
    """
    param_map = {p.parameter_id: p for p in spec.parameters}
    updated = False

    # mean_velocity = mass_flow_rate / (density * pi * (diameter/2)^2)
    # Or: mean_velocity = inlet_velocity (for external flow)
    if "mean_velocity" in param_map:
        p = param_map["mean_velocity"]
        if _should_derive(p):
            mfr = _get_value(param_map, "mass_flow_rate")
            rho = _get_value(param_map, "density")
            d = _get_value(param_map, "diameter")
            if mfr is not None and rho is not None and d is not None:
                area = math.pi * (d / 2) ** 2
                if area > 0 and rho > 0:
                    velocity = mfr / (rho * area)
                    new_p = _make_derived(
                        p, velocity,
                        "\u7531 mass_flow_rate\u3001density\u3001diameter \u63a8\u5bfc",
                    )
                    param_map["mean_velocity"] = new_p
                    updated = True

    # reynolds_number = velocity * diameter / kinematic_viscosity
    if "reynolds_number" in param_map:
        p = param_map["reynolds_number"]
        if _should_derive(p):
            velocity = _get_value(param_map, "mean_velocity") or _get_value(
                param_map, "inlet_velocity"
            )
            d = _get_value(param_map, "diameter") or _get_value(param_map, "side_length")
            nu = _get_value(param_map, "kinematic_viscosity")
            if velocity is not None and d is not None and nu is not None and nu > 0:
                re = velocity * d / nu
                new_p = _make_derived(
                    p, re,
                    "\u7531 velocity\u3001diameter\u3001kinematic_viscosity \u63a8\u5bfc",
                )
                param_map["reynolds_number"] = new_p
                updated = True

    if not updated:
        return spec

    new_params = [param_map.get(p.parameter_id, p) for p in spec.parameters]
    return spec.model_copy(update={"parameters": new_params})


def _should_derive(param: ParameterSpec) -> bool:
    """Check if a parameter should be derived (is None or has unknown source)."""
    if param.value is not None:
        return False
    return param.source.type in (ParameterSource.UNKNOWN, ParameterSource.SYSTEM_RECOMMENDED)


def _get_value(param_map: dict[str, ParameterSpec], param_id: str) -> float | None:
    """Get the numeric value of a parameter, or None."""
    p = param_map.get(param_id)
    if p is None or p.value is None:
        return None
    try:
        return float(p.value)
    except (TypeError, ValueError):
        return None


def _make_derived(
    original: ParameterSpec,
    value: float,
    reason: str,
) -> ParameterSpec:
    """Create a derived version of a parameter."""
    return original.model_copy(
        update={
            "value": value,
            "source": ParameterSourceInfo(
                type=ParameterSource.DERIVED,
                reason=reason,
                confidence="high",
                reference=f"derived:{original.parameter_id}",
            ),
        }
    )


def accept_all_recommendations(spec: ExperimentSpec) -> ExperimentSpec:
    """Accept all system_recommended parameters and compute derived values.

    - system_recommended -> status=ACCEPTED (value kept)
    - derived parameters computed
    - unknown_required parameters remain unchanged (blocking)
    - Returns updated spec
    """
    # First compute derived parameters
    spec = compute_derived_parameters(spec)

    # Then accept all system_recommended
    new_params = []
    for p in spec.parameters:
        if (
            p.source.type == ParameterSource.SYSTEM_RECOMMENDED
            and p.status == ParameterStatus.PENDING
        ):
            new_p = p.model_copy(update={"status": ParameterStatus.ACCEPTED})
            new_params.append(new_p)
        else:
            new_params.append(p)

    spec = spec.model_copy(update={"parameters": new_params})

    # Store acceptance metadata in a simple way - just return the spec
    # The caller can check parameter statuses to see what was accepted
    return spec


__all__ = [
    "compute_derived_parameters",
    "accept_all_recommendations",
]
