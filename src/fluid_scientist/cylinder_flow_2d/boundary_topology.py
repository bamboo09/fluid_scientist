"""CylinderFlow2D boundary topology resolution and combination validation.

This module provides two deterministic (code-only, no-LLM) components that
operate on :class:`CylinderFlow2DExperimentSpecV1`:

1. :class:`CylinderFlow2DBoundaryTopologyResolver`
   Inspects the boundary configuration and forcing specification to
   determine which of the six supported :class:`FlowMode` values applies
   to the experiment.

2. :class:`CylinderFlow2DBoundaryCombinationValidator`
   Validates that the boundary type combinations are physically
   consistent.  Rather than raising exceptions, the validator returns a
   list of issue dictionaries with natural-language (Chinese) messages
   so the UI can present actionable guidance to the user.

Design contract
---------------
* **2D hard rule**: ``front`` and ``back`` must **always** be
  ``empty``.  This is enforced by the model's ``enforce_2d_boundary``
  validator, but the combination validator performs a defensive
  re-check and flags any violation as a blocking issue.
* **Error messages are Chinese natural language**, not bare error
  codes.  Each issue carries a ``code`` for programmatic handling and
  a ``message`` for human display.
* The resolver never raises — it always returns a ``FlowMode`` (defaulting
  to ``INLET_OUTLET`` when the configuration is ambiguous).  It is the
  validator's responsibility to flag problematic configurations.
"""

from __future__ import annotations

from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    FieldSource,
    FieldStatus,
    FlowMode,
    SemanticBoundaryType,
)

__all__ = [
    "CylinderFlow2DBoundaryTopologyResolver",
    "CylinderFlow2DBoundaryCombinationValidator",
]


# ---------------------------------------------------------------------------
# Semantic boundary-type groups
# ---------------------------------------------------------------------------
#
# These frozensets group :class:`SemanticBoundaryType` values into the
# categories used by the resolver and validator.  Centralising them here
# keeps the detection logic readable and avoids scattered ``in`` checks.

#: Velocity-inlet boundary types — these *drive* flow by prescribing
#: an incoming velocity.
_VELOCITY_INLET_TYPES: frozenset[SemanticBoundaryType] = frozenset({
    SemanticBoundaryType.UNIFORM_VELOCITY_INLET,
    SemanticBoundaryType.TIME_VARYING_VELOCITY_INLET,
    SemanticBoundaryType.SPATIAL_NONUNIFORM_VELOCITY_INLET,
})

#: All inlet types (velocity inlets + pressure inlet).
_INLET_TYPES: frozenset[SemanticBoundaryType] = _VELOCITY_INLET_TYPES | {
    SemanticBoundaryType.PRESSURE_INLET,
}

#: Outlet boundary types — these allow flow to leave the domain.
_OUTLET_TYPES: frozenset[SemanticBoundaryType] = frozenset({
    SemanticBoundaryType.PRESSURE_OUTLET,
    SemanticBoundaryType.OPEN_OUTLET,
    SemanticBoundaryType.ADVECTIVE_OUTLET,
})

#: Static (non-moving) wall types.
_STATIC_WALL_TYPES: frozenset[SemanticBoundaryType] = frozenset({
    SemanticBoundaryType.NO_SLIP_WALL,
    SemanticBoundaryType.SLIP_WALL,
})

#: Moving-wall types — these *drive* flow through wall motion or shear.
_MOVING_WALL_TYPES: frozenset[SemanticBoundaryType] = frozenset({
    SemanticBoundaryType.MOVING_WALL,
    SemanticBoundaryType.SHEAR_STRESS,
})

#: Open / freestream boundary types — used for open-domain configurations.
_OPEN_TYPES: frozenset[SemanticBoundaryType] = frozenset({
    SemanticBoundaryType.FREESTREAM,
    SemanticBoundaryType.OPEN_BOUNDARY,
})


