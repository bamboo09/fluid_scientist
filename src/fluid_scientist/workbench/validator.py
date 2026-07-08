"""Workbench validator — validates ExperimentSpec for state transitions.

Checks whether the spec can transition to ready, confirmed, or compile
states by examining critical parameters, physics completeness, boundary
conditions, and missing capabilities.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.workbench.edit_models import ValidationResult

# Parameter IDs that represent inlet boundary conditions
_INLET_PARAMS = {
    "inlet_velocity",
    "mean_velocity",
    "lid_velocity",
    "mass_flow_rate",
}

# Parameter IDs that represent outlet boundary conditions
_OUTLET_PARAMS = {
    "outlet_pressure",
}


class WorkbenchValidator:
    """Validates ExperimentSpec for state transitions."""

    def validate(self, spec: dict[str, Any]) -> ValidationResult:
        """Check if spec can transition to ready/confirmed/compile.

        Args:
            spec: The ExperimentSpec as a dict.

        Returns:
            ValidationResult with blocking_issues, warnings, and
            transition capability flags.
        """
        blocking_issues: list[str] = []
        warnings: list[str] = []

        params: list[dict] = spec.get("parameters", [])

        # 1. Check critical parameters resolved
        for p in params:
            criticality = p.get("criticality", "medium")
            source = p.get("source", {})
            source_type = (
                source.get("type", "unknown")
                if isinstance(source, dict)
                else str(source)
            )
            if criticality == "critical" and (
                p.get("value") is None or source_type == "unknown"
            ):
                blocking_issues.append(
                    f"Critical parameter '{p['parameter_id']}' "
                    f"is unresolved (value or source unknown)"
                )

        # 2. Check physics completeness
        physics = spec.get("physics", {})
        if isinstance(physics, dict):
            if not physics.get("compressibility"):
                blocking_issues.append(
                    "compressibility is not set"
                )
            if not physics.get("temporal_type"):
                blocking_issues.append(
                    "temporal_type is not set"
                )
            if not physics.get("phases"):
                blocking_issues.append(
                    "phases is not set"
                )

        # 3. Check boundary conditions (at least inlet + outlet defined)
        has_inlet = any(
            p.get("parameter_id") in _INLET_PARAMS
            and p.get("value") is not None
            for p in params
        )
        has_outlet = any(
            p.get("parameter_id") in _OUTLET_PARAMS
            and p.get("value") is not None
            for p in params
        )

        if not has_inlet:
            warnings.append(
                "No inlet boundary condition has a value defined"
            )
        if not has_outlet:
            warnings.append(
                "No outlet boundary condition has a value defined"
            )

        # 4. Check for blocking missing capabilities
        missing_caps = spec.get("missing_capabilities", [])
        if isinstance(missing_caps, list):
            for cap in missing_caps:
                if isinstance(cap, dict) and cap.get("severity") == "blocking":
                    blocking_issues.append(
                        f"Blocking missing capability: "
                        f"{cap.get('capability_id', 'unknown')}"
                    )

        # 5. Determine transition capabilities
        has_blocking = len(blocking_issues) > 0
        is_valid = not has_blocking

        # Check status for can_confirm / can_compile
        status = spec.get("status", "draft")

        can_transition_to_ready = is_valid
        can_confirm = is_valid and status in ("ready", "draft")
        can_compile = is_valid and status in ("confirmed",)

        return ValidationResult(
            is_valid=is_valid,
            blocking_issues=blocking_issues,
            warnings=warnings,
            can_transition_to_ready=can_transition_to_ready,
            can_confirm=can_confirm,
            can_compile=can_compile,
        )


__all__ = ["WorkbenchValidator"]
