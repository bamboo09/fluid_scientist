"""Study decomposition data models.

This module defines the Pydantic v2 data structures used by the study
decomposition workflow.  The workflow takes a user's natural-language
description of one or more simulation studies and decomposes it into a
structured :class:`StudyIntent` (or a :class:`BatchStudyPlan` of several
intents) that downstream stages can consume.

All models intentionally use :class:`~pydantic.BaseModel` (rather than a
``StrictModel``) to keep them flexible: the decomposition stage must tolerate
extra, schema-driven fields supplied by the dynamic schema engine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from fluid_scientist.compat import UTC

# ---------------------------------------------------------------------------
# 1. ExtractedParameter
# ---------------------------------------------------------------------------


class ExtractedParameter(BaseModel):
    """A single parameter extracted from the user's natural-language input.

    Parameters are classified by *source* which records how the value was
    obtained.  ``affects`` lists the downstream model fields (geometry,
    boundary condition, solver, etc.) that this parameter influences, so the
    capability resolver can decide whether a missing capability is blocking.
    """

    canonical_id: str
    display_name: str
    value: Any | None = None
    unit: str | None = None
    dimensionless: bool = False
    source_text: str
    source: Literal["user_provided", "derived", "assumed", "unknown_required"]
    affects: list[str] = Field(default_factory=list)
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# 2. ObservableSpec
# ---------------------------------------------------------------------------


class ObservableSpec(BaseModel):
    """An observable / metric that the user wants to measure.

    ``category`` drives the selection of measurement planners and capability
    checks.  ``required_fields`` and ``required_sampling`` describe the data
    that must be produced by the simulation before the post-processing method
    can be applied.
    """

    observable_id: str
    display_name: str
    category: Literal[
        "force",
        "pressure",
        "heat_flux",
        "vortex_structure",
        "wake_deflection",
        "reattachment",
        "spectral",
        "turbulence_statistics",
        "internal_wave",
        "mixing",
        "custom",
    ]
    required_fields: list[str] = Field(default_factory=list)
    required_sampling: list[str] = Field(default_factory=list)
    postprocess_method: str | None = None
    capability_check_required: bool = True


# ---------------------------------------------------------------------------
# 3. AmbiguityItem
# ---------------------------------------------------------------------------


class AmbiguityItem(BaseModel):
    """An ambiguity or missing piece of information detected during parsing.

    ``severity`` controls how the orchestrator reacts:

    * ``non_blocking_assumption`` - a safe default was assumed and the study
      can still be drafted; the user is informed but not blocked.
    * ``needs_confirmation`` - a default was assumed but should be confirmed
      by the user before committing to a case.
    * ``blocking_for_case_generation`` - case generation cannot proceed until
      the user supplies the information.
    """

    field: str
    issue: str
    severity: Literal[
        "non_blocking_assumption",
        "needs_confirmation",
        "blocking_for_case_generation",
    ]
    reason: str
    suggested_question: str | None = None
    recommended_default: Any | None = None


# ---------------------------------------------------------------------------
# 4. StudyIntent
# ---------------------------------------------------------------------------


class StudyIntent(BaseModel):
    """A structured research study extracted from natural language.

    A :class:`StudyIntent` is the central artefact of the decomposition
    stage.  It captures the full picture of a single study: its objective,
    geometry, physical models, boundary/initial conditions, the parameters
    that were supplied / derived / assumed / still unknown, the observables
    the user cares about, analysis goals, and a readiness assessment.

    ``readiness_level`` summarises whether the intent is rich enough to move
    forward:

    * ``draftable`` - enough information to generate a draft experiment.
    * ``needs_clarification`` - some ambiguities should be resolved first.
    * ``not_compilable_yet`` - critical information is missing and the
      intent cannot be compiled into an experiment specification.
    """

    study_id: str
    batch_id: str | None = None
    title: str
    raw_text: str
    study_type: str
    research_objective: str
    geometry: dict = Field(default_factory=dict)
    physical_models: dict = Field(default_factory=dict)
    initial_conditions: list[dict] = Field(default_factory=list)
    boundary_conditions: list[dict] = Field(default_factory=list)
    known_parameters: list[ExtractedParameter] = Field(default_factory=list)
    derived_parameters: list[ExtractedParameter] = Field(default_factory=list)
    unknown_required_parameters: list[ExtractedParameter] = Field(
        default_factory=list
    )
    assumed_parameters: list[ExtractedParameter] = Field(default_factory=list)
    observables: list[ObservableSpec] = Field(default_factory=list)
    analysis_goals: list[str] = Field(default_factory=list)
    ambiguity_report: list[AmbiguityItem] = Field(default_factory=list)
    capability_requirements: list[str] = Field(default_factory=list)
    likely_missing_capabilities: list[dict] = Field(default_factory=list)
    readiness_level: Literal[
        "draftable", "needs_clarification", "not_compilable_yet"
    ] = "needs_clarification"
    recommended_priority: int = 0
    priority_reason: str = ""


# ---------------------------------------------------------------------------
# 5. BatchStudyPlan
# ---------------------------------------------------------------------------


class BatchStudyPlan(BaseModel):
    """A batch of one or more :class:`StudyIntent` objects.

    When a user submits several studies at once the splitter divides the
    message into individual blocks and each block is decomposed into a
    :class:`StudyIntent`.  All intents are grouped into a
    :class:`BatchStudyPlan` so the orchestrator can decide how to proceed
    (select one, generate all drafts, or ask a batch-level clarification).
    """

    batch_id: str
    input_type: Literal["single_study", "batch_study"] = "single_study"
    studies: list[StudyIntent] = Field(default_factory=list)
    batch_summary: str = ""
    suggested_next_action: Literal[
        "select_one_to_continue",
        "generate_all_draft_summaries",
        "ask_batch_level_clarification",
    ] = "select_one_to_continue"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# 6. PhysicsFrame
# ---------------------------------------------------------------------------


class PhysicsFrame(BaseModel):
    """Extracted physics framework from a study description.

    A lightweight, heuristic-friendly view of the physics that a study
    implies.  It is produced early in decomposition (often by the physics
    spec builder) and feeds into the dynamic-schema engine and capability
    resolver.  All fields are optional because a partial description is
    common during the first pass of parsing.
    """

    dimension: str | None = None  # e.g. "2D", "3D"
    temporal_type: str | None = None  # e.g. "steady", "transient"
    flow_regime: str | None = None  # e.g. "laminar", "turbulent", "transitional"
    is_wall_bounded: bool = False
    is_inclined: bool = False
    is_moving_body: bool = False
    has_thermal: bool = False
    has_buoyancy: bool = False
    has_density_stratification: bool = False
    has_spanwise_periodic: bool = False
    turbulence_model_candidate: str | None = None
    solver_family_candidate: str | None = None
    geometry_type: str | None = None
    near_wall: bool = False


__all__ = [
    "AmbiguityItem",
    "BatchStudyPlan",
    "ExtractedParameter",
    "ObservableSpec",
    "PhysicsFrame",
    "StudyIntent",
]
