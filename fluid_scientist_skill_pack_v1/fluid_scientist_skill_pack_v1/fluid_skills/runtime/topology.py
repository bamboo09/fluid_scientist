from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import SkillIssue, SkillResult
from .rules import ensure_spec_defaults

PERIODIC_TYPES = {"periodic", "periodic_pair", "cyclic"}
INLET_TYPES = {
    "uniform_velocity_inlet",
    "time_varying_velocity_inlet",
    "spatial_profile_velocity_inlet",
    "mass_flow_inlet",
}
OUTLET_TYPES = {
    "pressure_outlet",
    "open_outlet",
    "advective_outlet",
    "non_reflecting_outlet",
}
PRESSURE_TYPES = {"pressure_boundary", "fixed_pressure"}

def classify_flow_topology(spec: dict[str, Any]) -> SkillResult:
    data = ensure_spec_defaults(spec)
    boundaries = data.get("boundaries", {})
    left = boundaries.get("left", {}).get("semantic_type")
    right = boundaries.get("right", {}).get("semantic_type")
    top = boundaries.get("top", {}).get("semantic_type")
    forcing = data.get("forcing", {})
    pressure_gradient = forcing.get("pressure_gradient", {})
    body_force = forcing.get("body_force", {})
    driven = bool(
        pressure_gradient.get("enabled")
        or body_force.get("enabled")
        or top in {"moving_wall", "shear_stress"}
    )

    issues: list[SkillIssue] = []
    mode = None

    if left in PERIODIC_TYPES or right in PERIODIC_TYPES:
        if left not in PERIODIC_TYPES or right not in PERIODIC_TYPES:
            issues.append(SkillIssue(
                code="PERIODIC_PAIR_INCOMPLETE",
                message="左右周期边界必须成对设置。",
                blocking=True,
            ))
        elif not driven:
            issues.append(SkillIssue(
                code="PERIODIC_FLOW_HAS_NO_DRIVING",
                message="左右周期时需要压力梯度、体力、运动壁面或剪切应力。",
                blocking=True,
            ))
        else:
            mode = "PERIODIC_FORCED"
    elif left in INLET_TYPES and right in OUTLET_TYPES:
        mode = "OPEN_DOMAIN" if top in {"freestream", "open_boundary"} else "INLET_OUTLET"
        if driven:
            mode = "COMBINED_DRIVING"
    elif left in PRESSURE_TYPES and right in PRESSURE_TYPES:
        mode = "PRESSURE_DIFFERENCE"
    elif driven:
        mode = "WALL_DRIVEN"
    else:
        issues.append(SkillIssue(
            code="FLOW_TOPOLOGY_UNRESOLVED",
            message="无法根据当前边界和驱动方式确定流动拓扑。",
            blocking=True,
        ))

    data["flow_topology"] = {"mode": mode}
    return SkillResult(
        skill_id="fluid.flow_topology.classifier",
        status="FAILED" if issues else "SUCCESS",
        data=data,
        issues=issues,
    )

def enforce_2d_boundary_topology(spec: dict[str, Any]) -> SkillResult:
    data = ensure_spec_defaults(spec)
    boundaries = data["boundaries"]
    boundaries["front"] = {
        "semantic_type": "empty",
        "source": "SYSTEM_DERIVED",
        "status": "RESOLVED",
    }
    boundaries["back"] = {
        "semantic_type": "empty",
        "source": "SYSTEM_DERIVED",
        "status": "RESOLVED",
    }

    issues: list[SkillIssue] = []
    left = boundaries.get("left", {}).get("semantic_type")
    right = boundaries.get("right", {}).get("semantic_type")

    if left in PERIODIC_TYPES and right not in PERIODIC_TYPES:
        issues.append(SkillIssue(
            code="INVALID_BOUNDARY_COMBINATION",
            message="左边界为周期时，右边界也必须为周期。",
            blocking=True,
        ))
    if right in PERIODIC_TYPES and left not in PERIODIC_TYPES:
        issues.append(SkillIssue(
            code="INVALID_BOUNDARY_COMBINATION",
            message="右边界为周期时，左边界也必须为周期。",
            blocking=True,
        ))

    return SkillResult(
        skill_id="boundary.topology_2d",
        status="FAILED" if issues else "SUCCESS",
        data=data,
        issues=issues,
        evidence=[{"front": "empty", "back": "empty"}],
    )
