"""Physical parameter extraction from natural-language CFD study descriptions.

The :class:`ParameterExtractor` uses regex patterns to pull out dimensionless
numbers (Re, Fr), angles, and geometric ratios (aspect ratio, gap ratio,
expansion ratio) from free-form study text.  Each detected value is returned
as an :class:`~fluid_scientist.study_decomposition.models.ExtractedParameter`
together with a list of ambiguities for values that are mentioned but not
fully specified.
"""

from __future__ import annotations

import re

from fluid_scientist.study_decomposition.models import ExtractedParameter


class ParameterExtractor:
    """Extract physical parameters from study text.

    All regex patterns are compiled at class level.  Numeric values are
    coerced to ``int`` when no decimal point is present, otherwise ``float``.
    The *study_type* parameter is accepted for future study-type-specific
    parameter rules (e.g. pipe diameter for pipe flows) but the current
    implementation applies the same regex set regardless of study type.
    """

    # -- dimensionless numbers ----------------------------------------------
    _RE_RE = re.compile(
        r"\b(?:re|reynolds|雷诺数)\s*[=：:]\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )
    _FR_RE = re.compile(
        r"\b(?:fr|froude|弗劳德数?)\s*[=：:]\s*(\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )

    # -- angles & geometric ratios ------------------------------------------
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

    @staticmethod
    def _coerce_number(raw: str) -> int | float:
        """Convert a numeric string to ``int`` or ``float`` as appropriate."""
        return float(raw) if "." in raw else int(raw)

    def extract(
        self, text: str, study_type: str
    ) -> tuple[list[ExtractedParameter], list[dict]]:
        """Extract parameters from *text*.

        Returns a tuple ``(extracted_parameters, ambiguities)``.

        * ``extracted_parameters`` is a list of
          :class:`ExtractedParameter` instances for each value found.
        * ``ambiguities`` is a list of dicts describing parameters that are
          implied but not fully specified (currently empty; reserved for
          future enrichment).

        The *study_type* argument is accepted for context-aware extraction
        but does not yet change behaviour.
        """
        params: list[ExtractedParameter] = []
        ambiguities: list[dict] = []

        for match in self._RE_RE.finditer(text):
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

        for match in self._FR_RE.finditer(text):
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

        for match in self._ANGLE_RE.finditer(text):
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

        for match in self._ASPECT_RATIO_RE.finditer(text):
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

        for match in self._GAP_RE.finditer(text):
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

        for match in self._EXPANSION_RE.finditer(text):
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

        return params, ambiguities


__all__ = ["ParameterExtractor"]
