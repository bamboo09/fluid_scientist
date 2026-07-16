"""Canonical versioned :class:`SimulationStudySpec` package.

This package implements the single source of truth for a CFD simulation
study specification.  It re-exports the top-level model and key
supporting types so callers can import them directly from the package::

    from fluid_scientist.study_spec import SimulationStudySpec, SourcedValue

Sub-modules
-----------
``quantities``
    :class:`Quantity`, :class:`SourcedValue`, :class:`TimeControl`,
    :class:`TimeWindow` and the source-status hierarchy.
``geometry``
    :class:`GeometryEntity`, :class:`PlacementSpec`,
    :class:`GeometryRelation`, :class:`GeometryDefinition`,
    :class:`DomainSpec`.
``boundaries``
    :class:`BoundaryCondition`, :class:`BoundaryDefinition`.
``numerics``
    :class:`NumericsDefinition`.
``observations``
    :class:`ObservationTarget`, :class:`ProbeSpec`,
    :class:`ObservationDefinition`.
``provenance``
    :class:`SpecProvenance`.
``versioning``
    :class:`SpecVersion`, :class:`VersionedSpecStore`.
``schema_export``
    :class:`SchemaExporter`.
``migration``
    :class:`LegacyMigrator`.
``models``
    :class:`SimulationStudySpec` and sub-models
    (:class:`StudyDefinition`, :class:`PhysicsDefinition`, …).
"""

from __future__ import annotations

from .boundaries import BoundaryCondition, BoundaryDefinition
from .geometry import (
    DomainSpec,
    GeometryDefinition,
    GeometryEntity,
    GeometryRelation,
    PlacementSpec,
)
from .migration import LegacyMigrator
from .models import (
    ExecutionDefinition,
    MeshDefinition,
    PhysicsDefinition,
    SimulationStudySpec,
    StudyDefinition,
    ValidationDefinition,
)
from .numerics import NumericsDefinition
from .observations import ObservationDefinition, ObservationTarget, ProbeSpec
from .provenance import SpecProvenance
from .quantities import (
    Quantity,
    SourcedValue,
    TimeControl,
    TimeWindow,
    should_override,
    status_priority,
)
from .schema_export import SchemaExporter
from .versioning import SpecVersion, VersionedSpecStore

__all__ = [
    # Top-level model
    "SimulationStudySpec",
    "StudyDefinition",
    "PhysicsDefinition",
    "MeshDefinition",
    "ExecutionDefinition",
    "ValidationDefinition",
    # Quantities
    "Quantity",
    "SourcedValue",
    "TimeControl",
    "TimeWindow",
    "status_priority",
    "should_override",
    # Geometry
    "GeometryDefinition",
    "GeometryEntity",
    "GeometryRelation",
    "PlacementSpec",
    "DomainSpec",
    # Boundaries
    "BoundaryCondition",
    "BoundaryDefinition",
    # Numerics
    "NumericsDefinition",
    # Observations
    "ObservationDefinition",
    "ObservationTarget",
    "ProbeSpec",
    # Provenance
    "SpecProvenance",
    # Versioning
    "SpecVersion",
    "VersionedSpecStore",
    # Schema export
    "SchemaExporter",
    # Migration
    "LegacyMigrator",
]
