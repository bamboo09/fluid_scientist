"""Pass 3: Scientific Normalizer -- normalize user descriptions to concepts.

The :class:`ScientificNormalizer` takes the raw facts from Pass 1 and
maps each user-facing description to a canonical scientific concept.
When a description is ambiguous (i.e. multiple scientific interpretations
are valid), the normalizer preserves all candidate concepts rather than
arbitrarily picking one.  This ensures that ambiguities are surfaced to
the user rather than silently resolved.
"""

from __future__ import annotations

import re
from typing import Any

from fluid_scientist.llm_pipeline.models import ExtractedFact, NormalizedConcept


def _is_ascii(keyword: str) -> bool:
    """Check if a keyword contains only ASCII characters."""
    return all(ord(c) < 128 for c in keyword)


# ---------------------------------------------------------------------------
# Normalization mapping table
# ---------------------------------------------------------------------------
# Each entry maps a set of user-facing keywords / phrases to a canonical
# scientific concept.  When multiple concepts are valid candidates, they
# are listed in ``candidates`` and the concept is marked AMBIGUOUS.
#
# Structure:
#   keywords: list[str]  -- case-insensitive substrings to match
#   normalized: str      -- canonical concept (empty if ambiguous)
#   candidates: list[dict] -- alternative concepts with confidence
#   confidence: float    -- confidence in the mapping [0, 1]
# ---------------------------------------------------------------------------

_NORMALIZATION_TABLE: list[dict[str, Any]] = [
    # --- Initial conditions ---
    {
        "keywords": ["quiescent", "at rest", "全场静止", "静止", "初始静止"],
        "normalized": "quiescent_initial_velocity_field",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["fully developed", "充分发展"],
        "normalized": "developed_pipe_inlet",
        "candidates": [],
        "confidence": 0.9,
    },
    {
        "keywords": ["uniform inflow", "均匀来流", "uniform inlet"],
        "normalized": "uniform_velocity_inlet",
        "candidates": [],
        "confidence": 1.0,
    },
    # --- Boundary conditions ---
    {
        "keywords": ["no-slip", "no slip", "壁面黏住", "无滑移", "壁面无滑移"],
        "normalized": "no_slip_wall_intent",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["natural outflow", "自然流出", "convective outlet"],
        "normalized": "",
        "candidates": [
            {"concept": "advective_outlet", "confidence": 0.6},
            {"concept": "non_reflecting_outlet", "confidence": 0.4},
        ],
        "confidence": 0.0,
    },
    {
        "keywords": ["pressure outlet", "压力出口"],
        "normalized": "pressure_outlet_intent",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["periodic", "周期"],
        "normalized": "periodic_boundary_pair",
        "candidates": [],
        "confidence": 0.9,
    },
    {
        "keywords": ["symmetry", "对称"],
        "normalized": "symmetry_plane",
        "candidates": [],
        "confidence": 1.0,
    },
    # --- Turbulence ---
    {
        "keywords": ["les", "大涡"],
        "normalized": "large_eddy_simulation",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["rans", "雷诺平均"],
        "normalized": "reynolds_averaged_navier_stokes",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["komegasst", "k-omega-sst", "komega sst"],
        "normalized": "kOmegaSST",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["spalartallmaras", "spalart-allmaras"],
        "normalized": "SpalartAllmaras",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["kepsilon", "k-epsilon"],
        "normalized": "kEpsilon",
        "candidates": [],
        "confidence": 1.0,
    },
    # --- Observables ---
    {
        "keywords": ["drag", "阻力"],
        "normalized": "drag_coefficient",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["lift", "升力"],
        "normalized": "lift_coefficient",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["spectrum", "频谱"],
        "normalized": "frequency_spectrum",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["vortex shedding", "涡脱落"],
        "normalized": "vortex_shedding_detection",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["wake", "尾迹"],
        "normalized": "wake_analysis",
        "candidates": [],
        "confidence": 0.9,
    },
    {
        "keywords": ["heat flux", "热流"],
        "normalized": "wall_heat_flux",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["wall shear", "壁面剪应力"],
        "normalized": "wall_shear_stress",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["pressure coefficient", "压力系数"],
        "normalized": "pressure_coefficient",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["nu number", "努塞尔"],
        "normalized": "nusselt_number",
        "candidates": [],
        "confidence": 1.0,
    },
    # --- Constraints ---
    {
        "keywords": ["transient", "瞬态", "unsteady", "非定常"],
        "normalized": "transient_time_mode",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["steady", "稳态", "定常"],
        "normalized": "steady_time_mode",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["isothermal", "等温"],
        "normalized": "isothermal_flow",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["compressible", "可压缩"],
        "normalized": "compressible_flow",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["multiphase", "多相", "two-phase", "两相"],
        "normalized": "multiphase_flow",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["moving mesh", "moving", "动网格", "移动"],
        "normalized": "moving_mesh",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["gravity", "重力"],
        "normalized": "gravity_body_force",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["buoyancy", "浮力"],
        "normalized": "buoyancy_force",
        "candidates": [
            {"concept": "boussinesq_approximation", "confidence": 0.6},
            {"concept": "full_buoyancy", "confidence": 0.4},
        ],
        "confidence": 0.0,
    },
    # --- Entities ---
    {
        "keywords": ["cylinder", "圆柱"],
        "normalized": "cylinder_geometry",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["pipe", "管道"],
        "normalized": "pipe_geometry",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["sphere", "球"],
        "normalized": "sphere_geometry",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["nozzle", "喷嘴"],
        "normalized": "nozzle_geometry",
        "candidates": [],
        "confidence": 1.0,
    },
    {
        "keywords": ["airfoil", "翼型"],
        "normalized": "airfoil_geometry",
        "candidates": [],
        "confidence": 1.0,
    },
]


