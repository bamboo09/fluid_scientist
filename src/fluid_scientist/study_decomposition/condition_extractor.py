"""Boundary and initial condition extraction from CFD study descriptions.

The :class:`ConditionExtractor` identifies boundary conditions (no-slip walls,
pressure outlets, periodic boundaries, velocity inlet profiles, etc.) and
initial conditions (initially at rest, fully developed, linear stratification)
from bilingual free-text using keyword matching.
"""

from __future__ import annotations

from typing import Any


class ConditionExtractor:
    """Extract boundary and initial conditions from study text.

    Keyword matching is performed against a lower-cased copy of the input
    so that English keywords are case-insensitive while Chinese characters
    are unaffected.
    """

    # -- initial condition keywords -----------------------------------------
    _IC_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
        ("initially_at_rest", ("初始静止", "initially at rest", "at rest")),
        ("fully_developed", ("充分发展", "fully developed")),
        ("linear_stratification", ("线性分层", "linear stratification")),
    ]

    # -- boundary condition keywords ----------------------------------------
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

    def extract(
        self, text: str, study_type: str
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict]]:
        """Extract boundary and initial conditions from *text*.

        Returns a tuple ``(boundary_conditions, initial_conditions, ambiguities)``.

        * ``boundary_conditions`` is a dict keyed by condition type (e.g.
          ``"no_slip"``, ``"pressure_outlet"``) with each value containing
          the ``source_text`` keyword that triggered the match.
        * ``initial_conditions`` follows the same structure for ICs.
        * ``ambiguities`` is a list of dicts describing missing or unclear
          conditions (currently empty; reserved for future enrichment).

        The *study_type* argument is accepted for context-aware extraction
        (e.g. expecting an inlet BC for pipe flows) but does not yet change
        behaviour.
        """
        text_lower = text.lower()

        initial_conditions: dict[str, Any] = {}
        for cond_type, keywords in self._IC_PATTERNS:
            for kw in keywords:
                if kw in text_lower:
                    initial_conditions[cond_type] = {
                        "type": cond_type,
                        "source_text": kw,
                    }
                    break

        boundary_conditions: dict[str, Any] = {}
        for cond_type, keywords in self._BC_PATTERNS:
            for kw in keywords:
                if kw in text_lower:
                    boundary_conditions[cond_type] = {
                        "type": cond_type,
                        "source_text": kw,
                    }
                    break

        ambiguities: list[dict] = []

        return boundary_conditions, initial_conditions, ambiguities


__all__ = ["ConditionExtractor"]
