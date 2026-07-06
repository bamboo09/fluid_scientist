"""Core data models for the MetricSpec system."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base model with strict validation matching experiment_spec conventions."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class MetricCategory(str, Enum):
    """Category of a metric — determines how it is extracted and interpreted."""

    CONVERGENCE = "convergence"
    PHYSICAL = "physical"
    NUMERICAL = "numerical"
    DIMENSIONLESS = "dimensionless"
    DERIVED = "derived"


class MetricDataType(str, Enum):
    """Data type of a metric value."""

    SCALAR = "scalar"
    VECTOR = "vector"
    TIMESERIES = "timeseries"
    FIELD = "field"


class QualityCheckType(str, Enum):
    """Type of quality check applied to a metric or simulation result."""

    RESIDUAL_TOLERANCE = "residual_tolerance"
    MASS_IMBALANCE = "mass_imbalance"
    COURANT_NUMBER = "courant_number"
    GCI = "gci"
    RANGE_CHECK = "range_check"
    CUSTOM = "custom"


class MetricQualityStatus(str, Enum):
    """Status of a quality check result."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    NOT_CHECKED = "not_checked"


class MetricTarget(StrictModel):
    """Target value or range for a metric.

    Exactly one of ``target_value`` or ``range`` must be provided.
    """

    target_value: float | None = None
    range_min: float | None = None
    range_max: float | None = None
    tolerance_pct: float = Field(default=5.0, ge=0, le=100)
    description: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def validate_target(self) -> MetricTarget:
        has_point = self.target_value is not None
        has_range = self.range_min is not None or self.range_max is not None
        if not has_point and not has_range:
            raise ValueError("must provide target_value or range_min/range_max")
        if has_point and has_range:
            raise ValueError("cannot provide both target_value and range")
        if (
            self.range_min is not None
            and self.range_max is not None
            and self.range_min >= self.range_max
        ):
            raise ValueError("range_min must be less than range_max")
        return self


class MetricDefinition(StrictModel):
    """Definition of a metric — what to measure and how.

    Attributes:
        metric_id: Unique identifier within a MetricSpec.
        display_name: Human-readable name.
        category: Metric category (convergence, physical, etc.).
        data_type: Type of the metric value (scalar, vector, etc.).
        unit: Physical unit (e.g., "Pa", "m/s", dimensionless).
        formula: Mathematical formula or extraction method.
        function_object: OpenFOAM functionObject name for extraction.
        description: Detailed description.
        target: Optional target value or range.
        critical: If True, failed quality check blocks result acceptance.
    """

    metric_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=200)
    category: MetricCategory
    data_type: MetricDataType = MetricDataType.SCALAR
    unit: str = Field(default="dimensionless", max_length=50)
    formula: str = Field(default="", max_length=1000)
    function_object: str | None = Field(default=None, max_length=200)
    description: str = Field(default="", max_length=2000)
    target: MetricTarget | None = None
    critical: bool = False


class MetricQualityCheck(StrictModel):
    """A quality check applied to simulation results.

    Attributes:
        check_id: Unique identifier.
        check_type: Type of check (residual, mass imbalance, etc.).
        threshold: Numeric threshold for pass/fail.
        metric_id: Optional metric this check applies to.
        description: Human-readable description.
    """

    check_id: str = Field(min_length=1, max_length=128)
    check_type: QualityCheckType
    threshold: float = Field(ge=0)
    metric_id: str | None = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=500)


class MetricResult(StrictModel):
    """Result of a single metric extraction.

    Attributes:
        metric_id: The metric identifier.
        value: The extracted value (scalar, dict for vector, list for timeseries).
        unit: Unit of the value.
        status: Quality status (passed, warning, failed, not_checked).
        quality_checks: Results of quality checks for this metric.
        timestamp: ISO timestamp of extraction.
        notes: Additional notes.
    """

    metric_id: str = Field(min_length=1, max_length=128)
    value: float | dict[str, Any] | list[Any] | None = None
    unit: str = Field(default="dimensionless", max_length=50)
    status: MetricQualityStatus = MetricQualityStatus.NOT_CHECKED
    quality_checks: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    timestamp: str = Field(default="", max_length=100)
    notes: str = Field(default="", max_length=2000)


class MetricSpec(StrictModel):
    """Complete metric specification for an experiment.

    A MetricSpec defines what metrics to extract from simulation results,
    what quality checks to apply, and what targets to compare against.

    Attributes:
        spec_id: Unique identifier.
        experiment_type: Type of experiment (laminar_pipe, cylinder_flow, etc.).
        metrics: Tuple of MetricDefinitions.
        quality_checks: Tuple of MetricQualityChecks.
        schema_version: Schema version string.
    """

    spec_id: str = Field(min_length=1, max_length=128)
    experiment_type: str = Field(min_length=1, max_length=128)
    metrics: tuple[MetricDefinition, ...] = Field(min_length=1, max_length=100)
    quality_checks: tuple[MetricQualityCheck, ...] = Field(default_factory=tuple, max_length=50)
    schema_version: str = Field(default="1.0.0", max_length=20)

    @model_validator(mode="after")
    def validate_metric_ids(self) -> MetricSpec:
        ids = [m.metric_id for m in self.metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate metric_id values are not allowed")
        for check in self.quality_checks:
            if check.metric_id is not None and check.metric_id not in ids:
                raise ValueError(
                    f"quality check '{check.check_id}' references "
                    f"unknown metric_id '{check.metric_id}'"
                )
        return self

    def get_metric(self, metric_id: str) -> MetricDefinition | None:
        """Get a metric definition by ID."""
        for m in self.metrics:
            if m.metric_id == metric_id:
                return m
        return None

    def critical_metrics(self) -> tuple[MetricDefinition, ...]:
        """Return all critical metric definitions."""
        return tuple(m for m in self.metrics if m.critical)


__all__ = [
    "MetricCategory",
    "MetricDataType",
    "MetricDefinition",
    "MetricQualityCheck",
    "MetricQualityStatus",
    "MetricResult",
    "MetricSpec",
    "MetricTarget",
    "QualityCheckType",
    "StrictModel",
]
