"""Mesh components for the OpenFOAM 13 component system.

A :class:`MeshComponent` describes a mesh generation or refinement
strategy that contributes to ``system/blockMeshDict``,
``system/snappyHexMeshDict``, or mesh-related sections of other files.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from fluid_scientist.components.base_packs import _ComponentBase


class MeshComponent(_ComponentBase):
    """A reusable mesh generation or refinement strategy.

    Attributes:
        component_id: Unique identifier.
        description: Human-readable description.
        parameters: Parameter schema with default values and units.
        depends_on: Component ids that must be present first.
        produces: Files or dictionary sections this component contributes.
    """

    component_id: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Mesh generation
# ---------------------------------------------------------------------------

BLOCK_MESH_BASIC = MeshComponent(
    component_id="mesh-block-mesh-basic",
    description="Basic structured blockMeshDict with uniform hexahedral cells",
    parameters={
        "n_cells_x": {"type": "int", "default": 50, "unit": "dimensionless"},
        "n_cells_y": {"type": "int", "default": 20, "unit": "dimensionless"},
        "n_cells_z": {"type": "int", "default": 20, "unit": "dimensionless"},
        "grading_x": {"type": "float", "default": 1.0, "unit": "ratio"},
        "grading_y": {"type": "float", "default": 1.0, "unit": "ratio"},
        "grading_z": {"type": "float", "default": 1.0, "unit": "ratio"},
    },
    depends_on=["geometry-box", "geometry-pipe"],
    produces=["system/blockMeshDict"],
)

BODY_REFINEMENT = MeshComponent(
    component_id="mesh-body-refinement",
    description="Local mesh refinement around a solid body surface",
    parameters={
        "refinement_level": {"type": "int", "default": 3, "unit": "dimensionless"},
        "refinement_distance": {"type": "float", "default": 0.02, "unit": "m"},
        "body_name": {"type": "string", "default": "cylinder", "unit": "entity_id"},
    },
    depends_on=["geometry-cylinder", "geometry-sphere"],
    produces=["system/snappyHexMeshDict"],
)

WAKE_REFINEMENT = MeshComponent(
    component_id="mesh-wake-refinement",
    description="Refinement zone in the wake region downstream of a body",
    parameters={
        "refinement_level": {"type": "int", "default": 2, "unit": "dimensionless"},
        "wake_length": {"type": "float", "default": 0.1, "unit": "m"},
        "wake_width": {"type": "float", "default": 0.03, "unit": "m"},
    },
    depends_on=["geometry-cylinder", "geometry-sphere"],
    produces=["system/snappyHexMeshDict"],
)

NEAR_WALL_REFINEMENT = MeshComponent(
    component_id="mesh-near-wall-refinement",
    description="Near-wall refinement to achieve target y+ for wall functions or DNS",
    parameters={
        "target_y_plus": {"type": "float", "default": 1.0, "unit": "dimensionless"},
        "first_cell_height": {"type": "float", "default": 1e-05, "unit": "m"},
        "expansion_ratio": {"type": "float", "default": 1.2, "unit": "ratio"},
        "n_layers": {"type": "int", "default": 10, "unit": "dimensionless"},
    },
    depends_on=["geometry-box", "geometry-pipe", "geometry-cylinder"],
    produces=["system/snappyHexMeshDict", "system/blockMeshDict"],
)

BOUNDARY_LAYER_REFINEMENT = MeshComponent(
    component_id="mesh-boundary-layer-refinement",
    description="Boundary layer mesh with specified first cell height and growth ratio",
    parameters={
        "first_cell_height": {"type": "float", "default": 1e-05, "unit": "m"},
        "expansion_ratio": {"type": "float", "default": 1.2, "unit": "ratio"},
        "n_layers": {"type": "int", "default": 15, "unit": "dimensionless"},
        "patch_names": {"type": "list", "default": ["wall"], "unit": "patch_name"},
    },
    depends_on=["geometry-box", "geometry-pipe", "geometry-cylinder"],
    produces=["system/snappyHexMeshDict", "system/blockMeshDict"],
)

PERIODIC_MESH_PAIRING = MeshComponent(
    component_id="mesh-periodic-pairing",
    description="Mesh pairing for periodic boundaries using cyclic or cyclicAMI",
    parameters={
        "pair_type": {"type": "string", "default": "cyclicAMI", "unit": "dimensionless"},
        "separation_vector": {"type": "vector", "default": "(1 0 0)", "unit": "m"},
        "rotation_axis": {"type": "vector", "default": "(0 0 1)", "unit": "dimensionless"},
        "rotation_angle": {"type": "float", "default": 0.0, "unit": "deg"},
        "patch_a": {"type": "string", "default": "periodic_a", "unit": "patch_name"},
        "patch_b": {"type": "string", "default": "periodic_b", "unit": "patch_name"},
    },
    depends_on=["geometry-box"],
    produces=["system/blockMeshDict", "constant/polyMesh/boundary"],
)

SNAPPY_SURFACE_REFINEMENT = MeshComponent(
    component_id="mesh-snappy-surface-refinement",
    description="snappyHexMesh surface refinement from imported STL geometry",
    parameters={
        "stl_file": {"type": "string", "default": "geometry.stl", "unit": "filename"},
        "surface_refinement_level": {"type": "int", "default": 4, "unit": "dimensionless"},
        "volume_refinement_level": {"type": "int", "default": 2, "unit": "dimensionless"},
        "edge_refinement_level": {"type": "int", "default": 3, "unit": "dimensionless"},
        "n_layers": {"type": "int", "default": 5, "unit": "dimensionless"},
        "first_layer_height": {"type": "float", "default": 1e-05, "unit": "m"},
        "expansion_ratio": {"type": "float", "default": 1.2, "unit": "ratio"},
    },
    depends_on=["geometry-imported-stl"],
    produces=["system/snappyHexMeshDict"],
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

MESH_COMPONENTS: dict[str, MeshComponent] = {
    c.component_id: c
    for c in [
        BLOCK_MESH_BASIC,
        BODY_REFINEMENT,
        WAKE_REFINEMENT,
        NEAR_WALL_REFINEMENT,
        BOUNDARY_LAYER_REFINEMENT,
        PERIODIC_MESH_PAIRING,
        SNAPPY_SURFACE_REFINEMENT,
    ]
}


__all__ = [
    "BLOCK_MESH_BASIC",
    "BODY_REFINEMENT",
    "BOUNDARY_LAYER_REFINEMENT",
    "MeshComponent",
    "MESH_COMPONENTS",
    "NEAR_WALL_REFINEMENT",
    "PERIODIC_MESH_PAIRING",
    "SNAPPY_SURFACE_REFINEMENT",
    "WAKE_REFINEMENT",
]
