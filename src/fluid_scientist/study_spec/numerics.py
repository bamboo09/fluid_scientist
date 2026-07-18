"""Numerics definitions for the SimulationStudySpec.

This module defines the numerics block: temporal control, solver selection,
discretisation schemes, and the turbulence model.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .quantities import TimeControl

__all__ = ["NumericsDefinition"]

#: Supported turbulence model identifiers.
TurbulenceModel = Literal[
    "laminar",
    "RANS_kEpsilon",
    "RANS_kOmegaSST",
    "LES",
    "DES",
    "DNS",
]


class NumericsDefinition(BaseModel):
    """The numerics definition block.

    Parameters
    ----------
    time:
        Temporal control (mode, start/end time, delta-t, write control, …).
    solver:
        OpenFOAM solver name, e.g. ``"icoFoam"``, ``"simpleFoam"``,
        ``"pimpleFoam"``.
    discretization:
        Free-form dictionary of discretisation schemes
        (e.g. ``{"ddtSchemes": {"ddtScheme": "backward"}}``).
    turbulence_model:
        Turbulence model identifier, or ``None`` for laminar / unspecified.
    """

    model_config = ConfigDict(extra="forbid")

    time: TimeControl
    solver: str
    discretization: dict[str, Any] = Field(default_factory=dict)
    turbulence_model: TurbulenceModel | None = None
