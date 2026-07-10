"""Synthesize a complete experiment design from a study intent."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from fluid_scientist.study_decomposition.models import StudyIntent

ValueSource = Literal[
    "USER_SPECIFIED",
    "SYSTEM_DERIVED",
    "SYSTEM_SELECTED",
    "TEMPLATE_DEFAULT",
    "ASSUMED_BASELINE",
]


class DesignField(BaseModel):
    value: Any
    unit: str | None = None
    source: ValueSource
    reason: str = ""
    confidence: float = 0.8
    modifiable: bool = True


class AnalysisGoal(BaseModel):
    goal_id: str
    description: str
    category: str = "scientific"
    target_quantities: list[str] = Field(default_factory=list)
    source: ValueSource = "SYSTEM_SELECTED"


class ExperimentDesign(BaseModel):
    research_objective: str
    research_hypotheses: list[str] = Field(default_factory=list)
    target_phenomena: list[str] = Field(default_factory=list)
    boundary_facts: dict[str, Any] = Field(default_factory=dict)
    parameterization_strategy: dict[str, DesignField] = Field(default_factory=dict)
    geometry: dict[str, Any] = Field(default_factory=dict)
    computational_domain: dict[str, Any] = Field(default_factory=dict)
    material_properties: dict[str, DesignField] = Field(default_factory=dict)
    dimensionless_parameters: dict[str, DesignField] = Field(default_factory=dict)
    boundary_conditions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    initial_conditions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    physical_models: dict[str, Any] = Field(default_factory=dict)
    turbulence_model: dict[str, Any] = Field(default_factory=dict)
    solver: dict[str, Any] = Field(default_factory=dict)
    numerical_schemes: dict[str, Any] = Field(default_factory=dict)
    pressure_velocity_coupling: dict[str, Any] = Field(default_factory=dict)
    mesh_strategy: dict[str, Any] = Field(default_factory=dict)
    near_wall_strategy: dict[str, Any] = Field(default_factory=dict)
    time_control: dict[str, Any] = Field(default_factory=dict)
    sampling_strategy: dict[str, Any] = Field(default_factory=dict)
    output_control: dict[str, Any] = Field(default_factory=dict)
    analysis_goals: list[AnalysisGoal] = Field(default_factory=list)
    scientific_metrics: list[dict[str, Any]] = Field(default_factory=list)
    boundary_verification_metrics: list[dict[str, Any]] = Field(default_factory=list)
    credibility_metrics: list[dict[str, Any]] = Field(default_factory=list)
    post_processing: dict[str, Any] = Field(default_factory=dict)
    compute_resources: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)


class ExperimentDesignSynthesizer:
    """Build a complete design skeleton from a ``StudyIntent``."""

    def synthesize(self, study: StudyIntent) -> ExperimentDesign:
        text = f"{study.raw_text} {study.research_objective}".lower()
        geometry_type = self._geometry_type(study, text)
        re_value = self._extract_reynolds_number(study, text)
        goals = self._analysis_goals(study, text)
        boundary_conditions = self._boundary_conditions(study, text, geometry_type)

        return ExperimentDesign(
            research_objective=study.research_objective,
            research_hypotheses=[
                "The selected flow response is governed by the closed dimensionless controls."
            ],
            target_phenomena=[goal.goal_id for goal in goals],
            boundary_facts=boundary_conditions,
            parameterization_strategy={
                "reference_length": DesignField(
                    value="D",
                    source="SYSTEM_SELECTED",
                    reason="Use the characteristic diameter/height as reference length.",
                ),
                "reference_velocity": DesignField(
                    value="U_ref",
                    source="SYSTEM_SELECTED",
                    reason="Use inlet or bulk velocity as reference speed.",
                ),
                "reference_area": DesignField(
                    value="D^2",
                    source="SYSTEM_DERIVED",
                    reason="Reference area for nondimensional force coefficients.",
                ),
            },
            geometry={"type": geometry_type, "characteristic_length": "D", **study.geometry},
            computational_domain=self._domain(geometry_type, text),
            material_properties={
                "rho": DesignField(value=1.0, unit="kg/m3", source="ASSUMED_BASELINE", reason="Nondimensional baseline density."),
                "U_ref": DesignField(value=1.0, unit="m/s", source="ASSUMED_BASELINE", reason="Nondimensional baseline velocity."),
                "D": DesignField(value=1.0, unit="m", source="ASSUMED_BASELINE", reason="Nondimensional baseline length."),
            },
            dimensionless_parameters={
                "Re": DesignField(
                    value=re_value,
                    source="USER_SPECIFIED" if re_value is not None else "TEMPLATE_DEFAULT",
                    reason="Reynolds number from user input or a turbulent benchmark default.",
                    confidence=0.95 if re_value is not None else 0.65,
                )
            },
            boundary_conditions=boundary_conditions,
            initial_conditions={
                "U": {"value": "uniform U_ref", "source": "SYSTEM_SELECTED", "reason": "Stable initial flow field."},
                "p": {"value": 0.0, "unit": "Pa", "source": "TEMPLATE_DEFAULT", "reason": "Gauge pressure baseline."},
            },
            physical_models={"flow": "incompressible", "phase": "single_phase", "temporal": "transient"},
            analysis_goals=goals,
            assumptions=[
                {"field": "nondimensionalization", "reason": "Use D=1, U_ref=1, rho=1 unless user supplies dimensional values."}
            ],
        )

    @staticmethod
    def _extract_reynolds_number(study: StudyIntent, text: str) -> float | None:
        for param in [*study.known_parameters, *study.derived_parameters]:
            if param.canonical_id.lower() in {"re", "reynolds_number"} and param.value is not None:
                return float(param.value)
        match = re.search(r"\bre\s*[=:]?\s*(\d+(?:\.\d+)?)", text)
        if match:
            return float(match.group(1))
        match = re.search(r"雷诺数\s*([0-9]+(?:\.[0-9]+)?)", study.raw_text)
        if match:
            return float(match.group(1))
        return 3900.0 if "3900" in text else None

    @staticmethod
    def _geometry_type(study: StudyIntent, text: str) -> str:
        current = study.geometry.get("type") or study.study_type
        if current and current != "unknown":
            return str(current)
        if any(token in text for token in ("pipe", "管流", "管道")):
            return "pipe"
        if any(token in text for token in ("cylinder", "圆柱", "绕流")):
            return "cylinder_external_flow"
        return "generic_channel"

    @staticmethod
    def _domain(geometry_type: str, text: str) -> dict[str, Any]:
        span = "4D"
        match = re.search(r"(?:spanwise|展向).*?(\d+(?:\.\d+)?)\s*d", text, re.I)
        if match:
            span = f"{match.group(1)}D"
        if geometry_type == "pipe":
            return {"length": "20D", "diameter": "D", "spanwise_length": span, "source": "SYSTEM_SELECTED"}
        if geometry_type == "cylinder_external_flow":
            return {"upstream": "10D", "downstream": "25D", "cross_stream": "20D", "spanwise_length": span, "source": "SYSTEM_SELECTED"}
        return {"length": "20D", "height": "2D", "spanwise_length": span, "source": "SYSTEM_SELECTED"}

    @staticmethod
    def _boundary_conditions(study: StudyIntent, text: str, geometry_type: str) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for bc in study.boundary_conditions:
            location = str(bc.get("location") or bc.get("patch") or bc.get("name") or bc.get("type"))
            result[location] = {**bc, "source": bc.get("source", "USER_SPECIFIED")}
        if "上边界" in text or "top" in text:
            result["top"] = {"type": "free_slip" if ("自由滑移" in text or "free" in text) else "symmetry", "source": "USER_SPECIFIED"}
        if "周期" in text or "periodic" in text:
            result.setdefault("spanwise", {"type": "periodic", "source": "SYSTEM_SELECTED"})
        if geometry_type == "pipe":
            result.setdefault("inlet", {"type": "inlet_velocity", "value": "U_ref", "source": "SYSTEM_SELECTED"})
            result.setdefault("outlet", {"type": "outlet_pressure", "value": 0.0, "source": "SYSTEM_SELECTED"})
            result.setdefault("wall", {"type": "no_slip", "source": "TEMPLATE_DEFAULT"})
        else:
            result.setdefault("inlet", {"type": "inlet_velocity", "value": "U_ref", "source": "SYSTEM_SELECTED"})
            result.setdefault("outlet", {"type": "outlet_advective", "source": "SYSTEM_SELECTED"})
            if geometry_type == "cylinder_external_flow":
                result.setdefault("cylinder", {"type": "no_slip", "source": "TEMPLATE_DEFAULT"})
            result.setdefault("top", {"type": "free_slip", "source": "TEMPLATE_DEFAULT"})
            result.setdefault("bottom", {"type": "free_slip", "source": "TEMPLATE_DEFAULT"})
        return result

    @staticmethod
    def _analysis_goals(study: StudyIntent, text: str) -> list[AnalysisGoal]:
        candidates: list[AnalysisGoal] = []
        combined = f"{text} {' '.join(study.analysis_goals).lower()}"
        mapping = [
            ("wake_deflection", ("wake deflection", "尾迹偏", "尾迹偏斜", "尾迹偏转"), ["wake_center_offset", "wake_deflection_angle"]),
            ("spanwise_reversal", ("spanwise reversal", "展向翻转", "展向反转"), ["sign_change_rate", "phase_difference", "spanwise_correlation"]),
            ("force_spectrum", ("drag", "lift", "阻力", "升力", "阻升力", "频谱", "谱"), ["force_mean", "force_rms", "force_psd", "dominant_frequency", "strouhal"]),
            ("wall_vortex_structure", ("wall vortex", "近壁涡", "壁面涡", "涡结构", "q 准则", "q criterion", "lambda2"), ["Q", "lambda2", "wall_vorticity", "wall_shear_stress"]),
        ]
        for goal_id, keywords, quantities in mapping:
            if any(keyword in combined for keyword in keywords):
                candidates.append(AnalysisGoal(goal_id=goal_id, description=goal_id.replace("_", " "), target_quantities=quantities))
        if not candidates:
            candidates.append(AnalysisGoal(goal_id="baseline_flow_characterization", description="baseline flow characterization", target_quantities=["velocity_profile", "pressure_drop"]))
        return candidates


__all__ = ["AnalysisGoal", "DesignField", "ExperimentDesign", "ExperimentDesignSynthesizer"]
