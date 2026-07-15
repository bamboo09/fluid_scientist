"""V5 Pipeline state machine — maps internal pipeline stages to frontend-displayable states."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

# Pipeline stages that the frontend displays
PipelineDisplayStage = Literal[
    "正在提取研究条件",       # extracting research conditions
    "正在检查歧义和冲突",     # checking ambiguities and conflicts
    "正在构建物理问题",       # building physics problem
    "正在拆解所需能力",       # decomposing required capabilities
    "已识别可复用能力",       # identified reusable capabilities
    "发现需要扩展的能力",     # found capabilities needing extension
    "正在验证新能力",         # validating new capabilities
    "正在编译OpenFOAM Case", # compiling OpenFOAM case
    "正在检查网格",           # checking mesh
    "正在进行预运行",         # running smoke test
    "预运行通过",             # smoke test passed
    "可以提交",               # ready to submit
    "验证失败",               # validation failed
    "需要澄清",               # needs clarification
    "环境受阻",               # environment blocked
]

CapabilityDisplayStatus = Literal[
    "已支持",       # EXACT_SUPPORTED
    "可组合",       # COMPOSABLE_SUPPORTED
    "待扩展",       # EXTENDABLE
    "需要新物理",   # REQUIRES_NEW_PHYSICS
    "需要确认",     # NEEDS_CLARIFICATION
    "环境受阻",     # ENVIRONMENT_BLOCKED
]

class StageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: PipelineDisplayStage
    completed: bool = False
    in_progress: bool = False
    details: str = ""
    timestamp: str = ""

class CapabilityInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    description: str
    status: CapabilityDisplayStatus
    capability_id: str = ""
    can_proceed: bool = False

class ChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changed_paths: list[str] = Field(default_factory=list)
    description: str = ""
    requires_revalidation: bool = True
    old_case_ir_version: int = 0
    new_case_ir_version: int = 0

class ClarificationQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str
    question: str
    options: list[str] = Field(default_factory=list)
    recommended_option: str = ""
    impact: str = ""  # what different answers mean

class PipelineStateView(BaseModel):
    """Full pipeline state view sent to frontend."""
    model_config = ConfigDict(extra="forbid")
    session_id: str
    study_id: str
    case_id: str = ""
    case_ir_version: int = 0
    current_stage: PipelineDisplayStage = "正在提取研究条件"
    stages: list[StageInfo] = Field(default_factory=list)
    capabilities: list[CapabilityInfo] = Field(default_factory=list)
    clarifications: list[ClarificationQuestion] = Field(default_factory=list)
    change_summary: ChangeSummary | None = None
    validation_errors: list[str] = Field(default_factory=list)
    can_submit: bool = False
    evidence_valid: bool = True

# Mapping from internal states to display states
INTERNAL_TO_DISPLAY = {
    "UNDERSTANDING": "正在提取研究条件",
    "FACTS_EXTRACTED": "正在检查歧义和冲突",
    "AMBIGUITIES_ANALYZED": "正在构建物理问题",
    "REQUESTED_CASE_IR_CREATED": "正在构建物理问题",
    "REQUESTED_CASE_IR_VALIDATED": "正在拆解所需能力",
    "REQUIREMENT_COVERAGE_VALIDATED": "正在拆解所需能力",
    "REQUIREMENTS_DECOMPOSED": "正在拆解所需能力",
    "CAPABILITY_RESOLVING": "正在拆解所需能力",
    "CAPABILITY_RESOLVED": "已识别可复用能力",
    "NEEDS_EXTENSION": "发现需要扩展的能力",
    "NEEDS_NEW_PHYSICS": "发现需要扩展的能力",
    "NEEDS_CLARIFICATION": "需要澄清",
    "ENVIRONMENT_BLOCKED": "环境受阻",
    "EXTENSION_SPEC_CREATED": "正在验证新能力",
    "EXTENSION_IMPLEMENTED": "正在验证新能力",
    "EXTENSION_VALIDATING": "正在验证新能力",
    "CAPABILITY_REGISTERED": "正在编译OpenFOAM Case",
    "RESOLVED_CASE_IR_VALIDATED": "正在编译OpenFOAM Case",
    "COMPILED": "正在编译OpenFOAM Case",
    "STATIC_VALIDATED": "正在编译OpenFOAM Case",
    "DICTIONARY_VALIDATED": "正在检查网格",
    "MESH_BUILT": "正在检查网格",
    "MESH_VALIDATED": "正在进行预运行",
    "SERIAL_SMOKE_TEST_PASSED": "正在进行预运行",
    "PARALLEL_SMOKE_TEST_PASSED": "预运行通过",
    "READY_TO_SUBMIT": "可以提交",
    "VALIDATION_FAILED": "验证失败",
    "FAILED": "验证失败",
}

CAPABILITY_STATUS_TO_DISPLAY = {
    "EXACT_SUPPORTED": "已支持",
    "COMPOSABLE_SUPPORTED": "可组合",
    "EXTENDABLE": "待扩展",
    "REQUIRES_NEW_PHYSICS": "需要新物理",
    "NEEDS_CLARIFICATION": "需要确认",
    "ENVIRONMENT_BLOCKED": "环境受阻",
}

def to_display_stage(internal_stage: str) -> PipelineDisplayStage:
    return INTERNAL_TO_DISPLAY.get(internal_stage, "正在提取研究条件")

def to_capability_display(internal_status: str) -> CapabilityDisplayStatus:
    return CAPABILITY_STATUS_TO_DISPLAY.get(internal_status, "需要确认")
