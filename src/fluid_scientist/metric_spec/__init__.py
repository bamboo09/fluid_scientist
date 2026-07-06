"""MetricSpec system — structured metric definitions, quality checks, and results.

Implements P1 requirements: MetricSpec, metric registry, quality checks.
This module provides the deterministic metric layer that feeds into the
LLM-based ResultAnalyst for scientific interpretation.
"""

from fluid_scientist.metric_spec.models import (
    MetricCategory,
    MetricDataType,
    MetricDefinition,
    MetricQualityCheck,
    MetricQualityStatus,
    MetricResult,
    MetricSpec,
    MetricTarget,
    QualityCheckType,
)
from fluid_scientist.metric_spec.quality import (
    QualityCheckOutcome,
    aggregate_status,
    calculate_gci,
    check_courant_number,
    check_gci,
    check_mass_imbalance,
    check_range,
    check_residual_tolerance,
    evaluate_result,
)
from fluid_scientist.metric_spec.registry import (
    get_metric_spec,
    registered_types,
)

__all__ = [
    "MetricCategory",
    "MetricDataType",
    "MetricDefinition",
    "MetricQualityCheck",
    "MetricQualityStatus",
    "MetricResult",
    "MetricSpec",
    "MetricTarget",
    "QualityCheckOutcome",
    "QualityCheckType",
    "aggregate_status",
    "calculate_gci",
    "check_courant_number",
    "check_gci",
    "check_mass_imbalance",
    "check_range",
    "check_residual_tolerance",
    "evaluate_result",
    "get_metric_spec",
    "registered_types",
]
