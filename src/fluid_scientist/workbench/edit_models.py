"""Pydantic models for the workbench edit system.

Defines the structured EditProposal, SpecEditOperation, ChangeSummary,
and ValidationResult models used by the WorkbenchAgent and SpecEditExecutor.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# --- Edit Intent ---

EditIntent = Literal[
    "add_parameter",
    "update_parameter",
    "remove_parameter",
    "add_metric",
    "remove_metric",
    "change_physics_model",
    "set_boundary_condition",
    "accept_recommendations",
    "validate_spec",
    "prepare_compile",
    "clarification_required",
]

# --- Proposed entities (sections 2.1) ---


class ProposedParameter(BaseModel):
    """A parameter proposed by the agent for addition or modification."""

    parameter_id: str
    display_name: str
    category: str
    unit: str | None = None
    value: float | int | str | bool | None = None
    status: Literal[
        "user_confirmed",
        "derived",
        "system_recommended",
        "unknown_required",
    ] = "system_recommended"
    source: Literal[
        "user",
        "derived",
        "system_recommended",
        "template_default",
        "unknown",
    ] = "system_recommended"
    reason: str = ""
    criticality: Literal["critical", "high", "medium", "low"] = "medium"
    editable: bool = True
    dependencies: list[str] = Field(default_factory=list)
    affects: list[str] = Field(default_factory=list)


class ProposedMetric(BaseModel):
    """A metric proposed by the agent for addition."""

    metric_id: str
    display_name: str
    definition: str = ""
    required_data: list[str] = Field(default_factory=list)
    measurement_requirements: list[str] = Field(default_factory=list)
    analysis_pipeline: list[str] = Field(default_factory=list)
    quality_checks: list[str] = Field(default_factory=list)
    reason: str = ""


# --- Edit Operation (section 2.1) ---

OperationType = Literal[
    "add_parameter",
    "update_parameter",
    "remove_parameter",
    "add_metric",
    "remove_metric",
    "set_physics",
    "set_boundary_condition",
    "accept_recommendation",
]


class SpecEditOperation(BaseModel):
    """A single deterministic operation within an EditProposal."""

    operation: OperationType
    target_id: str | None = None
    parameter: ProposedParameter | None = None
    metric: ProposedMetric | None = None
    value: Any | None = None
    unit: str | None = None
    reason: str = ""


# --- Edit Proposal (section 2.1) ---


class EditProposal(BaseModel):
    """The agent's proposed set of operations for user confirmation.

    The agent NEVER directly modifies the spec.  It produces an
    EditProposal that the user reviews and confirms before
    SpecEditExecutor applies it deterministically.
    """

    proposal_id: str
    experiment_id: str
    experiment_version: int
    edit_intent: EditIntent
    summary: str = ""
    proposed_operations: list[SpecEditOperation] = Field(default_factory=list)
    clarification_question: str | None = None
    requires_confirmation: bool = True
    blocking_issues_preview: list[str] = Field(default_factory=list)
    warnings_preview: list[str] = Field(default_factory=list)
    invalidates: list[str] = Field(default_factory=list)


# --- Change Summary (section 2.2) ---


class ChangeSummary(BaseModel):
    """Summary of changes after applying an EditProposal."""

    direct_updates: list[dict[str, Any]] = Field(default_factory=list)
    derived_updates: list[dict[str, Any]] = Field(default_factory=list)
    added_parameters: list[str] = Field(default_factory=list)
    removed_parameters: list[str] = Field(default_factory=list)
    added_metrics: list[str] = Field(default_factory=list)
    removed_metrics: list[str] = Field(default_factory=list)
    invalidated: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_required_action: str | None = None
    can_confirm: bool = False
    can_compile: bool = False


# --- Validation Result (section 2.2) ---


class ValidationResult(BaseModel):
    """Result of validating an ExperimentSpec for state transitions."""

    is_valid: bool = True
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    can_transition_to_ready: bool = False
    can_confirm: bool = False
    can_compile: bool = False


__all__ = [
    "ChangeSummary",
    "EditIntent",
    "EditProposal",
    "OperationType",
    "ProposedMetric",
    "ProposedParameter",
    "SpecEditOperation",
    "ValidationResult",
]
