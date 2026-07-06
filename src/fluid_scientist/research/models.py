"""研究会话的核心数据模型。

定义研究需求收集、澄清、草稿生成等阶段所需的全部 Pydantic 模型。
这些模型使用 BaseModel 而非 StrictModel，因为研究流程需要允许
后续扩展（如动态 schema 追加字段）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from fluid_scientist.compat import StrEnum


class ResearchSessionStatus(StrEnum):
    """研究会话的生命周期状态。"""

    COLLECTING_REQUIREMENTS = "collecting_requirements"
    CLARIFICATION_REQUIRED = "clarification_required"
    DRAFT_READY = "draft_ready"
    AWAITING_USER_REVIEW = "awaiting_user_review"
    AWAITING_CODE_APPROVAL = "awaiting_code_approval"
    READY_TO_CONFIRM = "ready_to_confirm"
    EXPERIMENT_CREATED = "experiment_created"
    CLOSED = "closed"
    UNSUPPORTED = "unsupported"


class ClarificationQuestion(BaseModel):
    """向用户提出的单个澄清问题。"""

    question_id: str
    text: str
    options: list[str] = Field(default_factory=list)
    allow_free_text: bool = True
    rationale: str | None = None


class ExtractedFact(BaseModel):
    """从用户输入中抽取的结构化事实。"""

    fact_id: str
    category: str  # geometry, material, boundary, operating_condition, objective, metric
    key: str
    value: str
    confidence: float = 1.0
    source: str = "user_input"


class ClarificationTurn(BaseModel):
    """一轮澄清对话的完整记录。"""

    turn_id: str
    session_id: str
    user_message: str
    assistant_questions: list[ClarificationQuestion] = Field(default_factory=list)
    extracted_facts: list[ExtractedFact] = Field(default_factory=list)
    created_at: str


class Assumption(BaseModel):
    """研究过程中做出的假设。"""

    assumption_id: str
    description: str
    rationale: str
    impact_level: Literal["high", "medium", "low"] = "medium"
    validated: bool = False


class PhysicsUnknown(BaseModel):
    """物理参数中的未知项，需要用户确认或默认值填充。"""

    unknown_id: str
    category: str
    description: str
    default_value: str | None = None
    confidence: float = 0.0
    requires_user_input: bool = True


class IntentAssessment(BaseModel):
    """意图评估结果，描述用户的研究目标和物理系统。"""

    task_type: str  # new_simulation, parameter_sensitivity, mechanism_analysis, etc.
    research_objective: str | None = None
    physical_system: str | None = None
    target_phenomena: list[str] = Field(default_factory=list)
    comparison_dimensions: list[str] = Field(default_factory=list)
    requested_metrics: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    missing_critical_information: list[str] = Field(default_factory=list)
    ready_for_draft: bool = False
    unsupported_reason: str | None = None


class ResearchPhysicsSpec(BaseModel):
    """从研究需求推导出的物理规格，供后续 Dynamic Schema 使用。"""

    dimensions: str | None = None
    temporal_type: str | None = None
    phases: str | None = None
    compressibility: str | None = None
    flow_regime: str | None = None
    geometry_facts: dict[str, Any] = Field(default_factory=dict)
    material_facts: dict[str, Any] = Field(default_factory=dict)
    boundary_facts: dict[str, Any] = Field(default_factory=dict)
    initial_condition_facts: dict[str, Any] = Field(default_factory=dict)
    operating_conditions: dict[str, Any] = Field(default_factory=dict)
    target_phenomena: list[str] = Field(default_factory=list)
    user_metrics: list[str] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    unknowns: list[PhysicsUnknown] = Field(default_factory=list)


class MissingCapability(BaseModel):
    """当前系统尚不支持的 capability 描述。"""

    capability_id: str
    capability_type: str  # metric_algorithm, function_object, solver_adapter, ...
    description: str
    reason: str
    severity: Literal["warning", "blocking"] = "blocking"
    code_extension_allowed: bool = True
    suggested_extension_type: str | None = None


class ResearchSession(BaseModel):
    """研究会话，贯穿需求收集到实验创建的完整生命周期。"""

    session_id: str
    project_id: str
    status: ResearchSessionStatus = ResearchSessionStatus.COLLECTING_REQUIREMENTS
    original_request: str
    accumulated_context: dict[str, Any] = Field(default_factory=dict)
    confirmed_facts: list[ExtractedFact] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    intent_assessment: IntentAssessment | None = None
    physics_spec: ResearchPhysicsSpec | None = None
    experiment_spec_id: str | None = None
    turns: list[ClarificationTurn] = Field(default_factory=list)
    missing_capabilities: list[MissingCapability] = Field(default_factory=list)
    created_at: str
    updated_at: str


# --- 轮次结果类型（判别联合） ---


class ClarificationRequired(BaseModel):
    """需要用户进一步澄清的轮次结果。"""

    type: Literal["clarification_required"] = "clarification_required"
    session_id: str
    summary: str
    questions: list[ClarificationQuestion]
    current_understanding: dict[str, Any] = Field(default_factory=dict)


class DraftReady(BaseModel):
    """研究需求已充分，可以生成实验草稿的轮次结果。"""

    type: Literal["draft_ready"] = "draft_ready"
    session_id: str
    experiment_spec_id: str | None = None
    experiment_version: int = 1
    warnings: list[str] = Field(default_factory=list)


class UnsupportedRequest(BaseModel):
    """当前请求不被支持的轮次结果。"""

    type: Literal["unsupported"] = "unsupported"
    session_id: str
    reason: str
    missing_capabilities: list[MissingCapability] = Field(default_factory=list)


ResearchTurnResult = ClarificationRequired | DraftReady | UnsupportedRequest


__all__ = [
    "Assumption",
    "ClarificationQuestion",
    "ClarificationRequired",
    "ClarificationTurn",
    "DraftReady",
    "ExtractedFact",
    "IntentAssessment",
    "MissingCapability",
    "PhysicsUnknown",
    "ResearchPhysicsSpec",
    "ResearchSession",
    "ResearchSessionStatus",
    "ResearchTurnResult",
    "UnsupportedRequest",
]
