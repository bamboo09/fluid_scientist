"""Geometry components for the OpenFOAM 13 component system.

A :class:`GeometryComponent` describes a geometric primitive, an
operation on geometry, or a spatial relation between entities.  Each
component carries metadata about what files it contributes to (typically
``system/blockMeshDict`` or ``constant/triSurface/*``) and what it
depends on or conflicts with.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.components.base_packs import _ComponentBase


class GeometryComponent(_ComponentBase):
    """A reusable geometry primitive, operation, or relation.

    Attributes:
        component_id: Unique identifier.
        description: Human-readable description.
        component_type: One of ``"primitive"``, ``"operation"``,
            ``"relation"``, or ``"boolean"``.
        parameters: Parameter schema with default values and units.
        generates: Files or dictionary sections this component produces.
        depends_on: Component ids that must be present first.
        conflicts_with: Component ids that must NOT be present.
    """

    component_id: str
    description: str
    component_type: str = "primitive"
    parameters: dict[str, Any] = Field(default_factory=dict)
    generates: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

BOX = GeometryComponent(
    component_id="geometry-box",
    description="Rectangular box domain defined by two corner vertices",
    component_type="primitive",
    parameters={
        "min_point": {"type": "vector", "default": "(0 0 0)", "unit": "m"},
        "max_point": {"type": "vector", "default": "(1 1 1)", "unit": "m"},
    },
    generates=["system/blockMeshDict"],
    depends_on=[],
    conflicts_with=[],
)

PIPE = GeometryComponent(
    component_id="geometry-pipe",
    description="Cylindrical pipe domain with circular cross-section",
    component_type="primitive",
    parameters={
        "diameter": {"type": "float", "default": 0.01, "unit": "m"},
        "length": {"type": "float", "default": 0.1, "unit": "m"},
        "axis": {"type": "vector", "default": "(1 0 0)", "unit": "dimensionless"},
    },
    generates=["system/blockMeshDict"],
    depends_on=[],
    conflicts_with=[],
)

CYLINDER = GeometryComponent(
    component_id="geometry-cylinder",
    description="Solid cylinder obstacle for cross-flow simulations",
    component_type="primitive",
    parameters={
        "diameter": {"type": "float", "default": 0.01, "unit": "m"},
        "height": {"type": "float", "default": 0.1, "unit": "m"},
        "center": {"type": "vector", "default": "(0 0 0)", "unit": "m"},
        "axis": {"type": "vector", "default": "(0 0 1)", "unit": "dimensionless"},
    },
    generates=["system/blockMeshDict", "constant/triSurface/cylinder.stl"],
    depends_on=[],
    conflicts_with=[],
)

SPHERE = GeometryComponent(
    component_id="geometry-sphere",
    description="Spherical obstacle for cross-flow or settling simulations",
    component_type="primitive",
    parameters={
        "diameter": {"type": "float", "default": 0.01, "unit": "m"},
        "center": {"type": "vector", "default": "(0 0 0)", "unit": "m"},
    },
    generates=["system/blockMeshDict", "constant/triSurface/sphere.stl"],
    depends_on=[],
    conflicts_with=[],
)

PLANE_WALL = GeometryComponent(
    component_id="geometry-plane-wall",
    description="Flat planar wall surface used as a domain boundary",
    component_type="primitive",
    parameters={
        "origin": {"type": "vector", "default": "(0 0 0)", "unit": "m"},
        "normal": {"type": "vector", "default": "(0 1 0)", "unit": "dimensionless"},
        "width": {"type": "float", "default": 0.1, "unit": "m"},
        "height": {"type": "float", "default": 0.1, "unit": "m"},
    },
    generates=["system/blockMeshDict"],
    depends_on=[],
    conflicts_with=[],
)

CIRCULAR_NOZZLE = GeometryComponent(
    component_id="geometry-circular-nozzle",
    description="Circular nozzle inlet with contraction and throat",
    component_type="primitive",
    parameters={
        "inlet_diameter": {"type": "float", "default": 0.02, "unit": "m"},
        "throat_diameter": {"type": "float", "default": 0.005, "unit": "m"},
        "length": {"type": "float", "default": 0.05, "unit": "m"},
        "axis": {"type": "vector", "default": "(1 0 0)", "unit": "dimensionless"},
    },
    generates=["system/blockMeshDict"],
    depends_on=[],
    conflicts_with=[],
)

IMPORTED_STL = GeometryComponent(
    component_id="geometry-imported-stl",
    description="Imported STL surface geometry from external CAD file",
    component_type="primitive",
    parameters={
        "stl_file": {"type": "string", "default": "geometry.stl", "unit": "filename"},
        "scale": {"type": "float", "default": 1.0, "unit": "dimensionless"},
    },
    generates=["constant/triSurface/geometry.stl", "system/snappyHexMeshDict"],
    depends_on=[],
    conflicts_with=[],
)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

ROTATE = GeometryComponent(
    component_id="geometry-op-rotate",
    description="Rotate an entity about a specified axis and angle",
    component_type="operation",
    parameters={
        "angle": {"type": "float", "default": 0.0, "unit": "deg"},
        "axis": {"type": "vector", "default": "(0 0 1)", "unit": "dimensionless"},
        "origin": {"type": "vector", "default": "(0 0 0)", "unit": "m"},
    },
    generates=["system/blockMeshDict"],
    depends_on=["geometry-box", "geometry-pipe", "geometry-cylinder", "geometry-sphere"],
    conflicts_with=[],
)

TRANSLATE = GeometryComponent(
    component_id="geometry-op-translate",
    description="Translate an entity by a displacement vector",
    component_type="operation",
    parameters={
        "displacement": {"type": "vector", "default": "(0 0 0)", "unit": "m"},
    },
    generates=["system/blockMeshDict"],
    depends_on=["geometry-box", "geometry-pipe", "geometry-cylinder", "geometry-sphere"],
    conflicts_with=[],
)

SCALE = GeometryComponent(
    component_id="geometry-op-scale",
    description="Scale an entity uniformly or by axis factors",
    component_type="operation",
    parameters={
        "factor": {"type": "float", "default": 1.0, "unit": "dimensionless"},
        "origin": {"type": "vector", "default": "(0 0 0)", "unit": "m"},
    },
    generates=["system/blockMeshDict"],
    depends_on=["geometry-box", "geometry-pipe", "geometry-cylinder", "geometry-sphere"],
    conflicts_with=[],
)


# ---------------------------------------------------------------------------
# Relations
# ---------------------------------------------------------------------------

NEAR_WALL_RELATION = GeometryComponent(
    component_id="geometry-relation-near-wall",
    description="Specify that an entity is near a wall with a defined gap distance",
    component_type="relation",
    parameters={
        "gap_distance": {"type": "float", "default": 0.001, "unit": "m"},
        "source_entity": {"type": "string", "default": "", "unit": "entity_id"},
        "target_entity": {"type": "string", "default": "", "unit": "entity_id"},
    },
    generates=["system/blockMeshDict"],
    depends_on=[],
    conflicts_with=[],
)

MULTI_BODY_PLACEMENT = GeometryComponent(
    component_id="geometry-relation-multi-body",
    description="Place multiple bodies in the domain with specified spacing pattern",
    component_type="relation",
    parameters={
        "pattern": {"type": "string", "default": "inline", "unit": "dimensionless"},
        "count": {"type": "int", "default": 2, "unit": "dimensionless"},
        "pitch": {"type": "float", "default": 0.02, "unit": "m"},
    },
    generates=["system/blockMeshDict"],
    depends_on=[],
    conflicts_with=[],
)


# ---------------------------------------------------------------------------
# Boolean operations
# ---------------------------------------------------------------------------

BOOLEAN_UNION = GeometryComponent(
    component_id="geometry-boolean-union",
    description="Union of two geometric entities into a single combined shape",
    component_type="boolean",
    parameters={
        "entity_a": {"type": "string", "default": "", "unit": "entity_id"},
        "entity_b": {"type": "string", "default": "", "unit": "entity_id"},
    },
    generates=["system/blockMeshDict"],
    depends_on=["geometry-box", "geometry-cylinder", "geometry-sphere"],
    conflicts_with=["geometry-boolean-subtract"],
)

BOOLEAN_SUBTRACT = GeometryComponent(
    component_id="geometry-boolean-subtract",
    description="Subtract one entity from another to create a cavity or notch",
    component_type="boolean",
    parameters={
        "entity_a": {"type": "string", "default": "", "unit": "entity_id"},
        "entity_b": {"type": "string", "default": "", "unit": "entity_id"},
    },
    generates=["system/blockMeshDict"],
    depends_on=["geometry-box", "geometry-cylinder", "geometry-sphere"],
    conflicts_with=["geometry-boolean-union"],
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

GEOMETRY_COMPONENTS: dict[str, GeometryComponent] = {
    c.component_id: c
    for c in [
        BOX,
        PIPE,
        CYLINDER,
        SPHERE,
        PLANE_WALL,
        CIRCULAR_NOZZLE,
        IMPORTED_STL,
        ROTATE,
        TRANSLATE,
        SCALE,
        NEAR_WALL_RELATION,
        MULTI_BODY_PLACEMENT,
        BOOLEAN_UNION,
        BOOLEAN_SUBTRACT,
    ]
}


__all__ = [
    "BOOLEAN_SUBTRACT",
    "BOOLEAN_UNION",
    "BOX",
    "CIRCULAR_NOZZLE",
    "CYLINDER",
    "GEOMETRY_COMPONENTS",
    "GeometryComponent",
    "IMPORTED_STL",
    "MULTI_BODY_PLACEMENT",
    "NEAR_WALL_RELATION",
    "PIPE",
    "PLANE_WALL",
    "ROTATE",
    "SCALE",
    "SPHERE",
    "TRANSLATE",
]
