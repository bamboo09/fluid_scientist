"""Analysis engine — extract metrics from simulation results and apply quality checks.

Given raw OpenFOAM simulation data and a MetricSpec, this module extracts
metric values, applies deterministic quality checks, and produces a
structured MetricReport for scientific interpretation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.metric_spec.models import (
    MetricDefinition,
    MetricQualityStatus,
    MetricResult,
    MetricSpec,
    QualityCheckType,
)
from fluid_scientist.metric_spec.quality import (
    QualityCheckOutcome,
    aggregate_status,
    check_courant_number,
    check_gci,
    check_mass_imbalance,
    check_range,
    check_residual_tolerance,
    evaluate_result,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class SimulationData(StrictModel):
    """Raw simulation data extracted from OpenFOAM results.

    Attributes:
        residuals: Field name → final residual value.
        forces: Force/coefficient name → value (e.g., Cd, Cl).
        fluxes: Boundary patch name → mass flow rate [kg/s].
        max_courant: Maximum Courant number observed during simulation.
        probes: Probe name → value or list of values (for profiles).
        gci_value: Pre-computed GCI value if available.
        custom: Additional custom metric values.
    """

    residuals: dict[str, float] = Field(default_factory=dict)
    forces: dict[str, float] = Field(default_factory=dict)
    fluxes: dict[str, float] = Field(default_factory=dict)
    max_courant: float | None = None
    probes: dict[str, float | list[float]] = Field(default_factory=dict)
    gci_value: float | None = None
    custom: dict[str, Any] = Field(default_factory=dict)

    @property
    def max_residual(self) -> float:
        """Maximum residual value across all fields."""
        if not self.residuals:
            return 0.0
        return max(self.residuals.values())

    @property
    def mass_imbalance_pct(self) -> float:
        """Mass imbalance percentage: |net_flux| / total_absolute_flux * 100."""
        if not self.fluxes:
            return 0.0
        total = sum(self.fluxes.values())
        abs_sum = sum(abs(v) for v in self.fluxes.values())
        if abs_sum == 0:
            return 0.0
        return abs(total) / abs_sum * 100


@dataclass
class MetricReport:
    """Structured report of metric extraction and quality check results."""

    spec_id: str
    experiment_type: str
    metric_results: list[MetricResult] = field(default_factory=list)
    quality_check_outcomes: list[QualityCheckOutcome] = field(default_factory=list)
    overall_status: MetricQualityStatus = MetricQualityStatus.NOT_CHECKED
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary for JSON serialization."""
        return {
            "spec_id": self.spec_id,
            "experiment_type": self.experiment_type,
            "overall_status": self.overall_status.value,
            "summary": self.summary,
            "metric_results": [
                r.model_dump(mode="json") for r in self.metric_results
            ],
            "quality_checks": [
                {
                    "check_type": o.check_type.value,
                    "status": o.status.value,
                    "value": o.value,
                    "threshold": o.threshold,
                    "message": o.message,
                }
                for o in self.quality_check_outcomes
            ],
        }


def _extract_metric_value(
    definition: MetricDefinition,
    data: SimulationData,
) -> float | dict[str, Any] | list[Any] | None:
    """Extract a metric value from simulation data based on its definition."""
    mid = definition.metric_id

    # Direct lookup in forces
    if mid in data.forces:
        return data.forces[mid]

    # Direct lookup in custom
    if mid in data.custom:
        return data.custom[mid]

    # Residual tolerance
    if mid == "residual_tolerance":
        return data.max_residual

    # Reynolds number (if provided in custom or forces)
    if mid == "reynolds_number":
        return data.custom.get("reynolds_number")

    # Velocity/pressure profiles from probes
    if definition.data_type.value == "vector" and data.probes:
        matching = {
            k: v for k, v in data.probes.items() if mid in k.lower()
        }
        if matching:
            return matching

    return None


def _run_quality_checks(
    data: SimulationData,
    metric_spec: MetricSpec,
) -> list[QualityCheckOutcome]:
    """Run all applicable quality checks on the simulation data."""
    outcomes: list[QualityCheckOutcome] = []

    for check in metric_spec.quality_checks:
        if check.check_type == QualityCheckType.RESIDUAL_TOLERANCE:
            outcomes.append(
                check_residual_tolerance(data.max_residual, check.threshold)
            )
        elif check.check_type == QualityCheckType.MASS_IMBALANCE:
            outcomes.append(
                check_mass_imbalance(data.mass_imbalance_pct, check.threshold)
            )
        elif check.check_type == QualityCheckType.COURANT_NUMBER:
            if data.max_courant is not None:
                outcomes.append(
                    check_courant_number(data.max_courant, check.threshold)
                )
        elif (
            check.check_type == QualityCheckType.GCI
            and data.gci_value is not None
        ):
            outcomes.append(check_gci(data.gci_value, check.threshold))

    return outcomes


def _build_summary(
    report: MetricReport,
    data: SimulationData,
) -> str:
    """Build a human-readable summary of the analysis."""
    lines = [
        f"Analysis Report for {report.experiment_type}",
        f"  Overall Status: {report.overall_status.value}",
        f"  Metrics Extracted: {len(report.metric_results)}",
        f"  Quality Checks Run: {len(report.quality_check_outcomes)}",
    ]

    if data.residuals:
        lines.append(f"  Max Residual: {data.max_residual:.2e}")

    if data.fluxes:
        lines.append(f"  Mass Imbalance: {data.mass_imbalance_pct:.3f}%")

    if data.max_courant is not None:
        lines.append(f"  Max Courant: {data.max_courant:.3f}")

    failed = [o for o in report.quality_check_outcomes if o.status == MetricQualityStatus.FAILED]
    if failed:
        lines.append(f"  Failed Checks: {len(failed)}")
        for f in failed:
            lines.append(f"    - {f.message}")

    return "\n".join(lines)


def analyze_simulation(
    data: SimulationData,
    metric_spec: MetricSpec,
) -> MetricReport:
    """Analyze simulation data against a MetricSpec.

    Extracts metric values, applies quality checks, and produces a
    structured MetricReport.

    Args:
        data: Raw simulation data from OpenFOAM results.
        metric_spec: Metric specification defining what to extract and check.

    Returns:
        A MetricReport with all results and quality check outcomes.
    """
    quality_outcomes = _run_quality_checks(data, metric_spec)

    metric_results: list[MetricResult] = []
    for definition in metric_spec.metrics:
        value = _extract_metric_value(definition, data)

        # Apply range check if target is defined
        per_metric_outcomes: list[QualityCheckOutcome] = []
        if definition.target is not None and value is not None and isinstance(value, (int, float)):
            per_metric_outcomes.append(
                check_range(float(value), definition.target)
            )

        # Find quality checks specifically for this metric
        for outcome in quality_outcomes:
            for check in metric_spec.quality_checks:
                if check.metric_id == definition.metric_id and \
                   outcome.check_type == check.check_type:
                    per_metric_outcomes.append(outcome)

        result = MetricResult(
            metric_id=definition.metric_id,
            value=value,
            unit=definition.unit,
        )
        result = evaluate_result(result, per_metric_outcomes)
        metric_results.append(result)

    all_outcomes = quality_outcomes
    overall = aggregate_status(all_outcomes)

    report = MetricReport(
        spec_id=metric_spec.spec_id,
        experiment_type=metric_spec.experiment_type,
        metric_results=metric_results,
        quality_check_outcomes=all_outcomes,
        overall_status=overall,
    )
    report.summary = _build_summary(report, data)

    return report


__all__ = [
    "MetricReport",
    "SimulationData",
    "analyze_simulation",
]
