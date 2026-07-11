"""Unknown capability lifecycle orchestration.

This module owns the state machine between a capability requirement graph and
future config/code extension executors.  It deliberately refuses to register
anything until an executor has produced validation artifacts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from fluid_scientist.capabilities.resolution import (
    CapabilityRequirementGraph,
    CapabilityResolution,
)


ExtensionLifecycleStatus = Literal[
    "PROPOSED",
    "GENERATING",
    "GENERATED",
    "STATIC_VALIDATED",
    "UNIT_TESTED",
    "OPENFOAM_TESTED",
    "VERIFIED",
    "REGISTERED",
    "FAILED",
    "ROLLED_BACK",
]

ExtensionKind = Literal["CONFIG_EXTENSION", "CODE_EXTENSION"]


class ExtensionSpec(BaseModel):
    """Executable contract for generating and validating one capability."""

    extension_id: str
    capability_id: str
    capability_type: str
    extension_kind: ExtensionKind
    scientific_requirement: str = ""
    openfoam_requirement: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_files: list[str] = Field(default_factory=list)
    forbidden_files: list[str] = Field(default_factory=list)
    required_dependencies: list[str] = Field(default_factory=list)
    generated_artifacts: list[str] = Field(default_factory=list)
    minimal_test_case: dict[str, Any] = Field(default_factory=dict)
    unit_test_plan: list[dict[str, Any]] = Field(default_factory=list)
    openfoam_test_plan: list[dict[str, Any]] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    rollback_plan: dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"


class ExtensionRunRecord(BaseModel):
    """Persisted lifecycle record for a single extension attempt."""

    extension_id: str
    requirement_id: str
    status: ExtensionLifecycleStatus = "PROPOSED"
    workspace: str = ""
    spec: ExtensionSpec
    error: str = ""
    logs: list[str] = Field(default_factory=list)


class PipelineCheckpoint(BaseModel):
    """Checkpoint needed to resume the original study after registration."""

    session_id: str
    study_id: str = ""
    draft_version: int = 1
    pipeline_stage: str
    unresolved_requirement_ids: list[str] = Field(default_factory=list)
    extension_ids: list[str] = Field(default_factory=list)
    scientific_intent_hash: str = ""
    simulation_plan_hash: str = ""
    requirement_graph_hash: str = ""
    case_plan_hash: str = ""


class UnknownCapabilityResult(BaseModel):
    """Result of one orchestration pass."""

    checkpoint: PipelineCheckpoint
    extensions: list[ExtensionRunRecord] = Field(default_factory=list)

    @property
    def all_registered(self) -> bool:
        return bool(self.extensions) and all(
            record.status == "REGISTERED" for record in self.extensions
        )


class UnknownCapabilityOrchestrator:
    """Create extension specs and checkpoints for unresolved requirements."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root)

    def orchestrate(
        self,
        *,
        session_id: str,
        study_id: str = "",
        draft_version: int = 1,
        scientific_intent: dict[str, Any],
        simulation_plan: dict[str, Any],
        requirement_graph: CapabilityRequirementGraph,
        case_plan: dict[str, Any] | None = None,
    ) -> UnknownCapabilityResult:
        unresolved = [
            resolution for resolution in requirement_graph.unresolved
            if resolution.requirement.mandatory
        ]
        extension_records = [
            self._propose_extension(session_id, resolution)
            for resolution in unresolved
        ]
        checkpoint = PipelineCheckpoint(
            session_id=session_id,
            study_id=study_id,
            draft_version=draft_version,
            pipeline_stage="EXTENDING_CAPABILITIES",
            unresolved_requirement_ids=[
                record.requirement_id for record in extension_records
            ],
            extension_ids=[record.extension_id for record in extension_records],
            scientific_intent_hash=_stable_hash(scientific_intent),
            simulation_plan_hash=_stable_hash(simulation_plan),
            requirement_graph_hash=_stable_hash(requirement_graph.model_dump()),
            case_plan_hash=_stable_hash(case_plan or {}),
        )
        result = UnknownCapabilityResult(
            checkpoint=checkpoint,
            extensions=extension_records,
        )
        self._persist(session_id, result)
        return result

    def _propose_extension(
        self,
        session_id: str,
        resolution: CapabilityResolution,
    ) -> ExtensionRunRecord:
        requirement = resolution.requirement
        extension_id = f"ext-{uuid4().hex[:12]}"
        kind: ExtensionKind = (
            "CONFIG_EXTENSION"
            if resolution.status == "CONFIG_EXTENSION_PENDING"
            else "CODE_EXTENSION"
        )
        workspace = self.workspace_root / session_id / "extensions" / extension_id
        workspace.mkdir(parents=True, exist_ok=True)
        spec = ExtensionSpec(
            extension_id=extension_id,
            capability_id=requirement.capability_id
            or f"generated.{requirement.capability_type}.{extension_id}",
            capability_type=requirement.capability_type,
            extension_kind=kind,
            scientific_requirement=(
                requirement.scientific_reason or requirement.description
            ),
            openfoam_requirement=json.dumps(
                requirement.openfoam_mapping,
                sort_keys=True,
            ),
            input_schema=requirement.input_contract or requirement.required_input,
            output_schema=requirement.output_contract or requirement.expected_output,
            allowed_files=[
                "extensions/",
                "tests/",
                "minimal_case/0/",
                "minimal_case/constant/",
                "minimal_case/system/",
            ],
            forbidden_files=[
                "src/fluid_scientist/capabilities/",
                "src/fluid_scientist/case_generation/validator.py",
                ".env",
                ".ssh",
            ],
            minimal_test_case={"required": True, "openfoam_validation": True},
            unit_test_plan=[
                {
                    "name": "entrypoint_contract",
                    "purpose": "Verify generated entrypoint schema contract.",
                }
            ],
            openfoam_test_plan=[
                {
                    "name": "minimal_case_validation",
                    "commands": ["blockMesh", "checkMesh", "foamRun"],
                }
            ],
            acceptance_criteria=[
                "entrypoint imports",
                "unit tests pass",
                "minimal OpenFOAM case validates",
                "verification artifact saved",
            ],
            rollback_plan={"delete_workspace": str(workspace)},
        )
        return ExtensionRunRecord(
            extension_id=extension_id,
            requirement_id=requirement.requirement_id,
            status="PROPOSED",
            workspace=str(workspace),
            spec=spec,
            logs=["ExtensionSpec proposed; executor has not run yet."],
        )

    def _persist(self, session_id: str, result: UnknownCapabilityResult) -> None:
        session_dir = self.workspace_root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pipeline_checkpoint.json").write_text(
            result.checkpoint.model_dump_json(indent=2),
            encoding="utf-8",
        )
        (session_dir / "unknown_capability_extensions.json").write_text(
            json.dumps(
                [record.model_dump() for record in result.extensions],
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


__all__ = [
    "ExtensionLifecycleStatus",
    "ExtensionRunRecord",
    "ExtensionSpec",
    "PipelineCheckpoint",
    "UnknownCapabilityOrchestrator",
    "UnknownCapabilityResult",
]
