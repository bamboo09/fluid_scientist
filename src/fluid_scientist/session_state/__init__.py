"""Multi-turn session state management for the model-driven spec editing system.

This package provides the data structures and services needed to manage
research sessions that span multiple conversation turns:

* :class:`ResearchSessionState` -- the complete session state model,
  including conversation history, confirmed facts, unresolved conflicts,
  pending patch, patch history, and the current workflow phase.
* :class:`SessionManager` -- in-memory session persistence and mutation,
  integrated with :class:`VersionedSpecStore` and :class:`PatchHistory`.
* :class:`ContextBuilder` -- assembles the 11-section model context for
  each turn, with summary compression for long sessions.
* :class:`IntentDetector` -- classifies user messages into six
  high-level intents (create / modify / confirm / reject / undo /
  explain).

Usage::

    from fluid_scientist.session_state import (
        SessionManager,
        ContextBuilder,
        IntentDetector,
        ResearchSessionState,
    )

    manager = SessionManager()
    session = manager.create_session("project_1")

    detector = IntentDetector()
    intent = detector.detect_intent("把入口速度改成3m/s", session)

    builder = ContextBuilder()
    context = builder.build_context(
        session,
        spec=None,
        user_message="把入口速度改成3m/s",
        skills=["mesh_design"],
        openfoam_env={"version": "v2312"},
    )
"""

from __future__ import annotations

from .context_builder import ContextBuilder, ModelContext
from .intent_detector import IntentDetector, UserIntent
from .models import (
    ConflictRecord,
    ConversationTurn,
    FactRecord,
    ResearchSessionState,
    SessionPhase,
)
from .session_manager import SessionManager

__all__ = [
    # Models
    "ConflictRecord",
    "ConversationTurn",
    "FactRecord",
    "ResearchSessionState",
    "SessionPhase",
    # Services
    "ContextBuilder",
    "IntentDetector",
    "SessionManager",
    # Context
    "ModelContext",
    "UserIntent",
]
