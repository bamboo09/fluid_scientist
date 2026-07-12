"""Compile-Ready workflow state machine.

Replaces the legacy DraftSessionStatus with a pipeline that gates draft
publication on *actual* case generation and validation.  A draft is only
shown to the user as "ready" after the COMPILE_READY state is reached;
all intermediate states are internal progress indicators.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from fluid_scientist.compat import UTC


# ---------------------------------------------------------------------------
# PipelineStatus  -- the new fine-grained pipeline states
# ---------------------------------------------------------------------------


class PipelineStatus:
    """Status constants for the V5 compile-ready pipeline."""

    # --- Internal pipeline stages (not yet shown to user as "ready") ---
    UNDERSTANDING = "understanding"
    DESIGNING = "designing"
    CLOSING = "closing"
    RESOLVING_CAPABILITIES = "resolving_capabilities"
    EXTENDING_CAPABILITIES = "extending_capabilities"
    GENERATING_CASE = "generating_case"
    VALIDATING_CASE = "validating_case"
    # --- Terminal states ---
    COMPILE_READY = "compile_ready"
    FAILED = "failed"
    # --- Post-publication user-interaction states ---
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CHANGE_PROPOSAL_PENDING = "change_proposal_pending"
    CONFIRMED = "confirmed"
    SUBMITTED = "submitted"

    _ALL_INTERNAL = frozenset(
        {
            UNDERSTANDING,
            DESIGNING,
            CLOSING,
            RESOLVING_CAPABILITIES,
            EXTENDING_CAPABILITIES,
            GENERATING_CASE,
            VALIDATING_CASE,
        }
    )

    _ALL_USER_VISIBLE_READY = frozenset(
        {
            COMPILE_READY,
            AWAITING_CONFIRMATION,
            CHANGE_PROPOSAL_PENDING,
            CONFIRMED,
            SUBMITTED,
        }
    )

    @classmethod
    def is_internal_pipeline(cls, status: str) -> bool:
        return status in cls._ALL_INTERNAL

    @classmethod
    def is_user_visible_ready(cls, status: str) -> bool:
        """Return True when the frontend may display the full draft."""
        return status in cls._ALL_USER_VISIBLE_READY

    @classmethod
    def is_failed(cls, status: str) -> bool:
        return status == cls.FAILED


# ---------------------------------------------------------------------------
# Stage descriptors  (used for progress UI)
# ---------------------------------------------------------------------------

STAGE_DESCRIPTORS: dict[str, dict[str, str]] = {
    PipelineStatus.UNDERSTANDING: {
        "label": "正在理解研究问题",
        "description": "解析研究目标和完整物理语义",
        "progress": "10%",
    },
    PipelineStatus.DESIGNING: {
        "label": "正在生成完整实验设计",
        "description": "综合几何、材料、边界条件、物理模型、数值方法",
        "progress": "25%",
    },
    PipelineStatus.CLOSING: {
        "label": "正在完成物理、数值和观测闭合",
        "description": "执行参数依赖闭合和一致性检查",
        "progress": "45%",
    },
    PipelineStatus.RESOLVING_CAPABILITIES: {
        "label": "正在解析已有能力和扩展能力",
        "description": "匹配现有能力，自动生成缺失扩展",
        "progress": "60%",
    },
    PipelineStatus.EXTENDING_CAPABILITIES: {
        "label": "Resolving missing capabilities",
        "description": "Generating extension specs and validation checkpoints.",
        "progress": "68%",
    },
    PipelineStatus.GENERATING_CASE: {
        "label": "正在生成真实算例",
        "description": "生成 OpenFOAM Case 目录和字典文件",
        "progress": "75%",
    },
    PipelineStatus.VALIDATING_CASE: {
        "label": "正在执行静态、网格和最小运行验证",
        "description": "字典验证、网格生成、checkMesh、求解器 dry-run",
        "progress": "90%",
    },
    PipelineStatus.COMPILE_READY: {
        "label": "实验草案已生成",
        "description": "完整可执行草案已就绪，请审阅确认",
        "progress": "100%",
    },
    PipelineStatus.FAILED: {
        "label": "生成失败",
        "description": "系统无法自动完成，请查看失败原因",
        "progress": "0%",
    },
}

# ---------------------------------------------------------------------------
# Allowed transitions  (strict linear pipeline with failure branches)
# ---------------------------------------------------------------------------

_PIPELINE_TRANSITIONS: dict[str, frozenset[str]] = {
    "init": frozenset({PipelineStatus.UNDERSTANDING}),
    PipelineStatus.UNDERSTANDING: frozenset(
        {PipelineStatus.DESIGNING, PipelineStatus.FAILED}
    ),
    PipelineStatus.DESIGNING: frozenset(
        {PipelineStatus.CLOSING, PipelineStatus.FAILED}
    ),
    PipelineStatus.CLOSING: frozenset(
        {PipelineStatus.RESOLVING_CAPABILITIES, PipelineStatus.FAILED}
    ),
    PipelineStatus.RESOLVING_CAPABILITIES: frozenset(
        {
            PipelineStatus.EXTENDING_CAPABILITIES,
            PipelineStatus.GENERATING_CASE,
            PipelineStatus.FAILED,
        }
    ),
    PipelineStatus.EXTENDING_CAPABILITIES: frozenset(
        {
            PipelineStatus.RESOLVING_CAPABILITIES,
            PipelineStatus.GENERATING_CASE,
            PipelineStatus.FAILED,
        }
    ),
    PipelineStatus.GENERATING_CASE: frozenset(
        {PipelineStatus.VALIDATING_CASE, PipelineStatus.FAILED}
    ),
    PipelineStatus.VALIDATING_CASE: frozenset(
        {PipelineStatus.COMPILE_READY, PipelineStatus.FAILED}
    ),
    PipelineStatus.COMPILE_READY: frozenset(
        {
            PipelineStatus.AWAITING_CONFIRMATION,
            PipelineStatus.CHANGE_PROPOSAL_PENDING,
        }
    ),
    PipelineStatus.AWAITING_CONFIRMATION: frozenset(
        {
            PipelineStatus.CONFIRMED,
            PipelineStatus.CHANGE_PROPOSAL_PENDING,
        }
    ),
    PipelineStatus.CHANGE_PROPOSAL_PENDING: frozenset(
        {
            PipelineStatus.CLOSING,  # re-close after change
            PipelineStatus.AWAITING_CONFIRMATION,
            PipelineStatus.COMPILE_READY,
        }
    ),
    PipelineStatus.CONFIRMED: frozenset(
        {PipelineStatus.SUBMITTED, PipelineStatus.CHANGE_PROPOSAL_PENDING}
    ),
    PipelineStatus.SUBMITTED: frozenset(),
    PipelineStatus.FAILED: frozenset(
        {PipelineStatus.UNDERSTANDING}  # allow retry
    ),
}


# ---------------------------------------------------------------------------
# StageRecord  (captured each time the pipeline advances)
# ---------------------------------------------------------------------------


class StageRecord(BaseModel):
    """Serializable record of entering a pipeline stage."""

    stage: str
    entered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: str = ""
    error: str = ""
    duration_ms: float | None = None

    @property
    def label(self) -> str:
        return STAGE_DESCRIPTORS.get(self.stage, {}).get("label", self.stage)

    @property
    def description(self) -> str:
        return STAGE_DESCRIPTORS.get(self.stage, {}).get("description", "")

    @property
    def progress(self) -> str:
        return STAGE_DESCRIPTORS.get(self.stage, {}).get("progress", "0%")


# ---------------------------------------------------------------------------
# PipelineFailure  -- structured failure information
# ---------------------------------------------------------------------------


class PipelineFailure(BaseModel):
    """Describes why the pipeline failed at a particular stage."""

    failed_stage: str
    failure_category: Literal[
        "semantic_parsing",
        "design_incomplete",
        "closure_conflict",
        "missing_capability",
        "extension_pipeline_incomplete",
        "extension_generation_failed",
        "case_generation_failed",
        "validation_failed",
        "internal_error",
    ]
    message: str
    internal_details: dict[str, Any] = Field(default_factory=dict)
    can_retry: bool = True
    requires_user_input: bool = False
    user_facing_message: str = ""


# ---------------------------------------------------------------------------
# CompileReadyDraftView  -- the single unified DTO delivered to the frontend
# ---------------------------------------------------------------------------


class CompileReadyDraftView(BaseModel):
    """The one and only DTO the frontend consumes after COMPILE_READY.

    No more mixing of StudyDecomposition, PhysicsSpec, ExperimentSpec,
    Draft, CapabilityPreview and compiled_metrics.  The frontend renders
    exclusively from this view.
    """

    session_id: str
    draft_id: str
    draft_version: int
    status: str = PipelineStatus.COMPILE_READY
    research_objective: str
    research_hypotheses: list[str] = Field(default_factory=list)
    # Fully resolved design (every field has a concrete value + provenance)
    design: dict[str, Any] = Field(default_factory=dict)
    # Geometry
    geometry: dict[str, Any] = Field(default_factory=dict)
    # Materials
    materials: dict[str, Any] = Field(default_factory=dict)
    # Boundary conditions -- fully compiled to patch-level configs
    boundary_conditions: dict[str, Any] = Field(default_factory=dict)
    # Initial conditions
    initial_conditions: dict[str, Any] = Field(default_factory=dict)
    # Physical models
    physical_models: dict[str, Any] = Field(default_factory=dict)
    # Solver and numerics
    solver: dict[str, Any] = Field(default_factory=dict)
    numerics: dict[str, Any] = Field(default_factory=dict)
    # Mesh
    mesh: dict[str, Any] = Field(default_factory=dict)
    # Time control
    time_control: dict[str, Any] = Field(default_factory=dict)
    # Sampling and output
    sampling: dict[str, Any] = Field(default_factory=dict)
    output_control: dict[str, Any] = Field(default_factory=dict)
    # Metrics -- fully executable (have functionObjects, postprocessors, capabilities)
    scientific_metrics: list[dict[str, Any]] = Field(default_factory=list)
    boundary_verification_metrics: list[dict[str, Any]] = Field(default_factory=list)
    credibility_metrics: list[dict[str, Any]] = Field(default_factory=list)
    # Capabilities used
    capabilities_used: list[dict[str, Any]] = Field(default_factory=list)
    capabilities_extended: list[dict[str, Any]] = Field(default_factory=list)
    # Validation results
    validation_results: dict[str, Any] = Field(default_factory=dict)
    # Case manifest
    case_manifest: dict[str, Any] = Field(default_factory=dict)
    # Modifiable fields (user can change these via natural language)
    modifiable_fields: list[str] = Field(default_factory=list)
    # Assumptions made (transparent to user)
    assumptions: list[dict[str, Any]] = Field(default_factory=list)
    # Study type and objective (for frontend display)
    study_type: str = "cfd_simulation"
    objective: str = ""
    # Requested outputs / observables
    requested_outputs: list[Any] = Field(default_factory=list)
    # Analysis goals (from scientific intent)
    analysis_goals: list[Any] = Field(default_factory=list)
    # Blocking issues (if any)
    blocking_issues: list[dict[str, Any]] = Field(default_factory=list)
    # Created timestamp
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# Need Literal import at top; fix ordering
# (Literal is imported from typing at top, the re-import is harmless but
# we keep the import at module top to satisfy static analysis.)

__all__ = [
    "CompileReadyDraftView",
    "PipelineFailure",
    "PipelineState",
    "PipelineStatus",
    "STAGE_DESCRIPTORS",
    "StageRecord",
    "V5WorkflowPipeline",
]

# Import pipeline after all base models are defined to avoid circular imports
from fluid_scientist.workflow_pipeline.pipeline import PipelineState, V5WorkflowPipeline  # noqa: E402
