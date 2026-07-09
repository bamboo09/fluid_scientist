"""Deterministic draft generator.

The :class:`DraftGenerator` converts a
:class:`~fluid_scientist.study_decomposition.models.StudyIntent` into a
read-mostly :class:`~fluid_scientist.draft.models.ExperimentDraft` *without*
calling any LLM.  It is a pure, deterministic mapping so that the same
intent always yields the same draft shape (only the generated ``draft_id``
is non-deterministic).

The generator preserves provenance: every parameter carried over from the
intent keeps its source (user-supplied, derived, assumed or unknown) so the
validator and the change-proposal workflow can reason about it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping

from fluid_scientist.draft.models import (
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
)
from fluid_scientist.study_decomposition.models import (
    ExtractedParameter,
    StudyIntent,
)

# Maps an :class:`ExtractedParameter` source literal to the corresponding
# :class:`ParameterSource`.  Note that the intent uses ``"assumed"`` while
# the draft uses ``"assumption"``.
_SOURCE_MAP: dict[str, ParameterSource] = {
    "user_provided": ParameterSource.USER_PROVIDED,
    "derived": ParameterSource.DERIVED,
    "assumed": ParameterSource.ASSUMPTION,
    "unknown_required": ParameterSource.UNKNOWN_REQUIRED,
}

# Severity used by :class:`AmbiguityItem` for blocking ambiguities.
_BLOCKING_SEVERITY = "blocking_for_case_generation"


class DraftGenerator:
    """Generate a read-only :class:`ExperimentDraft` from a ``StudyIntent``."""

    def generate(
        self, study: StudyIntent, research_state: dict | None = None
    ) -> ExperimentDraft:
        """Generate a draft from a study intent.

        Args:
            study: The structured :class:`StudyIntent` to materialise.
            research_state: Optional session-level state.  When present,
                ``research_state["session_id"]`` is used as the draft's
                ``session_id``; otherwise the session id is left blank.

        Returns:
            A fresh :class:`ExperimentDraft` in the ``draft`` state with
            ``version=1``.
        """
        session_id = ""
        if research_state:
            session_id = str(research_state.get("session_id", ""))

        # 4-7. Materialise every parameter list, preserving provenance.
        control_parameters: list[DraftParameter] = []
        control_parameters.extend(self._convert_parameters(study.known_parameters))
        control_parameters.extend(self._convert_parameters(study.derived_parameters))
        control_parameters.extend(self._convert_parameters(study.assumed_parameters))
        control_parameters.extend(
            self._convert_parameters(study.unknown_required_parameters)
        )

        return ExperimentDraft(
            # 15-16. Identity & lifecycle.
            draft_id=str(uuid.uuid4()),
            session_id=session_id,
            study_id=study.study_id,
            version=1,
            status=DraftStatus.DRAFT,
            # 1-2. Objective & study type.
            objective=study.research_objective,
            study_type=study.study_type,
            # 3. Geometry.
            geometry=dict(study.geometry),
            # 8. Physics models.
            physics_models=dict(study.physical_models),
            # 9. Initial conditions (list[dict] -> dict keyed by field).
            initial_conditions=_list_to_dict(study.initial_conditions, "field"),
            # 10. Boundary conditions (list[dict] -> dict keyed by type).
            boundary_conditions=_list_to_dict(study.boundary_conditions, "type"),
            # 4-7. Parameters.
            control_parameters=control_parameters,
            # 11. Observables -> requested outputs.
            requested_outputs=[obs.model_dump() for obs in study.observables],
            # 12. Analysis goals.
            analysis_goals=list(study.analysis_goals),
            # 13. Assumptions from assumed parameters.
            assumptions=[p.model_dump() for p in study.assumed_parameters],
            # 14. Blocking issues from the ambiguity report.
            blocking_issues=[
                item.model_dump()
                for item in study.ambiguity_report
                if item.severity == _BLOCKING_SEVERITY
            ],
        )

    def _convert_parameters(
        self, parameters: Iterable[ExtractedParameter]
    ) -> list[DraftParameter]:
        """Convert :class:`ExtractedParameter` objects into draft parameters."""
        converted: list[DraftParameter] = []
        for param in parameters:
            source = _SOURCE_MAP.get(param.source, ParameterSource.UNKNOWN_REQUIRED)
            category = param.affects[0] if param.affects else ""
            converted.append(
                DraftParameter(
                    parameter_id=param.canonical_id,
                    display_name=param.display_name,
                    value=param.value,
                    unit=param.unit,
                    source=source,
                    source_reason=param.source_text,
                    category=category,
                )
            )
        return converted


def _list_to_dict(items: Iterable[Mapping], key_field: str) -> dict:
    """Convert a list of mappings into a dict keyed by ``key_field``.

    Boundary / initial conditions arrive from the intent as ``list[dict]``
    while the draft stores them as ``dict``.  Each entry is keyed by its
    ``key_field`` value (e.g. ``"type"`` for boundaries, ``"field"`` for
    initial conditions); entries lacking the field fall back to a positional
    key.  Duplicate keys are disambiguated by appending an index so no data
    is silently lost.
    """
    result: dict = {}
    used: set[str] = set()
    for index, item in enumerate(items):
        base_key = item.get(key_field) or item.get("name")
        if not base_key:
            base_key = f"{key_field}_{index}"
        key = str(base_key)
        if key in used:
            key = f"{base_key}_{index}"
        used.add(key)
        result[key] = dict(item)
    return result


__all__ = ["DraftGenerator"]
