"""Observation definitions for the SimulationStudySpec.

This module defines what the simulation should *measure* — the observation
targets (drag coefficient, Strouhal number, point velocity, …), probe
locations, statistics time windows, and post-processing function objects.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .quantities import TimeWindow

__all__ = [
    "ObservationDefinition",
    "ObservationTarget",
    "ProbeSpec",
]

#: Supported observation metrics.
ObservationMetric = Literal[
    "cd",
    "cl",
    "strouhal",
    "point_velocity",
    "section_mean_velocity",
    "wall_shear",
    "y_plus",
    "vorticity",
    "pressure_field",
    "velocity_field",
    "custom",
]


class ObservationTarget(BaseModel):
    """A single observation target (what to measure).

    Parameters
    ----------
    target_id:
        Unique identifier for the target.
    metric:
        The metric type, e.g. ``"cd"`` (drag coefficient), ``"cl"`` (lift
        coefficient), ``"strouhal"``, ``"point_velocity"`` …
    parameters:
        Free-form parameters for the metric (probe location, wall name,
        component, …).
    function_object_type:
        The OpenFOAM function-object type that realises this metric, e.g.
        ``"forceCoeffs"``, ``"probes"``, ``"fieldAverage"``.
    """

    model_config = ConfigDict(extra="forbid")

    target_id: str
    metric: ObservationMetric
    parameters: dict[str, Any] = Field(default_factory=dict)
    function_object_type: str | None = None


class ProbeSpec(BaseModel):
    """A single point probe.

    Parameters
    ----------
    probe_id:
        Unique identifier for the probe.
    location:
        Dict with ``x``, ``y``, ``z`` keys giving the probe coordinates.
    field:
        The field to sample, e.g. ``"U"``, ``"p"``.
    """

    model_config = ConfigDict(extra="forbid")

    probe_id: str
    location: dict[str, float]
    field: str


class ObservationDefinition(BaseModel):
    """The complete observation definition.

    Parameters
    ----------
    targets:
        List of observation targets (metrics to compute).
    probes:
        List of point probes.
    statistics_windows:
        Optional time windows over which statistics should be averaged.
    postprocessing:
        List of post-processing function-object names to enable.
    """

    model_config = ConfigDict(extra="forbid")

    targets: list[ObservationTarget] = Field(default_factory=list)
    probes: list[ProbeSpec] = Field(default_factory=list)
    statistics_windows: list[TimeWindow] | None = None
    postprocessing: list[str] = Field(default_factory=list)
