"""MetricSpec system — structured metric definitions, quality checks, and results.

Implements P1 requirements: MetricSpec, metric registry, quality checks,
sampling plan mapping, and analysis engine.
"""

from fluid_scientist.metric_spec.analysis import (
    MetricReport,
    SimulationData,
    analyze_simulation,
)
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
from fluid_scientist.metric_spec.sampling import (
    DOEConfig,
    DOEPlan,
    SamplePoint,
    SamplingConfig,
    SamplingPlan,
    SamplingStrategy,
    create_spec_variant,
    generate_doe_plan,
    generate_sampling_plan,
)

__all__ = [
    "DOEConfig",
    "DOEPlan",
    "MetricCategory",
    "MetricDataType",
    "MetricDefinition",
    "MetricQualityCheck",
    "MetricQualityStatus",
    "MetricReport",
    "MetricResult",
    "MetricSpec",
    "MetricTarget",
    "QualityCheckOutcome",
    "QualityCheckType",
    "SamplePoint",
    "SamplingConfig",
    "SamplingPlan",
    "SamplingStrategy",
    "SimulationData",
    "aggregate_status",
    "analyze_simulation",
    "calculate_gci",
    "check_courant_number",
    "check_gci",
    "check_mass_imbalance",
    "check_range",
    "check_residual_tolerance",
    "create_spec_variant",
    "evaluate_result",
    "generate_doe_plan",
    "generate_sampling_plan",
    "get_metric_spec",
    "registered_types",
]
