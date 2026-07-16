"""Boundary condition definitions for the SimulationStudySpec.

This module defines the boundary-condition block of the spec.  Each
:class:`BoundaryCondition` maps a named OpenFOAM patch to a semantic role
and a concrete boundary-condition type with parameters and source status.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BoundaryCondition",
    "BoundaryDefinition",
]

#: The canonical source-status hierarchy for boundary conditions.
BoundarySourceStatus = Literal[
    "user_explicit",
    "user_confirmed",
    "model_recommended",
    "derived",
    "default_pending",
    "unknown",
]

#: Semantic patch roles.
BoundaryRole = Literal[
    "inlet",
    "outlet",
    "wall",
    "freestream",
    "symmetry",
    "cyclic",
    "empty",
    "wedge",
    "custom",
]


class BoundaryCondition(BaseModel):
    """A single boundary condition on a named patch.

    Parameters
    ----------
    patch_name:
        The OpenFOAM patch name, e.g. ``"inlet"``, ``"outlet"``,
        ``"cylinder"``, ``"top"``.
    role:
        Semantic role of the patch.
    bc_type:
        The concrete boundary-condition type, e.g. ``"velocityInlet"``,
        ``"pressureOutlet"``, ``"noSlipWall"``, ``"slipWall"``.
    parameters:
        Free-form parameters for the BC (velocity value, pressure value,
        etc.).
    source_status:
        Provenance status using the same hierarchy as :class:`SourcedValue`.
    """

    model_config = ConfigDict(extra="forbid")

    patch_name: str
    role: BoundaryRole
    bc_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_status: BoundarySourceStatus = "unknown"


class BoundaryDefinition(BaseModel):
    """The complete boundary definition: a list of boundary conditions."""

    model_config = ConfigDict(extra="forbid")

    conditions: list[BoundaryCondition] = Field(default_factory=list)
