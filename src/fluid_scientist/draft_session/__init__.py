"""Draft session package.

Implements the conversational draft workflow that takes a user's
research request through study decomposition, clarification, draft
generation, change-proposal confirmation, case planning, compilation
and execution.  The package intentionally keeps an in-memory store and
a rule-first input router so the orchestration layer can be exercised
without external dependencies.
"""

from fluid_scientist.draft_session.input_router import InputRouter
from fluid_scientist.draft_session.models import (
    ClarificationQuestion,
    DraftSession,
    DraftSessionStatus,
    InputRoute,
    LLMCallRecord,
    ResearchState,
    SessionMessage,
)
from fluid_scientist.draft_session.session_store import DraftSessionStore
from fluid_scientist.draft_session.state_machine import (
    DraftSessionStateMachine,
    TransitionError,
)

__all__ = [
    "ClarificationQuestion",
    "DraftSession",
    "DraftSessionStateMachine",
    "DraftSessionStatus",
    "DraftSessionStore",
    "InputRoute",
    "InputRouter",
    "LLMCallRecord",
    "ResearchState",
    "SessionMessage",
    "TransitionError",
]
