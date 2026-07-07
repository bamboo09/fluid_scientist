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
    LineSamplingSpec,
    MeasurementPlan,
    MetricBinding,
    ProbeSpec,
    SpatialSamplingSpec,
    SpatialSamplingType,
    StorageEstimate,
    TimeSamplingSpec,
    VolumeSamplingSpec,
)
from fluid_scientist.measurement.planner import (
    MetricPlan,
    MetricPlanner,
)
from fluid_scientist.measurement.time_sampler import (
    PhysicalContext,
    TimeSampler,
    estimate_vortex_shedding_frequency,
)

__all__ = [
    "FieldOutputSpec",
    "FunctionObjectSpec",
    "FunctionObjectType",
    "LineSamplingSpec",
    "MeasurementPlan",
    "MetricBinding",
    "MetricPlan",
    "MetricPlanner",
    "PhysicalContext",
    "ProbeSpec",
    "SpatialSamplingSpec",
    "SpatialSamplingType",
    "StorageEstimate",
    "TimeSampler",
    "TimeSamplingSpec",
    "VolumeSamplingSpec",
    "estimate_vortex_shedding_frequency",
]
