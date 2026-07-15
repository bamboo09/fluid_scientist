from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .models import SkillIssue, SkillResult

def extract_observables(text: str, spec: dict[str, Any]) -> SkillResult:
    data = deepcopy(spec)
    observables = list(data.get("observables", []))
    issues: list[SkillIssue] = []

    if re.search(r"点.*平均.*流速|某点.*流速|点速度", text):
        observables.append({
            "type": "point_velocity",
            "component": "Ux",
            "temporal_operation": "time_average",
            "point": None,
            "source": "USER_EXPLICIT",
            "status": "PARTIALLY_RESOLVED",
            "missing_fields": ["point"],
        })
        issues.append(SkillIssue(
            code="POINT_LOCATION_REQUIRED",
            message="请提供观测点坐标。",
            blocking=True,
            path="/observables",
        ))

    if re.search(r"截面.*平均.*流速|断面.*平均.*流速|截面流速", text):
        observables.append({
            "type": "section_mean_velocity",
            "component": "Ux",
            "spatial_operation": "line_average",
            "temporal_operation": "time_average",
            "section_x": None,
            "source": "USER_EXPLICIT",
            "status": "PARTIALLY_RESOLVED",
            "missing_fields": ["section_x"],
        })
        issues.append(SkillIssue(
            code="SECTION_LOCATION_REQUIRED",
            message="请提供截面的x坐标。",
            blocking=True,
            path="/observables",
        ))

    if "阻力" in text:
        observables.append({
            "type": "cylinder_drag",
            "source": "USER_EXPLICIT",
            "status": "RESOLVED",
        })
    if "升力" in text:
        observables.append({
            "type": "cylinder_lift",
            "source": "USER_EXPLICIT",
            "status": "RESOLVED",
        })
    if "频率" in text or "涡脱落" in text:
        observables.append({
            "type": "wake_shedding_frequency",
            "source": "USER_EXPLICIT",
            "status": "RESOLVED",
        })

    data["observables"] = _deduplicate(observables)
    return SkillResult(
        skill_id="observable.extractor",
        status="PARTIAL" if issues else "SUCCESS",
        data=data,
        issues=issues,
    )

def recommend_observables(spec: dict[str, Any]) -> SkillResult:
    data = deepcopy(spec)
    observables = list(data.get("observables", []))
    existing = {item.get("type") for item in observables}
    recommended = [
        "cylinder_drag",
        "cylinder_lift",
        "downstream_point_velocity",
        "section_mean_velocity",
        "velocity_magnitude_field",
        "pressure_field",
        "vorticity_field",
        "streamlines",
    ]
    time_mode = data.get("simulation", {}).get("time_mode")
    if time_mode == "transient":
        recommended += ["drag_lift_time_series", "wake_shedding_frequency"]

    for item_type in recommended:
        if item_type not in existing:
            observables.append({
                "type": item_type,
                "source": "MODEL_RECOMMENDED",
                "status": "AWAITING_CONFIRMATION",
                "reason": "圆柱绕流基础观测建议。",
            })
    data["observables"] = observables
    return SkillResult(
        skill_id="observable.recommender",
        status="SUCCESS",
        data=data,
    )

def build_analysis_goals(spec: dict[str, Any]) -> SkillResult:
    data = deepcopy(spec)
    goals = list(data.get("analysis_goals", []))
    goal_texts = {g.get("text") for g in goals if isinstance(g, dict)}

    base_goals = [
        "分析圆柱周围的流动分离及尾迹结构。",
        "评估圆柱的阻力和升力特征。",
        "分析圆柱下游速度亏损及恢复过程。",
    ]

    observable_types = {o.get("type") for o in data.get("observables", [])}
    if "section_mean_velocity" in observable_types:
        base_goals.append("计算指定截面的平均流速并分析其稳定性。")
    if data.get("bottom_profile", {}).get("enabled"):
        base_goals.append("分析底部轮廓对圆柱附近流动和回流区的影响。")
    if data.get("simulation", {}).get("time_mode") == "transient":
        base_goals.append("分析升阻力波动及周期性涡脱落特征。")

    for text in base_goals:
        if text not in goal_texts:
            goals.append({
                "text": text,
                "source": "MODEL_RECOMMENDED",
                "status": "AWAITING_CONFIRMATION",
            })
    data["analysis_goals"] = goals
    return SkillResult(
        skill_id="analysis_goal.builder",
        status="SUCCESS",
        data=data,
    )

def _deduplicate(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        key = (item.get("type"), jsonable(item.get("point")), item.get("section_x"))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

def jsonable(value: Any) -> str:
    return repr(value)
