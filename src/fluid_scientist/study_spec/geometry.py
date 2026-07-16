"""Geometry definitions for the SimulationStudySpec.

This module captures the spatial configuration of a simulation: the
computational :class:`DomainSpec`, the :class:`GeometryEntity` objects
placed within it, and the :class:`GeometryRelation` links between them.

A key design principle is the **separation of semantic type from primitive
type**.  ``GeometryEntity.semantic_type`` stores the user-facing scientific
concept (e.g. ``"triangle_2d"``), while ``primitive`` stores the solver-
level representation (e.g. ``{"type": "polygon", "n_vertices": 3}``).
This allows the semantic meaning to survive even when the primitive is
re-parameterised.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .quantities import SourcedValue

__all__ = [
    "DomainSpec",
    "GeometryDefinition",
    "GeometryEntity",
    "GeometryRelation",
    "PlacementSpec",
]


# ---------------------------------------------------------------------------
# PlacementSpec — where an entity sits in the domain
# ---------------------------------------------------------------------------


class PlacementSpec(BaseModel):
    """Placement of a geometry entity within the domain.

    Parameters
    ----------
    x, y:
        Sourced coordinates of the entity anchor point.
    orientation:
        Human-readable orientation hint, e.g. ``"apex_up"``,
        ``"rotated_30deg"``.
    attachment:
        Semantic attachment target, e.g. ``"bottom_wall"``,
        ``"centered"``, ``"top_wall"``.
    """

    model_config = ConfigDict(extra="forbid")

    x: SourcedValue | None = None
    y: SourcedValue | None = None
    orientation: str | None = None
    attachment: str | None = None


# ---------------------------------------------------------------------------
# GeometryEntity — a single obstacle or feature in the domain
# ---------------------------------------------------------------------------


class GeometryEntity(BaseModel):
    """A single geometric entity (obstacle, feature, boundary shape).

    The ``semantic_type`` field preserves the user-facing scientific concept
    independently of the ``primitive`` dict, which holds the solver-level
    representation.  This separation means that even if the primitive is
    re-parameterised (e.g. a triangle is converted from a dedicated primitive
    to a generic polygon), the original semantic intent is retained.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    semantic_type: str
    primitive: dict[str, Any] | None = None
    polygon_vertices: list[dict[str, Any]] | None = None
    original_user_semantics: str
    placement: PlacementSpec | None = None


# ---------------------------------------------------------------------------
# GeometryRelation — a typed link between two entities
# ---------------------------------------------------------------------------


class GeometryRelation(BaseModel):
    """A typed spatial relationship between two geometry entities."""

    model_config = ConfigDict(extra="forbid")

    relation_id: str
    type: Literal[
        "attached_to",
        "aligned_below",
        "aligned_above",
        "centered_in",
        "distance_to",
        "tangent_to",
        "inside",
        "outside",
        "intersects",
        "custom",
    ]
    subject_id: str
    object_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# DomainSpec — the bounding computational domain
# ---------------------------------------------------------------------------


class DomainSpec(BaseModel):
    """The computational domain bounding box.

    Parameters
    ----------
    length:
        Domain length (streamwise) — always required.
    width:
        Domain width (spanwise) — required for 2D, optional for 3D where
        ``height`` is used instead.
    height:
        Domain height (vertical) — required for 3D, optional for 2D.
    dimensions:
        ``"2d"`` or ``"3d"``.
    """

    model_config = ConfigDict(extra="forbid")

    length: SourcedValue
    width: SourcedValue | None = None
    height: SourcedValue | None = None
    dimensions: Literal["2d", "3d"]


# ---------------------------------------------------------------------------
# GeometryDefinition — the full geometry block
# ---------------------------------------------------------------------------


class GeometryDefinition(BaseModel):
    """The complete geometry definition: domain + entities + relations."""

    model_config = ConfigDict(extra="forbid")

    domain: DomainSpec
    entities: dict[str, GeometryEntity] = Field(default_factory=dict)
    relations: list[GeometryRelation] = Field(default_factory=list)
