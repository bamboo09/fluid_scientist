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
    "USER_SPECIFIED": ParameterSource.USER_PROVIDED,
    "SYSTEM_DERIVED": ParameterSource.DERIVED,
    "SYSTEM_SELECTED": ParameterSource.SYSTEM_RECOMMENDED,
    "TEMPLATE_DEFAULT": ParameterSource.CAPABILITY_DEFAULT,
    "ASSUMED_BASELINE": ParameterSource.ASSUMPTION,
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
        design = study.experiment_design or {}
        if design:
            control_parameters.extend(_parameters_from_design(design))

        draft = ExperimentDraft(
            # 15-16. Identity & lifecycle.
            draft_id=str(uuid.uuid4()),
            session_id=session_id,
            study_id=study.study_id,
            version=1,
            status=DraftStatus.DRAFT,
            # 1-2. Objective & study type.
            objective=design.get("research_objective", study.research_objective),
            study_type=study.study_type,
            # 3. Geometry.
            geometry=_design_or_study_dict(design, "geometry", study.geometry),
            physical_system={
                "research_hypotheses": design.get("research_hypotheses", []),
                "target_phenomena": design.get("target_phenomena", []),
                "boundary_facts": design.get("boundary_facts", {}),
                "parameterization_strategy": design.get("parameterization_strategy", {}),
                "computational_domain": design.get("computational_domain", {}),
                "dimensionless_parameters": design.get("dimensionless_parameters", {}),
            } if design else {},
            materials=dict(design.get("material_properties", {})) if design else {},
            # 8. Physics models.
            physics_models=(
                {
                    **dict(design.get("physical_models", {})),
                    "turbulence_model": design.get("turbulence_model", {}),
                }
                if design
                else dict(study.physical_models)
            ),
            # 9. Initial conditions (list[dict] -> dict keyed by field).
            initial_conditions=(
                dict(design.get("initial_conditions", {}))
                if design
                else _list_to_dict(study.initial_conditions, "field")
            ),
            # 10. Boundary conditions (list[dict] -> dict keyed by type).
            boundary_conditions=(
                dict(design.get("boundary_conditions", {}))
                if design
                else _list_to_dict(study.boundary_conditions, "type")
            ),
            # 4-7. Parameters.
            control_parameters=control_parameters,
            solver=dict(design.get("solver", {})) if design else {},
            numerics={
                "schemes": design.get("numerical_schemes", {}),
                "pressure_velocity_coupling": design.get("pressure_velocity_coupling", {}),
                "time_control": design.get("time_control", {}),
            } if design else {},
            mesh={
                "strategy": design.get("mesh_strategy", {}),
                "near_wall_strategy": design.get("near_wall_strategy", {}),
            } if design else {},
            # 11. Observables -> requested outputs.
            requested_outputs=(
                _requested_outputs_from_design(study)
                if design
                else [obs.model_dump() for obs in study.observables]
            ),
            measurement_plan=_measurement_plan_from_design(study),
            postprocess_plan=dict(design.get("post_processing", {})) if design else {},
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
        draft.capability_preview = _field_capability_preview(draft)

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


def _design_or_study_dict(design: dict, key: str, fallback: Mapping) -> dict:
    value = design.get(key)
    if isinstance(value, Mapping):
        return dict(value)
    return dict(fallback)


def _parameters_from_design(design: dict) -> list[DraftParameter]:
    params: list[DraftParameter] = []
    buckets = {
        "material_properties": "material",
        "dimensionless_parameters": "dimensionless",
        "parameterization_strategy": "parameterization",
    }
    for bucket, category in buckets.items():
        values = design.get(bucket, {})
        if not isinstance(values, Mapping):
            continue
        for name, spec in values.items():
            if not isinstance(spec, Mapping):
                continue
            source = _SOURCE_MAP.get(
                str(spec.get("source", "")),
                ParameterSource.SYSTEM_RECOMMENDED,
            )
            params.append(
                DraftParameter(
                    parameter_id=str(name),
                    display_name=str(name),
                    value=spec.get("value"),
                    unit=spec.get("unit"),
                    source=source,
                    source_reason=str(spec.get("reason", "")),
                    category=category,
                    editable=bool(spec.get("modifiable", True)),
                )
            )
    return params


def _requested_outputs_from_design(study: StudyIntent) -> list[dict]:
    outputs: list[dict] = []
    for layer, metrics in (
        ("scientific", study.scientific_metrics),
        ("boundary_verification", study.boundary_verification_metrics),
        ("numerical_credibility", study.credibility_metrics),
        ("comparison", study.comparison_metrics),
        ("optional_diagnostics", study.optional_diagnostics),
    ):
        for metric in metrics:
            outputs.append({**metric, "category": layer})
    return outputs


def _measurement_plan_from_design(study: StudyIntent) -> dict:
    if not study.experiment_design:
        return {}
    return {
        "sampling_strategy": study.experiment_design.get("sampling_strategy", {}),
        "output_control": study.experiment_design.get("output_control", {}),
        "scientific_metrics": study.scientific_metrics,
        "boundary_verification_metrics": study.boundary_verification_metrics,
        "credibility_metrics": study.credibility_metrics,
        "comparison_metrics": study.comparison_metrics,
        "optional_diagnostics": study.optional_diagnostics,
    }


def _field_capability_preview(draft: ExperimentDraft) -> dict:
    native_bcs = {
        "no_slip",
        "free_slip",
        "inlet_velocity",
        "outlet_pressure",
        "outlet_advective",
        "periodic",
    }

    def value_status(value: object, missing_label: str = "MISSING_REQUIRED") -> str:
        if value in ({}, [], None, ""):
            return missing_label
        return "USER_EXTRACTED"

    fields: dict[str, dict[str, str]] = {
        "solver": {
            "value_status": value_status(draft.solver),
            "capability_status": "SUPPORTED_NATIVE",
            "display_value": "待选择" if not draft.solver else str(draft.solver),
        },
        "mesh": {
            "value_status": value_status(draft.mesh),
            "capability_status": "SUPPORTED_NATIVE",
            "display_value": "待设计" if not draft.mesh else str(draft.mesh),
        },
        "requested_outputs": {
            "value_status": value_status(draft.requested_outputs),
            "capability_status": "SUPPORTED_NATIVE",
            "display_value": "待补充" if not draft.requested_outputs else str(draft.requested_outputs),
        },
    }
    fields["solver"] = {
        "value_status": "SYSTEM_DERIVED" if draft.solver else "MISSING_REQUIRED",
        "capability_status": "SUPPORTED_NATIVE",
        "display_value": "待选择" if not draft.solver else str(draft.solver),
    }
    fields["mesh"] = {
        "value_status": "SYSTEM_DERIVED" if draft.mesh else "MISSING_REQUIRED",
        "capability_status": "SUPPORTED_NATIVE",
        "display_value": "待设计" if not draft.mesh else str(draft.mesh),
    }
    fields["requested_outputs"] = {
        "value_status": "SYSTEM_DERIVED" if draft.requested_outputs else "MISSING_REQUIRED",
        "capability_status": "SUPPORTED_NATIVE",
        "display_value": "待补充" if not draft.requested_outputs else str(draft.requested_outputs),
    }
    for boundary, spec in draft.boundary_conditions.items():
        bc_type = spec.get("type") if isinstance(spec, dict) else None
        fields[f"boundary_conditions.{boundary}"] = {
            "value_status": "USER_EXTRACTED" if bc_type else "MISSING_REQUIRED",
            "capability_status": (
                "SUPPORTED_NATIVE"
                if bc_type in native_bcs
                else "UNKNOWN" if bc_type else "NOT_CHECKED"
            ),
            "display_value": str(bc_type or "待补充"),
        }
    return {"fields": fields}


__all__ = ["DraftGenerator"]