# ---------------------------------------------------------------------------
# Driving-mechanism detection (shared by resolver and validator)
# ---------------------------------------------------------------------------


def _count_driving_mechanisms(
    spec: CylinderFlow2DExperimentSpecV1,
) -> int:
    """Count the number of independent driving mechanisms in *spec*.

    A "driving mechanism" is any boundary condition or forcing that can
    sustain fluid motion on its own:

    * **Velocity inlet** — left boundary prescribes incoming velocity.
    * **Pressure gradient forcing** — ``forcing.pressure_gradient.enabled``.
    * **Moving wall / shear stress** — top boundary drives flow via
      wall motion or applied shear.
    * **Pressure difference** — both left and right are
      ``pressure_boundary`` with different pressure values.
    * **Body force** — ``forcing.body_force.enabled``.

    Periodic boundaries alone are **not** a driving mechanism; they only
    connect the domain.  The driving comes from the pressure gradient
    that is typically paired with periodic boundaries.

    Returns
    -------
    int
        The number of distinct driving mechanisms detected (0–5).
    """
    b = spec.boundaries
    left = b.left.semantic_type
    right = b.right.semantic_type
    top = b.top.semantic_type

    count = 0
    if left in _VELOCITY_INLET_TYPES:
        count += 1
    if spec.forcing.pressure_gradient.enabled:
        count += 1
    if top in _MOVING_WALL_TYPES:
        count += 1
    if (
        left == SemanticBoundaryType.PRESSURE_BOUNDARY
        and right == SemanticBoundaryType.PRESSURE_BOUNDARY
    ):
        count += 1
    if spec.forcing.body_force.enabled:
        count += 1
    return count


# ---------------------------------------------------------------------------
# CylinderFlow2DBoundaryTopologyResolver
# ---------------------------------------------------------------------------


class CylinderFlow2DBoundaryTopologyResolver:
    """Resolve flow topology from boundary configuration.

    The resolver inspects the boundary types and forcing specification
    to determine which of the six supported :class:`FlowMode` values
    applies:

    ============================ ==================================================
    Mode                         Detection criteria
    ============================ ==================================================
    ``INLET_OUTLET``             Left = velocity inlet, right = outlet
    ``PERIODIC_FORCED``          Left/right = periodic (pressure gradient expected)
    ``PRESSURE_DIFFERENCE``      Left and right = pressure boundary
    ``OPEN_DOMAIN``              Left = velocity inlet, top = freestream / open
    ``WALL_DRIVEN``              Top = moving wall or shear stress
    ``COMBINED_DRIVING``         Two or more driving mechanisms present
    ============================ ==================================================

    When multiple driving mechanisms are detected (≥ 2), the resolver
    returns ``COMBINED_DRIVING`` regardless of the specific combination.
    Otherwise it returns the mode corresponding to the single driving
    mechanism, or ``INLET_OUTLET`` as a fallback when no mechanism is
    clearly identifiable (the validator will flag the issue).
    """

    def resolve(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
    ) -> FlowMode:
        """Determine flow mode from boundary configuration.

        Parameters
        ----------
        spec:
            The complete experiment specification.

        Returns
        -------
        FlowMode
            The detected flow mode.  Never raises; ambiguous or
            under-specified configurations default to
            :attr:`FlowMode.INLET_OUTLET` so the validator can report
            the missing pieces.
        """
        b = spec.boundaries
        left = b.left.semantic_type
        right = b.right.semantic_type
        top = b.top.semantic_type

        is_periodic = (
            left == SemanticBoundaryType.PERIODIC
            and right == SemanticBoundaryType.PERIODIC
        )
        has_pressure_gradient = spec.forcing.pressure_gradient.enabled
        has_body_force = spec.forcing.body_force.enabled
        has_moving_wall = top in _MOVING_WALL_TYPES
        has_velocity_inlet = left in _VELOCITY_INLET_TYPES
        has_pressure_difference = (
            left == SemanticBoundaryType.PRESSURE_BOUNDARY
            and right == SemanticBoundaryType.PRESSURE_BOUNDARY
        )

        # --- Combined driving: two or more independent mechanisms ---
        driving_count = _count_driving_mechanisms(spec)
        if driving_count >= 2:
            return FlowMode.COMBINED_DRIVING

        # --- Single (or zero) driving mechanism ---
        if is_periodic:
            # Periodic boundaries — the intended mode is PERIODIC_FORCED.
            # If pressure_gradient is missing the validator will flag it.
            return FlowMode.PERIODIC_FORCED

        if has_pressure_difference:
            return FlowMode.PRESSURE_DIFFERENCE

        if has_velocity_inlet and top in _OPEN_TYPES:
            return FlowMode.OPEN_DOMAIN

        if has_moving_wall:
            return FlowMode.WALL_DRIVEN

        if has_velocity_inlet:
            return FlowMode.INLET_OUTLET

        # Body-force-only does not map cleanly to any single mode.
        # Fall back to INLET_OUTLET; the validator will flag the
        # under-specified configuration.
        return FlowMode.INLET_OUTLET


