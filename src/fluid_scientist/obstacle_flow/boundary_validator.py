"""Boundary combination and flow topology validation.

Implements BoundaryTopologyResolver and BoundaryCombinationValidator
from the plan Section 11.

Validates that boundary type combinations are physically consistent
with the detected flow topology.
"""

from __future__ import annotations

from fluid_scientist.obstacle_flow.models import (
    BoundaryConfig,
    BoundarySpec,
    BoundaryType,
    FlowMode,
    ForcingSpec,
    ObstacleFlowExperimentSpecV1,
)


class BoundaryTopologyError(ValueError):
    """Raised when boundary combination is invalid for the flow topology."""

    def __init__(self, code: str, message: str, suggested_changes: list[str] | None = None):
        self.code = code
        self.message = message
        self.suggested_changes = suggested_changes or []
        super().__init__(f"[{code}] {message}")


class BoundaryTopologyResolver:
    """Resolves flow topology from boundary configuration.

    Implements Section 3 of the plan — detects the flow mode from
    boundary types and forcing configuration.
    """

    def resolve(
        self,
        boundaries: BoundaryConfig,
        forcing: ForcingSpec,
    ) -> FlowMode:
        """Determine the flow mode from boundary and forcing configuration."""
        left = boundaries.left.type
        right = boundaries.right.type
        top = boundaries.top.type

        is_periodic = left == BoundaryType.PERIODIC and right == BoundaryType.PERIODIC

        # Check for periodic forced
        if is_periodic:
            pg_enabled = forcing.pressure_gradient.enabled
            top_moving = top in (BoundaryType.MOVING_WALL, BoundaryType.SHEAR_STRESS)
            if pg_enabled and top_moving:
                return FlowMode.COMBINED_DRIVING
            if pg_enabled:
                return FlowMode.PERIODIC_FORCED
            if top_moving:
                return FlowMode.WALL_DRIVEN
            # Periodic with no forcing — invalid, but return periodic_forced
            # so the validator can catch it
            return FlowMode.PERIODIC_FORCED

        # Check for pressure difference
        if left == BoundaryType.PRESSURE_BOUNDARY and right == BoundaryType.PRESSURE_BOUNDARY:
            return FlowMode.PRESSURE_DIFFERENCE

        # Check for wall-driven (non-periodic)
        if top in (BoundaryType.MOVING_WALL, BoundaryType.SHEAR_STRESS):
            has_inlet = left in (BoundaryType.VELOCITY_INLET, BoundaryType.MASS_FLOW_INLET)
            if has_inlet:
                return FlowMode.COMBINED_DRIVING
            return FlowMode.WALL_DRIVEN

        # Check for open domain
        if top in (BoundaryType.FREESTREAM, BoundaryType.OPEN_BOUNDARY):
            return FlowMode.OPEN_DOMAIN

        # Default: inlet-outlet
        if left in (BoundaryType.VELOCITY_INLET, BoundaryType.MASS_FLOW_INLET, BoundaryType.PRESSURE_INLET):
            return FlowMode.INLET_OUTLET

        # Cannot determine
        raise BoundaryTopologyError(
            "INVALID_FLOW_TOPOLOGY",
            "Cannot determine flow topology from boundary configuration",
            ["Specify an inlet boundary on the left", "Or set left/right to periodic"],
        )


