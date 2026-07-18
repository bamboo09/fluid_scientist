"""Session state data structures for multi-turn spec editing.

This module defines the core data models that track the state of a
research session across multiple conversation turns: the conversation
history, confirmed facts, unresolved conflicts, the current workflow
phase, and the pending patch awaiting user confirmation.

The :class:`ResearchSessionState` is the single source of truth that
the :class:`~fluid_scientist.session_state.session_manager.SessionManager`
persists in memory across method calls.  The
:class:`~fluid_scientist.session_state.context_builder.ContextBuilder`
reads it to assemble the model context on every turn.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.compat import StrEnum
from fluid_scientist.spec_editing.models import SimulationSpecPatch

__all__ = [
    "ConversationTurn",
    "FactRecord",
    "ConflictRecord",
    "SessionPhase",
    "ResearchSessionState",
]


class SessionPhase(StrEnum):
    """Workflow phases of a research session.

    The phases follow the model-driven spec editing workflow, from
    initial understanding through clarification, drafting, plan
    confirmation, compilation, execution, and review.

    Members
    -------
    UNDERSTANDING:
        The model is gathering information and understanding the user's
        research objectives.
    CLARIFYING:
        The model is asking clarifying questions to resolve ambiguities.
    DRAFT_READY:
        A draft spec has been produced and is ready for user review.
    PLAN_CONFIRMED:
        The user has confirmed the draft spec.
    COMPILED:
        The spec has been compiled into OpenFOAM case files.
    RUN_CONFIRMED:
        The user has confirmed that the simulation should run.
    RUNNING:
        The simulation is currently running.
    RESULTS_READY:
        Simulation results are available for analysis.
    REVIEWED:
        The results have been reviewed by the user.
    """

    UNDERSTANDING = "understanding"
    CLARIFYING = "clarifying"
    DRAFT_READY = "draft_ready"
    PLAN_CONFIRMED = "plan_confirmed"
    COMPILED = "compiled"
    RUN_CONFIRMED = "run_confirmed"
    RUNNING = "running"
    RESULTS_READY = "results_ready"
    REVIEWED = "reviewed"


class ConversationTurn(BaseModel):
    """A single turn in the research conversation.

    A turn records the user's message, the assistant's response, and
    any patch or model traces produced during the turn.

    Parameters
    ----------
    turn_id:
        Unique identifier for this turn.
    timestamp:
        ISO-8601 timestamp of the turn.
    user_message:
        The user's message in this turn.
    assistant_message:
        The assistant's response, or ``None`` if not yet generated.
    patch_id:
        Identifier of the patch produced in this turn, if any.
    model_trace_ids:
        List of model trace identifiers produced in this turn.
    intent:
        The detected high-level intent for this turn (stored as a
        string value, e.g. ``"modify_existing_spec"``).
    """

    model_config = ConfigDict(extra="forbid")

    turn_id: str
    timestamp: str
    user_message: str
    assistant_message: str | None = None
    patch_id: str | None = None
    model_trace_ids: list[str] = Field(default_factory=list)
    intent: str


class FactRecord(BaseModel):
    """A confirmed fact extracted from the conversation.

    Facts are key-value pairs that the model has extracted from the
    user's messages.  They carry their source turn and a confirmation
    flag so the system knows which facts the user has explicitly
    confirmed versus which are still tentative.

    Parameters
    ----------
    fact_id:
        Unique identifier for this fact.
    key:
        Human-readable key (e.g. ``"inlet_velocity"``,
        ``"reynolds_number"``, ``"geometry_relation"``).
    value:
        The fact value (any type -- numeric, string, dict, ...).  When
        the value is a dict it may carry ``{"value": ..., "unit": ...}``
        for physical quantities.
    source_turn_id:
        The turn from which this fact was extracted.
    confirmed:
        Whether the user has explicitly confirmed this fact.
    """

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    key: str
    value: Any
    source_turn_id: str
    confirmed: bool = False


class ConflictRecord(BaseModel):
    """A detected conflict in the spec or conversation.

    Conflicts arise when the user's instructions are contradictory or
    when a proposed change conflicts with the existing spec.  Each
    conflict tracks its status through resolution.

    Parameters
    ----------
    conflict_id:
        Unique identifier for this conflict.
    description:
        Human-readable description of the conflict.
    paths:
        JSON Pointer paths involved in the conflict.
    status:
        Current status: ``"unresolved"``, ``"clarifying"``, or
        ``"resolved"``.
    resolution_turn_id:
        The turn in which the conflict was resolved, or ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    description: str
    paths: list[str] = Field(default_factory=list)
    status: Literal["unresolved", "clarifying", "resolved"] = "unresolved"
    resolution_turn_id: str | None = None


class ResearchSessionState(BaseModel):
    """The complete state of a research session.

    This model is the single source of truth for everything that
    persists across conversation turns: the conversation history,
    confirmed facts, unresolved conflicts, the pending patch, the
    patch history, model trace references, and the current workflow
    phase.

    The session is created by
    :meth:`~fluid_scientist.session_state.session_manager.SessionManager.create_session`
    and mutated through the manager's methods.  It is read by the
    :class:`~fluid_scientist.session_state.context_builder.ContextBuilder`
    to assemble the model context on each turn.

    Parameters
    ----------
    session_id:
        Unique identifier for this session.
    project_id:
        The project this session belongs to.
    active_spec_id:
        The ``spec_id`` of the currently active spec.  Empty string
        if no spec has been created yet.
    active_spec_version:
        The version of the currently active spec.  ``0`` if no spec
        has been created yet.
    turns:
        Ordered list of conversation turns (oldest first).
    compact_summary:
        Compressed summary of earlier conversation (used for long
        sessions to keep the context window manageable).
    confirmed_facts:
        List of confirmed facts extracted from the conversation.
    unresolved_conflicts:
        List of currently unresolved conflicts.
    pending_patch:
        The patch awaiting user confirmation, or ``None``.
    patch_history:
        List of ``patch_id`` strings that have been confirmed/applied.
    model_trace_ids:
        List of model trace identifiers for this session.
    current_phase:
        The current workflow phase (defaults to ``UNDERSTANDING``).
    created_at:
        ISO-8601 timestamp of session creation.
    last_active_at:
        ISO-8601 timestamp of the last activity in this session.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    project_id: str
    active_spec_id: str = ""
    active_spec_version: int = 0
    turns: list[ConversationTurn] = Field(default_factory=list)
    compact_summary: str = ""
    confirmed_facts: list[FactRecord] = Field(default_factory=list)
    unresolved_conflicts: list[ConflictRecord] = Field(default_factory=list)
    pending_patch: SimulationSpecPatch | None = None
    patch_history: list[str] = Field(default_factory=list)
    model_trace_ids: list[str] = Field(default_factory=list)
    current_phase: SessionPhase = SessionPhase.UNDERSTANDING
    created_at: str = ""
    last_active_at: str = ""
