"""Boundary condition components for the OpenFOAM 13 component system.

A :class:`BoundaryComponent` maps a semantic boundary-condition intent
(e.g. ``"uniform_velocity_inlet"``) to concrete Foundation 13 OpenFOAM
boundary-condition type names for each field (U, p, k, omega, nut, etc.).

Each component also lists its supported fields, parameter schema, and
dependency/conflict metadata.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from fluid_scientist.components.base_packs import _ComponentBase


class BoundaryComponent(_ComponentBase):
    """A reusable boundary-condition mapping.

    Attributes:
        component_id: Unique identifier.
        description: Human-readable description.
        semantic_role: Semantic role name used in Case IR boundary intents.
        supported_fields: Fields this BC applies to (e.g.
            ``["U", "p", "k", "omega"]``).
        foundation13_mapping: Mapping from field name to the Foundation 13
            BC type name and default value specification.
        parameters: Parameter schema for this boundary condition.
        dependencies: Component ids that must be present first.
        conflicts: Component ids that must NOT be present.
    """

    component_id: str
    description: str
    semantic_role: str = ""
    supported_fields: list[str] = Field(default_factory=list)
    foundation13_mapping: dict[str, dict[str, str]] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Inlet conditions
# ---------------------------------------------------------------------------

UNIFORM_VELOCITY_INLET = BoundaryComponent(
    component_id="bc-uniform-velocity-inlet",
    description="Uniform velocity inlet with fixed velocity vector and zeroGradient pressure",
    semantic_role="uniform_velocity_inlet",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "fixedValue", "value": "uniform (1 0 0)"},
        "p": {"type": "zeroGradient", "value": ""},
        "k": {"type": "fixedValue", "value": "uniform 0.1"},
        "omega": {"type": "fixedValue", "value": "uniform 100"},
        "epsilon": {"type": "fixedValue", "value": "uniform 0.01"},
        "nuTilda": {"type": "fixedValue", "value": "uniform 0"},
        "nut": {"type": "calculated", "value": "uniform 0"},
    },
    parameters={
        "velocity": {"type": "vector", "default": "(1 0 0)", "unit": "m/s"},
        "turbulent_k": {"type": "float", "default": 0.1, "unit": "m2/s2"},
        "turbulent_omega": {"type": "float", "default": 100.0, "unit": "1/s"},
    },
    dependencies=[],
    conflicts=["bc-pressure-outlet"],
)

DEVELOPED_PIPE_INLET = BoundaryComponent(
    component_id="bc-developed-pipe-inlet",
    description="Fully-developed pipe flow inlet using mapped or profile velocity",
    semantic_role="developed_pipe_inlet",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "fixedValue", "value": "uniform (1 0 0)"},
        "p": {"type": "zeroGradient", "value": ""},
        "k": {"type": "fixedValue", "value": "uniform 0.1"},
        "omega": {"type": "fixedValue", "value": "uniform 100"},
        "epsilon": {"type": "fixedValue", "value": "uniform 0.01"},
        "nuTilda": {"type": "fixedValue", "value": "uniform 0"},
        "nut": {"type": "calculated", "value": "uniform 0"},
    },
    parameters={
        "bulk_velocity": {"type": "float", "default": 1.0, "unit": "m/s"},
        "pipe_diameter": {"type": "float", "default": 0.01, "unit": "m"},
        "reynolds_number": {"type": "float", "default": 10000.0, "unit": "dimensionless"},
    },
    dependencies=["geometry-pipe"],
    conflicts=["bc-uniform-velocity-inlet"],
)


# ---------------------------------------------------------------------------
# Outlet conditions
# ---------------------------------------------------------------------------

PRESSURE_OUTLET = BoundaryComponent(
    component_id="bc-pressure-outlet",
    description="Pressure outlet with fixed pressure and zeroGradient velocity",
    semantic_role="pressure_outlet",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "zeroGradient", "value": ""},
        "p": {"type": "fixedValue", "value": "uniform 0"},
        "k": {"type": "zeroGradient", "value": ""},
        "omega": {"type": "zeroGradient", "value": ""},
        "epsilon": {"type": "zeroGradient", "value": ""},
        "nuTilda": {"type": "zeroGradient", "value": ""},
        "nut": {"type": "calculated", "value": "uniform 0"},
    },
    parameters={
        "pressure": {"type": "float", "default": 0.0, "unit": "m2/s2"},
    },
    dependencies=[],
    conflicts=["bc-convective-outlet"],
)

CONVECTIVE_OUTLET = BoundaryComponent(
    component_id="bc-convective-outlet",
    description="Convective outlet using advective outflow condition",
    semantic_role="convective_outlet",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "inletOutlet", "value": "uniform (0 0 0)", "inletValue": "uniform (0 0 0)"},
        "p": {"type": "fixedValue", "value": "uniform 0"},
        "k": {"type": "inletOutlet", "value": "uniform 0", "inletValue": "uniform 0"},
        "omega": {"type": "inletOutlet", "value": "uniform 0", "inletValue": "uniform 0"},
        "epsilon": {"type": "inletOutlet", "value": "uniform 0", "inletValue": "uniform 0"},
        "nuTilda": {"type": "inletOutlet", "value": "uniform 0", "inletValue": "uniform 0"},
        "nut": {"type": "calculated", "value": "uniform 0"},
    },
    parameters={
        "convective_velocity": {"type": "float", "default": 1.0, "unit": "m/s"},
    },
    dependencies=[],
    conflicts=["bc-pressure-outlet"],
)


# ---------------------------------------------------------------------------
# Wall conditions
# ---------------------------------------------------------------------------

NO_SLIP_WALL = BoundaryComponent(
    component_id="bc-no-slip-wall",
    description="No-slip wall with zero velocity and zeroGradient pressure",
    semantic_role="no_slip_wall",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "noSlip", "value": "uniform (0 0 0)"},
        "p": {"type": "zeroGradient", "value": ""},
        "k": {"type": "kqRWallFunction", "value": "uniform 0"},
        "omega": {"type": "omegaWallFunction", "value": "uniform 0"},
        "epsilon": {"type": "epsilonWallFunction", "value": "uniform 0"},
        "nuTilda": {"type": "fixedValue", "value": "uniform 0"},
        "nut": {"type": "nutkWallFunction", "value": "uniform 0"},
    },
    parameters={
        "roughness": {"type": "float", "default": 0.0, "unit": "m"},
    },
    dependencies=[],
    conflicts=["bc-slip-wall", "bc-moving-wall"],
)

SLIP_WALL = BoundaryComponent(
    component_id="bc-slip-wall",
    description="Slip wall with zero normal velocity and zero shear",
    semantic_role="slip_wall",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "slip", "value": ""},
        "p": {"type": "zeroGradient", "value": ""},
        "k": {"type": "zeroGradient", "value": ""},
        "omega": {"type": "zeroGradient", "value": ""},
        "epsilon": {"type": "zeroGradient", "value": ""},
        "nuTilda": {"type": "zeroGradient", "value": ""},
        "nut": {"type": "calculated", "value": "uniform 0"},
    },
    parameters={},
    dependencies=[],
    conflicts=["bc-no-slip-wall", "bc-moving-wall"],
)

MOVING_WALL = BoundaryComponent(
    component_id="bc-moving-wall",
    description="Moving wall with specified wall velocity and zeroGradient pressure",
    semantic_role="moving_wall",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "fixedValue", "value": "uniform (1 0 0)"},
        "p": {"type": "zeroGradient", "value": ""},
        "k": {"type": "kqRWallFunction", "value": "uniform 0"},
        "omega": {"type": "omegaWallFunction", "value": "uniform 0"},
        "epsilon": {"type": "epsilonWallFunction", "value": "uniform 0"},
        "nuTilda": {"type": "fixedValue", "value": "uniform 0"},
        "nut": {"type": "nutkWallFunction", "value": "uniform 0"},
    },
    parameters={
        "wall_velocity": {"type": "vector", "default": "(1 0 0)", "unit": "m/s"},
    },
    dependencies=[],
    conflicts=["bc-no-slip-wall", "bc-slip-wall"],
)


# ---------------------------------------------------------------------------
# Symmetry and periodic
# ---------------------------------------------------------------------------

SYMMETRY_PLANE = BoundaryComponent(
    component_id="bc-symmetry-plane",
    description="Symmetry plane boundary condition for all fields",
    semantic_role="symmetry_plane",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "symmetry", "value": ""},
        "p": {"type": "symmetry", "value": ""},
        "k": {"type": "symmetry", "value": ""},
        "omega": {"type": "symmetry", "value": ""},
        "epsilon": {"type": "symmetry", "value": ""},
        "nuTilda": {"type": "symmetry", "value": ""},
        "nut": {"type": "symmetry", "value": ""},
    },
    parameters={},
    dependencies=[],
    conflicts=[],
)

PERIODIC_PAIR = BoundaryComponent(
    component_id="bc-periodic-pair",
    description="Periodic boundary pair using cyclicAMI for translational or rotational periodicity",
    semantic_role="periodic_pair",
    supported_fields=["U", "p", "k", "omega", "epsilon", "nuTilda", "nut"],
    foundation13_mapping={
        "U": {"type": "cyclicAMI", "value": ""},
        "p": {"type": "cyclicAMI", "value": ""},
        "k": {"type": "cyclicAMI", "value": ""},
        "omega": {"type": "cyclicAMI", "value": ""},
        "epsilon": {"type": "cyclicAMI", "value": ""},
        "nuTilda": {"type": "cyclicAMI", "value": ""},
        "nut": {"type": "cyclicAMI", "value": ""},
    },
    parameters={
        "periodicity_type": {"type": "string", "default": "translational", "unit": "dimensionless"},
        "separation_vector": {"type": "vector", "default": "(1 0 0)", "unit": "m"},
        "rotation_axis": {"type": "vector", "default": "(0 0 1)", "unit": "dimensionless"},
        "rotation_angle": {"type": "float", "default": 0.0, "unit": "deg"},
    },
    dependencies=["mesh-periodic-pairing"],
    conflicts=[],
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

BOUNDARY_COMPONENTS: dict[str, BoundaryComponent] = {
    c.component_id: c
    for c in [
        UNIFORM_VELOCITY_INLET,
        DEVELOPED_PIPE_INLET,
        PRESSURE_OUTLET,
        CONVECTIVE_OUTLET,
        NO_SLIP_WALL,
        SLIP_WALL,
        MOVING_WALL,
        SYMMETRY_PLANE,
        PERIODIC_PAIR,
    ]
}


__all__ = [
    "BOUNDARY_COMPONENTS",
    "BoundaryComponent",
    "CONVECTIVE_OUTLET",
    "DEVELOPED_PIPE_INLET",
    "MOVING_WALL",
    "NO_SLIP_WALL",
    "PERIODIC_PAIR",
    "PRESSURE_OUTLET",
    "SLIP_WALL",
    "SYMMETRY_PLANE",
    "UNIFORM_VELOCITY_INLET",
]
