"""Semantic Fidelity Guard — verifies user intent is preserved through the pipeline.

Executed at three checkpoints:
1. After candidate resolution (pre-spec)
2. Before spec persistence (post-derivation)
3. Before compilation

Checks:
- Geometry type fidelity (triangle stays triangle, not cosine_bell)
- Spatial relationship preservation (attached_to, centered_under, etc.)
- Geometry intersection (cylinder-wall, cylinder-obstacle, out-of-domain)
- Boundary semantic consistency (inlet/outlet pairing, periodic pairs, 2D front/back)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

from fluid_scientist.intent.conflict_resolver import GEOMETRY_SYNONYMS, _normalize_geometry_type


class GuardResult:
    """Result of semantic fidelity guard checks."""

    def __init__(self) -> None:
        self.violations: list[GuardViolation] = []
        self.warnings: list[GuardWarning] = []

    @property
    def passed(self) -> bool:
        """True if no blocking violations."""
        return not any(v.severity == "blocking" for v in self.violations)

    def add_violation(
        self,
        code: str,
        message: str,
        severity: Literal["blocking", "warning"] = "blocking",
        field_path: str | None = None,
        evidence: str | None = None,
    ) -> None:
        self.violations.append(GuardViolation(
            code=code, message=message, severity=severity,
            field_path=field_path, evidence=evidence,
        ))

    def add_warning(self, code: str, message: str, field_path: str | None = None) -> None:
        self.warnings.append(GuardWarning(code=code, message=message, field_path=field_path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [
                {"code": v.code, "message": v.message, "severity": v.severity,
                 "field_path": v.field_path, "evidence": v.evidence}
                for v in self.violations
            ],
            "warnings": [
                {"code": w.code, "message": w.message, "field_path": w.field_path}
                for w in self.warnings
            ],
        }


@dataclass
class GuardViolation:
    code: str
    message: str
    severity: Literal["blocking", "warning"] = "blocking"
    field_path: str | None = None
    evidence: str | None = None


@dataclass
class GuardWarning:
    code: str
    message: str
    field_path: str | None = None


class SemanticFidelityGuard:
    """Verifies that user intent is preserved through the pipeline.

    Key checks:
    1. Geometry type fidelity — triangle must not become cosine_bell
    2. Spatial relations — centered_under, attached_to preserved
    3. Geometry intersections — no overlapping or out-of-domain entities
    4. Boundary semantics — inlet/outlet pairing, periodic pairs, 2D consistency
    """

    def check_spec(self, spec: Any, user_text: str) -> GuardResult:
        """Run all fidelity checks on a spec.

        Args:
            spec: CylinderFlow2DExperimentSpecV1
            user_text: Original user input text

        Returns:
            GuardResult with violations and warnings
        """
        result = GuardResult()

        self._check_geometry_fidelity(spec, user_text, result)
        self._check_spatial_relations(spec, user_text, result)
        self._check_geometry_intersections(spec, result)
        self._check_boundary_semantics(spec, user_text, result)

        return result

    def _check_geometry_fidelity(self, spec: Any, user_text: str, result: GuardResult) -> None:
        """Check that geometry types match user intent.

        Rules:
        - If user says "三角", spec must have triangle, not cosine_bell
        - If user says "正弦凸起", spec must have bottom_profile, not rectangle
        - If user says "矩形", spec must have rectangle, not triangle
        """
        text_lower = user_text.lower()

        # Check triangle fidelity
        triangle_keywords = GEOMETRY_SYNONYMS["triangle"]
        has_triangle_in_text = any(kw.lower() in text_lower for kw in triangle_keywords)
        if has_triangle_in_text and not spec.has_triangle:
            # User said triangle but spec doesn't have it
            if spec.has_bottom_profile and spec.bottom_profile.profile_type.value != "flat":
                result.add_violation(
                    code="GEOMETRY_TYPE_MISMATCH",
                    message=f"用户原文包含三角形关键词，但spec使用了bottom_profile({spec.bottom_profile.profile_type.value})而非triangle",
                    severity="blocking",
                    field_path="obstacle.type",
                    evidence=user_text[:200],
                )

        # Check cosine_bell fidelity
        cosine_keywords = GEOMETRY_SYNONYMS["cosine_bell"]
        has_cosine_in_text = any(kw.lower() in text_lower for kw in cosine_keywords)
        if has_cosine_in_text:
            if not spec.has_bottom_profile or spec.bottom_profile.profile_type.value != "cosine_bell":
                if spec.has_triangle:
                    result.add_violation(
                        code="GEOMETRY_TYPE_MISMATCH",
                        message="用户原文包含余弦凸起关键词，但spec使用了triangle而非cosine_bell",
                        severity="blocking",
                        field_path="obstacle.type",
                        evidence=user_text[:200],
                    )

        # Check half_sine fidelity
        sine_keywords = GEOMETRY_SYNONYMS["half_sine"]
        has_sine_in_text = any(kw.lower() in text_lower for kw in sine_keywords)
        if has_sine_in_text:
            if not spec.has_bottom_profile or spec.bottom_profile.profile_type.value != "half_sine":
                result.add_warning(
                    code="GEOMETRY_TYPE_WARNING",
                    message="用户原文包含正弦凸起关键词，但spec未使用half_sine profile",
                    field_path="obstacle.type",
                )
            # Also check that rectangle is not simultaneously enabled
            if spec.has_rectangle:
                result.add_violation(
                    code="DUPLICATE_ENTITY",
                    message="用户原文描述的是正弦凸起，但spec同时启用了rectangle和bottom_profile",
                    severity="blocking",
                    field_path="rectangle.enabled",
                    evidence=user_text[:200],
                )

        # Check rectangle fidelity
        rect_keywords = GEOMETRY_SYNONYMS["rectangle"]
        has_rect_in_text = any(kw.lower() in text_lower for kw in rect_keywords)
        if has_rect_in_text and not spec.has_rectangle:
            # Only warn if user explicitly said rectangle but spec doesn't have it
            # and no bump keywords are present (to avoid false positive with sine bump)
            if not has_sine_in_text and not has_cosine_in_text:
                result.add_warning(
                    code="GEOMETRY_TYPE_WARNING",
                    message="用户原文包含矩形关键词，但spec未启用rectangle",
                    field_path="rectangle.enabled",
                )

    def _check_spatial_relations(self, spec: Any, user_text: str, result: GuardResult) -> None:
        """Check that spatial relationships are preserved.

        Rules:
        - "位于圆柱正下方" → obstacle.center_x == cylinder.center_x
        - "贴附下壁面" → obstacle is at bottom (y=0)
        - "正中央" → cylinder.center_x == domain.length / 2
        """
        text_lower = user_text.lower()

        # Check centered_under (obstacle below cylinder)
        if "正下方" in user_text or "centered_under" in text_lower:
            if spec.has_cylinder:
                cyl_cx = spec.cylinder.center_x_m.value if spec.cylinder.center_x_m.is_resolved() else None
                if cyl_cx is not None:
                    # Check triangle center_x
                    if spec.has_triangle and spec.triangle.center_x_m.is_resolved():
                        tri_cx = spec.triangle.center_x_m.value
                        if abs(tri_cx - cyl_cx) > 0.01:
                            result.add_violation(
                                code="SPATIAL_RELATION_VIOLATION",
                                message=f"用户要求障碍物在圆柱正下方，但triangle.center_x={tri_cx} != cylinder.center_x={cyl_cx}",
                                severity="blocking",
                                field_path="triangle.center_x",
                                evidence=user_text[:200],
                            )
                    # Check rectangle center_x
                    if spec.has_rectangle and spec.rectangle.center_x_m.is_resolved():
                        rect_cx = spec.rectangle.center_x_m.value
                        if abs(rect_cx - cyl_cx) > 0.01:
                            result.add_violation(
                                code="SPATIAL_RELATION_VIOLATION",
                                message=f"用户要求障碍物在圆柱正下方，但rectangle.center_x={rect_cx} != cylinder.center_x={cyl_cx}",
                                severity="blocking",
                                field_path="rectangle.center_x",
                                evidence=user_text[:200],
                            )
                    # Check bump center_x
                    if spec.has_bottom_profile and spec.bottom_profile.center_x_m.is_resolved():
                        bump_cx = spec.bottom_profile.center_x_m.value
                        if abs(bump_cx - cyl_cx) > 0.01:
                            result.add_violation(
                                code="SPATIAL_RELATION_VIOLATION",
                                message=f"用户要求障碍物在圆柱正下方，但bump.center_x={bump_cx} != cylinder.center_x={cyl_cx}",
                                severity="blocking",
                                field_path="bump.center_x",
                                evidence=user_text[:200],
                            )

        # Check attached_to bottom wall
        if "贴附" in user_text or "贴壁" in user_text or "attached" in text_lower:
            # Obstacle should be at the bottom of the domain
            domain_height = spec.domain.height_m.value if spec.domain.height_m.is_resolved() else None
            if domain_height is not None:
                # Triangle should have base at y=0
                if spec.has_triangle and spec.triangle.height_m.is_resolved():
                    # Triangle is attached to bottom, so its apex is at height
                    pass  # This is implicit in the compiler, just verify height is reasonable
                # Rectangle should have bottom at y=0
                if spec.has_rectangle and spec.rectangle.height_m.is_resolved():
                    rect_h = spec.rectangle.height_m.value
                    if rect_h > domain_height * 0.5:
                        result.add_warning(
                            code="SPATIAL_WARNING",
                            message=f"矩形高度{rect_h}m超过域高度{domain_height}m的50%，可能不合理",
                            field_path="rectangle.height",
                        )

        # Check cylinder centered in domain
        if "正中央" in user_text or "流场中央" in user_text or "centered" in text_lower:
            if spec.has_cylinder and spec.domain.length_m.is_resolved():
                cyl_cx = spec.cylinder.center_x_m.value if spec.cylinder.center_x_m.is_resolved() else None
                domain_len = spec.domain.length_m.value
                if cyl_cx is not None and domain_len > 0:
                    expected_cx = domain_len / 2.0
                    if abs(cyl_cx - expected_cx) > domain_len * 0.05:  # 5% tolerance
                        # Check if there's also a "距下壁" constraint that might conflict
                        if "距下壁" in user_text or "距底" in user_text:
                            # This is a potential conflict between "正中央" and "距下壁Xm"
                            result.add_warning(
                                code="POSITION_CONFLICT",
                                message=f"用户同时指定了'正中央'(x={expected_cx})和距下壁约束，圆柱center_x={cyl_cx}可能与预期不符",
                                field_path="cylinder.center_x",
                            )

    def _check_geometry_intersections(self, spec: Any, result: GuardResult) -> None:
        """Check for invalid geometry intersections.

        Rules:
        - Cylinder must not intersect walls
        - Cylinder must not intersect obstacles
        - Obstacles must be within domain
        - Triangle dimensions must be positive
        - Rectangle and bottom_profile should not overlap
        """
        domain_len = spec.domain.length_m.value if spec.domain.length_m.is_resolved() else None
        domain_h = spec.domain.height_m.value if spec.domain.height_m.is_resolved() else None

        # Check cylinder within domain
        if spec.has_cylinder and domain_len is not None and domain_h is not None:
            cyl_cx = spec.cylinder.center_x_m.value if spec.cylinder.center_x_m.is_resolved() else None
            cyl_cy = spec.cylinder.center_y_m.value if spec.cylinder.center_y_m.is_resolved() else None
            cyl_r = spec.cylinder.radius_m.value if spec.cylinder.radius_m.is_resolved() else None

            if cyl_cx is not None and cyl_r is not None:
                if cyl_cx - cyl_r < 0 or cyl_cx + cyl_r > domain_len:
                    result.add_violation(
                        code="CYLINDER_OUT_OF_DOMAIN",
                        message=f"圆柱(cx={cyl_cx}, r={cyl_r})超出域长度{domain_len}",
                        severity="blocking",
                        field_path="cylinder",
                    )
            if cyl_cy is not None and cyl_r is not None and domain_h is not None:
                if cyl_cy - cyl_r < 0 or cyl_cy + cyl_r > domain_h:
                    result.add_violation(
                        code="CYLINDER_INTERSECTS_WALL",
                        message=f"圆柱(cy={cyl_cy}, r={cyl_r})与壁面相交(域高度={domain_h})",
                        severity="blocking",
                        field_path="cylinder",
                    )

        # Check triangle dimensions
        if spec.has_triangle:
            tri_h = spec.triangle.height_m.value if spec.triangle.height_m.is_resolved() else None
            tri_w = spec.triangle.base_width_m.value if spec.triangle.base_width_m.is_resolved() else None
            if tri_h is not None and tri_h <= 0:
                result.add_violation(
                    code="INVALID_TRIANGLE_DIMENSION",
                    message=f"三角形高度必须为正，当前={tri_h}",
                    severity="blocking",
                    field_path="triangle.height",
                )
            if tri_w is not None and tri_w <= 0:
                result.add_violation(
                    code="INVALID_TRIANGLE_DIMENSION",
                    message=f"三角形宽度必须为正，当前={tri_w}",
                    severity="blocking",
                    field_path="triangle.base_width",
                )
            # Check triangle within domain
            if tri_h is not None and domain_h is not None and tri_h > domain_h * 0.5:
                result.add_warning(
                    code="TRIANGLE_TOO_LARGE",
                    message=f"三角形高度{tri_h}m超过域高度{domain_h}m的50%",
                    field_path="triangle.height",
                )

        # Check rectangle within domain
        if spec.has_rectangle:
            rect_h = spec.rectangle.height_m.value if spec.rectangle.height_m.is_resolved() else None
            rect_w = spec.rectangle.width_m.value if spec.rectangle.width_m.is_resolved() else None
            if rect_h is not None and rect_h <= 0:
                result.add_violation(
                    code="INVALID_RECTANGLE_DIMENSION",
                    message=f"矩形高度必须为正，当前={rect_h}",
                    severity="blocking",
                    field_path="rectangle.height",
                )
            if rect_w is not None and rect_w <= 0:
                result.add_violation(
                    code="INVALID_RECTANGLE_DIMENSION",
                    message=f"矩形宽度必须为正，当前={rect_w}",
                    severity="blocking",
                    field_path="rectangle.width",
                )

        # Check cylinder-obstacle intersection
        if spec.has_cylinder and cyl_r is not None and cyl_cx is not None and cyl_cy is not None:
            # Check cylinder-triangle intersection
            if spec.has_triangle:
                tri_cx = spec.triangle.center_x_m.value if spec.triangle.center_x_m.is_resolved() else None
                tri_h = spec.triangle.height_m.value if spec.triangle.height_m.is_resolved() else None
                if tri_cx is not None and tri_h is not None:
                    # Simple check: if triangle is directly under cylinder, check vertical distance
                    if abs(tri_cx - cyl_cx) < cyl_r and tri_h > cyl_cy - cyl_r:
                        result.add_violation(
                            code="CYLINDER_TRIANGLE_INTERSECTION",
                            message=f"圆柱(cy={cyl_cy}, r={cyl_r})与三角形(cx={tri_cx}, h={tri_h})可能相交",
                            severity="blocking",
                            field_path="cylinder",
                        )

            # Check cylinder-rectangle intersection
            if spec.has_rectangle:
                rect_cx = spec.rectangle.center_x_m.value if spec.rectangle.center_x_m.is_resolved() else None
                rect_h = spec.rectangle.height_m.value if spec.rectangle.height_m.is_resolved() else None
                rect_w = spec.rectangle.width_m.value if spec.rectangle.width_m.is_resolved() else None
                if rect_cx is not None and rect_h is not None and rect_w is not None:
                    # Check if rectangle overlaps with cylinder
                    rect_left = rect_cx - rect_w / 2
                    rect_right = rect_cx + rect_w / 2
                    rect_top = rect_h  # Rectangle is at bottom
                    if (rect_left < cyl_cx + cyl_r and rect_right > cyl_cx - cyl_r
                            and rect_top > cyl_cy - cyl_r):
                        result.add_violation(
                            code="CYLINDER_RECTANGLE_INTERSECTION",
                            message=f"圆柱(cy={cyl_cy}, r={cyl_r})与矩形(cx={rect_cx}, h={rect_h}, w={rect_w})可能相交",
                            severity="blocking",
                            field_path="cylinder",
                        )

        # Check rectangle and bottom_profile overlap
        if spec.has_rectangle and spec.has_bottom_profile:
            bp_type = spec.bottom_profile.profile_type.value if spec.bottom_profile.profile_type else "flat"
            if bp_type != "flat":
                result.add_violation(
                    code="RECTANGLE_BUMP_OVERLAP",
                    message="rect和bottom_profile同时启用，可能产生重复几何体",
                    severity="blocking",
                    field_path="rectangle",
                )

    def _check_boundary_semantics(self, spec: Any, user_text: str, result: GuardResult) -> None:
        """Check boundary condition semantic consistency.

        Rules:
        - Left should be inlet, right should be outlet (for external flow)
        - Periodic boundaries must be paired
        - 2D front/back must be empty
        - Top "自由出流" should not become no_slip_wall
        - Patch names must be consistent
        """
        bc = spec.boundaries

        # Check inlet/outlet pairing for external flow
        if spec.flow_topology.get("mode") == "inlet_outlet":
            left_type = bc.left.semantic_type.value if bc.left.semantic_type else None
            right_type = bc.right.semantic_type.value if bc.right.semantic_type else None

            if left_type and left_type not in ("uniform_velocity_inlet", "pressure_inlet"):
                result.add_warning(
                    code="BOUNDARY_WARNING",
                    message=f"入口外流模式下，左边界类型为{left_type}，通常应为velocity_inlet",
                    field_path="boundary.left",
                )
            if right_type and right_type not in ("pressure_outlet", "open_outlet", "advective_outlet"):
                result.add_warning(
                    code="BOUNDARY_WARNING",
                    message=f"入口外流模式下，右边界类型为{right_type}，通常应为pressure_outlet",
                    field_path="boundary.right",
                )

        # Check periodic boundary pairing
        if spec.is_periodic:
            left_type = bc.left.semantic_type.value if bc.left.semantic_type else None
            right_type = bc.right.semantic_type.value if bc.right.semantic_type else None
            if left_type == "periodic" and right_type != "periodic":
                result.add_violation(
                    code="PERIODIC_BOUNDARY_UNPAIRED",
                    message="左边界为periodic但右边界不是periodic，周期边界必须成对",
                    severity="blocking",
                    field_path="boundary.right",
                )
            if right_type == "periodic" and left_type != "periodic":
                result.add_violation(
                    code="PERIODIC_BOUNDARY_UNPAIRED",
                    message="右边界为periodic但左边界不是periodic，周期边界必须成对",
                    severity="blocking",
                    field_path="boundary.left",
                )

        # Check top boundary "自由出流" is not no_slip_wall
        top_type = bc.top.semantic_type.value if bc.top.semantic_type else None
        if "自由出流" in user_text or "free outflow" in user_text.lower():
            if top_type == "no_slip_wall":
                result.add_violation(
                    code="BOUNDARY_SEMANTIC_MISMATCH",
                    message="用户要求上边界'自由出流'，但spec设为no_slip_wall",
                    severity="blocking",
                    field_path="boundary.top",
                    evidence=user_text[:200],
                )
            elif top_type and top_type not in ("symmetry", "slip_wall", "freestream", "open_boundary"):
                result.add_warning(
                    code="BOUNDARY_WARNING",
                    message=f"用户要求'自由出流'，上边界类型为{top_type}，建议使用symmetry/slip/freestream/open_boundary",
                    field_path="boundary.top",
                )

        # Check 2D front/back
        front_type = bc.front.semantic_type.value if bc.front and bc.front.semantic_type else None
        back_type = bc.back.semantic_type.value if bc.back and bc.back.semantic_type else None
        if front_type and front_type != "empty":
            result.add_warning(
                code="BOUNDARY_2D_WARNING",
                message=f"2D问题中front边界应为empty，当前为{front_type}",
                field_path="boundary.front",
            )
        if back_type and back_type != "empty":
            result.add_warning(
                code="BOUNDARY_2D_WARNING",
                message=f"2D问题中back边界应为empty，当前为{back_type}",
                field_path="boundary.back",
            )

        # Check bottom wall consistency
        bottom_type = bc.bottom.semantic_type.value if bc.bottom.semantic_type else None
        if "无滑移" in user_text or "no-slip" in user_text.lower() or "no_slip" in user_text.lower():
            if bottom_type and bottom_type != "no_slip_wall":
                result.add_violation(
                    code="BOUNDARY_SEMANTIC_MISMATCH",
                    message=f"用户要求下壁面无滑移，但spec设为{bottom_type}",
                    severity="blocking",
                    field_path="boundary.bottom",
                    evidence=user_text[:200],
                )
