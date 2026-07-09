"""Study type classification from natural-language CFD study descriptions.

The :class:`StudyTypeClassifier` uses bilingual keyword heuristics to
determine the canonical study type (geometry class) from a free-form
description.  It returns a confidence score and supporting evidence so
that downstream stages can decide whether to ask for clarification.
"""

from __future__ import annotations

# Canonical study type identifiers (aligned with geometry keywords used
# throughout the decomposition pipeline).
STUDY_TYPE_CYLINDER = "cylinder"
STUDY_TYPE_ELLIPTIC_CYLINDER = "elliptic"
STUDY_TYPE_JET = "jet"
STUDY_TYPE_BACKWARD_FACING_STEP = "step"
STUDY_TYPE_PIPE_FLOW = "pipe"
STUDY_TYPE_CAVITY = "cavity"


class StudyTypeClassifier:
    """Classify the study type from free-text input.

    Matching is performed against a lower-cased copy of the input so that
    English keywords are case-insensitive while Chinese characters are
    unaffected.  Geometry keywords are ordered from most specific to most
    generic (e.g. ``elliptic`` precedes ``cylinder`` so that "椭圆柱" is
    not mis-classified as a plain cylinder).
    """

    # -- geometry keywords (order matters: specific before generic) ----------
    _GEOMETRY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
        (STUDY_TYPE_ELLIPTIC_CYLINDER, ("椭圆", "elliptic", "ellipse")),
        (STUDY_TYPE_CYLINDER, ("圆柱", "cylinder")),
        (STUDY_TYPE_JET, ("射流", "jet")),
        (STUDY_TYPE_BACKWARD_FACING_STEP, ("后台阶", "台阶", "backward-facing", "step")),
        (STUDY_TYPE_PIPE_FLOW, ("管道", "圆管", "pipe", "tube", "duct")),
        (STUDY_TYPE_CAVITY, ("方腔", "空腔", "cavity", "lid-driven")),
    ]

    def classify(self, text: str) -> tuple[str, float, dict]:
        """Classify the study type from *text*.

        Returns a tuple ``(study_type, confidence, evidence)`` where:

        * ``study_type`` is a canonical string identifier (e.g. ``"cylinder"``,
          ``"pipe"``, ``"step"``).  Returns ``"unknown"`` when no geometry
          keyword is matched.
        * ``confidence`` is a heuristic score in ``[0, 1]``.  Explicit keyword
          matches score ``0.9``; an unmatched text scores ``0.0``.
        * ``evidence`` is a dictionary containing the matched keyword(s) and
          their locations, useful for traceability and LLM-based verification.
        """
        text_lower = text.lower()

        for study_type, keywords in self._GEOMETRY_KEYWORDS:
            for kw in keywords:
                if kw in text_lower:
                    return (
                        study_type,
                        0.9,
                        {
                            "matched_keyword": kw,
                            "source": "keyword_match",
                            "study_type": study_type,
                        },
                    )

        return (
            "unknown",
            0.0,
            {
                "matched_keyword": None,
                "source": "no_match",
                "study_type": "unknown",
            },
        )


__all__ = ["StudyTypeClassifier"]
