from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import SkillIssue, SkillResult
from .rules import ensure_spec_defaults

def normalize_cylinder_geometry(spec: dict[str, Any]) -> SkillResult:
    data = ensure_spec_defaults(spec)
    cylinder = data["cylinder"]
    cylinder["type"] = "cylinder"

    radius = cylinder.get("radius_m")
    diameter = cylinder.get("diameter_m")
    issues: list[SkillIssue] = []

    if radius is not None and radius <= 0:
        issues.append(SkillIssue(
            code="INVALID_CYLINDER_RADIUS",
            message="圆柱半径必须大于0。",
            blocking=True,
            path="/cylinder/radius_m",
        ))
    if diameter is not None and diameter <= 0:
        issues.append(SkillIssue(
            code="INVALID_CYLINDER_DIAMETER",
            message="圆柱直径必须大于0。",
            blocking=True,
            path="/cylinder/diameter_m",
        ))

    if not issues:
        if radius is not None and diameter is None:
            diameter = 2.0 * radius
            cylinder["diameter_m"] = diameter
            cylinder["diameter_source"] = "FORMULA_DERIVED"
        elif diameter is not None and radius is None:
            radius = diameter / 2.0
            cylinder["radius_m"] = radius
            cylinder["radius_source"] = "FORMULA_DERIVED"
        elif radius is not None and diameter is not None:
            tolerance = max(1e-12, abs(diameter) * 1e-8)
            if abs(diameter - 2.0 * radius) > tolerance:
                issues.append(SkillIssue(
                    code="CYLINDER_DIMENSION_CONFLICT",
                    message="圆柱半径和直径不一致。",
                    blocking=True,
                    path="/cylinder",
                    details={"radius_m": radius, "diameter_m": diameter},
                ))

    if diameter is not None and not issues:
        cylinder["characteristic_dimension_m"] = diameter
        cylinder["characteristic_dimension_source"] = "FORMULA_DERIVED"

    if radius is None and diameter is None:
        issues.append(SkillIssue(
            code="CYLINDER_SIZE_REQUIRED",
            message="需要圆柱半径或直径。",
            blocking=True,
            path="/cylinder",
        ))

    return SkillResult(
        skill_id="geometry.cylinder.normalizer",
        status="FAILED" if any(x.blocking for x in issues) else "SUCCESS",
        data=data,
        issues=issues,
        evidence=[{"rule": "D=2R", "applied": diameter is not None}],
    )

def validate_geometry_feasibility(spec: dict[str, Any]) -> SkillResult:
    data = deepcopy(spec)
    issues: list[SkillIssue] = []
    domain = data.get("domain", {})
    cylinder = data.get("cylinder", {})
    length = domain.get("length_m")
    height = domain.get("height_m")
    x = cylinder.get("center_x_m")
    y = cylinder.get("center_y_m")
    radius = cylinder.get("radius_m")

    if all(v is not None for v in [length, height, x, y, radius]):
        if x - radius <= 0 or x + radius >= length:
            issues.append(SkillIssue(
                code="CYLINDER_INTERSECTS_SIDE_BOUNDARY",
                message="圆柱与左右边界相交或距离过小。",
                blocking=True,
            ))
        if y - radius <= 0:
            issues.append(SkillIssue(
                code="CYLINDER_INTERSECTS_BOTTOM",
                message="圆柱与下边界相交。",
                blocking=True,
            ))
        if y + radius >= height:
            issues.append(SkillIssue(
                code="CYLINDER_INTERSECTS_TOP",
                message="圆柱与上边界相交。",
                blocking=True,
            ))

    return SkillResult(
        skill_id="geometry.cylinder.feasibility",
        status="FAILED" if issues else "SUCCESS",
        data=data,
        issues=issues,
    )
