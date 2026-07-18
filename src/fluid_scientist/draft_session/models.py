"""Draft session data models.

Defines the Pydantic v2 data structures used by the conversational
draft workflow.  A :class:`DraftSession` is the top-level aggregate that
tracks the lifecycle of a single user-driven research drafting
conversation, while :class:`SessionMessage`, :class:`ResearchState` and
:class:`InputRoute` capture the conversation history, accumulated
research context and the routing decision for each incoming user
message.

All models intentionally use :class:`~pydantic.BaseModel` (rather than a
``StrictModel``) to keep them flexible: the draft workflow must tolerate
extra, schema-driven fields supplied by downstream stages such as the
dynamic schema engine and the capability resolver.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from fluid_scientist.compat import UTC, StrEnum

# ---------------------------------------------------------------------------
# 1. DraftSessionStatus
# ---------------------------------------------------------------------------


class DraftSessionStatus(StrEnum):
    """Lifecycle status of a :class:`DraftSession`.

    The status drives the conversational state machine, button
    visibility in the workbench UI and the set of allowed input routes.
    """

    COLLECTING_INTENT = "collecting_intent"
    BATCH_REVIEW = "batch_review"
    CLARIFYING = "clarifying"
    DRAFT_READY = "draft_ready"
    PROPOSAL_PENDING = "proposal_pending"
    READY = "ready"
    CONFIRMED = "confirmed"
    CASE_PLANNING = "case_planning"
    AWAITING_CODE_EXTENSION = "awaiting_code_extension"
    COMPILED = "compiled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# 2. DraftSession
# ---------------------------------------------------------------------------


class DraftSession(BaseModel):
    """The top-level conversational draft session aggregate.

    A :class:`DraftSession` holds the mutable state of one user's
    drafting conversation: the currently selected study, the active
    draft (and its version), any pending clarification questions or
    change proposal, and the lifecycle ``status`` that drives the state
    machine.  All cross-cutting research context is persisted separately
    in a :class:`ResearchState` linked via ``research_state_id``.
    """

    session_id: str
    user_id: str | None = None
    status: DraftSessionStatus = DraftSessionStatus.COLLECTING_INTENT
    batch_id: str | None = None
    selected_study_id: str | None = None
    research_state_id: str | None = None
    current_draft_id: str | None = None
    current_draft_version: int | None = None
    pending_question_ids: list[str] = Field(default_factory=list)
    pending_proposal_id: str | None = None
    pending_missing_capability_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# 3. SessionMessage
# ---------------------------------------------------------------------------


class SessionMessage(BaseModel):
    """A single message in a draft session conversation.

    ``role`` follows the conventional assistant/user/system/tool split,
    while ``message_type`` records the semantic role of the message
    within the draft workflow (research request, clarification question,
    draft summary, change proposal, etc.).  The ``linked_*`` fields let
    a message point at the study, question, proposal or draft it refers
    to so the orchestration layer can reconstruct provenance without
    re-parsing the message content.
    """

    message_id: str
    session_id: str
    role: Literal["user", "assistant", "system", "tool"]
    message_type: Literal[
        "research_request",
        "batch_summary",
        "study_selection",
        "clarification_question",
        "clarification_answer",
        "draft_summary",
        "draft_change_request",
        "change_proposal",
        "proposal_confirmation",
        "proposal_cancel",
        "question_about_draft",
        "compile_request",
        "case_plan_summary",
        "missing_capability_summary",
        "error",
    ]
    content: str
    linked_study_id: str | None = None
    linked_question_id: str | None = None
    linked_proposal_id: str | None = None
    linked_draft_id: str | None = None
    linked_draft_version: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# 4. ResearchState
# ---------------------------------------------------------------------------


class ResearchState(BaseModel):
    """Accumulated research context for a draft session.

    The :class:`ResearchState` is the long-lived, append-only-ish view
    of what the system has learned about a user's research goal: the
    original request, confirmed facts, accepted/rejected assumptions,
    detected unknowns and blocking issues, plus the high-level physical
    and study intents.  It is versioned so concurrent draft versions
    can reference a consistent snapshot.
    """

    research_state_id: str
    session_id: str
    selected_study_id: str | None = None
    original_user_request: str
    confirmed_facts: dict = Field(default_factory=dict)
    accepted_assumptions: dict = Field(default_factory=dict)
    rejected_assumptions: dict = Field(default_factory=dict)
    unknowns: list[dict] = Field(default_factory=list)
    blocking_issues: list[dict] = Field(default_factory=list)
    physical_intent: dict | None = None
    study_intent: dict | None = None
    last_updated_by_message_id: str | None = None
    version: int = 1
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# 5. ClarificationQuestion
# ---------------------------------------------------------------------------


class ClarificationQuestion(BaseModel):
    """A structured clarification question posed to the user.

    ``severity`` distinguishes questions that merely need confirmation
    (``"needs_confirmation"``) from those that must be answered before case
    generation can proceed (``"blocking_for_case_generation"``).
    ``options`` may provide a set of pre-defined answer choices, while
    ``recommended_answer`` can suggest a default.  When ``allow_free_text``
    is ``True`` the user may supply a free-form answer beyond the listed
    options.
    """

    question_id: str
    field: str
    question: str
    reason: str
    severity: Literal["needs_confirmation", "blocking_for_case_generation"]
    options: list[dict] | None = None
    recommended_answer: dict | None = None
    allow_free_text: bool = True


# ---------------------------------------------------------------------------
# 6. InputRoute
# ---------------------------------------------------------------------------


class InputRoute(BaseModel):
    """The result of routing a single user message.

    ``input_type`` is the canonical category the orchestrator switches
    on.  ``confidence`` records how sure the router is (strong rules
    yield >= 0.9, the LLM fallback or default yield lower values).
    ``should_call_llm`` tells the orchestrator whether the route was
    produced purely from deterministic state rules (``False``) or
    whether an LLM should refine the decision (``True``).
    """

    input_type: Literal[
        "new_research_request",
        "batch_research_request",
        "study_selection",
        "clarification_answer",
        "draft_change_request",
        "proposal_confirmation",
        "proposal_cancel",
        "question_about_draft",
        "compile_request",
        "run_request",
        "unknown",
    ]
    intent: Literal[
        "NEW_RESEARCH",
        "MODIFY_DRAFT",
        "SUPPLEMENT_DRAFT",
        "ANSWER_CLARIFICATION",
        "ASK_ABOUT_DRAFT",
        "CONFIRM_PROPOSAL",
        "REJECT_PROPOSAL",
        "CONFIRM_DRAFT",
        "SELECT_STUDY",
        "UNRESOLVED",
    ] = "UNRESOLVED"
    confidence: float
    reason: str
    should_call_llm: bool


# ---------------------------------------------------------------------------
# 7. LLMCallRecord
# ---------------------------------------------------------------------------


class LLMCallRecord(BaseModel):
    """An audit record of a single LLM invocation within a draft session.

    Captures the full provenance of an LLM call: which provider/model was
    used, which prompt template/version, what inputs were referenced, a
    summary of the input, the expected output schema, the raw and parsed
    outputs, whether the call succeeded, whether a fallback was used, and
    any error information.  This enables replay, debugging and quality
    analysis of the AI-driven workflow.
    """

    call_id: str
    session_id: str
    purpose: Literal[
        "input_routing",
        "study_decomposition",
        "physics_intent",
        "clarification_extract",
        "clarification_planning",
        "draft_generation",
        "draft_change_proposal",
        "unknown_parameter_mapping",
        "unknown_metric_mapping",
        "case_plan_generation",
        "missing_capability_analysis",
        "code_extension_spec",
        "code_generation",
        "code_review",
        "explanation",
        "spec_editing",
        "structured_understanding",
    ]
    provider: str = "unknown"
    model_name: str = "unknown"
    prompt_name: str = ""
    prompt_version: str = ""
    input_refs: list[str] = Field(default_factory=list)
    input_summary: str = ""
    output_schema: str = ""
    raw_output: str | None = None
    parsed_output: dict | None = None
    success: bool = False
    fallback_used: bool = False
    fallback_reason: str | None = None
    original_purpose: str | None = None
    error: str | None = None
    latency_ms: float | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = [
    "ClarificationQuestion",
    "DraftSession",
    "DraftSessionStatus",
    "InputRoute",
    "LLMCallRecord",
    "ResearchState",
    "SessionMessage",
]
