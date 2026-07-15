"""Pass 1: Fact Extractor -- extract only what the user explicitly stated.

The :class:`FactExtractor` is the first pass of the multi-pass LLM
decomposition pipeline.  Its sole responsibility is to identify and
structure the facts that the user *actually wrote* -- nothing more.

Design rules (enforced in the prompt and the rule-based fallback):

* Do **NOT** add default values the user did not state.
* Do **NOT** recommend solvers or turbulence models.
* Do **NOT** map concepts to OpenFOAM terminology.
* Do **NOT** add facts the user did not state.
"""

from __future__ import annotations

import re
from typing import Any

from fluid_scientist.llm_pipeline.models import ExtractedFact

# Category names accepted by ExtractedFact.category
_VALID_CATEGORIES: frozenset[str] = frozenset({
    "entity",
    "parameter",
    "initial_condition",
    "boundary",
    "time_sequence",
    "research_goal",
    "observable",
    "constraint",
    "material",
})


class FactExtractor:
    """Extract explicit facts from a user research description.

    Parameters:
        llm_client: An optional LLM client with a ``call`` method.  When
            provided, the extractor attempts LLM-based extraction first
            and falls back to rule-based extraction on failure.  When
            ``None`` (default), rule-based extraction is used directly.
    """

    def __init__(self, llm_client: Any = None) -> None:
        self._llm = llm_client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, user_text: str) -> list[ExtractedFact]:
        """Extract structured facts from *user_text*.

        Args:
            user_text: The user's natural-language research description.

        Returns:
            A list of :class:`ExtractedFact` objects.  Never empty -- if
            no specific facts are found, the entire text is captured as
            a ``research_goal`` fact.
        """
        if self._llm is not None:
            try:
                return self._extract_with_llm(user_text)
            except Exception:
                # Fall back to rule-based extraction on any LLM failure.
                pass
        return self._extract_rule_based(user_text)

    # ------------------------------------------------------------------
    # LLM-based extraction
    # ------------------------------------------------------------------

    def _extract_with_llm(self, user_text: str) -> list[ExtractedFact]:
        """Use the LLM to extract facts.

        The prompt strictly instructs the model to extract only what the
        user stated -- no defaults, no OpenFOAM mapping, no additions.
        """
        system_prompt = self._build_prompt(user_text)
        output_schema = {
            "type": "object",
            "properties": {
                "facts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "fact_id": {"type": "string"},
                            "category": {"type": "string"},
                            "raw_text": {"type": "string"},
                            "value": {},
                            "unit": {"type": "string"},
                            "source_location": {"type": "string"},
                        },
                        "required": ["fact_id", "category", "raw_text"],
                    },
                }
            },
            "required": ["facts"],
        }

        parsed, _record = self._llm.call(
            purpose="study_decomposition",
            prompt_name="fact_extraction",
            system_prompt=system_prompt,
            user_message=user_text,
            output_schema=output_schema,
        )

        raw_facts = parsed.get("facts", [])
        if not isinstance(raw_facts, list):
            return self._extract_rule_based(user_text)

        facts: list[ExtractedFact] = []
        for i, raw in enumerate(raw_facts):
            if not isinstance(raw, dict):
                continue
            category = raw.get("category", "research_goal")
            if category not in _VALID_CATEGORIES:
                category = "research_goal"
            try:
                facts.append(
                    ExtractedFact(
                        fact_id=raw.get("fact_id", f"F{i + 1}"),
                        category=category,  # type: ignore[arg-type]
                        raw_text=str(raw.get("raw_text", "")),
                        value=raw.get("value"),
                        unit=str(raw.get("unit", "")),
                        source_location=str(raw.get("source_location", "")),
                    )
                )
            except Exception:
                # Skip malformed fact entries from the LLM.
                continue

        if not facts:
            return self._extract_rule_based(user_text)
        return facts

    # ------------------------------------------------------------------
    # Rule-based fallback extraction
    # ------------------------------------------------------------------

    def _extract_rule_based(self, user_text: str) -> list[ExtractedFact]:
        """Rule-based fallback fact extraction.

        Uses regular expressions and keyword matching to identify common
        physical parameters, entities, boundary conditions, observables,
        and constraints from both English and Chinese text.
        """
        facts: list[ExtractedFact] = []
        text_lower = user_text.lower()

        # --- Reynolds number ---
        re_match = re.search(r'Re\s*=\s*(\d+\.?\d*)', user_text, re.IGNORECASE)
        if re_match:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="parameter",
                raw_text=re_match.group(0),
                value=(
                    int(re_match.group(1))
                    if "." not in re_match.group(1)
                    else float(re_match.group(1))
                ),
                unit="dimensionless",
                source_location=f"char {re_match.start()}-{re_match.end()}",
            ))

        # --- Velocity (e.g. "1.5 m/s" or "U = 2 m/s") ---
        vel_match = re.search(r'(\d+\.?\d*)\s*m/s', user_text, re.IGNORECASE)
        if vel_match:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="parameter",
                raw_text=vel_match.group(0),
                value=float(vel_match.group(1)),
                unit="m/s",
                source_location=f"char {vel_match.start()}-{vel_match.end()}",
            ))

        # --- Diameter ---
        dia_match = re.search(
            r'(?:diameter|D|直径)\s*=?\s*(\d+\.?\d*)\s*(m|mm|cm)?',
            user_text,
            re.IGNORECASE,
        )
        if dia_match:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="parameter",
                raw_text=dia_match.group(0),
                value=float(dia_match.group(1)),
                unit=dia_match.group(2) or "m",
                source_location=f"char {dia_match.start()}-{dia_match.end()}",
            ))

        # --- Kinematic viscosity (nu) ---
        nu_match = re.search(
            r'(?:nu|ν|运动黏度|黏度)\s*=?\s*(\d+\.?\d*e?-?\d*)',
            user_text,
            re.IGNORECASE,
        )
        if nu_match:
            raw_val = nu_match.group(1)
            try:
                val = float(raw_val)
            except ValueError:
                val = raw_val
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="parameter",
                raw_text=nu_match.group(0),
                value=val,
                unit="m^2/s",
                source_location=f"char {nu_match.start()}-{nu_match.end()}",
            ))

        # --- Density ---
        rho_match = re.search(
            r'(?:rho|ρ|密度)\s*=?\s*(\d+\.?\d*)\s*(kg/m\^?3|kg/m3)?',
            user_text,
            re.IGNORECASE,
        )
        if rho_match:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="parameter",
                raw_text=rho_match.group(0),
                value=float(rho_match.group(1)),
                unit=rho_match.group(2) or "kg/m^3",
                source_location=f"char {rho_match.start()}-{rho_match.end()}",
            ))

        # --- Temperature ---
        temp_match = re.search(
            r'(\d+\.?\d*)\s*(K|°?C|摄氏度|开尔文)',
            user_text,
            re.IGNORECASE,
        )
        if temp_match:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="parameter",
                raw_text=temp_match.group(0),
                value=float(temp_match.group(1)),
                unit=temp_match.group(2),
                source_location=f"char {temp_match.start()}-{temp_match.end()}",
            ))

        # --- Entities ---
        entity_keywords: dict[str, str] = {
            "cylinder": "cylinder",
            "圆柱": "cylinder",
            "pipe": "pipe",
            "管道": "pipe",
            "sphere": "sphere",
            "球": "sphere",
            "box": "box",
            "nozzle": "nozzle",
            "喷嘴": "nozzle",
            "wall": "plane_wall",
            "壁面": "plane_wall",
            "airfoil": "airfoil",
            "翼型": "airfoil",
            "flat plate": "plane_wall",
            "平板": "plane_wall",
        }
        for keyword, kind in entity_keywords.items():
            if keyword in text_lower:
                loc = text_lower.find(keyword)
                facts.append(ExtractedFact(
                    fact_id=f"F{len(facts) + 1}",
                    category="entity",
                    raw_text=keyword,
                    value=kind,
                    source_location=f"char {loc}",
                ))

        # --- Boundary conditions ---
        boundary_keywords: dict[str, str] = {
            "no-slip": "no_slip_wall",
            "no slip": "no_slip_wall",
            "无滑移": "no_slip_wall",
            "inlet": "inlet",
            "入口": "inlet",
            "outlet": "outlet",
            "出口": "outlet",
            "periodic": "periodic",
            "周期": "periodic",
            "symmetry": "symmetry",
            "对称": "symmetry",
            "convective outlet": "advective_outlet",
            "自然流出": "advective_outlet",
            "pressure outlet": "pressure_outlet",
            "压力出口": "pressure_outlet",
            "uniform inflow": "uniform_velocity_inlet",
            "均匀来流": "uniform_velocity_inlet",
        }
        for keyword, kind in boundary_keywords.items():
            if keyword in text_lower:
                loc = text_lower.find(keyword)
                facts.append(ExtractedFact(
                    fact_id=f"F{len(facts) + 1}",
                    category="boundary",
                    raw_text=keyword,
                    value=kind,
                    source_location=f"char {loc}",
                ))

        # --- Time mode constraints ---
        if any(w in text_lower for w in ["transient", "瞬态", "unsteady", "非定常"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="transient",
                value="transient",
            ))
        if any(w in text_lower for w in ["steady", "稳态", "定常"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="steady",
                value="steady",
            ))

        # --- Dimensionality ---
        if "2d" in text_lower or "二维" in text_lower:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="2D",
                value="2D",
            ))
        if "3d" in text_lower or "三维" in text_lower:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="3D",
                value="3D",
            ))

        # --- Spanwise ---
        if "spanwise" in text_lower or "展向" in text_lower:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="spanwise",
                value="spanwise",
            ))

        # --- Initial conditions ---
        if any(w in text_lower for w in ["quiescent", "静止", "at rest", "初始静止"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="initial_condition",
                raw_text="quiescent",
                value="quiescent",
            ))
        if any(w in text_lower for w in ["fully developed", "充分发展"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="initial_condition",
                raw_text="fully developed",
                value="fully_developed",
            ))

        # --- Observables ---
        observable_keywords: dict[str, str] = {
            "drag": "drag_coefficient",
            "阻力": "drag_coefficient",
            "lift": "lift_coefficient",
            "升力": "lift_coefficient",
            "spectrum": "frequency_spectrum",
            "频谱": "frequency_spectrum",
            "vortex shedding": "vortex_shedding",
            "涡脱落": "vortex_shedding",
            "wake": "wake_analysis",
            "尾迹": "wake_analysis",
            "heat flux": "wall_heat_flux",
            "热流": "wall_heat_flux",
            "wall shear": "wall_shear_stress",
            "壁面剪应力": "wall_shear_stress",
            "pressure coefficient": "pressure_coefficient",
            "压力系数": "pressure_coefficient",
            "nu number": "nusselt_number",
            "努塞尔": "nusselt_number",
        }
        for keyword, kind in observable_keywords.items():
            if keyword in text_lower:
                loc = text_lower.find(keyword)
                facts.append(ExtractedFact(
                    fact_id=f"F{len(facts) + 1}",
                    category="observable",
                    raw_text=keyword,
                    value=kind,
                    source_location=f"char {loc}",
                ))

        # --- Turbulence model constraints ---
        # Use word boundaries to avoid matching substrings (e.g. "rans"
        # inside "transient", "les" inside other words).
        if re.search(r'\bles\b', text_lower) or "大涡" in text_lower:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="LES",
                value="LES",
            ))
        if re.search(r'\brans\b', text_lower) or "雷诺平均" in text_lower:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="RANS",
                value="RANS",
            ))
        if re.search(r'\bdns\b', text_lower) or "直接数值模拟" in text_lower:
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="DNS",
                value="DNS",
            ))

        # --- Specific RANS models ---
        rans_models = ["komegasst", "k-omega-sst", "komega sst",
                       "spalartallmaras", "spalart-allmaras",
                       "kepsilon", "k-epsilon"]
        for model in rans_models:
            if model in text_lower:
                facts.append(ExtractedFact(
                    fact_id=f"F{len(facts) + 1}",
                    category="constraint",
                    raw_text=model,
                    value=model.upper().replace("-", ""),
                ))
                break

        # --- Heat transfer ---
        if any(w in text_lower for w in ["isothermal", "等温"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="isothermal",
                value="isothermal",
            ))

        # --- Multiphase ---
        if any(w in text_lower for w in ["multiphase", "多相", "two-phase", "两相"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="multiphase",
                value="multiphase",
            ))

        # --- Moving mesh ---
        if any(w in text_lower for w in ["moving", "移动", "动网格", "moving mesh"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="moving_mesh",
                value="moving_mesh",
            ))

        # --- Gravity / buoyancy ---
        if any(w in text_lower for w in ["gravity", "重力", "buoyancy", "浮力"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="gravity",
                value="gravity",
            ))

        # --- Compressibility ---
        if any(w in text_lower for w in ["compressible", "可压缩", "ma > 0.3", "mach"]):
            facts.append(ExtractedFact(
                fact_id=f"F{len(facts) + 1}",
                category="constraint",
                raw_text="compressible",
                value="compressible",
            ))

        # --- If no facts found, add the whole text as a research goal ---
        if not facts:
            facts.append(ExtractedFact(
                fact_id="F1",
                category="research_goal",
                raw_text=user_text[:200],
                source_location="char 0",
            ))

        return facts

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(self, user_text: str) -> str:
        """Build the system prompt for LLM-based fact extraction."""
        categories_str = ", ".join(sorted(_VALID_CATEGORIES))
        return f"""You are a Fact Extractor. Extract ONLY the facts that the user explicitly stated.
Do NOT add default values. Do NOT recommend solvers. Do NOT map to OpenFOAM.
Do NOT add facts the user did not state.

User text:
{user_text}

Output a JSON object with a "facts" array. Each fact must have:
- fact_id: a unique identifier like "F1", "F2", etc.
- category: one of {categories_str}
- raw_text: the exact text snippet from the user input
- value: the parsed value (number, string, or null)
- unit: the physical unit if applicable, empty string otherwise
- source_location: approximate character position in the original text

Only extract what the user explicitly stated. If unsure, do not include it.
"""


__all__ = ["FactExtractor"]
