"""Derivation engine — propagates parameter changes and computes derived values.

When a parameter changes, dependent derived parameters (e.g. Reynolds number,
mean velocity) must be recomputed.  This engine applies the derivation rules
deterministically.
"""

from __future__ import annotations

import math
from typing import Any


class DerivationEngine:
    """Propagates parameter changes and computes derived values."""

    DERIVATION_RULES: dict[str, dict[str, Any]] = {
        "mean_velocity": {
            "from": ["mass_flow_rate", "density", "diameter"],
            "formula": "m_dot / (rho * pi * D^2 / 4)",
        },
        "reynolds_number": {
            "from": ["mean_velocity", "diameter", "kinematic_viscosity"],
            "formula": "U * D / nu",
        },
        "reynolds_number_from_inlet": {
            "from": ["inlet_velocity", "diameter", "kinematic_viscosity"],
            "formula": "U * D / nu",
        },
        "area": {
            "from": ["diameter"],
            "formula": "pi * D^2 / 4",
        },
    }

    def propagate(
        self,
        spec: dict,
        changed_parameter_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Propagate changes and return derived updates.

        For each derivation rule, checks if all source parameters have
        values.  If a changed parameter is a source, recomputes the
        derived parameter.

        Args:
            spec: The current ExperimentSpec as a dict.
            changed_parameter_ids: IDs of parameters that were directly
                modified.

        Returns:
            List of dicts with keys: parameter_id, old_value, new_value,
            reason, derived_from.
        """
        params = {
            p["parameter_id"]: p
            for p in spec.get("parameters", [])
        }
        updates: list[dict[str, Any]] = []

        for derived_id, rule in self.DERIVATION_RULES.items():
            source_ids: list[str] = rule["from"]

            # Check if any changed parameter is a source for this rule
            if not any(
                sid in changed_parameter_ids for sid in source_ids
            ):
                continue

            # Check if all source parameters have values
            source_values: dict[str, float] = {}
            all_available = True
            for sid in source_ids:
                p = params.get(sid)
                if p is None or p.get("value") is None:
                    all_available = False
                    break
                try:
                    source_values[sid] = float(p["value"])
                except (TypeError, ValueError):
                    all_available = False
                    break

            if not all_available:
                continue

            # Find the derived parameter in the spec
            # reynolds_number_from_inlet maps to reynolds_number param
            target_param = params.get(derived_id)
            if target_param is None:
                if derived_id == "reynolds_number_from_inlet":
                    target_param = params.get("reynolds_number")
                if target_param is None:
                    continue

            old_value = target_param.get("value")

            # Compute new value
            new_value = self._compute(derived_id, source_values)
            if new_value is None:
                continue

            updates.append({
                "parameter_id": target_param["parameter_id"],
                "old_value": old_value,
                "new_value": new_value,
                "reason": f"\u7531 {', '.join(source_ids)} \u63a8\u5bfc",
                "derived_from": list(source_ids),
            })

        return updates

    @staticmethod
    def _compute(
        derived_id: str,
        values: dict[str, float],
    ) -> float | None:
        """Compute a derived value from source values."""
        if derived_id == "mean_velocity":
            m_dot = values.get("mass_flow_rate")
            rho = values.get("density")
            d = values.get("diameter")
            if m_dot is not None and rho is not None and d is not None:
                area = math.pi * (d / 2) ** 2
                if area > 0 and rho > 0:
                    return m_dot / (rho * area)
        elif derived_id in (
            "reynolds_number",
            "reynolds_number_from_inlet",
        ):
            velocity = values.get("mean_velocity")
            if velocity is None:
                velocity = values.get("inlet_velocity")
            d = values.get("diameter")
            nu = values.get("kinematic_viscosity")
            if (
                velocity is not None
                and d is not None
                and nu is not None
                and nu > 0
            ):
                return velocity * d / nu
        elif derived_id == "area":
            d = values.get("diameter")
            if d is not None:
                return math.pi * (d / 2) ** 2
        return None


__all__ = ["DerivationEngine"]
