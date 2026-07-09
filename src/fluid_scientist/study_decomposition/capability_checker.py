"""Capability pre-checking and study priority ranking.

The :class:`CapabilityPreChecker` inspects a :class:`StudyIntent` against a
:class:`CapabilityRegistry` of natively supported capabilities and predicts
whether the study can be compiled, needs clarification, or requires code
extensions.

The :class:`PriorityRanker` orders a batch of studies by their likelihood of
successful compilation, so the user can pick the "easiest win" first.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from fluid_scientist.capabilities.models import CapabilityRegistry
from fluid_scientist.study_decomposition.models import StudyIntent


class CapabilityCheckResult(BaseModel):
    """Result of pre-checking a study against registered capabilities."""

    study_id: str
    supported_capabilities: list[str] = Field(default_factory=list)
    uncertain_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[dict] = Field(default_factory=list)
    can_compile: bool = False
    blocking_reasons: list[str] = Field(default_factory=list)
    readiness_level: Literal[
        "draftable", "needs_clarification", "not_compilable_yet"
    ] = "needs_clarification"


# ---------------------------------------------------------------------------
# Native capabilities that the system already supports
# ---------------------------------------------------------------------------

_NATIVE_CAPABILITIES: dict[str, list[str]] = {
    "solver": ["icoFoam", "simpleFoam", "pisoFoam", "pimpleFoam"],
    "geometry_generator": [
        "cylinder",
        "pipe",
        "backward_facing_step",
        "cavity",
    ],
    "boundary_condition_writer": [
        "no_slip",
        "free_slip",
        "inlet_velocity",
        "outlet_pressure",
        "outlet_advective",
        "periodic",
    ],
    "initial_condition_writer": ["uniform", "potential_flow"],
    "physical_model_writer": [
        "incompressible",
        "turbulent_rans",
        "turbulent_les",
    ],
    "function_object_writer": [
        "forces",
        "forceCoeffs",
        "probes",
        "fieldAverage",
    ],
    "postprocess_metric": [
        "drag_coefficient",
        "lift_coefficient",
        "pressure_drop",
        "velocity_profile",
    ],
}

# Studies requiring capabilities beyond native support
_MISSING_CAPABILITY_MAP: dict[str, list[dict]] = {
    "density_stratification": [
        {
            "capability_id": "buoyancy_model_writer",
            "capability_type": "physical_model_writer",
            "reason": "密度分层需要浮力模型写入器",
            "severity": "blocking",
        },
        {
            "capability_id": "density_stratification_initializer",
            "capability_type": "initial_condition_writer",
            "reason": "需要密度分层初始化器",
            "severity": "blocking",
        },
    ],
    "moving_body": [
        {
            "capability_id": "dynamic_mesh_writer",
            "capability_type": "mesh_generator",
            "reason": "运动边界需要动态网格支持",
            "severity": "blocking",
        },
    ],
    "inclined": [
        {
            "capability_id": "inclined_geometry_generator",
            "capability_type": "geometry_generator",
            "reason": "倾斜几何需要专用网格生成器",
            "severity": "warning",
        },
    ],
    "thermal": [
        {
            "capability_id": "energy_equation_solver",
            "capability_type": "solver",
            "reason": "热分析需要能量方程求解器",
            "severity": "blocking",
        },
    ],
    "elliptic": [
        {
            "capability_id": "elliptic_geometry_generator",
            "capability_type": "geometry_generator",
            "reason": "椭圆柱需要专用网格生成器",
            "severity": "warning",
        },
    ],
    "jet": [
        {
            "capability_id": "impinging_jet_geometry_generator",
            "capability_type": "geometry_generator",
            "reason": "冲击射流需要专用几何生成器",
            "severity": "warning",
        },
    ],
}

_MISSING_OBSERVABLE_CAPS: dict[str, dict] = {
    "internal_wave": {
        "capability_id": "internal_wave_postprocess",
        "capability_type": "postprocess_metric",
        "reason": "内波辐射后处理能力缺失",
        "severity": "blocking",
    },
    "mixing_layer": {
        "capability_id": "mixing_layer_postprocess",
        "capability_type": "postprocess_metric",
        "reason": "混合层厚度后处理能力缺失",
        "severity": "blocking",
    },
}

# Geometry complexity ranking for priority (lower = simpler)
_GEO_COMPLEXITY: dict[str, int] = {
    "backward_facing_step": 1,
    "cavity": 1,
    "pipe": 2,
    "cylinder": 3,
    "elliptic": 4,
    "jet": 5,
}


class CapabilityPreChecker:
    """Pre-check whether the system can handle a given study."""

    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._registry = registry or CapabilityRegistry()
        self._native = dict(_NATIVE_CAPABILITIES)

    def check(self, study: StudyIntent) -> CapabilityCheckResult:
        """Check *study* against registered and native capabilities."""
        supported: list[str] = []
        uncertain: list[str] = []
        missing: list[dict] = []
        blocking_reasons: list[str] = []

        # --- Check geometry ---
        geo_type = study.geometry.get("type", "")
        if geo_type in self._native.get("geometry_generator", []):
            supported.append(f"geometry_generator:{geo_type}")
        elif geo_type:
            uncertain.append(f"geometry_generator:{geo_type}")

        # --- Check physics flags for missing capabilities ---
        physics = study.physical_models
        flags_to_check = [
            ("density_stratification", physics.get("density_stratification", False)),
            ("moving_body", physics.get("moving_body", False)),
            ("inclined", physics.get("inclined", False)),
            ("thermal", physics.get("thermal", False)),
            ("elliptic", geo_type == "elliptic"),
            ("jet", geo_type == "jet"),
        ]

        for flag_key, flag_val in flags_to_check:
            if not flag_val:
                continue
            caps = _MISSING_CAPABILITY_MAP.get(flag_key, [])
            for cap in caps:
                cap_id = cap["capability_id"]
                if self._registry.has_capability(cap_id):
                    supported.append(cap_id)
                else:
                    missing.append(cap)
                    if cap["severity"] == "blocking":
                        blocking_reasons.append(cap["reason"])

        # --- Check observables for missing postprocess capabilities ---
        for obs in study.observables:
            obs_cap = _MISSING_OBSERVABLE_CAPS.get(obs.observable_id)
            if obs_cap:
                if self._registry.has_capability(obs_cap["capability_id"]):
                    supported.append(obs_cap["capability_id"])
                else:
                    missing.append(obs_cap)
                    if obs_cap["severity"] == "blocking":
                        blocking_reasons.append(obs_cap["reason"])

        # --- Check basic solver / BC / IC support ---
        if physics.get("turbulent", False) and "turbulent_les" in self._native.get(
            "physical_model_writer", []
        ):
            supported.append("physical_model_writer:turbulent_les")

        for bc in study.boundary_conditions:
            bc_type = bc.get("type", "")
            if bc_type in self._native.get("boundary_condition_writer", []):
                supported.append(f"boundary_condition_writer:{bc_type}")
            elif bc_type:
                uncertain.append(f"boundary_condition_writer:{bc_type}")

        # --- Determine readiness ---
        has_blocking = any(m["severity"] == "blocking" for m in missing)
        has_warning = any(m["severity"] == "warning" for m in missing)
        has_uncertain = len(uncertain) > 0

        if has_blocking:
            readiness = "not_compilable_yet"
            can_compile = False
        elif has_uncertain or has_warning:
            readiness = "needs_clarification"
            can_compile = False
        else:
            readiness = "draftable"
            can_compile = True

        return CapabilityCheckResult(
            study_id=study.study_id,
            supported_capabilities=supported,
            uncertain_capabilities=uncertain,
            missing_capabilities=missing,
            can_compile=can_compile,
            blocking_reasons=blocking_reasons,
            readiness_level=readiness,
        )


class PriorityRanker:
    """Rank studies by recommended compilation priority (1 = highest)."""

    def rank(
        self,
        studies: list[StudyIntent],
        check_results: dict[str, CapabilityCheckResult],
    ) -> list[StudyIntent]:
        """Return studies sorted by priority, with ``recommended_priority`` set."""
        readiness_order = {
            "draftable": 0,
            "needs_clarification": 1,
            "not_compilable_yet": 2,
        }

        def sort_key(study: StudyIntent) -> tuple:
            result = check_results.get(study.study_id)
            readiness_rank = readiness_order.get(
                result.readiness_level if result else "needs_clarification", 1
            )
            geo_type = study.geometry.get("type", "")
            geo_complexity = _GEO_COMPLEXITY.get(geo_type, 99)
            missing = result.missing_capabilities if result else []
            num_blocking = sum(1 for m in missing if m.get("severity") == "blocking")
            num_missing = len(missing)
            # Within same readiness: fewer blocking missing = higher priority,
            # then fewer total missing, then simpler geometry.
            return (readiness_rank, num_blocking, num_missing, geo_complexity)

        sorted_studies = sorted(studies, key=sort_key)
        for i, study in enumerate(sorted_studies, 1):
            result = check_results.get(study.study_id)
            study.recommended_priority = i
            study.priority_reason = self._build_reason(study, result)
        return sorted_studies

    def _build_reason(
        self, study: StudyIntent, result: CapabilityCheckResult | None
    ) -> str:
        if result is None:
            return "未进行能力检查"
        if result.readiness_level == "draftable":
            return "几何和边界条件最接近当前可落地 OpenFOAM case"
        if result.readiness_level == "needs_clarification":
            return "物理较清楚，但需要部分澄清或能力确认"
        reasons = (
            "; ".join(result.blocking_reasons)
            if result.blocking_reasons
            else "当前系统需要能力扩展"
        )
        return f"当前系统需要能力扩展: {reasons}"


__all__ = [
    "CapabilityCheckResult",
    "CapabilityPreChecker",
    "PriorityRanker",
]