class BoundaryCombinationValidator:
    """Validates boundary combinations for physical consistency.

    Implements Section 11.2 of the plan — rejects illegal combinations.
    """

    def __init__(self) -> None:
        self._resolver = BoundaryTopologyResolver()

    def validate(self, spec: ObstacleFlowExperimentSpecV1) -> None:
        """Validate boundary combinations, raising BoundaryTopologyError on failure."""
        b = spec.boundaries
        left = b.left.type
        right = b.right.type
        top = b.top.type

        # Rule: front/back must be empty
        if b.front.type != BoundaryType.EMPTY or b.back.type != BoundaryType.EMPTY:
            raise BoundaryTopologyError(
                "INVALID_BOUNDARY_COMBINATION",
                "front and back must be 'empty' for 2D simulation",
            )

        # Rule: periodic left + non-periodic right (or vice versa)
        if left == BoundaryType.PERIODIC and right != BoundaryType.PERIODIC:
            raise BoundaryTopologyError(
                "INVALID_BOUNDARY_COMBINATION",
                "left is periodic but right is not — periodic boundaries must be paired",
                ["Set right to periodic", "Set left to a non-periodic type"],
            )
        if right == BoundaryType.PERIODIC and left != BoundaryType.PERIODIC:
            raise BoundaryTopologyError(
                "INVALID_BOUNDARY_COMBINATION",
                "right is periodic but left is not — periodic boundaries must be paired",
                ["Set left to periodic", "Set right to a non-periodic type"],
            )

        # Rule: periodic + velocity inlet simultaneously
        is_periodic = left == BoundaryType.PERIODIC and right == BoundaryType.PERIODIC
        if is_periodic and left == BoundaryType.VELOCITY_INLET:
            raise BoundaryTopologyError(
                "INVALID_BOUNDARY_COMBINATION",
                "Cannot have periodic boundaries and velocity inlet simultaneously",
            )

        # Rule: periodic + pressure outlet on the other side
        if is_periodic and (right == BoundaryType.PRESSURE_OUTLET or left == BoundaryType.PRESSURE_OUTLET):
            raise BoundaryTopologyError(
                "INVALID_BOUNDARY_COMBINATION",
                "Cannot mix periodic boundaries with pressure outlet",
            )

        # Rule: pressure-difference mode + periodic
        if (
            left == BoundaryType.PRESSURE_BOUNDARY
            and right == BoundaryType.PRESSURE_BOUNDARY
            and is_periodic
        ):
            raise BoundaryTopologyError(
                "INVALID_BOUNDARY_COMBINATION",
                "Cannot have pressure-difference mode with periodic boundaries",
            )

        # Rule: top cannot be both wall and open boundary
        if top == BoundaryType.OPEN_BOUNDARY and top == BoundaryType.NO_SLIP_WALL:
            # This is impossible due to enum, but keep for documentation
            raise BoundaryTopologyError(
                "INVALID_BOUNDARY_COMBINATION",
                "Top boundary cannot be both wall and open boundary",
            )

        # Rule: all boundaries are static walls with no driving force
        all_static_walls = (
            left in (BoundaryType.NO_SLIP_WALL, BoundaryType.SLIP_WALL)
            and right in (BoundaryType.NO_SLIP_WALL, BoundaryType.SLIP_WALL)
            and top in (BoundaryType.NO_SLIP_WALL, BoundaryType.SLIP_WALL)
            and b.bottom_flat.type in (BoundaryType.NO_SLIP_WALL, BoundaryType.SLIP_WALL)
        )
        has_forcing = (
            spec.forcing.pressure_gradient.enabled
            or spec.forcing.body_force.enabled
        )
        has_moving = (
            top in (BoundaryType.MOVING_WALL, BoundaryType.SHEAR_STRESS)
            or b.bottom_flat.type == BoundaryType.MOVING_WALL
        )
        if all_static_walls and not has_forcing and not has_moving:
            raise BoundaryTopologyError(
                "INVALID_BOUNDARY_COMBINATION",
                "All boundaries are static walls with no pressure gradient, "
                "body force, or moving wall — no flow will be generated",
                [
                    "Add an inlet boundary",
                    "Enable pressure gradient forcing",
                    "Set top to moving wall",
                ],
            )

        # Validate topology-specific rules
        flow_mode = spec.flow_definition.mode
        self._validate_topology_specific(spec, flow_mode)

    def _validate_topology_specific(
        self, spec: ObstacleFlowExperimentSpecV1, mode: FlowMode
    ) -> None:
        """Validate rules specific to each flow topology."""
        b = spec.boundaries

        if mode == FlowMode.INLET_OUTLET:
            # Left must be inlet, right must be outlet
            if b.left.type not in (
                BoundaryType.VELOCITY_INLET,
                BoundaryType.MASS_FLOW_INLET,
                BoundaryType.PRESSURE_INLET,
            ):
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "INLET_OUTLET mode requires left boundary to be an inlet type",
                    ["Set left to velocity_inlet"],
                )
            if b.right.type not in (
                BoundaryType.PRESSURE_OUTLET,
                BoundaryType.OPEN_OUTLET,
                BoundaryType.ADVECTIVE_OUTLET,
                BoundaryType.NON_REFLECTING_OUTLET,
            ):
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "INLET_OUTLET mode requires right boundary to be an outlet type",
                    ["Set right to pressure_outlet"],
                )

        elif mode == FlowMode.PERIODIC_FORCED:
            # Must have periodic pair and forcing
            if not spec.is_periodic:
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "PERIODIC_FORCED mode requires left/right to be periodic",
                    ["Set both left and right to periodic"],
                )
            if not spec.forcing.pressure_gradient.enabled:
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "PERIODIC_FORCED mode requires pressure gradient forcing",
                    ["Enable pressure_gradient in forcing"],
                )

        elif mode == FlowMode.PRESSURE_DIFFERENCE:
            # Both left and right must be pressure boundaries
            if b.left.type != BoundaryType.PRESSURE_BOUNDARY:
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "PRESSURE_DIFFERENCE mode requires left to be pressure_boundary",
                )
            if b.right.type != BoundaryType.PRESSURE_BOUNDARY:
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "PRESSURE_DIFFERENCE mode requires right to be pressure_boundary",
                )
            # Pressure values must be different
            left_p = b.left.pressure_value
            right_p = b.right.pressure_value
            if left_p is not None and right_p is not None and left_p <= right_p:
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "PRESSURE_DIFFERENCE mode requires left pressure > right pressure",
                    ["Increase left pressure", "Decrease right pressure"],
                )

        elif mode == FlowMode.OPEN_DOMAIN:
            # Left must be velocity inlet, top must be freestream/open
            if b.left.type != BoundaryType.VELOCITY_INLET:
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "OPEN_DOMAIN mode requires left to be velocity_inlet",
                )
            if b.top.type not in (BoundaryType.FREESTREAM, BoundaryType.OPEN_BOUNDARY):
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "OPEN_DOMAIN mode requires top to be freestream or open_boundary",
                )

        elif mode == FlowMode.WALL_DRIVEN:
            # Top must be moving wall or shear stress
            if b.top.type not in (BoundaryType.MOVING_WALL, BoundaryType.SHEAR_STRESS):
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "WALL_DRIVEN mode requires top to be moving_wall or shear_stress",
                )

        elif mode == FlowMode.COMBINED_DRIVING:
            # Must have at least two driving mechanisms
            driving_count = 0
            if spec.is_periodic:
                driving_count += 1
            if spec.forcing.pressure_gradient.enabled:
                driving_count += 1
            if b.top.type in (BoundaryType.MOVING_WALL, BoundaryType.SHEAR_STRESS):
                driving_count += 1
            if b.left.type == BoundaryType.VELOCITY_INLET:
                driving_count += 1
            if driving_count < 2:
                raise BoundaryTopologyError(
                    "INVALID_FLOW_TOPOLOGY",
                    "COMBINED_DRIVING mode requires at least two driving mechanisms",
                )


__all__ = [
    "BoundaryCombinationValidator",
    "BoundaryTopologyError",
    "BoundaryTopologyResolver",
]
