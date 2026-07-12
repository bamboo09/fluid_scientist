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

from fluid_scientist.capabilities.dynamic_store import DynamicCapabilityStore
from fluid_scientist.capabilities.evidence import (
    EvidenceStore,
    TestManifest,
    VerificationArtifact,
)
from fluid_scientist.capabilities.registry import (
    Capability,
    CapabilityRegistry,
    CapabilityStatus,
)
from fluid_scientist.capabilities.resolution import (
    CapabilityRequirementGraph,
    CapabilityResolution,
)
from fluid_scientist.validation.openfoam import (
    OpenFOAMValidationReport,
    OpenFOAMValidationRunner,
    RemoteOpenFOAMValidationRunner,
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
    "ENVIRONMENT_BLOCKED",
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

    checkpoint_id: str = ""
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


class PipelineResumeRecord(BaseModel):
    """Evidence that the original pipeline resumed after capability extension."""

    checkpoint_id: str
    session_id: str
    study_id: str = ""
    draft_version: int = 1
    resumed_stage: str
    resolved_requirement_ids: list[str] = Field(default_factory=list)
    registered_capability_ids: list[str] = Field(default_factory=list)
    status: Literal["RESUMED", "ENVIRONMENT_BLOCKED", "FAILED"]


class UnknownCapabilityExecutionResult(BaseModel):
    """Result of executing extension records for a checkpoint."""

    checkpoint: PipelineCheckpoint
    extensions: list[ExtensionRunRecord] = Field(default_factory=list)
    verification_artifacts: list[str] = Field(default_factory=list)
    test_manifests: list[str] = Field(default_factory=list)
    registered_capability_ids: list[str] = Field(default_factory=list)
    resume: PipelineResumeRecord


class UnknownCapabilityOrchestrator:
    """Create extension specs and checkpoints for unresolved requirements."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        registry: CapabilityRegistry | None = None,
        validation_runner: OpenFOAMValidationRunner | None = None,
        config_executor: Any | None = None,
    ) -> None:
        from fluid_scientist.capabilities.config_extension import ConfigExtensionExecutor

        self.workspace_root = Path(workspace_root)
        self.registry = registry or CapabilityRegistry()
        self.validation_runner = validation_runner or RemoteOpenFOAMValidationRunner()
        self.config_executor = config_executor or ConfigExtensionExecutor()

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
            checkpoint_id=f"chk-{uuid4().hex[:12]}",
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

    def execute(self, checkpoint_id: str) -> UnknownCapabilityExecutionResult:
        """Execute pending extensions and persist resume/evidence records."""

        session_dir, checkpoint, records = self._load_checkpoint(checkpoint_id)
        evidence_store = EvidenceStore(session_dir / "evidence")
        dynamic_store = DynamicCapabilityStore(session_dir / "dynamic_capabilities.json")
        verification_artifacts: list[str] = []
        test_manifests: list[str] = []
        registered_capability_ids: list[str] = []
        executed_records: list[ExtensionRunRecord] = []
        blocked = False
        failed = False

        for record in records:
            if record.spec.extension_kind != "CONFIG_EXTENSION":
                failed_record = record.model_copy(update={
                    "status": "FAILED",
                    "error": "CODE_EXTENSION execution is not implemented in this pipeline.",
                    "logs": [*record.logs, "Skipped non-config extension."],
                })
                executed_records.append(failed_record)
                failed = True
                continue

            execution = self.config_executor.execute(
                record,
                run_openfoam=True,
                validation_runner=self.validation_runner,
            )
            artifact_status = _artifact_status(execution.validation_report)
            if artifact_status == "ENVIRONMENT_BLOCKED":
                blocked = True
                final_record = execution.record.model_copy(update={
                    "status": "ENVIRONMENT_BLOCKED",
                    "error": execution.record.error,
                })
            elif execution.record.status == "OPENFOAM_TESTED":
                capability = self._register_verified_capability(
                    execution.record,
                    verification_artifact=execution.verification_artifact,
                )
                health = self.registry.health_check(mutate=True)
                health_record = next(
                    item for item in health.records
                    if item.capability_id == capability.capability_id
                )
                if not health_record.healthy:
                    final_record = execution.record.model_copy(update={
                        "status": "FAILED",
                        "error": "Registered capability failed registry health check.",
                    })
                    failed = True
                else:
                    final_record = execution.record.model_copy(update={
                        "status": "REGISTERED",
                        "logs": [
                            *execution.record.logs,
                            f"Registered capability {capability.capability_id}.",
                        ],
                    })
                    registered_capability_ids.append(capability.capability_id)
            else:
                final_record = execution.record
                failed = True

            artifact_path, manifest_path = evidence_store.save(
                VerificationArtifact(
                    artifact_id=f"artifact-{final_record.extension_id}",
                    extension_id=final_record.extension_id,
                    capability_id=final_record.spec.capability_id,
                    requirement_id=final_record.requirement_id,
                    status=artifact_status,
                    case_dir=execution.case_dir,
                    validation_report=execution.validation_report,
                    generated_files=execution.generated_files,
                ),
                TestManifest(
                    manifest_id=f"manifest-{final_record.extension_id}",
                    extension_id=final_record.extension_id,
                    capability_id=final_record.spec.capability_id,
                    tests=["static_compile_readiness", "openfoam_minimal_case"],
                    fixtures=[execution.case_dir] if execution.case_dir else [],
                    commands=_openfoam_commands(execution.validation_report),
                    result=artifact_status,
                ),
            )
            verification_artifacts.append(artifact_path)
            test_manifests.append(manifest_path)
            executed_records.append(final_record.model_copy(update={
                "spec": final_record.spec.model_copy(update={
                    "generated_artifacts": [
                        *final_record.spec.generated_artifacts,
                        artifact_path,
                        manifest_path,
                    ],
                })
            }))

        if registered_capability_ids:
            dynamic_store.save_from(self.registry)

        status: Literal["RESUMED", "ENVIRONMENT_BLOCKED", "FAILED"]
        if blocked:
            status = "ENVIRONMENT_BLOCKED"
        elif failed:
            status = "FAILED"
        else:
            status = "RESUMED"

        resume = PipelineResumeRecord(
            checkpoint_id=checkpoint.checkpoint_id,
            session_id=checkpoint.session_id,
            study_id=checkpoint.study_id,
            draft_version=checkpoint.draft_version,
            resumed_stage="CAPABILITIES_RESOLVED",
            resolved_requirement_ids=[
                record.requirement_id
                for record in executed_records
                if record.status == "REGISTERED"
            ],
            registered_capability_ids=registered_capability_ids,
            status=status,
        )
        result = UnknownCapabilityExecutionResult(
            checkpoint=checkpoint.model_copy(update={
                "pipeline_stage": resume.resumed_stage if status == "RESUMED" else checkpoint.pipeline_stage,
                "unresolved_requirement_ids": [
                    requirement_id for requirement_id in checkpoint.unresolved_requirement_ids
                    if requirement_id not in resume.resolved_requirement_ids
                ],
            }),
            extensions=executed_records,
            verification_artifacts=verification_artifacts,
            test_manifests=test_manifests,
            registered_capability_ids=registered_capability_ids,
            resume=resume,
        )
        self._persist_execution(session_dir, result)
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

    def _load_checkpoint(
        self,
        checkpoint_id: str,
    ) -> tuple[Path, PipelineCheckpoint, list[ExtensionRunRecord]]:
        for checkpoint_path in self.workspace_root.rglob("pipeline_checkpoint.json"):
            checkpoint = PipelineCheckpoint.model_validate_json(
                checkpoint_path.read_text(encoding="utf-8")
            )
            if checkpoint.checkpoint_id == checkpoint_id or checkpoint.session_id == checkpoint_id:
                session_dir = checkpoint_path.parent
                records = [
                    ExtensionRunRecord.model_validate(item)
                    for item in json.loads(
                        (session_dir / "unknown_capability_extensions.json").read_text(
                            encoding="utf-8"
                        )
                    )
                ]
                return session_dir, checkpoint, records
        raise FileNotFoundError(f"No pipeline checkpoint found for {checkpoint_id!r}")

    def _persist_execution(
        self,
        session_dir: Path,
        result: UnknownCapabilityExecutionResult,
    ) -> None:
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
        (session_dir / "pipeline_resume.json").write_text(
            result.resume.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def _register_verified_capability(
        self,
        record: ExtensionRunRecord,
        *,
        verification_artifact: str,
    ) -> Capability:
        capability = Capability(
            capability_id=record.spec.capability_id,
            capability_type=record.spec.capability_type,
            name=record.spec.capability_id,
            description=record.spec.scientific_requirement,
            input_schema=record.spec.input_schema,
            output_schema=record.spec.output_schema,
            supported_versions=["openfoam13", "openfoam2412"],
            implementation_entrypoint=(
                "fluid_scientist.capabilities.generated_function_objects:"
                "residuals_function_object_config"
            ),
            test_manifest=[f"manifest:{record.extension_id}"],
            verification_artifact=verification_artifact,
            status=CapabilityStatus.VERIFIED,
            is_native=False,
            metadata={
                "extension_id": record.extension_id,
                "requirement_id": record.requirement_id,
                "extension_kind": record.spec.extension_kind,
            },
        )
        self.registry.register(capability)
        return capability


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _artifact_status(
    validation_report: dict[str, Any],
) -> Literal["PASSED", "FAILED", "ENVIRONMENT_BLOCKED"]:
    openfoam = _openfoam_report(validation_report)
    if openfoam and openfoam.error_code in {
        "WORKSTATION_PROFILE_REQUIRED",
        "WORKSTATION_NOT_READY",
        "LOCAL_OPENFOAM_NOT_FOUND",
    }:
        return "ENVIRONMENT_BLOCKED"
    if openfoam and not openfoam.passed:
        return "FAILED"
    if validation_report.get("openfoam_runner", {}).get("passed") is True:
        return "PASSED"
    return "FAILED"


def _openfoam_report(validation_report: dict[str, Any]) -> OpenFOAMValidationReport | None:
    payload = validation_report.get("openfoam_runner")
    if not isinstance(payload, dict):
        return None
    return OpenFOAMValidationReport.model_validate(payload)


def _openfoam_commands(validation_report: dict[str, Any]) -> list[str]:
    openfoam = _openfoam_report(validation_report)
    return openfoam.commands if openfoam else []


__all__ = [
    "ExtensionLifecycleStatus",
    "ExtensionRunRecord",
    "ExtensionSpec",
    "PipelineCheckpoint",
    "PipelineResumeRecord",
    "UnknownCapabilityOrchestrator",
    "UnknownCapabilityExecutionResult",
    "UnknownCapabilityResult",
]
