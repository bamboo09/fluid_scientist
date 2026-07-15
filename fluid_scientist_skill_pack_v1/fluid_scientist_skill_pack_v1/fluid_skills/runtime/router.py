from __future__ import annotations

import re

from .models import SkillIssue, SkillResult

CYLINDER_TERMS = r"圆柱|圆形障碍|cylinder|circular obstacle"
FLOW_TERMS = r"流场|绕流|来流|入口|出口|压力梯度|flow"

def route(text: str) -> SkillResult:
    matched = bool(re.search(CYLINDER_TERMS, text, re.I) and re.search(FLOW_TERMS, text, re.I))
    issues = []
    if not matched:
        issues.append(SkillIssue(
            code="NOT_CYLINDER_FLOW_2D",
            message="输入未命中二维圆柱绕流实验族。",
            blocking=False,
        ))
    return SkillResult(
        skill_id="cylinder_flow_2d.router",
        status="SUCCESS" if matched else "PARTIAL",
        data={
            "matched": matched,
            "pipeline_id": "cylinder-flow-2d-v1" if matched else None,
            "schema_name": "CylinderFlow2DExperimentSpecV1" if matched else None,
        },
        issues=issues,
    )
