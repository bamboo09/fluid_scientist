from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import SkillIssue, SkillResult

ALLOWED_PLOTS = {
    "velocity_magnitude",
    "ux",
    "pressure",
    "vorticity",
    "streamlines",
    "point_velocity_history",
    "section_mean_velocity_history",
    "drag_lift_history",
    "inlet_response",
}

def build_plot_spec(spec: dict[str, Any], run: dict[str, Any]) -> SkillResult:
    requested = list(spec.get("plot_requests", []))
    if not requested:
        requested = ["velocity_magnitude", "pressure", "vorticity", "streamlines"]

    issues: list[SkillIssue] = []
    plots = []
    for plot_type in requested:
        if plot_type not in ALLOWED_PLOTS:
            issues.append(SkillIssue(
                code="PLOT_TYPE_NOT_ALLOWED",
                message=f"不支持的绘图类型：{plot_type}",
                blocking=True,
            ))
            continue
        plots.append({
            "type": plot_type,
            "output_name": f"{plot_type}.png",
        })

    observable_types = {o.get("type") for o in spec.get("observables", [])}
    if "point_velocity" in observable_types:
        plots.append({"type": "point_velocity_history", "output_name": "point_velocity_history.png"})
    if "section_mean_velocity" in observable_types:
        plots.append({
            "type": "section_mean_velocity_history",
            "output_name": "section_mean_velocity_history.png",
        })
    if {"cylinder_drag", "cylinder_lift"} & observable_types:
        plots.append({"type": "drag_lift_history", "output_name": "drag_lift_history.png"})

    result = {
        "run_id": run.get("run_id"),
        "case_id": run.get("case_id"),
        "spec_version": run.get("spec_version"),
        "case_path": run.get("remote_case_path"),
        "time_selection": {"mode": "latest"},
        "plots": _dedupe(plots),
        "artifact_requirements": {
            "mime_type": "image/png",
            "must_be_nonempty": True,
            "must_not_be_uniform": True,
            "bind_to_run_id": True,
            "bind_to_spec_version": True,
        },
    }
    return SkillResult(
        skill_id="postprocess.flow_visualization",
        status="FAILED" if issues else "SUCCESS",
        data=result,
        issues=issues,
    )

def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for item in items:
        key = (item["type"], item["output_name"])
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