class ScientificNormalizer:
    """Normalize user descriptions to canonical scientific concepts.

    For each extracted fact, the normalizer looks up the user's raw text
    in the normalization table and produces a :class:`NormalizedConcept`.
    When multiple interpretations are valid, the concept is marked
    ``AMBIGUOUS`` and all candidates are preserved.
    """

    def normalize(self, facts: list[ExtractedFact]) -> list[NormalizedConcept]:
        """Normalize all facts to scientific concepts.

        Args:
            facts: The list of facts extracted in Pass 1.

        Returns:
            A list of :class:`NormalizedConcept` objects, one per fact
            that could be normalized.  Facts that do not match any
            normalization entry are still included with status
            ``UNRESOLVED``.
        """
        concepts: list[NormalizedConcept] = []
        for fact in facts:
            concept = self._normalize_fact(fact)
            if concept is not None:
                concepts.append(concept)
        return concepts

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _normalize_fact(self, fact: ExtractedFact) -> NormalizedConcept | None:
        """Normalize a single fact to a scientific concept."""
        raw_lower = fact.raw_text.lower()

        # Also check the fact value if raw_text doesn't match.
        value_str = str(fact.value).lower() if fact.value is not None else ""

        for entry in _NORMALIZATION_TABLE:
            for keyword in entry["keywords"]:
                if self._keyword_matches(keyword, raw_lower, value_str):
                    if entry["candidates"]:
                        # Ambiguous: preserve all candidates.
                        return NormalizedConcept(
                            raw_text=fact.raw_text,
                            normalized_concept="",
                            candidate_concepts=list(entry["candidates"]),
                            confidence=0.0,
                            status="AMBIGUOUS",
                        )
                    return NormalizedConcept(
                        raw_text=fact.raw_text,
                        normalized_concept=entry["normalized"],
                        candidate_concepts=[],
                        confidence=entry["confidence"],
                        status="CONFIRMED",
                    )

        # No match found -- mark as unresolved if the fact is meaningful.
        if fact.category in ("boundary", "initial_condition", "constraint",
                             "observable", "entity", "material"):
            return NormalizedConcept(
                raw_text=fact.raw_text,
                normalized_concept="",
                candidate_concepts=[],
                confidence=0.0,
                status="UNRESOLVED",
            )

        # Parameters and research goals don't need normalization.
        return None

    @staticmethod
    def _keyword_matches(keyword: str, raw_lower: str, value_str: str) -> bool:
        """Check if a keyword matches the fact text.

        For ASCII keywords (English), uses word-boundary regex matching
        to avoid false positives (e.g. ``"rans"`` inside ``"transient"``).
        For non-ASCII keywords (Chinese), uses simple substring matching
        since word boundaries are not well-defined for CJK characters.
        """
        kw_lower = keyword.lower()
        if _is_ascii(keyword):
            # Use word boundaries for ASCII keywords.
            pattern = r'\b' + re.escape(kw_lower) + r'\b'
            if re.search(pattern, raw_lower):
                return True
            return bool(value_str and re.search(pattern, value_str))
        # Simple substring matching for non-ASCII (Chinese) keywords.
        return kw_lower in raw_lower or kw_lower in value_str


__all__ = ["ScientificNormalizer"]
