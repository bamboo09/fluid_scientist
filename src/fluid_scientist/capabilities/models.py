"""Unified MissingCapability and CodeExtension data structures."""

from __future__ import annotations

from datetime import datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from fluid_scientist.compat import UTC


class CapabilityType:
    """Capability type constants."""

    METRIC_OPERATOR = "metric_operator"
    BOUNDARY_CONDITION = "boundary_condition"
    SOLVER_EXTENSION = "solver_extension"
    POST_PROCESSOR = "post_processor"
    MESH_GENERATOR = "mesh_generator"
    ANALYSIS_PLUGIN = "analysis_plugin"


class MissingCapability(BaseModel):
    """统一缺失能力数据结构。

    Multiple sources can generate MissingCapability:
    - Intent Engine (unsupported research objective)
    - Dynamic Schema (unknown physics)
    - Metric Planner (unknown metric)
    - Measurement Compiler (unsupported functionObject)
    - Solver Capability Resolver (missing solver feature)
    - Experiment Compiler (unsupported experiment type)
    - Result Ingestor (unsupported result format)
    - Metric Executor (unsupported calculation)
    """

    capability_id: str
    capability_type: str  # CapabilityType constants
    requested_behavior: str
    reason: str
    severity: Literal["warning", "blocking"] = "warning"
    code_extension_allowed: bool = True
    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    suggested_extension_type: str | None = None
    related_metric_ids: list[str] = Field(default_factory=list)
    related_parameter_ids: list[str] = Field(default_factory=list)
    source_module: str = ""  # which module detected this

    def is_blocking(self) -> bool:
        return self.severity == "blocking"


class CodeExtensionSpec(BaseModel):
    """代码扩展规格 — 描述需要开发的代码扩展。

    Lifecycle: DRAFT → SANDBOX_TESTED → AUTO_TESTED → APPROVED → REGISTERED
                                                    ↘ REJECTED
    """

    extension_id: str
    extension_name: str
    extension_type: str  # metric_operator, boundary_condition, etc.

    description: str
    rationale: str  # why this extension is needed

    required_inputs: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)

    # Generated code
    generated_code: str | None = None
    code_language: str = "python"

    # Test results
    unit_tests: list[dict[str, Any]] = Field(default_factory=list)
    benchmark_tests: list[dict[str, Any]] = Field(default_factory=list)
    security_checks: list[dict[str, Any]] = Field(default_factory=list)

    # Approval
    state: Literal[
        "draft",
        "sandbox_tested",
        "auto_tested",
        "approved",
        "conditionally_approved",
        "rejected",
        "revision_required",
        "sandbox_only",
        "registered",
    ] = "draft"
    approval_comment: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None

    # Linking
    related_capability_id: str | None = None
    research_session_id: str | None = None
    experiment_spec_id: str | None = None

    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Transition validation
    _VALID_TRANSITIONS: ClassVar[dict[str, set[str]]] = {
        "draft": {"sandbox_tested", "rejected"},
        "sandbox_tested": {"auto_tested", "rejected", "revision_required"},
        "auto_tested": {
            "approved",
            "conditionally_approved",
            "rejected",
            "revision_required",
        },
        "approved": {"registered"},
        "conditionally_approved": {"registered", "rejected"},
        "revision_required": {"draft"},
        "sandbox_only": {"approved", "rejected"},
        "registered": set(),  # terminal state
        "rejected": set(),  # terminal state
    }

    def can_transition_to(self, new_state: str) -> bool:
        return new_state in self._VALID_TRANSITIONS.get(self.state, set())

    def transition_to(
        self, new_state: str, comment: str | None = None
    ) -> CodeExtensionSpec:
        if not self.can_transition_to(new_state):
            raise ValueError(f"Invalid transition: {self.state} → {new_state}")
        return self.model_copy(
            update={
                "state": new_state,
                "approval_comment": comment,
                "updated_at": datetime.now(UTC).isoformat(),
                **(
                    {
                        "approved_by": "expert",
                        "approved_at": datetime.now(UTC).isoformat(),
                    }
                    if new_state in ("approved", "conditionally_approved")
                    else {}
                ),
            }
        )


class CapabilityRegistry:
    """Registry of registered capabilities (code extensions that have been approved)."""

    def __init__(self) -> None:
        self._capabilities: dict[str, dict[str, Any]] = {}

    def register(self, extension: CodeExtensionSpec) -> None:
        """Register an approved extension as an available capability.

        The extension is indexed both by its ``extension_id`` and by its
        ``related_capability_id`` (when present) so that
        :meth:`has_capability` can detect whether a missing capability has
        been satisfied by a registered extension.
        """
        if extension.state not in (
            "approved",
            "conditionally_approved",
            "registered",
        ):
            raise ValueError(
                f"Cannot register extension in state '{extension.state}'"
            )
        data = {
            "extension_id": extension.extension_id,
            "extension_name": extension.extension_name,
            "extension_type": extension.extension_type,
            "description": extension.description,
            "required_inputs": extension.required_inputs,
            "expected_outputs": extension.expected_outputs,
            "generated_code": extension.generated_code,
            "related_capability_id": extension.related_capability_id,
        }
        self._capabilities[extension.extension_id] = data
        # Also index by related_capability_id so that resolve() can filter
        # capabilities that have been satisfied by a registered extension.
        if extension.related_capability_id:
            self._capabilities[extension.related_capability_id] = data

    def has_capability(self, capability_id: str) -> bool:
        return capability_id in self._capabilities

    def get_capability(self, capability_id: str) -> dict[str, Any] | None:
        return self._capabilities.get(capability_id)

    def list_capabilities(self) -> list[dict[str, Any]]:
        return list(self._capabilities.values())


__all__ = [
    "CapabilityRegistry",
    "CapabilityType",
    "CodeExtensionSpec",
    "MissingCapability",
]
