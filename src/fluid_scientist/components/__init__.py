"""OpenFOAM 13 component system.

This package provides the reusable component definitions used by the
deterministic OpenFOAM 13 compiler.  Components are organised into five
categories:

* **Base packs** -- solver-module choice and default dictionary templates.
* **Geometry components** -- geometric primitives, operations, and relations.
* **Boundary components** -- semantic-to-concrete boundary condition mappings.
* **Mesh components** -- mesh generation and refinement strategies.
* **Observable components** -- measurement / function-object configurations.

All components are registered in the :class:`ComponentRegistry` which
provides lookup by id, by category, and by semantic role.
"""

from fluid_scientist.components.base_packs import (
    BASE_PACKS,
    FOUNDATION13_INCOMPRESSIBLE_LAMINAR_TRANSIENT,
    FOUNDATION13_INCOMPRESSIBLE_LES_TRANSIENT,
    FOUNDATION13_INCOMPRESSIBLE_RANS_STEADY,
    FOUNDATION13_INCOMPRESSIBLE_RANS_TRANSIENT,
    BasePack,
)
from fluid_scientist.components.boundary_components import (
    BOUNDARY_COMPONENTS,
    BoundaryComponent,
    CONVECTIVE_OUTLET,
    DEVELOPED_PIPE_INLET,
    MOVING_WALL,
    NO_SLIP_WALL,
    PERIODIC_PAIR,
    PRESSURE_OUTLET,
    SLIP_WALL,
    SYMMETRY_PLANE,
    UNIFORM_VELOCITY_INLET,
)
from fluid_scientist.components.geometry_components import (
    BOOLEAN_SUBTRACT,
    BOOLEAN_UNION,
    BOX,
    CIRCULAR_NOZZLE,
    CYLINDER,
    GEOMETRY_COMPONENTS,
    GeometryComponent,
    IMPORTED_STL,
    MULTI_BODY_PLACEMENT,
    NEAR_WALL_RELATION,
    PIPE,
    PLANE_WALL,
    ROTATE,
    SCALE,
    SPHERE,
    TRANSLATE,
)
from fluid_scientist.components.mesh_components import (
    BLOCK_MESH_BASIC,
    BODY_REFINEMENT,
    BOUNDARY_LAYER_REFINEMENT,
    MESH_COMPONENTS,
    MeshComponent,
    NEAR_WALL_REFINEMENT,
    PERIODIC_MESH_PAIRING,
    SNAPPY_SURFACE_REFINEMENT,
    WAKE_REFINEMENT,
)
from fluid_scientist.components.observable_components import (
    FIELD_AVERAGE,
    FORCE_COEFFICIENTS,
    FORCES,
    FREQUENCY_SPECTRUM,
    OBSERVABLE_COMPONENTS,
    ObservableComponent,
    PRESSURE_COEFFICIENT,
    PROBES,
    SURFACE_AVERAGE,
    VORTEX_IDENTIFICATION,
    WAKE_DEFLECTION,
    WALL_SHEAR_STRESS,
)
from fluid_scientist.components.registry import AnyComponent, ComponentRegistry

__all__ = [
    # Base models
    "BasePack",
    "BoundaryComponent",
    "GeometryComponent",
    "MeshComponent",
    "ObservableComponent",
    # Base packs
    "BASE_PACKS",
    "FOUNDATION13_INCOMPRESSIBLE_LAMINAR_TRANSIENT",
    "FOUNDATION13_INCOMPRESSIBLE_LES_TRANSIENT",
    "FOUNDATION13_INCOMPRESSIBLE_RANS_STEADY",
    "FOUNDATION13_INCOMPRESSIBLE_RANS_TRANSIENT",
    # Geometry
    "BOOLEAN_SUBTRACT",
    "BOOLEAN_UNION",
    "BOX",
    "CIRCULAR_NOZZLE",
    "CYLINDER",
    "GEOMETRY_COMPONENTS",
    "IMPORTED_STL",
    "MULTI_BODY_PLACEMENT",
    "NEAR_WALL_RELATION",
    "PIPE",
    "PLANE_WALL",
    "ROTATE",
    "SCALE",
    "SPHERE",
    "TRANSLATE",
    # Boundary
    "BOUNDARY_COMPONENTS",
    "CONVECTIVE_OUTLET",
    "DEVELOPED_PIPE_INLET",
    "MOVING_WALL",
    "NO_SLIP_WALL",
    "PERIODIC_PAIR",
    "PRESSURE_OUTLET",
    "SLIP_WALL",
    "SYMMETRY_PLANE",
    "UNIFORM_VELOCITY_INLET",
    # Mesh
    "BLOCK_MESH_BASIC",
    "BODY_REFINEMENT",
    "BOUNDARY_LAYER_REFINEMENT",
    "MESH_COMPONENTS",
    "NEAR_WALL_REFINEMENT",
    "PERIODIC_MESH_PAIRING",
    "SNAPPY_SURFACE_REFINEMENT",
    "WAKE_REFINEMENT",
    # Observable
    "FIELD_AVERAGE",
    "FORCE_COEFFICIENTS",
    "FORCES",
    "FREQUENCY_SPECTRUM",
    "OBSERVABLE_COMPONENTS",
    "PRESSURE_COEFFICIENT",
    "PROBES",
    "SURFACE_AVERAGE",
    "VORTEX_IDENTIFICATION",
    "WAKE_DEFLECTION",
    "WALL_SHEAR_STRESS",
    # Registry
    "AnyComponent",
    "ComponentRegistry",
]
