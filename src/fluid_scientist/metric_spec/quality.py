"""Deterministic quality check implementations for simulation results.

These functions evaluate OpenFOAM simulation outputs against the quality
checks defined in a MetricSpec.  They are purely numerical — no LLM involved.
"""

from __future__ import annotations

from dataclasses import dataclass

from fluid_scientist.metric_spec.models import (
    MetricQualityStatus,
    MetricResult,
    MetricTarget,
    QualityCheckType,
)


@dataclass(frozen=True)
class QualityCheckOutcome:
    """Outcome of a single quality check evaluation."""

    check_type: QualityCheckType
    status: MetricQualityStatus
    value: float
    threshold: float
    message: str


def check_residual_tolerance(
    max_residual: float,
    threshold: float = 1e-4,
) -> QualityCheckOutcome:
    """Check if the maximum residual meets the tolerance threshold."""
    if max_residual < 0:
        return QualityCheckOutcome(
            check_type=QualityCheckType.RESIDUAL_TOLERANCE,
            status=MetricQualityStatus.FAILED,
            value=max_residual,
            threshold=threshold,
            message="residual is negative — possible parse error",
        )
    if max_residual <= threshold:
        return QualityCheckOutcome(
            check_type=QualityCheckType.RESIDUAL_TOLERANCE,
            status=MetricQualityStatus.PASSED,
            value=max_residual,
            threshold=threshold,
            message=f"residual {max_residual:.2e} <= {threshold:.2e}",
        )
    if max_residual <= threshold * 10:
        return QualityCheckOutcome(
            check_type=QualityCheckType.RESIDUAL_TOLERANCE,
            status=MetricQualityStatus.WARNING,
            value=max_residual,
            threshold=threshold,
            message=f"residual {max_residual:.2e} within 10x of {threshold:.2e}",
        )
    return QualityCheckOutcome(
        check_type=QualityCheckType.RESIDUAL_TOLERANCE,
        status=MetricQualityStatus.FAILED,
        value=max_residual,
        threshold=threshold,
        message=f"residual {max_residual:.2e} exceeds {threshold:.2e}",
    )


def check_mass_imbalance(
    mass_imbalance_pct: float,
    threshold: float = 1.0,
) -> QualityCheckOutcome:
    """Check if the mass imbalance percentage is within acceptable range."""
    abs_imbalance = abs(mass_imbalance_pct)
    if abs_imbalance <= threshold:
        return QualityCheckOutcome(
            check_type=QualityCheckType.MASS_IMBALANCE,
            status=MetricQualityStatus.PASSED,
            value=mass_imbalance_pct,
            threshold=threshold,
            message=f"mass imbalance {mass_imbalance_pct:.3f}% <= {threshold}%",
        )
    if abs_imbalance <= threshold * 2:
        return QualityCheckOutcome(
            check_type=QualityCheckType.MASS_IMBALANCE,
            status=MetricQualityStatus.WARNING,
            value=mass_imbalance_pct,
            threshold=threshold,
            message=f"mass imbalance {mass_imbalance_pct:.3f}% within 2x of {threshold}%",
        )
    return QualityCheckOutcome(
        check_type=QualityCheckType.MASS_IMBALANCE,
        status=MetricQualityStatus.FAILED,
        value=mass_imbalance_pct,
        threshold=threshold,
        message=f"mass imbalance {mass_imbalance_pct:.3f}% exceeds {threshold}%",
    )


def check_courant_number(
    max_courant: float,
    threshold: float = 1.0,
) -> QualityCheckOutcome:
    """Check if the maximum Courant number is within acceptable range."""
    if max_courant <= threshold:
        return QualityCheckOutcome(
            check_type=QualityCheckType.COURANT_NUMBER,
            status=MetricQualityStatus.PASSED,
            value=max_courant,
            threshold=threshold,
            message=f"max Courant {max_courant:.3f} <= {threshold}",
        )
    if max_courant <= threshold * 2:
        return QualityCheckOutcome(
            check_type=QualityCheckType.COURANT_NUMBER,
            status=MetricQualityStatus.WARNING,
            value=max_courant,
            threshold=threshold,
            message=f"max Courant {max_courant:.3f} within 2x of {threshold}",
        )
    return QualityCheckOutcome(
        check_type=QualityCheckType.COURANT_NUMBER,
        status=MetricQualityStatus.FAILED,
        value=max_courant,
        threshold=threshold,
        message=f"max Courant {max_courant:.3f} exceeds {threshold}",
    )


