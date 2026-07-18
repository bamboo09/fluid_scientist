"""Top-level :class:`SimulationStudySpec` and its sub-models.

The :class:`SimulationStudySpec` is the canonical, versioned specification
for a CFD simulation study.  It aggregates study metadata, physics,
geometry, boundaries, initial conditions, numerics, mesh, observations,
execution, validation, and provenance into a single Pydantic v2 model.

This model is the **single source of truth** that the compiler reads when
producing OpenFOAM case files.  It is designed to be:

* **Versioned** — every mutation creates a new version via
  :class:`~fluid_scientist.study_spec.versioning.VersionedSpecStore`.
* **Provenance-tracked** — every value can carry a :class:`SourcedValue`
  with source hierarchy and confidence.
* **Extensible** — the ``extensions`` dict allows forward-compatible
  additions without schema changes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .boundaries import BoundaryDefinition
from .geometry import GeometryDefinition
from .numerics import NumericsDefinition
from .observations import ObservationDefinition
from .provenance import SpecProvenance
from .quantities import SourcedValue

__all__ = [
    "ExecutionDefinition",
    "MeshDefinition",
    "PhysicsDefinition",
    "SimulationStudySpec",
    "StudyDefinition",
    "ValidationDefinition",
]


# ---------------------------------------------------------------------------
# Study metadata
# ---------------------------------------------------------------------------


class StudyDefinition(BaseModel):
    """Human-readable study metadata.

    Parameters
    ----------
    title:
        Short title of the study.
    objective:
        One-sentence scientific objective.
    research_questions:
        List of specific research questions the study addresses.
    """

    model_config = ConfigDict(extra="forbid")

    title: str
    objective: str
    research_questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Physics
# ---------------------------------------------------------------------------


class PhysicsDefinition(BaseModel):
    """Physics and material properties.

    Parameters
    ----------
    material:
        The fluid material identifier (e.g. ``"water"``).
    density:
        Fluid density.  Optional — may be derived.
    kinematic_viscosity:
        Kinematic viscosity.  Optional — may be derived.
    reynolds_number:
        Target Reynolds number.  Optional — may be derived.
    velocity:
        Characteristic velocity.  Optional — may be derived.
    characteristic_length:
        Characteristic length.  Optional — may be derived.
    """

    model_config = ConfigDict(extra="forbid")

    material: SourcedValue
    density: SourcedValue | None = None
    kinematic_viscosity: SourcedValue | None = None
    reynolds_number: SourcedValue | None = None
    velocity: SourcedValue | None = None
    characteristic_length: SourcedValue | None = None


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------


class MeshDefinition(BaseModel):
    """Mesh definition.

    Parameters
    ----------
    resolution:
        Mesh resolution (e.g. cell count, base cell size, or refinement
        level).
    mesh_type:
        Mesh topology identifier, e.g. ``"blockMesh"``,
        ``"snappyHexMesh"``, ``"structured"``.
    refinement_regions:
        List of refinement region descriptors (dicts).
    """

    model_config = ConfigDict(extra="forbid")

    resolution: SourcedValue
    mesh_type: str
    refinement_regions: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class ExecutionDefinition(BaseModel):
    """Execution target and parallelism settings.

    Parameters
    ----------
    target_id:
        Identifier of the execution target (workstation / HPC cluster).
    parallel:
        Whether to run in parallel.
    cores:
        Number of CPU cores (``None`` = serial / auto).
    """

    model_config = ConfigDict(extra="forbid")

    target_id: str
    parallel: bool = False
    cores: int | None = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationDefinition(BaseModel):
    """Validation checks to run after simulation.

    Parameters
    ----------
    checks:
        List of check identifiers, e.g. ``"courant_number"``,
        ``"mass_balance"``, ``"grid_convergence"``.
    """

    model_config = ConfigDict(extra="forbid")

    checks: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# SimulationStudySpec — the top-level model
# ---------------------------------------------------------------------------


class SimulationStudySpec(BaseModel):
    """The canonical, versioned simulation study specification.

    This is the single source of truth for a CFD simulation study.  All
    sub-blocks are Pydantic models defined in their respective modules.

    Parameters
    ----------
    schema_version:
        Schema version string (default ``"1.0"``).
    spec_id:
        Unique identifier for this spec (stable across versions).
    session_id:
        The research session this spec belongs to.
    version:
        Monotonically increasing version number (default ``1``).
    parent_version:
        The version this one was derived from, or ``None`` for v1.
    study:
        Human-readable study metadata.
    physics:
        Physics and material properties.
    geometry:
        Geometry definition (domain + entities + relations).
    boundaries:
        Boundary conditions.
    initial_conditions:
        Free-form initial-conditions list (dicts).
    numerics:
        Numerics definition (time control, solver, discretisation, …).
    mesh:
        Mesh definition.
    observations:
        Observation targets and probes.
    execution:
        Execution target and parallelism.
    validation:
        Validation checks.
    extensions:
        Forward-compatible extension dict.
    provenance:
        Spec provenance metadata.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    spec_id: str
    session_id: str
    version: int = 1
    parent_version: int | None = None
    study: StudyDefinition
    physics: PhysicsDefinition
    geometry: GeometryDefinition
    boundaries: BoundaryDefinition
    initial_conditions: list[dict[str, Any]] = Field(default_factory=list)
    numerics: NumericsDefinition
    mesh: MeshDefinition
    observations: ObservationDefinition
    execution: ExecutionDefinition
    validation: ValidationDefinition
    extensions: dict[str, Any] = Field(default_factory=dict)
    provenance: SpecProvenance
