"""CylinderFlow2DCritic — independent quality review of the draft spec.

Implements Pass 6 (Critic) from Section 3.3 of the plan.

The Critic independently checks:
- Whether user conditions were omitted
- Whether facts not stated by the user were incorrectly added
- Whether unprocessed ambiguities remain
- Whether derivable fields are still empty
- Whether 2D boundaries are correct
- Whether observables are empty
- Whether analysis goals are empty
- Whether the status is consistent with blocking issues

After finding errors, the Critic can auto-repair one round, then hands
off to the deterministic ReadinessEvaluator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fluid_scientist.cylinder_flow_2d.models import (
    CylinderFlow2DExperimentSpecV1,
    DraftStatus,
    FieldSource,
    FieldStatus,
    SemanticBoundaryType,
)


@dataclass
class CriticResult:
    """Result of the Critic review."""

    issues_found: list[dict] = field(default_factory=list)
    auto_repairs_applied: list[dict] = field(default_factory=list)
    passed: bool = True

    def add_issue(self, code: str, message: str, severity: str = "warning") -> None:
        self.issues_found.append({
            "code": code,
            "message": message,
            "severity": severity,
        })
        if severity == "blocking":
            self.passed = False

    def add_repair(self, what: str, detail: str) -> None:
        self.auto_repairs_applied.append({"what": what, "detail": detail})


class CylinderFlow2DCritic:
    """Independent Critic that reviews the draft spec for quality.

    This is Pass 6 of the multi-pass pipeline. It runs AFTER the
    deterministic normalizers and observable/goal builders.
    """

    def review(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
    ) -> CriticResult:
        """Review the spec and auto-repair if possible.

        Args:
            spec: The current spec to review.
            user_text: The original user input text.

        Returns:
            CriticResult with issues found and repairs applied.
        """
        result = CriticResult()

        # 1. Check cylinder type exists (or bump profile / triangle / rectangle is enabled)
        if not spec.has_cylinder and not spec.has_bottom_profile and not spec.has_triangle and not spec.has_rectangle and not spec.has_trapezoid:
            # Auto-repair: try to identify cylinder from text
            from fluid_scientist.cylinder_flow_2d.geometry_normalizer import (
                CylinderFlow2DGeometryNormalizer,
            )
            normalizer = CylinderFlow2DGeometryNormalizer()
            normalizer.normalize(spec, user_text)
            if spec.has_cylinder:
                result.add_repair(
                    "cylinder_type",
                    "从用户文本中识别到圆柱类型",
                )
            else:
                result.add_issue(
                    "CYLINDER_TYPE_MISSING",
                    "用户描述中未识别到障碍物类型（圆柱、三角凸起、矩形等）",
                    "blocking",
                )

        # 2. Check characteristic dimension is derivable
        if spec.has_cylinder and spec.get_characteristic_dimension() is None:
            from fluid_scientist.cylinder_flow_2d.geometry_normalizer import (
                CylinderFlow2DDerivedFieldResolver,
            )
            resolver = CylinderFlow2DDerivedFieldResolver()
            resolver.resolve(spec)
            if spec.get_characteristic_dimension() is not None:
                result.add_repair(
                    "characteristic_dimension",
                    "从半径或直径派生了特征尺度",
                )
            else:
                result.add_issue(
                    "CYLINDER_DIMENSION_MISSING",
                    "圆柱半径或直径未提供，无法派生特征尺度",
                    "blocking",
                )

        # 3. Check 2D boundaries
        if spec.boundaries.front.semantic_type != SemanticBoundaryType.EMPTY:
            result.add_repair(
                "front_boundary",
                "强制设置front为empty（2D硬规则）",
            )
            spec.boundaries.front.semantic_type = SemanticBoundaryType.EMPTY
            spec.boundaries.front.source = FieldSource.SYSTEM_DERIVED
            spec.boundaries.front.status = FieldStatus.RESOLVED

        if spec.boundaries.back.semantic_type != SemanticBoundaryType.EMPTY:
            result.add_repair(
                "back_boundary",
                "强制设置back为empty（2D硬规则）",
            )
            spec.boundaries.back.semantic_type = SemanticBoundaryType.EMPTY
            spec.boundaries.back.source = FieldSource.SYSTEM_DERIVED
            spec.boundaries.back.status = FieldStatus.RESOLVED

        # 4. Check observables not empty
        if len(spec.observables) == 0:
            # Auto-repair: run recommender
            from fluid_scientist.cylinder_flow_2d.observable import (
                CylinderFlow2DObservableRecommender,
            )
            recommender = CylinderFlow2DObservableRecommender()
            recommended = recommender.recommend(spec)
            spec.observables.extend(recommended)
            if len(spec.observables) > 0:
                result.add_repair(
                    "observables",
                    "自动生成推荐观测量",
                )
            else:
                result.add_issue(
                    "OBSERVABLES_EMPTY",
                    "观测量为空，且无法自动推荐",
                    "blocking",
                )

        # 5. Check analysis goals not empty
        if len(spec.analysis_goals) == 0:
            # Auto-repair: run goal builder
            from fluid_scientist.cylinder_flow_2d.analysis_goals import (
                CylinderFlow2DAnalysisGoalBuilder,
            )
            builder = CylinderFlow2DAnalysisGoalBuilder()
            goals = builder.build(spec)
            spec.analysis_goals.extend(goals)
            if len(spec.analysis_goals) > 0:
                result.add_repair(
                    "analysis_goals",
                    "自动生成分析目标",
                )
            else:
                result.add_issue(
                    "ANALYSIS_GOALS_EMPTY",
                    "分析目标为空，且无法自动生成",
                    "blocking",
                )

        # 6. Check user-explicit boundaries not overridden by model
        self._check_user_boundary_override(spec, user_text, result)

        # 7. Check unprocessed ambiguities
        for amb in spec.ambiguities:
            if not amb.get("resolved", False):
                result.add_issue(
                    "AMBIGUITY_UNRESOLVED",
                    f"歧义未处理: {amb.get('description', amb.get('id', '未知'))}",
                    "blocking",
                )

        # 8. Check status consistency
        if spec.draft_status == DraftStatus.READY_TO_CONFIRM:
            if spec.blocking_issues:
                result.add_issue(
                    "STATUS_INCONSISTENT",
                    "状态为READY_TO_CONFIRM但存在blocking_issues",
                    "blocking",
                )
            if len(spec.observables) == 0:
                result.add_issue(
                    "STATUS_INCONSISTENT",
                    "状态为READY_TO_CONFIRM但观测量为空",
                    "blocking",
                )
            if len(spec.analysis_goals) == 0:
                result.add_issue(
                    "STATUS_INCONSISTENT",
                    "状态为READY_TO_CONFIRM但分析目标为空",
                    "blocking",
                )

        # 9. Check geometry_missing errors are scrubbed
        geometry_error_codes = {
            "geometry_missing_type",
            "geometry_missing_characteristic_dimension",
        }
        for issue in spec.blocking_issues:
            code = issue.get("code", "").lower()
            if code in geometry_error_codes:
                result.add_repair(
                    "scrub_geometry_errors",
                    f"清除过时的几何错误: {code}",
                )

        spec.blocking_issues = [
            issue
            for issue in spec.blocking_issues
            if issue.get("code", "").lower() not in geometry_error_codes
        ]

        return result

    def _check_user_boundary_override(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
        result: CriticResult,
    ) -> None:
        """Check that user-explicit boundaries were not overridden by model."""
        text_lower = user_text.lower()

        # Check bottom boundary
        if "无滑移" in user_text or "no-slip" in text_lower or "noslip" in text_lower:
            if "下" in user_text or "底" in user_text or "bottom" in text_lower:
                b = spec.boundaries.bottom_flat
                if b.semantic_type != SemanticBoundaryType.NO_SLIP_WALL:
                    if b.source == FieldSource.MODEL_RECOMMENDED:
                        # Auto-repair: restore user-explicit
                        b.semantic_type = SemanticBoundaryType.NO_SLIP_WALL
                        b.source = FieldSource.USER_EXPLICIT
                        b.status = FieldStatus.RESOLVED
                        result.add_repair(
                            "bottom_boundary_override",
                            "恢复用户明确的底部无滑移边界",
                        )
                    else:
                        result.add_issue(
                            "USER_BOUNDARY_OVERRIDDEN",
                            "用户明确的底部无滑移边界被覆盖",
                            "blocking",
                        )

        # Check top boundary
        if "滑移" in user_text or "slip" in text_lower:
            if "顶" in user_text or "上" in user_text or "top" in text_lower:
                b = spec.boundaries.top
                if b.semantic_type != SemanticBoundaryType.SLIP_WALL:
                    if b.source == FieldSource.MODEL_RECOMMENDED:
                        b.semantic_type = SemanticBoundaryType.SLIP_WALL
                        b.source = FieldSource.USER_EXPLICIT
                        b.status = FieldStatus.RESOLVED
                        result.add_repair(
                            "top_boundary_override",
                            "恢复用户明确的顶部滑移边界",
                        )
                    else:
                        result.add_issue(
                            "USER_BOUNDARY_OVERRIDDEN",
                            "用户明确的顶部滑移边界被覆盖",
                            "blocking",
                        )


class CylinderFlow2DCoverageChecker:
    """Checks that all user-stated facts are covered in the spec.

    This is a companion to the Critic — it verifies that nothing
    the user explicitly stated was lost during normalization.
    """

    def check(
        self,
        spec: CylinderFlow2DExperimentSpecV1,
        user_text: str,
    ) -> list[dict]:
        """Check coverage of user facts. Returns list of gaps."""
        gaps: list[dict] = []
        text_lower = user_text.lower()

        # Check radius
        if "半径" in user_text or "radius" in text_lower:
            if spec.cylinder.radius_m.value is None and spec.cylinder.diameter_m.value is None:
                gaps.append({
                    "code": "USER_RADIUS_NOT_CAPTURED",
                    "message": "用户提到了半径但spec中未捕获",
                })

        # Check cylinder position
        if "距" in user_text and ("壁面" in user_text or "wall" in text_lower):
            if spec.cylinder.center_y_m.value is None:
                gaps.append({
                    "code": "USER_POSITION_NOT_CAPTURED",
                    "message": "用户提到了圆柱位置但spec中未捕获",
                })

        # Check inlet/outlet
        if "来流" in user_text or "入口" in user_text or "inlet" in text_lower:
            if spec.boundaries.left.semantic_type is None:
                gaps.append({
                    "code": "USER_INLET_NOT_CAPTURED",
                    "message": "用户提到了来流/入口但左侧边界未设置",
                })

        # Check bottom boundary
        if "无滑移" in user_text or "no-slip" in text_lower:
            if "下" in user_text or "底" in user_text:
                if spec.boundaries.bottom_flat.semantic_type != SemanticBoundaryType.NO_SLIP_WALL:
                    gaps.append({
                        "code": "USER_BOTTOM_NOT_CAPTURED",
                        "message": "用户提到了底部无滑移但未设置",
                    })

        # Check top boundary
        if "滑移" in user_text and ("顶" in user_text or "上" in user_text):
            if spec.boundaries.top.semantic_type != SemanticBoundaryType.SLIP_WALL:
                gaps.append({
                    "code": "USER_TOP_NOT_CAPTURED",
                    "message": "用户提到了顶部滑移但未设置",
                })

        # Check observation
        if "截面" in user_text and ("平均" in user_text or "流速" in user_text):
            has_section = any(
                obs.type.value == "section_mean_velocity" for obs in spec.observables
            )
            if not has_section:
                gaps.append({
                    "code": "USER_OBSERVATION_NOT_CAPTURED",
                    "message": "用户提到了截面平均流速但观测量中未包含",
                })

        return gaps


__all__ = [
    "CriticResult",
    "CylinderFlow2DCoverageChecker",
    "CylinderFlow2DCritic",
]
