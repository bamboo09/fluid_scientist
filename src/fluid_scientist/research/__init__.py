"""ResearchSession 模块：研究需求收集与多轮澄清工作流。

提供从用户自然语言输入到结构化实验草稿的完整编排能力，
包括意图评估、范围澄清、会话管理和流程编排。
"""

from fluid_scientist.research.intent_engine import IntentEngine
from fluid_scientist.research.models import (
    Assumption,
    ClarificationQuestion,
    ClarificationRequired,
    ClarificationTurn,
    CriticalUnknown,
    DraftReady,
    ExtractedFact,
    IntentAssessment,
    MissingCapability,
    PhysicsUnknown,
    ProposedAssumption,
    ResearchPhysicsSpec,
    ResearchSession,
    ResearchSessionStatus,
    ResearchTurnResult,
    UnsupportedRequest,
)
from fluid_scientist.research.orchestrator import ResearchOrchestrator
from fluid_scientist.research.scope_engine import ScopeEngine
from fluid_scientist.research.session_store import SessionStore
from fluid_scientist.research.spec_factory import ExperimentSpecFactory

__all__ = [
    "Assumption",
    "ClarificationQuestion",
    "ClarificationRequired",
    "ClarificationTurn",
    "CriticalUnknown",
    "DraftReady",
    "ExperimentSpecFactory",
    "ExtractedFact",
    "IntentAssessment",
    "IntentEngine",
    "MissingCapability",
    "PhysicsUnknown",
    "ProposedAssumption",
    "ResearchOrchestrator",
    "ResearchPhysicsSpec",
    "ResearchSession",
    "ResearchSessionStatus",
    "ResearchTurnResult",
    "ScopeEngine",
    "SessionStore",
    "UnsupportedRequest",
]
