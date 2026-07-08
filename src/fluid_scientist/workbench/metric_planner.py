"""Dynamic metric planner — plans metrics with unknown metric detection.

Uses the existing MetricPlanner.propose_metrics() and
detect_missing_capabilities_from_metrics() to generate a comprehensive
metric plan that includes core, credibility, extended, and unknown metrics.
"""

from __future__ import annotations

from typing import Any

from fluid_scientist.capabilities.resolver import (
    detect_missing_capabilities_from_metrics,
)
from fluid_scientist.measurement.planner import MetricPlanner
from fluid_scientist.research.models import ResearchPhysicsSpec

# Mapping from intent physical_system to experiment_type for metric planning
_PHYSICAL_SYSTEM_TO_EXP_TYPE: dict[str, str] = {
    "pipe_flow": "laminar_pipe",
    "internal_flow": "laminar_pipe",
    "cylinder_external_flow": "cylinder_flow",
    "external_flow": "cylinder_flow",
    "cavity_flow": "lid_driven_cavity",
}


class DynamicMetricPlanner:
    """Plans metrics based on research objective, with unknown metric detection."""

    def plan(
        self,
        intent_assessment: dict,
        physics_spec: dict,
        user_requested_metrics: list[str],
    ) -> dict:
        """Generate metric plan with core/credibility/extended/unknown metrics.

        Uses existing MetricPlanner.propose_metrics() and
        detect_missing_capabilities_from_metrics() for unknown metrics.

        Args:
            intent_assessment: Intent assessment dict containing
                research_objective, physical_system, etc.
            physics_spec: Physics specification dict with fields like
                compressibility, temporal_type, geometry_facts, etc.
            user_requested_metrics: List of metric IDs the user explicitly
                requested.

        Returns:
            Dict with keys:
            - metrics: list of metric dicts with metric_id, display_name,
              category, definition, required_data, quality_checks, reason
            - missing_capabilities: list of capability dicts
            - measurement_plan: dict representation of the MeasurementPlan
        """
        # 1. Extract research_objective and experiment_type
        research_objective = intent_assessment.get(
            "research_objective", ""
        )
        experiment_type = self._extract_experiment_type(intent_assessment)

        # 2. Build ResearchPhysicsSpec from physics_spec dict
        physics = self._build_research_physics_spec(physics_spec)

        # 3. Call MetricPlanner.propose_metrics
        planner = MetricPlanner()
        metric_plan = planner.propose_metrics(
            research_objective=research_objective,
            physics_spec=physics,
            user_metrics=list(user_requested_metrics),
            experiment_type=experiment_type,
        )

        # 4. Extract core/credibility/extended/unknown metrics
        metrics = self._extract_metrics(metric_plan)

        # 5. Detect missing capabilities for unknown metrics
        missing_caps = detect_missing_capabilities_from_metrics(metric_plan)
        missing_capabilities = [
            {
                "capability_id": cap.capability_id,
                "capability_type": cap.capability_type,
                "description": cap.requested_behavior,
                "reason": cap.reason,
                "severity": cap.severity,
                "related_metric_ids": cap.related_metric_ids,
            }
            for cap in missing_caps
        ]

        # 6. Return dict with metrics, missing_capabilities, measurement_plan
        return {
            "metrics": metrics,
            "missing_capabilities": missing_capabilities,
            "measurement_plan": metric_plan.measurement_plan.model_dump(),
        }

    def generate_measurement_requirements(
        self, metrics: list[dict],
    ) -> list[dict]:
        """Generate measurement requirements from metrics.

        For each metric, determine what OpenFOAM functionObjects are needed.
        """
        requirements = []
        for metric in metrics:
            metric_id = metric.get("metric_id", "")
            required_data = metric.get("required_data", [])
            for data in required_data:
                if "forceCoeffs" in data:
                    requirements.append({
                        "type": "forceCoeffs",
                        "fields": ["p", "U"],
                        "reason": f"{metric_id} 需要力系数数据",
                        "metric_id": metric_id,
                    })
                elif "pressure" in data.lower():
                    requirements.append({
                        "type": "surfaceFieldValue",
                        "fields": ["p"],
                        "reason": f"{metric_id} 需要压力采样",
                        "metric_id": metric_id,
                    })
                elif "velocity" in data.lower():
                    requirements.append({
                        "type": "probes",
                        "fields": ["U"],
                        "reason": f"{metric_id} 需要速度采样",
                        "metric_id": metric_id,
                    })
        return requirements

    @staticmethod
    def _extract_experiment_type(intent: dict) -> str:
        """Extract experiment type from intent assessment."""
        physical_system = intent.get("physical_system")
        if physical_system and physical_system in _PHYSICAL_SYSTEM_TO_EXP_TYPE:
            return _PHYSICAL_SYSTEM_TO_EXP_TYPE[physical_system]

        geometry_type = intent.get("geometry_type")
        if geometry_type:
            gt = geometry_type.lower()
            if "pipe" in gt or "tube" in gt:
                return "laminar_pipe"
            if "cylinder" in gt:
                return "cylinder_flow"
            if "cavity" in gt:
                return "lid_driven_cavity"

        return "unknown"

    @staticmethod
    def _build_research_physics_spec(
        physics_spec: dict,
    ) -> ResearchPhysicsSpec:
        """Build a ResearchPhysicsSpec from a physics_spec dict."""
        return ResearchPhysicsSpec(
            compressibility=physics_spec.get("compressibility"),
            temporal_type=physics_spec.get("temporal_type"),
            phases=physics_spec.get("phases"),
            flow_regime=physics_spec.get("flow_regime"),
            geometry_facts=physics_spec.get("geometry_facts", {}),
            material_facts=physics_spec.get("material_facts", {}),
            boundary_facts=physics_spec.get("boundary_facts", {}),
            operating_conditions=physics_spec.get("operating_conditions", {}),
            target_phenomena=physics_spec.get("target_phenomena", []),
        )

    @staticmethod
    def _extract_metrics(metric_plan: Any) -> list[dict[str, Any]]:
        """Extract metrics from a MetricPlan into a list of dicts."""
        metrics: list[dict[str, Any]] = []
        definitions = metric_plan.metric_definitions

        category_map = {
            "core": metric_plan.core_metrics,
            "credibility": metric_plan.credibility_metrics,
            "comparison": metric_plan.comparison_metrics,
            "extension": metric_plan.extension_metrics,
            "optional": metric_plan.optional_metrics,
        }

        seen: set[str] = set()

        for category, metric_ids in category_map.items():
            for mid in metric_ids:
                if mid in seen:
                    continue
                seen.add(mid)
                defn = definitions.get(mid, {})
                metrics.append({
                    "metric_id": mid,
                    "display_name": defn.get("display_name", mid),
                    "category": category,
                    "definition": defn.get("formula", ""),
                    "unit": defn.get("unit", ""),
                    "required_data": defn.get("required_data", []),
                    "quality_checks": defn.get("quality_checks", []),
                    "reason": _category_reason(category, mid),
                })

        # Add unknown metrics
        for mid in metric_plan.unknown_metrics:
            if mid in seen:
                continue
            seen.add(mid)
            metrics.append({
                "metric_id": mid,
                "display_name": mid,
                "category": "unknown_metric",
                "definition": "",
                "unit": "",
                "required_data": [],
                "quality_checks": [],
                "reason": "Unknown metric - requires code extension or clarification",
            })

        return metrics


def _category_reason(category: str, metric_id: str) -> str:
    """Generate a human-readable reason for a metric's category."""
    reasons = {
        "core": f"{metric_id} directly answers the research question",
        "credibility": f"{metric_id} verifies numerical quality and convergence",
        "comparison": f"{metric_id} enables comparison across cases or models",
        "extension": f"{metric_id} provides additional useful information",
        "optional": f"{metric_id} is a nice-to-have metric",
    }
    return reasons.get(category, f"{metric_id} included in metric plan")


__all__ = ["DynamicMetricPlanner"]
