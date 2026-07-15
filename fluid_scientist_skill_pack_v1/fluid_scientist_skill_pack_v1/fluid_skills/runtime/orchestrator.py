from __future__ import annotations

from typing import Any

from .geometry import normalize_cylinder_geometry, validate_geometry_feasibility
from .models import SkillIssue, SkillResult
from .observables import build_analysis_goals, extract_observables, recommend_observables
from .readiness import evaluate_readiness
from .router import route
from .rules import ensure_spec_defaults
from .topology import classify_flow_topology, enforce_2d_boundary_topology
from .visualization import build_plot_spec

class CylinderFlow2DSkillOrchestrator:
    """Project-local deterministic harness around model-produced semantic specs."""

    def prepare_draft(self, text: str, initial_spec: dict[str, Any]) -> SkillResult:
        route_result = route(text)
        if not route_result.data.get("matched"):
            return route_result

        spec = ensure_spec_defaults(initial_spec)
        issues: list[SkillIssue] = []
        evidence: list[dict[str, Any]] = []

        for result in [
            normalize_cylinder_geometry(spec),
        ]:
            spec = result.data
            issues.extend(result.issues)
            evidence.extend(result.evidence)

        boundary_result = enforce_2d_boundary_topology(spec)
        spec = boundary_result.data
        issues.extend(boundary_result.issues)
        evidence.extend(boundary_result.evidence)

        topology_result = classify_flow_topology(spec)
        spec = topology_result.data
        issues.extend(topology_result.issues)

        observable_result = extract_observables(text, spec)
        spec = observable_result.data
        issues.extend(observable_result.issues)

        if not spec.get("observables"):
            recommendation_result = recommend_observables(spec)
            spec = recommendation_result.data

        goals_result = build_analysis_goals(spec)
        spec = goals_result.data

        geometry_result = validate_geometry_feasibility(spec)
        spec = geometry_result.data
        issues.extend(geometry_result.issues)

        readiness = evaluate_readiness(spec, issues)
        readiness.evidence.extend(evidence)
        readiness.data["pipeline_id"] = "cylinder-flow-2d-v1"
        readiness.data["schema_name"] = "CylinderFlow2DExperimentSpecV1"
        return readiness

    def build_visualization_request(
        self,
        confirmed_spec: dict[str, Any],
        run: dict[str, Any],
    ) -> SkillResult:
        if confirmed_spec.get("draft_status") != "SPEC_CONFIRMED":
            return SkillResult(
                skill_id="cylinder_flow_2d.e2e_loop",
                status="FAILED",
                data={},
                issues=[SkillIssue(
                    code="SPEC_NOT_CONFIRMED",
                    message="只有已确认的实验规格才能进入后处理。",
                    blocking=True,
                )],
            )
        return build_plot_spec(confirmed_spec, run)