def calculate_gci(
    fine_value: float,
    coarse_value: float,
    grid_ratio: float = 2.0,
    order: float = 2.0,
    safety_factor: float = 1.25,
) -> float:
    """Calculate the Grid Convergence Index (GCI).

    GCI = Fs * |epsilon| / (r^p - 1)

    where:
        Fs = safety factor (1.25 for 3 grids, 3.0 for 2 grids)
        epsilon = (coarse - fine) / fine
        r = grid refinement ratio
        p = observed order of accuracy
    """
    if fine_value == 0:
        raise ValueError("fine_value cannot be zero for GCI calculation")
    if grid_ratio <= 1:
        raise ValueError("grid_ratio must be greater than 1")
    if order <= 0:
        raise ValueError("order must be positive")

    epsilon = abs(coarse_value - fine_value) / abs(fine_value)
    denominator = grid_ratio**order - 1
    return safety_factor * epsilon / denominator


def check_gci(
    gci_value: float,
    threshold: float = 0.05,
) -> QualityCheckOutcome:
    """Check if the GCI value indicates grid independence."""
    if gci_value <= threshold:
        return QualityCheckOutcome(
            check_type=QualityCheckType.GCI,
            status=MetricQualityStatus.PASSED,
            value=gci_value,
            threshold=threshold,
            message=f"GCI {gci_value:.4f} <= {threshold}",
        )
    if gci_value <= threshold * 2:
        return QualityCheckOutcome(
            check_type=QualityCheckType.GCI,
            status=MetricQualityStatus.WARNING,
            value=gci_value,
            threshold=threshold,
            message=f"GCI {gci_value:.4f} within 2x of {threshold}",
        )
    return QualityCheckOutcome(
        check_type=QualityCheckType.GCI,
        status=MetricQualityStatus.FAILED,
        value=gci_value,
        threshold=threshold,
        message=f"GCI {gci_value:.4f} exceeds {threshold}",
    )


def check_range(
    value: float,
    target: MetricTarget,
) -> QualityCheckOutcome:
    """Check if a value meets the target range or point tolerance."""
    if target.target_value is not None:
        tolerance = abs(target.target_value) * target.tolerance_pct / 100
        diff = abs(value - target.target_value)
        if diff <= tolerance:
            return QualityCheckOutcome(
                check_type=QualityCheckType.RANGE_CHECK,
                status=MetricQualityStatus.PASSED,
                value=value,
                threshold=target.target_value,
                message=f"value {value} within tolerance of {target.target_value}",
            )
        return QualityCheckOutcome(
            check_type=QualityCheckType.RANGE_CHECK,
            status=MetricQualityStatus.FAILED,
            value=value,
            threshold=target.target_value,
            message=f"value {value} outside tolerance of {target.target_value}",
        )

    lo = target.range_min
    hi = target.range_max
    if lo is not None and value < lo:
        return QualityCheckOutcome(
            check_type=QualityCheckType.RANGE_CHECK,
            status=MetricQualityStatus.FAILED,
            value=value,
            threshold=lo if lo is not None else 0,
            message=f"value {value} below minimum {lo}",
        )
    if hi is not None and value > hi:
        return QualityCheckOutcome(
            check_type=QualityCheckType.RANGE_CHECK,
            status=MetricQualityStatus.FAILED,
            value=value,
            threshold=hi if hi is not None else 0,
            message=f"value {value} above maximum {hi}",
        )
    return QualityCheckOutcome(
        check_type=QualityCheckType.RANGE_CHECK,
        status=MetricQualityStatus.PASSED,
        value=value,
        threshold=0,
        message=f"value {value} within range [{lo}, {hi}]",
    )


def aggregate_status(
    outcomes: list[QualityCheckOutcome],
) -> MetricQualityStatus:
    """Aggregate multiple quality check outcomes into a single status.

    Priority: FAILED > WARNING > PASSED > NOT_CHECKED
    """
    if not outcomes:
        return MetricQualityStatus.NOT_CHECKED
    statuses = {o.status for o in outcomes}
    if MetricQualityStatus.FAILED in statuses:
        return MetricQualityStatus.FAILED
    if MetricQualityStatus.WARNING in statuses:
        return MetricQualityStatus.WARNING
    if MetricQualityStatus.PASSED in statuses:
        return MetricQualityStatus.PASSED
    return MetricQualityStatus.NOT_CHECKED


def evaluate_result(
    result: MetricResult,
    outcomes: list[QualityCheckOutcome],
) -> MetricResult:
    """Attach quality check outcomes to a MetricResult."""
    status = aggregate_status(outcomes)
    messages = tuple(o.message for o in outcomes) if outcomes else ()
    return result.model_copy(
        update={
            "status": status,
            "quality_checks": messages,
        }
    )


__all__ = [
    "QualityCheckOutcome",
    "aggregate_status",
    "calculate_gci",
    "check_courant_number",
    "check_gci",
    "check_mass_imbalance",
    "check_range",
    "check_residual_tolerance",
    "evaluate_result",
]
