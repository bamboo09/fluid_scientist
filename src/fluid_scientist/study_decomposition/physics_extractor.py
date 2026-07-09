"""Physics frame extraction from natural-language CFD study descriptions.

The :class:`PhysicsFrameExtractor` uses lightweight regex and keyword
heuristics to turn a free-form study description (often a single sentence
mixing Chinese and English) into a structured :class:`PhysicsFrame` plus
companion lists of parameters, observables, conditions and analysis goals.

This is a heuristic, first-pass extractor: it deliberately favours recall
over precision and leaves deeper reasoning to downstream LLM-driven stages.
"""

from __future__ import annotations

import re
from typing import Any

from fluid_scientist.study_decomposition.models import (
    ExtractedParameter,
    ObservableSpec,
    PhysicsFrame,
)


class PhysicsFrameExtractor:
    """Extract structured physics information from a study description.

    The extractor is intentionally rule-based so that it runs quickly and
    deterministically during the first decomposition pass.  It recognises
    bilingual (Chinese / English) keywords for dimensions, temporal type,
    flow regime, geometry, wall proximity, inclination, moving bodies,
    thermal / buoyancy / stratification effects and spanwise periodicity,
    and it parses dimensionless numbers (Re, Fr), angles and geometric
    ratios from the raw text.

    All keyword matching is performed against a lower-cased copy of the
    input: Chinese characters are unaffected by ``str.lower()`` while
    English keywords become case-insensitive, so a single substring test
    covers both languages.
    """

    # -- dimension -----------------------------------------------------------
    _DIMENSION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
        ("3D", re.compile(r"3d|三维", re.IGNORECASE)),
        ("2D", re.compile(r"2d|二维", re.IGNORECASE)),
    ]

    # -- temporal type -------------------------------------------------------
    # ``steady`` is matched with a regex rather than a plain substring so
    # that "unsteady" / "非定常" (which contain "steady" / "定常") are not
    # mis-read as steady.  The negative lookbehind ``(?<!非)`` rejects the
    # "定常" embedded in "非定常", and ``\bsteady\b`` rejects the "steady"
    # embedded in "unsteady".
    _STEADY_RE = re.compile(r"(?:(?<!非)定常|\bsteady\b)", re.IGNORECASE)
    _TRANSIENT_KEYWORDS: tuple[str, ...] = ("transient", "非定常", "unsteady")

    # -- flow regime ---------------------------------------------------------
    _TURBULENT_KEYWORDS: tuple[str, ...] = ("turbulent", "turbulence", "湍流")
    _LAMINAR_KEYWORDS: tuple[str, ...] = ("laminar", "层流")
    _TRANSITIONAL_KEYWORDS: tuple[str, ...] = ("transitional", "转捩")

    # -- geometry (order matters: specific before generic) ------------------
    # ``elliptic`` must precede ``cylinder`` because the Chinese term
    # "椭圆柱" (elliptic cylinder) contains "圆柱" (cylinder).
    _GEOMETRY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
        ("elliptic", ("椭圆", "elliptic", "ellipse")),
        ("cylinder", ("圆柱", "cylinder")),
        ("jet", ("射流", "jet")),
        ("step", ("后台阶", "台阶", "backward-facing", "step")),
        ("pipe", ("管道", "圆管", "pipe", "tube", "duct")),
        ("cavity", ("方腔", "空腔", "cavity", "lid-driven")),
    ]

    # -- boolean flags -------------------------------------------------------
    _NEAR_WALL_KEYWORDS: tuple[str, ...] = (
        "近壁", "near wall", "near-wall", "wall-bounded",
    )
    _INCLINED_KEYWORDS: tuple[str, ...] = (
        "倾斜", "inclined", "inclination", "angle", "角度",
    )
    _MOVING_BODY_KEYWORDS: tuple[str, ...] = (
        "振荡", "oscillat", "moving", "运动",
    )
    _THERMAL_KEYWORDS: tuple[str, ...] = (
        "热", "thermal", "heat", "温度", "temperature",
    )
    _BUOYANCY_KEYWORDS: tuple[str, ...] = ("浮力", "buoyan")
    _STRATIFICATION_KEYWORDS: tuple[str, ...] = (
        "密度分层", "density stratification", "stratif", "密度梯度",
    )
    _SPANWISE_PERIODIC_KEYWORDS: tuple[str, ...] = (
        "展向周期", "spanwise periodic", "spanwise-periodic", "周期",
    )

    # -- dimensionless numbers & ratios -------------------------------------
    _RE_RE = re.compile(
        r"\b(?:re|reynolds|雷诺数)\s*[=：:]\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    _FR_RE = re.compile(
        r"\b(?:fr|froude|弗劳德)\s*[=：:]\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    _ANGLE_RE = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:度|°|deg(?:ree)?s?)",
        re.IGNORECASE,
    )
    _ASPECT_RATIO_RE = re.compile(
        r"(?:\baspect\s+ratio\b|长短轴比)(?:\s*[=：:]\s*|\s+)(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    _GAP_RE = re.compile(
        r"(?:\bgap(?:\s+ratio)?\b|间隙)(?:\s*[=：:]\s*|\s+)(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    _EXPANSION_RE = re.compile(
        r"(?:\bexpansion\s+ratio\b|扩张比)(?:\s*[=：:]\s*|\s+)(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    # -- initial / boundary conditions --------------------------------------
    _IC_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
        ("initially_at_rest", ("初始静止", "initially at rest", "at rest")),
        ("fully_developed", ("充分发展", "fully developed")),
        ("linear_stratification", ("线性分层", "linear stratification")),
    ]
    _BC_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
        ("no_slip", ("无滑移", "no-slip", "no slip")),
        ("free_slip", ("自由滑移", "free-slip", "free slip")),
        ("advective", ("对流边界", "advective")),
        ("pressure_outlet", ("压力出口", "pressure outlet")),
        ("periodic", ("周期", "periodic")),
        ("velocity_profile", ("速度剖面", "velocity profile")),
        ("power_law", ("幂律", "power law", "power-law")),
        ("parabolic", ("抛物型", "parabolic")),
    ]

    # -- observables: (keywords, observable_id, display_name, category) -----
    _OBSERVABLE_MAP: list[tuple[tuple[str, ...], str, str, str]] = [
        (("阻力", "drag"), "drag", "Drag", "force"),
        (("升力", "lift"), "lift", "Lift", "force"),
        (("斯特劳哈尔", "strouhal"), "strouhal_number", "Strouhal Number", "spectral"),
        (("频谱", "spectral", "spectrum"), "spectrum", "Spectrum", "spectral"),
        (("压力", "pressure"), "pressure", "Pressure", "pressure"),
        (("热通量", "heat flux"), "heat_flux", "Heat Flux", "heat_flux"),
        (("再附", "reattachment"), "reattachment", "Reattachment", "reattachment"),
        (("回流区", "recirculation"), "recirculation", "Recirculation", "vortex_structure"),
        (("涡", "vortex"), "vortex_structure", "Vortex Structure", "vortex_structure"),
        (("尾迹", "wake"), "wake", "Wake", "wake_deflection"),
        (("混合层", "mixing layer"), "mixing_layer", "Mixing Layer", "mixing"),
        (("内波", "internal wave"), "internal_wave", "Internal Wave", "internal_wave"),
        (
            ("雷诺应力", "reynolds stress"),
            "reynolds_stress",
            "Reynolds Stress",
            "turbulence_statistics",
        ),
    ]

    # -- analysis goals: (keyword, canonical goal description) --------------
    _GOAL_KEYWORDS: list[tuple[str, str]] = [
        ("揭示", "揭示流动机制"),
        ("reveal", "reveal flow mechanism"),
        ("investigate", "investigate flow physics"),
        ("机理", "理解流动机理"),
        ("机制", "理解物理机制"),
        ("mechanism", "understand flow mechanism"),
    ]

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _contains(text_lower: str, keywords: tuple[str, ...]) -> bool:
        """Return ``True`` if any *keyword* appears in *text_lower*."""
        return any(kw in text_lower for kw in keywords)

    @staticmethod
    def _coerce_number(raw: str) -> int | float:
        """Convert a numeric string to ``int`` or ``float`` as appropriate."""
        return float(raw) if "." in raw else int(raw)

    # ------------------------------------------------------------------ extract
    def extract(self, study_text: str) -> PhysicsFrame:
        """Extract physics frame from study description text."""
        text_lower = study_text.lower()

        # dimension
        dimension: str | None = None
        for dim, pattern in self._DIMENSION_PATTERNS:
            if pattern.search(text_lower):
                dimension = dim
                break

        # temporal type (with turbulent -> transient inference)
        flow_regime = self._detect_flow_regime(text_lower)
        is_steady = bool(self._STEADY_RE.search(text_lower))
        is_transient_kw = self._contains(text_lower, self._TRANSIENT_KEYWORDS)
        if is_steady:
            temporal_type: str | None = "steady"
        elif is_transient_kw:
            temporal_type = "transient"
        elif flow_regime == "turbulent":
            # Turbulent flows are inherently unsteady; absent an explicit
            # "steady" statement we default to transient.
            temporal_type = "transient"
        else:
            temporal_type = None

        # boolean flags
        near_wall = self._contains(text_lower, self._NEAR_WALL_KEYWORDS)
        is_inclined = self._contains(text_lower, self._INCLINED_KEYWORDS)
        is_moving_body = self._contains(text_lower, self._MOVING_BODY_KEYWORDS)
        has_thermal = self._contains(text_lower, self._THERMAL_KEYWORDS)
        has_density_stratification = self._contains(
            text_lower, self._STRATIFICATION_KEYWORDS
        )
        has_buoyancy = self._contains(text_lower, self._BUOYANCY_KEYWORDS)
        # Density stratification implies buoyancy effects (Boussinesq etc.).
        if has_density_stratification:
            has_buoyancy = True
        has_spanwise_periodic = self._contains(
            text_lower, self._SPANWISE_PERIODIC_KEYWORDS
        )

        return PhysicsFrame(
            dimension=dimension,
            temporal_type=temporal_type,
            flow_regime=flow_regime,
            is_wall_bounded=near_wall,
            is_inclined=is_inclined,
            is_moving_body=is_moving_body,
            has_thermal=has_thermal,
            has_buoyancy=has_buoyancy,
            has_density_stratification=has_density_stratification,
            has_spanwise_periodic=has_spanwise_periodic,
            geometry_type=self._detect_geometry(text_lower),
            near_wall=near_wall,
        )

    def _detect_flow_regime(self, text_lower: str) -> str | None:
        if self._contains(text_lower, self._TURBULENT_KEYWORDS):
            return "turbulent"
        if self._contains(text_lower, self._TRANSITIONAL_KEYWORDS):
            return "transitional"
        if self._contains(text_lower, self._LAMINAR_KEYWORDS):
            return "laminar"
        return None

    def _detect_geometry(self, text_lower: str) -> str | None:
        for geo_id, keywords in self._GEOMETRY_KEYWORDS:
            if any(kw in text_lower for kw in keywords):
                return geo_id
        return None

    # ------------------------------------------------------ extract_parameters
    def extract_parameters(self, study_text: str) -> list[ExtractedParameter]:
        """Return parameters (Re, Fr, angles, ratios) found in the text.

        Each detected value is returned as a user-provided
        :class:`ExtractedParameter` with ``affects`` listing the downstream
        model fields it influences and a ``confidence`` reflecting how
        directly the value was stated in the source text.
        """
        params: list[ExtractedParameter] = []

        for match in self._RE_RE.finditer(study_text):
            params.append(
                ExtractedParameter(
                    canonical_id="reynolds_number",
                    display_name="Reynolds Number",
                    value=self._coerce_number(match.group(1)),
                    unit=None,
                    dimensionless=True,
                    source_text=match.group(0),
                    source="user_provided",
                    affects=["solver", "turbulence_model", "mesh"],
                    confidence=0.99,
                )
            )

        for match in self._FR_RE.finditer(study_text):
            params.append(
                ExtractedParameter(
                    canonical_id="froude_number",
                    display_name="Froude Number",
                    value=float(match.group(1)),
                    unit=None,
                    dimensionless=True,
                    source_text=match.group(0),
                    source="user_provided",
                    affects=["solver", "physical_model", "boundary_condition"],
                    confidence=0.99,
                )
            )

        for match in self._ANGLE_RE.finditer(study_text):
            params.append(
                ExtractedParameter(
                    canonical_id="inclination_angle",
                    display_name="Inclination Angle",
                    value=float(match.group(1)),
                    unit="deg",
                    dimensionless=False,
                    source_text=match.group(0),
                    source="user_provided",
                    affects=["geometry", "mesh"],
                    confidence=0.9,
                )
            )

        for match in self._ASPECT_RATIO_RE.finditer(study_text):
            params.append(
                ExtractedParameter(
                    canonical_id="aspect_ratio",
                    display_name="Aspect Ratio",
                    value=self._coerce_number(match.group(1)),
                    unit=None,
                    dimensionless=True,
                    source_text=match.group(0),
                    source="user_provided",
                    affects=["geometry", "mesh"],
                    confidence=0.9,
                )
            )

        for match in self._GAP_RE.finditer(study_text):
            params.append(
                ExtractedParameter(
                    canonical_id="gap_ratio",
                    display_name="Gap Ratio",
                    value=self._coerce_number(match.group(1)),
                    unit=None,
                    dimensionless=True,
                    source_text=match.group(0),
                    source="user_provided",
                    affects=["geometry", "mesh"],
                    confidence=0.85,
                )
            )

        for match in self._EXPANSION_RE.finditer(study_text):
            params.append(
                ExtractedParameter(
                    canonical_id="expansion_ratio",
                    display_name="Expansion Ratio",
                    value=self._coerce_number(match.group(1)),
                    unit=None,
                    dimensionless=True,
                    source_text=match.group(0),
                    source="user_provided",
                    affects=["geometry", "mesh"],
                    confidence=0.85,
                )
            )

        return params

    # ------------------------------------------------------ extract_observables
    def extract_observables(self, study_text: str) -> list[ObservableSpec]:
        """Return observables (measurement targets) detected in the text."""
        text_lower = study_text.lower()
        observables: list[ObservableSpec] = []
        seen: set[str] = set()
        for keywords, obs_id, display_name, category in self._OBSERVABLE_MAP:
            if obs_id in seen:
                continue
            if any(kw in text_lower for kw in keywords):
                observables.append(
                    ObservableSpec(
                        observable_id=obs_id,
                        display_name=display_name,
                        category=category,  # type: ignore[arg-type]
                    )
                )
                seen.add(obs_id)
        return observables

    # --------------------------------------------------------- extract_conditions
    def extract_conditions(
        self, study_text: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return ``(initial_conditions, boundary_conditions)`` as dicts.

        Each dict carries a ``type`` label and the matching ``source_text``
        keyword so downstream stages can trace the evidence back to the
        user's wording.
        """
        text_lower = study_text.lower()

        initial_conditions: list[dict[str, Any]] = []
        for cond_type, keywords in self._IC_PATTERNS:
            for kw in keywords:
                if kw in text_lower:
                    initial_conditions.append(
                        {"type": cond_type, "source_text": kw}
                    )
                    break

        boundary_conditions: list[dict[str, Any]] = []
        for cond_type, keywords in self._BC_PATTERNS:
            for kw in keywords:
                if kw in text_lower:
                    boundary_conditions.append(
                        {"type": cond_type, "source_text": kw}
                    )
                    break

        return initial_conditions, boundary_conditions

    # --------------------------------------------------- extract_analysis_goals
    def extract_analysis_goals(self, study_text: str) -> list[str]:
        """Detect mechanism / research goals expressed in the text.

        Looks for verbs and nouns signalling a research intent such as
        "揭示", "reveal", "investigate", "mechanism", "机理" and "机制",
        returning a de-duplicated list of canonical goal descriptions.
        """
        text_lower = study_text.lower()
        goals: list[str] = []
        seen: set[str] = set()
        for keyword, goal in self._GOAL_KEYWORDS:
            if keyword in text_lower and goal not in seen:
                goals.append(goal)
                seen.add(goal)
        return goals


__all__ = ["PhysicsFrameExtractor"]
