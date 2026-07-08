"""PhysicsSpecBuilder — builds physics specification from research question.

The builder uses LLM (or rule-based fallback) to understand the research
problem and output a structured PhysicsSpec, NOT a parameter table.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import Any

from fluid_scientist.prompts import load_prompt

logger = logging.getLogger(__name__)


class PhysicsSpecResult:
    """Result of physics spec building."""

    def __init__(
        self,
        physical_system: str = "unknown",
        geometry_type: str = "unknown",
        phase_model: str = "unknown",
        material_or_fluid_name: str = "unknown",
        compressibility: str = "unknown",
        thermal_model: str = "isothermal",
        flow_regime: str = "unknown",
        temporal_type: str = "unknown",
        research_objective: str = "",
        target_metrics: list[str] | None = None,
        known_conditions: dict[str, Any] | None = None,
        uncertain_assumptions: list[dict] | None = None,
        required_clarifications: list[dict] | None = None,
        confidence: float = 0.0,
        fallback_used: bool = False,
    ):
        self.physical_system = physical_system
        self.geometry_type = geometry_type
        self.phase_model = phase_model
        self.material_or_fluid_name = material_or_fluid_name
        self.compressibility = compressibility
        self.thermal_model = thermal_model
        self.flow_regime = flow_regime
        self.temporal_type = temporal_type
        self.research_objective = research_objective
        self.target_metrics = target_metrics or []
        self.known_conditions = known_conditions or {}
        self.uncertain_assumptions = uncertain_assumptions or []
        self.required_clarifications = required_clarifications or []
        self.confidence = confidence
        self.fallback_used = fallback_used

    def to_dict(self) -> dict:
        return {
            "physical_system": self.physical_system,
            "geometry_type": self.geometry_type,
            "phase_model": self.phase_model,
            "material_or_fluid_name": self.material_or_fluid_name,
            "compressibility": self.compressibility,
            "thermal_model": self.thermal_model,
            "flow_regime": self.flow_regime,
            "temporal_type": self.temporal_type,
            "research_objective": self.research_objective,
            "target_metrics": self.target_metrics,
            "known_conditions": self.known_conditions,
            "uncertain_assumptions": self.uncertain_assumptions,
            "required_clarifications": self.required_clarifications,
            "confidence": self.confidence,
            "fallback_used": self.fallback_used,
        }


class PhysicsSpecBuilder:
    """Builds PhysicsSpec from a research question.

    Uses LLM when available, falls back to rule-based detection.
    """

    # Keyword maps for fake mode
    _SYSTEM_KEYWORDS = {
        "pipe_flow": ["管流", "管道", "圆管", "pipe", "tube", "duct"],
        "external_flow": [
            "绕流", "圆柱", "外流", "external", "cylinder", "airfoil", "翼型",
        ],
        "cavity_flow": ["方腔", "空腔", "cavity", "lid-driven"],
        "channel_flow": ["通道", "channel"],
        "rotating_flow": ["旋转", "rotating", "disk"],
        "heat_transfer": ["传热", "heat", "thermal"],
        "multiphase_flow": ["多相", "multiphase", "两相", "气液"],
    }

    _GEOMETRY_KEYWORDS = {
        "pipe": ["管", "pipe", "tube"],
        "cylinder": ["圆柱", "cylinder"],
        "cavity": ["方腔", "空腔", "cavity"],
        "channel": ["通道", "channel"],
        "plate": ["平板", "plate"],
    }

    _FLUID_KEYWORDS = {
        "water": ["水", "water"],
        "air": ["空气", "air"],
        "oil": ["油", "oil"],
    }

    _METRIC_KEYWORDS = {
        "pressure_drop": ["压降", "pressure drop", "压力损失"],
        "drag_coefficient": ["阻力", "drag", "阻力系数"],
        "lift_coefficient": ["升力", "lift", "升力系数"],
        "strouhal_number": ["涡脱落", "strouhal", "涡街"],
        "velocity_uniformity": ["速度均匀性", "velocity uniformity"],
        "wall_shear_stress": ["壁面剪应力", "wall shear"],
        "nusselt_number": ["努塞尔", "nusselt"],
    }

    def __init__(
        self,
        llm_client: Any | None = None,
        model_name: str = "gpt-4",
        provider: str = "openai",
    ):
        self.llm_client = llm_client
        self.model_name = model_name
        self.provider = provider
        self._fake_mode = llm_client is None

    def build(
        self,
        research_question: str,
        accumulated_context: str = "",
    ) -> PhysicsSpecResult:
        """Build physics spec from research question."""
        if self._fake_mode:
            return self._build_fake(research_question, accumulated_context)
        return self._build_llm(research_question, accumulated_context)

    def _build_fake(
        self,
        question: str,
        context: str,
    ) -> PhysicsSpecResult:
        """Rule-based physics spec building."""
        q_lower = question.lower()

        # Detect physical system
        physical_system = "unknown"
        for sys_id, keywords in self._SYSTEM_KEYWORDS.items():
            if any(kw in q_lower for kw in keywords):
                physical_system = sys_id
                break

        # Detect geometry
        geometry_type = "unknown"
        for geo_id, keywords in self._GEOMETRY_KEYWORDS.items():
            if any(kw in q_lower for kw in keywords):
                geometry_type = geo_id
                break

        # Detect fluid
        material = "unknown"
        for fluid_id, keywords in self._FLUID_KEYWORDS.items():
            if any(kw in q_lower for kw in keywords):
                material = fluid_id
                break

        # Detect metrics
        target_metrics = []
        for metric_id, keywords in self._METRIC_KEYWORDS.items():
            if any(kw in q_lower for kw in keywords):
                target_metrics.append(metric_id)

        # Detect compressibility
        compressibility = "unknown"
        if any(kw in q_lower for kw in ["可压", "compressible", "马赫", "mach"]):
            compressibility = "compressible"
        elif any(kw in q_lower for kw in ["不可压", "incompressible"]):
            compressibility = "incompressible"

        # Detect temporal type
        temporal_type = "unknown"
        if any(kw in q_lower for kw in ["瞬态", "transient", "非定常", "unsteady"]):
            temporal_type = "transient"
        elif any(kw in q_lower for kw in ["稳态", "steady", "定常"]):
            temporal_type = "steady"

        # Detect flow regime
        flow_regime = "unknown"
        if any(kw in q_lower for kw in ["湍流", "turbulent", "turbulence"]):
            flow_regime = "turbulent"
        elif any(kw in q_lower for kw in ["层流", "laminar"]):
            flow_regime = "laminar"

        # Detect thermal
        thermal_model = "isothermal"
        if any(kw in q_lower for kw in ["传热", "heat", "thermal", "温度"]):
            thermal_model = "heat_transfer"

        # Extract known values from question (simple number extraction)
        known_conditions = self._extract_known_values(question)

        return PhysicsSpecResult(
            physical_system=physical_system,
            geometry_type=geometry_type,
            phase_model="single_phase",
            material_or_fluid_name=material,
            compressibility=compressibility,
            thermal_model=thermal_model,
            flow_regime=flow_regime,
            temporal_type=temporal_type,
            research_objective=question,
            target_metrics=target_metrics,
            known_conditions=known_conditions,
            confidence=0.7 if physical_system != "unknown" else 0.3,
            fallback_used=True,
        )

    def _build_llm(
        self,
        question: str,
        context: str,
    ) -> PhysicsSpecResult:
        """LLM-based physics spec building."""
        try:
            system_prompt = load_prompt("physics_spec_prompt")
            # Construct messages
            messages = [
                {"role": "system", "content": system_prompt},
            ]
            if context:
                messages.append({"role": "user", "content": context})
            messages.append({"role": "user", "content": question})

            # Call LLM (implementation depends on provider)
            response = self.llm_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.1,
            )
            content = response.choices[0].message.content
            data = json.loads(content)

            return PhysicsSpecResult(
                physical_system=data.get("physical_system", "unknown"),
                geometry_type=data.get("geometry_type", "unknown"),
                phase_model=data.get("phase_model", "unknown"),
                material_or_fluid_name=data.get(
                    "material_or_fluid_name", "unknown",
                ),
                compressibility=data.get("compressibility", "unknown"),
                thermal_model=data.get("thermal_model", "isothermal"),
                flow_regime=data.get("flow_regime", "unknown"),
                temporal_type=data.get("temporal_type", "unknown"),
                research_objective=data.get("research_objective", question),
                target_metrics=data.get("target_metrics", []),
                known_conditions=data.get("known_conditions", {}),
                uncertain_assumptions=data.get("uncertain_assumptions", []),
                required_clarifications=data.get(
                    "required_clarifications", [],
                ),
                confidence=data.get("confidence", 0.8),
                fallback_used=False,
            )
        except Exception as e:
            logger.warning(
                "LLM physics spec failed: %s, falling back to fake mode", e,
            )
            return self._build_fake(question, context)

    def _extract_known_values(self, question: str) -> dict:
        """Extract known numerical values from the question."""
        known = {}

        # Pattern: parameter_name + number + unit
        patterns = [
            (
                r"(管径|直径|diameter|D)\s*[：:=]?\s*(\d+\.?\d*)\s*(mm|cm|m|毫米|厘米|米)?",
                "diameter",
            ),
            (
                r"(长度|length|L)\s*[：:=]?\s*(\d+\.?\d*)\s*(mm|cm|m|毫米|厘米|米)?",
                "length",
            ),
            (
                r"(速度|velocity|U)\s*[：:=]?\s*(\d+\.?\d*)\s*(m/s|cm/s)?",
                "inlet_velocity",
            ),
            (
                r"(质量流量|mass.?flow)\s*[：:=]?\s*(\d+\.?\d*)\s*(kg/s|kg/h)?",
                "mass_flow_rate",
            ),
            (
                r"(雷诺数|Reynolds|Re)\s*[：:=]?\s*(\d+\.?\d*)",
                "reynolds_number",
            ),
            (
                r"(边长|side.?length)\s*[：:=]?\s*(\d+\.?\d*)\s*(mm|cm|m|毫米|厘米|米)?",
                "side_length",
            ),
        ]

        for pattern, param_id in patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                value_str = match.group(2)
                unit = match.group(3) if match.lastindex >= 3 else None
                with contextlib.suppress(ValueError):
                    value = float(value_str)
                    # Convert units
                    if unit in ("mm", "毫米"):
                        value *= 0.001
                        unit = "m"
                    elif unit in ("cm", "厘米"):
                        value *= 0.01
                        unit = "m"
                    elif unit in ("m", "米"):
                        unit = "m"

                    known[param_id] = {
                        "value": value,
                        "unit": unit or "",
                        "evidence": match.group(0),
                    }

        return known
