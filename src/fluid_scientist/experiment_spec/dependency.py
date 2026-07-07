"""Parameter dependency graph and propagation.

When a user modifies a parameter, the system must:
1. Automatically recompute derived parameters (no ambiguity).
2. Request user choice when multiple valid options exist.
3. Mark dependent artifacts as stale (mesh, functionObjects, metrics).
4. Produce a change summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fluid_scientist.experiment_spec.models import (
    ConfirmationPolicy,
    Criticality,
    ExperimentSpec,
    ParameterSpec,
)


@dataclass(frozen=True)
class PropagationResult:
    """Result of a parameter change propagation."""

    directly_modified: str
    auto_recomputed: list[str] = field(default_factory=list)
    requires_choice: list[dict[str, Any]] = field(default_factory=list)
    stale_artifacts: list[str] = field(default_factory=list)
    new_warnings: list[str] = field(default_factory=list)
    needs_new_version: bool = False


def propagate_change(
    spec: ExperimentSpec,
    parameter_id: str,
    new_value: float | int | str | bool,
) -> tuple[ExperimentSpec, PropagationResult]:
    """Apply a parameter change and propagate dependencies.

    Returns the updated spec and a propagation result describing
    what was automatically recomputed, what needs user choice,
    and what artifacts are now stale.
    """
    param = spec.get_parameter(parameter_id)
    if param is None:
        raise KeyError(f"parameter {parameter_id} not found")
    if not param.editable:
        raise ValueError(f"parameter {parameter_id} is not editable")

    # Apply the direct change
    updated = spec.update_parameter(parameter_id, new_value)

    # Find all parameters that depend on this one
    auto_recomputed: list[str] = []
    requires_choice: list[dict[str, Any]] = []
    stale: list[str] = []
    warnings: list[str] = []

    for p in updated.parameters:
        if parameter_id in p.dependencies.depends_on:
            if p.source.type == "derived":
                # Auto-recompute placeholder — actual computation
                # would be done by a physics calculator
                auto_recomputed.append(p.parameter_id)
            elif p.confirmation_policy == ConfirmationPolicy.REQUIRE_EXPLICIT:
                requires_choice.append({
                    "parameter_id": p.parameter_id,
                    "display_name": p.display_name,
                    "reason": f"depends on {parameter_id} which was changed",
                    "current_value": p.value,
                })

    # Check for stale artifacts
    _check_stale(param, stale)

    # Check if this requires a new version
    needs_new_version = False
    from fluid_scientist.experiment_spec.state_machine import is_immutable
    if is_immutable(spec.status.value if hasattr(spec.status, 'value') else str(spec.status)):
        needs_new_version = True
        warnings.append("Experiment is in an immutable state; a new version will be created")

    # Warn about critical parameter changes
    if param.criticality == Criticality.CRITICAL:
        warnings.append(
            f"Critical parameter '{param.display_name}' was modified; "
            f"physics review recommended"
        )

    result = PropagationResult(
        directly_modified=parameter_id,
        auto_recomputed=auto_recomputed,
        requires_choice=requires_choice,
        stale_artifacts=stale,
        new_warnings=warnings,
        needs_new_version=needs_new_version,
    )

    return updated, result


def _check_stale(param: ParameterSpec, stale: list[str]) -> None:
    """Check which artifacts are stale after a parameter change."""
    impact = set(param.impact_scope)
    if any(k in impact for k in ("mesh", "grid", "cells")):
        stale.append("mesh")
    if any(k in impact for k in ("function_object", "functionObject", "probe", "sampling")):
        stale.append("functionObjects")
    if any(k in impact for k in ("metric", "indicator", "output")):
        stale.append("metrics")
    if any(k in impact for k in ("time_step", "courant", "end_time")):
        stale.append("control_dict")


def change_summary(result: PropagationResult) -> str:
    """Generate a human-readable change summary."""
    lines = [f"Modified: {result.directly_modified}"]
    if result.auto_recomputed:
        lines.append(f"Auto-updated: {', '.join(result.auto_recomputed)}")
    if result.requires_choice:
        choices = [c["display_name"] for c in result.requires_choice]
        lines.append(f"Needs decision: {', '.join(choices)}")
    if result.stale_artifacts:
        lines.append(f"Stale: {', '.join(result.stale_artifacts)}")
    if result.new_warnings:
        lines.append(f"Warnings: {'; '.join(result.new_warnings)}")
    if result.needs_new_version:
        lines.append("A new experiment version will be created")
    return "\n".join(lines)
