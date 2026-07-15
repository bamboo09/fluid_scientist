from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import SkillIssue, SkillResult

def evaluate_readiness(spec: dict[str, Any], inherited_issues: list[SkillIssue] | None = None) -> SkillResult:
    data = deepcopy(spec)
    issues = list(inherited_issues or [])

    cylinder = data.get("cylinder", {})
    if cylinder.get("type") != "cylinder":
        issues.append(SkillIssue(
            code="GEOMETRY_TYPE_REQUIRED",
            message="圆柱类型尚未确定。",
            blocking=True,
        ))
    if cylinder.get("characteristic_dimension_m") is None:
        issues.append(SkillIssue(
            code="CHARACTERISTIC_DIMENSION_REQUIRED",
            message="圆柱特征尺度尚未确定。",
            blocking=True,
        ))
    if data.get("flow_topology", {}).get("mode") is None:
        issues.append(SkillIssue(
            code="FLOW_TOPOLOGY_REQUIRED",
            message="流动拓扑尚未确定。",
            blocking=True,
        ))
    if not data.get("observables"):
        issues.append(SkillIssue(
            code="OBSERVABLE_REQUIRED",
            message="至少需要一个观测量。",
            blocking=True,
        ))
    if not data.get("analysis_goals"):
        issues.append(SkillIssue(
            code="ANALYSIS_GOAL_REQUIRED",
            message="至少需要一个分析目标。",
            blocking=True,
        ))

    blocking = [issue for issue in issues if issue.blocking]
    awaiting = any(
        item.get("status") == "AWAITING_CONFIRMATION"
        for item in data.get("observables", []) + data.get("analysis_goals", [])
        if isinstance(item, dict)
    )

    if blocking:
        status = "NEEDS_CLARIFICATION"
    elif awaiting:
        status = "AWAITING_CONFIRMATION"
    else:
        status = "READY_TO_CONFIRM"

    data["blocking_issues"] = [
        {
            "code": issue.code,
            "message": issue.message,
            "path": issue.path,
            "details": issue.details,
        }
        for issue in blocking
    ]
    data["draft_status"] = status

    return SkillResult(
        skill_id="cylinder_flow_2d.readiness",
        status="PARTIAL" if blocking or awaiting else "SUCCESS",
        data=data,
        issues=issues,
    )