# ---------------------------------------------------------------------------
# CylinderFlow2DBoundaryCombinationValidator
# ---------------------------------------------------------------------------


class CylinderFlow2DBoundaryCombinationValidator:
    """Validate boundary combinations for physical consistency.

    Unlike :class:`~fluid_scientist.obstacle_flow.boundary_validator.BoundaryCombinationValidator`
    (which raises exceptions), this validator returns a **list of issue
    dictionaries** so the caller can present all problems at once rather
    than failing on the first error.

    Each issue has the shape::

        {
            "code": str,            # machine-readable identifier
            "message": str,         # Chinese natural-language message
            "severity": "blocking" | "warning",
        }

    Hard rules enforced
    -------------------
    * **2D hard rule**: ``front`` and ``back`` must be ``empty`` with
      ``SYSTEM_DERIVED`` provenance — never modified by model or user.
    * **Periodic pairing**: periodic left requires periodic right (and
      vice versa).
    * **Periodic + velocity inlet**: periodic boundaries cannot coexist
      with a velocity inlet (boundary type or ``inlet_profile``).
    * **No driving force**: all-static-wall configurations with no
      forcing, moving wall, or body force are rejected.
    """

    def __init__(self) -> None:
        self._resolver = CylinderFlow2DBoundaryTopologyResolver()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
    ) -> list[dict]:
        """Validate boundary combinations.

        Parameters
        ----------
        spec:
            The complete experiment specification.

        Returns
        -------
        list[dict]
            A list of issue dictionaries.  An empty list means the
            configuration is valid.  Each issue has keys ``code``,
            ``message`` (Chinese), and ``severity``
            (``"blocking"`` or ``"warning"``).
        """
        issues: list[dict] = []

        # 1. 2D hard rule — front/back must be empty
        self._check_2d_hard_rule(spec, issues)

        # 2. Periodic boundary pairing
        self._check_periodic_pairing(spec, issues)

        # 3. Periodic + velocity inlet conflict
        self._check_periodic_with_inlet(spec, issues)

        # 4. Periodic without forcing
        self._check_periodic_without_forcing(spec, issues)

        # 5. All static walls with no driving force
        self._check_no_driving_force(spec, issues)

        # 6. Topology-specific rules
        flow_mode = self._resolver.resolve(spec)
        self._validate_topology_specific(spec, flow_mode, issues)

        return issues

    # ------------------------------------------------------------------
    # Individual rule checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_2d_hard_rule(
        spec: CylinderFlow2DExperimentSpecV1,
        issues: list[dict],
    ) -> None:
        """Verify front/back are ``empty`` with ``SYSTEM_DERIVED`` source.

        The model's ``enforce_2d_boundary`` validator already enforces
        this at instantiation time, but this method performs a defensive
        re-check in case the spec was mutated after creation.

        Three aspects are verified for each of ``front`` and ``back``:

        * ``semantic_type`` must be :attr:`SemanticBoundaryType.EMPTY`.
        * ``source`` must be :attr:`FieldSource.SYSTEM_DERIVED`.
        * ``status`` must be :attr:`FieldStatus.RESOLVED`.
        """
        for side_name, side_label in (("front", "前侧"), ("back", "后侧")):
            boundary = getattr(spec.boundaries, side_name)
            if boundary.semantic_type != SemanticBoundaryType.EMPTY:
                issues.append({
                    "code": f"{side_name.upper()}_NOT_EMPTY",
                    "message": (
                        f"二维计算中{side_label}（{side_name}）边界必须为 "
                        f"empty 类型，此为不可逾越的硬性规则，"
                        f"任何模型或用户均不可修改。"
                    ),
                    "severity": "blocking",
                })
            elif boundary.source != FieldSource.SYSTEM_DERIVED:
                issues.append({
                    "code": f"{side_name.upper()}_SOURCE_VIOLATION",
                    "message": (
                        f"二维{side_label}（{side_name}）边界的来源（source）"
                        f"应为 SYSTEM_DERIVED，当前为 "
                        f"{boundary.source.value}，可能被非法修改。"
                    ),
                    "severity": "warning",
                })
            elif boundary.status != FieldStatus.RESOLVED:
                issues.append({
                    "code": f"{side_name.upper()}_STATUS_VIOLATION",
                    "message": (
                        f"二维{side_label}（{side_name}）边界的状态（status）"
                        f"应为 RESOLVED，当前为 "
                        f"{boundary.status.value}，可能被非法修改。"
                    ),
                    "severity": "warning",
                })

    @staticmethod
    def _check_periodic_pairing(
        spec: CylinderFlow2DExperimentSpecV1,
        issues: list[dict],
    ) -> None:
        """Reject periodic left + non-periodic right (and vice versa)."""
        b = spec.boundaries
        left = b.left.semantic_type
        right = b.right.semantic_type

        left_periodic = left == SemanticBoundaryType.PERIODIC
        right_periodic = right == SemanticBoundaryType.PERIODIC

        if left_periodic == right_periodic:
            return  # both periodic or both non-periodic — OK

        # Determine which side is periodic and what the other side is
        if left_periodic:
            periodic_side, other_type = "左", right
        else:
            periodic_side, other_type = "右", left

        # If the non-periodic side is an inlet or outlet, use the
        # canonical example message from the task spec.
        if other_type in _INLET_TYPES or other_type in _OUTLET_TYPES:
            issues.append({
                "code": "PERIODIC_MISMATCH",
                "message": (
                    "左右周期边界不能再分别设置成入口和出口，"
                    "请选择周期压力驱动或入口—出口模式。"
                ),
                "severity": "blocking",
            })
        else:
            issues.append({
                "code": "PERIODIC_MISMATCH",
                "message": (
                    f"{periodic_side}侧为周期边界而另一侧不是，"
                    f"周期边界必须成对出现。请将两侧均设为周期边界，"
                    f"或将周期侧改为非周期类型。"
                ),
                "severity": "blocking",
            })

    @staticmethod
    def _check_periodic_with_inlet(
        spec: CylinderFlow2DExperimentSpecV1,
        issues: list[dict],
    ) -> None:
        """Reject periodic boundaries coexisting with a velocity inlet.

        This covers two sub-cases:

        1. One side is periodic and the other is a velocity-inlet type
           (already caught by :meth:`_check_periodic_pairing`, so we do
           not duplicate the issue here).
        2. Both sides are periodic **but** ``inlet_profile`` is enabled
           — the user has configured an inlet velocity profile alongside
           periodic boundaries, which is physically contradictory.
        """
        b = spec.boundaries
        left = b.left.semantic_type
        right = b.right.semantic_type

        is_periodic = (
            left == SemanticBoundaryType.PERIODIC
            and right == SemanticBoundaryType.PERIODIC
        )
        if not is_periodic:
            return

        # Sub-case 2: periodic + inlet_profile enabled
        if spec.inlet_profile.enabled:
            issues.append({
                "code": "PERIODIC_WITH_INLET",
                "message": (
                    "周期边界与速度入口不能同时使用。"
                    "周期边界意味着左右两侧流体连通，"
                    "无法再单独施加入口速度。"
                    "请选择周期压力驱动或入口—出口模式。"
                ),
                "severity": "blocking",
            })

    @staticmethod
    def _check_periodic_without_forcing(
        spec: CylinderFlow2DExperimentSpecV1,
        issues: list[dict],
    ) -> None:
        """Flag periodic boundaries without any driving force."""
        b = spec.boundaries
        left = b.left.semantic_type
        right = b.right.semantic_type

        is_periodic = (
            left == SemanticBoundaryType.PERIODIC
            and right == SemanticBoundaryType.PERIODIC
        )
        if not is_periodic:
            return

        has_pressure_gradient = spec.forcing.pressure_gradient.enabled
        has_body_force = spec.forcing.body_force.enabled
        has_moving_wall = b.top.semantic_type in _MOVING_WALL_TYPES

        if not has_pressure_gradient and not has_body_force and not has_moving_wall:
            issues.append({
                "code": "PERIODIC_NO_FORCING",
                "message": (
                    "左右均为周期边界但未启用压力梯度驱动，"
                    "流体将无法产生运动。"
                    "请在 forcing 中启用 pressure_gradient，"
                    "或设置运动壁面/体积力作为替代驱动力。"
                ),
                "severity": "blocking",
            })

    @staticmethod
    def _check_no_driving_force(
        spec: CylinderFlow2DExperimentSpecV1,
        issues: list[dict],
    ) -> None:
        """Reject configurations where all walls are static with no forcing."""
        b = spec.boundaries
        left = b.left.semantic_type
        right = b.right.semantic_type
        top = b.top.semantic_type
        bottom = b.bottom_flat.semantic_type

        all_static_walls = (
            left in _STATIC_WALL_TYPES
            and right in _STATIC_WALL_TYPES
            and top in _STATIC_WALL_TYPES
            and bottom in _STATIC_WALL_TYPES
        )
        if not all_static_walls:
            return

        has_forcing = (
            spec.forcing.pressure_gradient.enabled
            or spec.forcing.body_force.enabled
        )
        has_moving = (
            top in _MOVING_WALL_TYPES
            or bottom == SemanticBoundaryType.MOVING_WALL
        )
        if not has_forcing and not has_moving:
            issues.append({
                "code": "NO_DRIVING_FORCE",
                "message": (
                    "所有边界均为静止壁面，且未启用任何驱动力"
                    "（压力梯度、体积力或运动壁面），"
                    "流体将无法产生运动。"
                    "请至少添加一种驱动机制："
                    "设置入口边界、启用压力梯度、或设置运动壁面。"
                ),
                "severity": "blocking",
            })

    # ------------------------------------------------------------------
    # Topology-specific validation
    # ------------------------------------------------------------------

    def _validate_topology_specific(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        mode: FlowMode,
        issues: list[dict],
    ) -> None:
        """Validate rules specific to each flow topology.

        Parameters
        ----------
        spec:
            The experiment specification.
        mode:
            The flow mode resolved by
            :class:`CylinderFlow2DBoundaryTopologyResolver`.
        issues:
            The issue list to append to (mutated in place).
        """
        b = spec.boundaries
        left = b.left.semantic_type
        right = b.right.semantic_type
        top = b.top.semantic_type

        if mode == FlowMode.INLET_OUTLET:
            if left not in _INLET_TYPES:
                issues.append({
                    "code": "INLET_OUTLET_NO_INLET",
                    "message": (
                        "入口—出口模式要求左侧边界为入口类型"
                        "（速度入口或压力入口），"
                        "当前左侧边界类型不满足要求。"
                    ),
                    "severity": "blocking",
                })
            if right not in _OUTLET_TYPES:
                issues.append({
                    "code": "INLET_OUTLET_NO_OUTLET",
                    "message": (
                        "入口—出口模式要求右侧边界为出口类型"
                        "（压力出口、开放出口或对流出口），"
                        "当前右侧边界类型不满足要求。"
                    ),
                    "severity": "blocking",
                })

        elif mode == FlowMode.PERIODIC_FORCED:
            if not spec.is_periodic:
                issues.append({
                    "code": "PERIODIC_FORCED_NO_PERIODIC",
                    "message": (
                        "周期压力驱动模式要求左右边界均为周期边界。"
                    ),
                    "severity": "blocking",
                })
            if not spec.forcing.pressure_gradient.enabled:
                issues.append({
                    "code": "PERIODIC_FORCED_NO_GRADIENT",
                    "message": (
                        "周期压力驱动模式要求启用压力梯度"
                        "（pressure_gradient）作为驱动力。"
                    ),
                    "severity": "blocking",
                })

        elif mode == FlowMode.PRESSURE_DIFFERENCE:
            if left != SemanticBoundaryType.PRESSURE_BOUNDARY:
                issues.append({
                    "code": "PRESSURE_DIFF_NO_LEFT",
                    "message": (
                        "压差驱动模式要求左侧边界为压力边界"
                        "（pressure_boundary）。"
                    ),
                    "severity": "blocking",
                })
            if right != SemanticBoundaryType.PRESSURE_BOUNDARY:
                issues.append({
                    "code": "PRESSURE_DIFF_NO_RIGHT",
                    "message": (
                        "压差驱动模式要求右侧边界为压力边界"
                        "（pressure_boundary）。"
                    ),
                    "severity": "blocking",
                })
            # Verify pressure gradient direction
            left_p = b.left.pressure_value
            right_p = b.right.pressure_value
            if (
                left_p is not None
                and right_p is not None
                and left_p <= right_p
            ):
                issues.append({
                    "code": "PRESSURE_DIFF_GRADIENT_INVALID",
                    "message": (
                        f"压差驱动模式要求左侧压力大于右侧压力"
                        f"（当前左侧 {left_p} Pa，右侧 {right_p} Pa），"
                        f"否则流体无法从左向右流动。"
                    ),
                    "severity": "blocking",
                })

        elif mode == FlowMode.OPEN_DOMAIN:
            if left not in _VELOCITY_INLET_TYPES:
                issues.append({
                    "code": "OPEN_DOMAIN_NO_INLET",
                    "message": (
                        "开放域模式要求左侧边界为速度入口类型。"
                    ),
                    "severity": "blocking",
                })
            if top not in _OPEN_TYPES:
                issues.append({
                    "code": "OPEN_DOMAIN_NO_OPEN_TOP",
                    "message": (
                        "开放域模式要求顶部边界为自由流（freestream）"
                        "或开放边界（open_boundary）。"
                    ),
                    "severity": "blocking",
                })

        elif mode == FlowMode.WALL_DRIVEN:
            if top not in _MOVING_WALL_TYPES:
                issues.append({
                    "code": "WALL_DRIVEN_NO_MOVING_WALL",
                    "message": (
                        "壁面驱动模式要求顶部边界为运动壁面"
                        "（moving_wall）或剪切应力（shear_stress）。"
                    ),
                    "severity": "blocking",
                })

        elif mode == FlowMode.COMBINED_DRIVING:
            if _count_driving_mechanisms(spec) < 2:
                issues.append({
                    "code": "COMBINED_DRIVING_INSUFFICIENT",
                    "message": (
                        "组合驱动模式要求至少存在两种驱动机制"
                        "（如入口速度+运动壁面、"
                        "周期压力梯度+运动壁面等），"
                        "当前仅检测到一种或零种。"
                    ),
                    "severity": "blocking",
                })
