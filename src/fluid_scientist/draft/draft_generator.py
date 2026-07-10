"""Deterministic draft generator with optional LLM-based semantic enhancement.

The :class:`DraftGenerator` converts a
:class:`~fluid_scientist.study_decomposition.models.StudyIntent` into a
read-mostly :class:`~fluid_scientist.draft.models.ExperimentDraft`.

The *core* mapping is a pure, deterministic function so that the same
intent always yields the same draft shape (only the generated ``draft_id``
is non-deterministic).  This deterministic core preserves provenance:
every parameter carried over from the intent keeps its source
(user-supplied, derived, assumed or unknown) so the validator and the
change-proposal workflow can reason about it.

When an :class:`~fluid_scientist.llm.LLMClient` is provided, the
generator performs a *best-effort* semantic enhancement step after the
deterministic draft is built.  The LLM may enrich the draft's title or
sections; if the call fails or returns no usable content, the
deterministic draft is returned unchanged.  In other words, the LLM is
strictly additive and never required.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterable, Mapping

from fluid_scientist.draft.models import (
    DraftParameter,
    DraftStatus,
    ExperimentDraft,
    ParameterSource,
)
from fluid_scientist.llm import LLMClient
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

# Prefix produced by the default mock LLM response for ``draft_generation``
# - such titles are not informative and should be ignored.
_MOCK_TITLE_PREFIX = "Draft for:"


class DraftGenerator:
    """Generate a read-only :class:`ExperimentDraft` from a ``StudyIntent``.

    Args:
        llm_client: Optional :class:`~fluid_scientist.llm.LLMClient` used
            to perform a best-effort semantic enhancement of the draft
            (e.g. producing a richer title or summary).  When ``None``
            the generator behaves as a pure deterministic function.
            Failures from the LLM are swallowed; the deterministic
            draft is always returned.
    """

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm_client = llm_client

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
            ``version=1``.  If an LLM client was provided and the LLM
            produces usable semantic enrichment, the returned draft may
            carry a richer title; otherwise it is identical to the
            deterministic baseline.
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

        draft = ExperimentDraft(
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

        # Optional LLM enhancement: produce a richer title if the LLM
        # returns one.  The whole step is best-effort: any failure is
        # suppressed so deterministic generation is never broken.
        if self._llm_client is not None:
            self._apply_llm_enhancement(draft, study, session_id)

        return draft

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_llm_enhancement(
        self,
        draft: ExperimentDraft,
        study: StudyIntent,
        session_id: str,
    ) -> None:
        """Best-effort semantic enhancement of *draft* using the LLM.

        Only side-effects allowed on *draft* are non-structural: setting
        a ``title`` attribute (when the model permits) so that downstream
        consumers can see the LLM's preferred wording.  All exceptions
        are caught - the deterministic draft must always be returned.
        """
        try:
            with contextlib.suppress(Exception):
                output, _record = self._llm_client.call(
                    purpose="draft_generation",
                    prompt_name="draft_generation",
                    system_prompt="",
                    user_message=(
                        f"Study: {study.title}\n"
                        f"Objective: {study.research_objective}"
                    ),
                    session_id=session_id,
                )
            # If the LLM produced a draft with a non-empty title, prefer it.
            if isinstance(output, dict):
                draft_section = output.get("draft")
                if isinstance(draft_section, dict):
                    llm_title = draft_section.get("title", "")
                    if (
                        isinstance(llm_title, str)
                        and llm_title
                        and not llm_title.startswith(_MOCK_TITLE_PREFIX)
                    ):
                        with contextlib.suppress(Exception):
                            draft.title = llm_title
        except Exception:
            # LLM is best-effort; never break generation if LLM fails.
            pass

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
