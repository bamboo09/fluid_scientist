"""CylinderFlow2DDraftReadinessEvaluator — draft readiness and state machine.

Implements the readiness evaluation from Section 10 of the plan.

States (only these four):
  NEEDS_CLARIFICATION  — blocking issues exist
  AWAITING_CONFIRMATION — no blocking, but recommendations pending
  READY_TO_CONFIRM     — all requirements met
  SPEC_CONFIRMED       — user has confirmed

HARD CONSTRAINTS:
  - If blocking_issues is non-empty → status MUST NOT be READY_TO_CONFIRM
  - If observables is empty → status MUST NOT be READY_TO_CONFIRM
  - If analysis_goals is empty → status MUST NOT be READY_TO_CONFIRM
"""

from __future__ import annotations

from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    DraftStatus,
    FieldSource,
    FieldStatus,
    FlowMode,
    SemanticBoundaryType,
)


class CylinderFlow2DDraftReadinessEvaluator:
    """Evaluates draft readiness and determines the correct status."""

    def evaluate(self, spec: CylinderFlow2DExperimentSpecV1) -> DraftStatus:
        """Evaluate the spec and return the correct draft status.

        The evaluation follows a strict waterfall:
        1. Check blocking issues → NEEDS_CLARIFICATION
        2. Check minimum completeness → NEEDS_CLARIFICATION
        3. Check recommendations pending → AWAITING_CONFIRMATION
        4. All clear → READY_TO_CONFIRM
        """
        blocking = self._collect_blocking_issues(spec)

        if blocking:
            spec.blocking_issues = blocking
            spec.draft_status = DraftStatus.NEEDS_CLARIFICATION
            return DraftStatus.NEEDS_CLARIFICATION

        # Check minimum completeness
        if not self._is_minimum_complete(spec):
            spec.draft_status = DraftStatus.NEEDS_CLARIFICATION
            return DraftStatus.NEEDS_CLARIFICATION

        # Check if there are pending recommendations
        if self._has_pending_recommendations(spec):
            spec.draft_status = DraftStatus.AWAITING_CONFIRMATION
            return DraftStatus.AWAITING_CONFIRMATION

        spec.draft_status = DraftStatus.READY_TO_CONFIRM
        spec.blocking_issues = []
        return DraftStatus.READY_TO_CONFIRM

    def _collect_blocking_issues(
        self, spec: CylinderFlow2DExperimentSpecV1
    ) -> list[dict]:
        """Collect all blocking issues. Returns empty list if none."""
        issues: list[dict] = []

        # 1. Cylinder type must exist (or bump profile / triangle / rectangle must be enabled)
        if not spec.has_cylinder and not spec.has_bottom_profile and not spec.has_triangle and not spec.has_rectangle and not spec.has_trapezoid:
            issues.append({
                "code": "CYLINDER_TYPE_MISSING",
                "message": "障碍物类型未指定，请描述圆柱、三角凸起、矩形障碍物等，或设置底面凸起。",
                "severity": "blocking",
            })

        # 2. Cylinder characteristic dimension must exist or be derivable
        if spec.has_cylinder and spec.get_characteristic_dimension() is None:
            issues.append({
                "code": "CYLINDER_DIMENSION_MISSING",
                "message": "圆柱半径或直径缺失，请提供圆柱尺寸。",
                "severity": "blocking",
            })

        # 3. Flow topology must be valid
        flow_mode = spec.flow_topology.get("mode")
        if flow_mode is None:
            issues.append({
                "code": "FLOW_TOPOLOGY_UNRESOLVED",
                "message": "流动拓扑未确定，请描述入口、出口或驱动方式。",
                "severity": "blocking",
            })

        # 4. 2D front/back must be empty
        if spec.boundaries.front.semantic_type != SemanticBoundaryType.EMPTY:
            issues.append({
                "code": "FRONT_NOT_EMPTY",
                "message": "二维模拟的前侧边界必须为empty。",
                "severity": "blocking",
            })
        if spec.boundaries.back.semantic_type != SemanticBoundaryType.EMPTY:
            issues.append({
                "code": "BACK_NOT_EMPTY",
                "message": "二维模拟的后侧边界必须为empty。",
                "severity": "blocking",
            })

        # 5. Boundary combination must be valid
        from fluid_scientist.cylinder_flow_2d.boundary_topology import (
            CylinderFlow2DBoundaryCombinationValidator,
        )
        validator = CylinderFlow2DBoundaryCombinationValidator()
        boundary_issues = validator.validate(spec)
        for issue in boundary_issues:
            if issue.get("severity") == "blocking":
                issues.append(issue)

        # 6. If inlet mode requires velocity but velocity is missing
        left_type = spec.boundaries.left.semantic_type
        if left_type in (
            SemanticBoundaryType.UNIFORM_VELOCITY_INLET,
            SemanticBoundaryType.TIME_VARYING_VELOCITY_INLET,
            SemanticBoundaryType.SPATIAL_NONUNIFORM_VELOCITY_INLET,
        ):
            if spec.boundaries.left.inlet_velocity is None:
                issues.append({
                    "code": "INLET_VELOCITY_MISSING",
                    "message": "入口速度未指定，请提供来流速度。",
                    "severity": "blocking",
                })

        # 7. Pressure gradient missing unit
        if spec.forcing.pressure_gradient.enabled:
            if spec.forcing.pressure_gradient.magnitude.value is None:
                issues.append({
                    "code": "PRESSURE_GRADIENT_MISSING_MAGNITUDE",
                    "message": "压力梯度大小未指定。",
                    "severity": "blocking",
                })
            if spec.forcing.pressure_gradient.unit.value is None:
                issues.append({
                    "code": "PRESSURE_GRADIENT_MISSING_UNIT",
                    "message": "压力梯度单位未指定，请确认是Pa/m还是m/s²。",
                    "severity": "blocking",
                })

        # 8. Shear stress missing magnitude
        if spec.boundaries.top.semantic_type == SemanticBoundaryType.SHEAR_STRESS:
            if spec.boundaries.top.shear_magnitude is None:
                issues.append({
                    "code": "SHEAR_STRESS_MISSING_MAGNITUDE",
                    "message": "剪切应力大小未指定。",
                    "severity": "blocking",
                })

        # 9. Cylinder wall distance ambiguity — only if NOT resolved
        for amb in spec.ambiguities:
            if amb.get("id") == "cylinder_wall_distance_meaning" and not amb.get("resolved"):
                issues.append({
                    "code": "CYLINDER_WALL_DISTANCE_AMBIGUOUS",
                    "message": amb.get("description", "圆柱距壁面的距离语义不明确。"),
                    "severity": "blocking",
                    "question_id": "cylinder_wall_distance_meaning",
                })

        # 10. Observation point/section missing — only for USER_EXPLICIT observables
        # Recommended observables with missing fields are AWAITING_CONFIRMATION, not blocking
        for obs in spec.observables:
            if obs.missing_fields and obs.source in (
                FieldSource.USER_EXPLICIT,
                FieldSource.USER_CONFIRMED,
            ):
                for field in obs.missing_fields:
                    if field == "point":
                        issues.append({
                            "code": "OBSERVATION_POINT_MISSING",
                            "message": f"观测点位置未指定（观测量: {obs.type.value}）。",
                            "severity": "blocking",
                        })
                    elif field == "section_x":
                        issues.append({
                            "code": "OBSERVATION_SECTION_MISSING",
                            "message": f"截面位置未指定（观测量: {obs.type.value}）。",
                            "severity": "blocking",
                        })

        return issues

    def _is_minimum_complete(
        self, spec: CylinderFlow2DExperimentSpecV1
    ) -> bool:
        """Check if the minimum requirements for any status are met."""
        # At least one obstacle type must exist (cylinder, triangle, rectangle, etc.)
        has_any_obstacle = (
            spec.has_cylinder
            or spec.has_bottom_profile
            or spec.has_triangle
            or spec.has_rectangle
            or spec.has_trapezoid
        )
        if not has_any_obstacle:
            return False

        # Characteristic dimension exists or is derivable (only for cylinder)
        if spec.has_cylinder and spec.get_characteristic_dimension() is None:
            return False

        # Flow topology is resolved
        if spec.flow_topology.get("mode") is None:
            return False

        # front/back are empty
        if spec.boundaries.front.semantic_type != SemanticBoundaryType.EMPTY:
            return False
        if spec.boundaries.back.semantic_type != SemanticBoundaryType.EMPTY:
            return False

        # At least one observable exists
        if len(spec.observables) == 0:
            return False

        # At least one analysis goal exists
        if len(spec.analysis_goals) == 0:
            return False

        return True

    def _has_pending_recommendations(
        self, spec: CylinderFlow2DExperimentSpecV1
    ) -> bool:
        """Check if there are recommendations awaiting user confirmation."""
        # Check observables with AWAITING_CONFIRMATION
        for obs in spec.observables:
            if obs.status == FieldStatus.AWAITING_CONFIRMATION:
                return True

        # Check analysis goals with AWAITING_CONFIRMATION
        for goal in spec.analysis_goals:
            if goal.status == FieldStatus.AWAITING_CONFIRMATION:
                return True

        # Check fluid properties with MODEL_RECOMMENDED
        if spec.fluid.type.status == FieldStatus.AWAITING_CONFIRMATION:
            return True
        if spec.fluid.density_kg_m3.status == FieldStatus.AWAITING_CONFIRMATION:
            return True
        if spec.fluid.kinematic_viscosity_m2_s.status == FieldStatus.AWAITING_CONFIRMATION:
            return True

        # Check simulation time mode
        if spec.simulation.time_mode.value == "auto":
            return True

        return False


__all__ = ["CylinderFlow2DDraftReadinessEvaluator"]
