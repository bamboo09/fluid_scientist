"""Draft experiment data models.

This module defines the Pydantic v2 data structures used by the draft
workflow.  An :class:`ExperimentDraft` is a read-mostly, structured snapshot
of a single simulation study that the user can review, refine, confirm and
eventually compile into a runnable experiment specification.

The draft intentionally tracks *where* every parameter value came from
(:class:`ParameterSource`) so that the orchestrator can distinguish
user-supplied facts from derived values, system recommendations, working
assumptions and still-unknown required quantities.

All models use :class:`~pydantic.BaseModel` (rather than a ``StrictModel``)
to stay flexible for schema-driven extensions added by downstream stages.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from fluid_scientist.compat import UTC, StrEnum

# ---------------------------------------------------------------------------
# 1. DraftStatus
# ---------------------------------------------------------------------------


class DraftStatus(StrEnum):
    """Lifecycle status of an :class:`ExperimentDraft`.

    The draft moves roughly::

        draft -> ready -> confirmed -> compiled -> running -> completed
                                                                -> failed
    """

    DRAFT = "draft"
    READY = "ready"
    CONFIRMED = "confirmed"
    COMPILED = "compiled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# 2. ParameterSource
# ---------------------------------------------------------------------------


class ParameterSource(StrEnum):
    """How a :class:`DraftParameter` value was obtained.

    * ``user_provided`` - explicitly stated by the user.
    * ``derived`` - computed from other parameters / physics.
    * ``system_recommended`` - the agent proposed a value awaiting
      confirmation.
    * ``assumption`` - a working assumption was made (must be flagged).
    * ``unknown_required`` - the value is required but currently unknown.
    * ``capability_default`` - filled in from a capability/template default.
    """

    USER_PROVIDED = "user_provided"
    DERIVED = "derived"
    SYSTEM_RECOMMENDED = "system_recommended"
    ASSUMPTION = "assumption"
    UNKNOWN_REQUIRED = "unknown_required"
    CAPABILITY_DEFAULT = "capability_default"


# ---------------------------------------------------------------------------
# 3. DraftParameter
# ---------------------------------------------------------------------------


class DraftParameter(BaseModel):
    """A single parameter carried by an :class:`ExperimentDraft`.

    ``source`` records provenance so the validator and the change-proposal
    workflow can reason about confidence and whether a value still needs
    confirmation.  ``category`` is a coarse grouping (geometry, material,
    boundary_condition, solver, ...) used for display and validation.
    """

    parameter_id: str
    display_name: str
    value: Any | None = None
    unit: str | None = None
    source: ParameterSource
    source_reason: str = ""
    category: str = ""  # geometry, material, boundary_condition, etc.
    editable: bool = True


# ---------------------------------------------------------------------------
# 4. ExperimentDraft
# ---------------------------------------------------------------------------


class ExperimentDraft(BaseModel):
    """A structured, reviewable snapshot of a single simulation study.

    A draft is produced deterministically from a
    :class:`~fluid_scientist.study_decomposition.models.StudyIntent` by the
    :class:`~fluid_scientist.draft.draft_generator.DraftGenerator`.  It is
    the central artefact that the user inspects and refines before a case is
    compiled and run.

    Drafts are *read-mostly*: once confirmed (or any later state) the draft
    is locked and mutations must go through the change-proposal workflow.
    """

    draft_id: str
    session_id: str
    study_id: str | None = None
    version: int = 1
    status: DraftStatus = DraftStatus.DRAFT
    locked: bool = False
    objective: str = ""
    study_type: str = ""
    physical_system: dict = Field(default_factory=dict)
    geometry: dict = Field(default_factory=dict)
    materials: dict = Field(default_factory=dict)
    control_parameters: list[DraftParameter] = Field(default_factory=list)
    physics_models: dict = Field(default_factory=dict)
    initial_conditions: dict = Field(default_factory=dict)
    boundary_conditions: dict = Field(default_factory=dict)
    mesh: dict = Field(default_factory=dict)
    numerics: dict = Field(default_factory=dict)
    solver: dict = Field(default_factory=dict)
    requested_outputs: list[dict] = Field(default_factory=list)
    measurement_plan: dict = Field(default_factory=dict)
    postprocess_plan: dict = Field(default_factory=dict)
    analysis_goals: list[str] = Field(default_factory=list)
    assumptions: list[dict] = Field(default_factory=list)
    risks_and_limits: list[dict] = Field(default_factory=list)
    blocking_issues: list[dict] = Field(default_factory=list)
    capability_preview: dict | None = None
    validation_result: dict | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # -- lifecycle helpers -------------------------------------------------

    def is_read_only(self) -> bool:
        """Return ``True`` when the draft must not be mutated directly.

        A draft is read-only once it has been confirmed or has progressed
        to a later lifecycle state, or when it has been explicitly locked.
        """
        return self.locked or self.status in (
            DraftStatus.CONFIRMED,
            DraftStatus.COMPILED,
            DraftStatus.RUNNING,
            DraftStatus.COMPLETED,
        )

    def clone(self, new_draft_id: str) -> ExperimentDraft:
        """Create a new editable version derived from this draft.

        The clone keeps the same content but gets a fresh ``draft_id``, an
        incremented ``version``, and is reset to the editable ``draft``
        state (``status=draft``, ``locked=False``).

        Uses deep copy so mutable fields (lists, dicts) are not shared
        between the original and the clone.
        """
        now = datetime.now(UTC)
        return self.model_copy(
            deep=True,
            update={
                "draft_id": new_draft_id,
                "version": self.version + 1,
                "status": DraftStatus.DRAFT,
                "locked": False,
                "created_at": now,
                "updated_at": now,
            },
        )

    def confirm(self) -> ExperimentDraft:
        """Return a confirmed, locked copy of this draft."""
        return self.model_copy(
            update={
                "status": DraftStatus.CONFIRMED,
                "locked": True,
                "updated_at": datetime.now(UTC),
            }
        )


# ---------------------------------------------------------------------------
# 5. DraftChange
# ---------------------------------------------------------------------------


class DraftChange(BaseModel):
    """A single atomic change to a draft.

    Each change describes one modification with its ``change_type``,
    ``target_path`` (a dot-separated path into the draft structure), the
    ``old_value`` / ``new_value``, an optional ``unit``, the ``reason`` for
    the change, and a ``confidence`` score (0.0–1.0).
    """

    change_type: Literal[
        "set_parameter",
        "add_parameter",
        "remove_parameter",
        "add_output",
        "remove_output",
        "change_boundary_condition",
        "change_initial_condition",
        "change_physics_model",
        "change_geometry",
        "change_mesh",
        "change_numerics",
        "change_solver",
        "add_assumption",
        "remove_assumption",
        "question",
        "clarification_required",
        "missing_capability",
    ]
    target_path: str
    old_value: Any | None = None
    new_value: Any | None = None
    unit: str | None = None
    reason: str = ""
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# 6. ChangeProposal
# ---------------------------------------------------------------------------


class ChangeProposal(BaseModel):
    """A proposed set of edits to a confirmed/locked :class:`ExperimentDraft`.

    Because confirmed drafts are read-only, any refinement must be expressed
    as a :class:`ChangeProposal` that the user reviews before it is applied
    deterministically.  Each entry in ``changes`` is a :class:`DraftChange`
    describing a single modification.
    """

    proposal_id: str
    session_id: str
    draft_id: str
    base_draft_version: int
    status: Literal["pending", "applied", "cancelled", "expired"] = "pending"
    summary: str = ""
    user_message: str = ""
    changes: list[DraftChange] = Field(default_factory=list)
    impact_summary: list[str] = Field(default_factory=list)
    invalidates: list[str] = Field(default_factory=list)
    requires_confirmation: bool = True
    missing_capabilities: list[dict] = Field(default_factory=list)
    clarification_required: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# 7. ValidationResult
# ---------------------------------------------------------------------------


class ValidationResult(BaseModel):
    """Result of validating an :class:`ExperimentDraft`.

    ``blocking_issues`` are structured dicts (``{"check", "message"}``) that
    prevent the draft from advancing; ``warnings`` and ``errors`` are plain
    strings.  ``valid`` is ``True`` only when there are no blocking issues
    and no errors.
    """

    valid: bool = True
    blocking_issues: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


__all__ = [
    "ChangeProposal",
    "DraftChange",
    "DraftParameter",
    "DraftStatus",
    "ExperimentDraft",
    "ParameterSource",
    "ValidationResult",
]
