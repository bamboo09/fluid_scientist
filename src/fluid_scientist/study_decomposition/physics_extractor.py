"""Physics frame extraction from natural-language CFD study descriptions.

The :class:`PhysicsFrameExtractor` uses lightweight regex and keyword
heuristics to turn a free-form study description (often a single sentence
mixing Chinese and English) into a structured :class:`PhysicsFrame` plus
companion lists of parameters, observables, conditions and analysis goals.

This is a heuristic, first-pass extractor: it deliberately favours recall
over precision and leaves deeper reasoning to downstream LLM-driven stages.

Extraction is delegated to four specialised sub-extractors:

* :class:`StudyTypeClassifier` -- geometry / study-type classification
* :class:`ParameterExtractor` -- dimensionless numbers, angles, ratios
* :class:`ConditionExtractor` -- boundary and initial conditions
* :class:`ObservableExtractor` -- measurement targets / observables
"""

from __future__ import annotations

import re
from typing import Any

from fluid_scientist.study_decomposition.condition_extractor import ConditionExtractor
from fluid_scientist.study_decomposition.models import (
    ExtractedParameter,
    ObservableSpec,
    PhysicsFrame,
)
from fluid_scientist.study_decomposition.observable_extractor import ObservableExtractor
from fluid_scientist.study_decomposition.parameter_extractor import ParameterExtractor
from fluid_scientist.study_decomposition.study_type_classifier import StudyTypeClassifier


class PhysicsFrameExtractor:
    """Extract structured physics information from a study description.

    The extractor is intentionally rule-based so that it runs quickly and
    deterministically during the first decomposition pass.  It recognises
    bilingual (Chinese / English) keywords for dimensions, temporal type,
    flow regime, geometry, wall proximity, inclination, moving bodies,
    thermal / buoyancy / stratification effects and spanwise periodicity.

    All keyword matching is performed against a lower-cased copy of the
    input: Chinese characters are unaffected by ``str.lower()`` while
    English keywords become case-insensitive, so a single substring test
    covers both languages.

    Parameter, condition, observable and study-type extraction are delegated
    to the four specialised sub-extractor classes.
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

    # -- analysis goals: (keyword, canonical goal description) --------------
    _GOAL_KEYWORDS: list[tuple[str, str]] = [
        ("揭示", "揭示流动机制"),
        ("reveal", "reveal flow mechanism"),
        ("investigate", "investigate flow physics"),
        ("机理", "理解流动机理"),
        ("机制", "理解物理机制"),
        ("mechanism", "understand flow mechanism"),
    ]

    def __init__(self) -> None:
        self._type_classifier = StudyTypeClassifier()
        self._param_extractor = ParameterExtractor()
        self._cond_extractor = ConditionExtractor()
        self._obs_extractor = ObservableExtractor()

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _contains(text_lower: str, keywords: tuple[str, ...]) -> bool:
        """Return ``True`` if any *keyword* appears in *text_lower*."""
        return any(kw in text_lower for kw in keywords)

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

        # Delegate geometry/study-type classification to StudyTypeClassifier.
        study_type, _confidence, _evidence = self._type_classifier.classify(study_text)
        geometry_type = study_type if study_type != "unknown" else None

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
            geometry_type=geometry_type,
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

    # ------------------------------------------------------ extract_parameters
    def extract_parameters(self, study_text: str) -> list[ExtractedParameter]:
        """Return parameters (Re, Fr, angles, ratios) found in the text.

        Each detected value is returned as a user-provided
        :class:`ExtractedParameter` with ``affects`` listing the downstream
        model fields it influences and a ``confidence`` reflecting how
        directly the value was stated in the source text.
        """
        study_type, _conf, _ev = self._type_classifier.classify(study_text)
        params, _ambiguities = self._param_extractor.extract(study_text, study_type)
        return params

    # ------------------------------------------------------ extract_observables
    def extract_observables(self, study_text: str) -> list[ObservableSpec]:
        """Return observables (measurement targets) detected in the text."""
        study_type, _conf, _ev = self._type_classifier.classify(study_text)
        observables, _ambiguities = self._obs_extractor.extract(study_text, study_type)
        return observables

    # --------------------------------------------------------- extract_conditions
    def extract_conditions(
        self, study_text: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return ``(initial_conditions, boundary_conditions)`` as lists of dicts.

        Each dict carries a ``type`` label and the matching ``source_text``
        keyword so downstream stages can trace the evidence back to the
        user's wording.
        """
        study_type, _conf, _ev = self._type_classifier.classify(study_text)
        bcs_dict, ics_dict, _amb = self._cond_extractor.extract(study_text, study_type)

        # Convert dicts back to list-of-dicts format for backward compatibility.
        initial_conditions = list(ics_dict.values())
        boundary_conditions = list(bcs_dict.values())

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
