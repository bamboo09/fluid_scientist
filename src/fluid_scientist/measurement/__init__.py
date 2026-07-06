"""MeasurementPlan module — metric-driven in-simulation sampling configuration.

The ``MeasurementPlan`` describes what field variables, functionObjects,
spatial sampling locations, and time sampling a single simulation should
produce so that the metrics requested by the researcher can be extracted.
This is distinct from ``DOEPlan`` (formerly ``SamplingPlan``), which
describes the design-of-experiments matrix across multiple simulation runs.
"""

from fluid_scientist.measurement.models import (
    FieldOutputSpec,
    FunctionObjectSpec,
    FunctionObjectType,
    MeasurementPlan,
    MetricBinding,
    SpatialSamplingSpec,
    SpatialSamplingType,
    TimeSamplingSpec,
)
from fluid_scientist.measurement.planner import (
    MetricPlan,
    MetricPlanner,
)

__all__ = [
    "FieldOutputSpec",
    "FunctionObjectSpec",
    "FunctionObjectType",
    "MeasurementPlan",
    "MetricBinding",
    "MetricPlan",
    "MetricPlanner",
    "SpatialSamplingSpec",
    "SpatialSamplingType",
    "TimeSamplingSpec",
]
