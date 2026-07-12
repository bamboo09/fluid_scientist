"""Unknown capability orchestration checkpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fluid_scientist.capabilities import (
    CapabilityRegistry,
    CapabilityRequirement,
    CapabilityStatus,
    RequirementGraphResolver,
    UnknownCapabilityOrchestrator,
)
from fluid_scientist.capabilities.dynamic_store import DynamicCapabilityStore
from fluid_scientist.validation.openfoam import (
    OpenFOAMValidationReport,
    OpenFOAMValidationRequest,
    RemoteOpenFOAMValidationRunner,
)
from fluid_scientist.workstations.profile_store import WorkstationProfileStore


class PassingOpenFOAMRunner:
    def validate(self, request: OpenFOAMValidationRequest) -> OpenFOAMValidationReport:
        return OpenFOAMValidationReport(
            runner="remote",
            passed=True,
            profile_id="ws-ready",
            openfoam_version="13",
            artifact_hash="sha256:case",
            commands=request.commands,
            exit_codes=[0 for _ in request.commands],
            expected_outputs=request.expected_outputs,
            actual_outputs=request.expected_outputs,
        )


def test_orchestrator_persists_checkpoint_and_extension_specs(tmp_path) -> None:
    registry = CapabilityRegistry()
    requirements = [
        CapabilityRequirement(
            requirement_id="req_function_object",
            capability_type="function_object_generator",
            keywords=["phase_difference_probe"],
            description="Need a phase-difference functionObject configuration.",
            mandatory=True,
        ),
        CapabilityRequirement(
            requirement_id="req_motion",
            capability_type="motion_compiler",
            keywords=["compound_nonexistent_motion"],
            description="Need a new motion compiler.",
            mandatory=True,
        ),
    ]
    graph = RequirementGraphResolver(registry).resolve(requirements)

    result = UnknownCapabilityOrchestrator(tmp_path).orchestrate(
        session_id="session-001",
        scientific_intent={"research_objective": "probe phase lag"},
        simulation_plan={"solver_plan": {"solver": "pimpleFoam"}},
        requirement_graph=graph,
        case_plan={"case": "minimal"},
    )

    assert not result.all_registered
    assert result.checkpoint.pipeline_stage == "EXTENDING_CAPABILITIES"
    assert result.checkpoint.unresolved_requirement_ids == [
        "req_function_object",
        "req_motion",
    ]
    assert [record.status for record in result.extensions] == [
        "PROPOSED",
        "PROPOSED",
    ]
    assert result.extensions[0].spec.extension_kind == "CONFIG_EXTENSION"
    assert result.extensions[1].spec.extension_kind == "CODE_EXTENSION"

    checkpoint_path = tmp_path / "session-001" / "pipeline_checkpoint.json"
    extensions_path = (
        tmp_path / "session-001" / "unknown_capability_extensions.json"
    )
    assert checkpoint_path.exists()
    assert extensions_path.exists()
    assert json.loads(checkpoint_path.read_text(encoding="utf-8"))[
        "requirement_graph_hash"
    ].startswith("sha256:")


def test_orchestrator_execute_registers_verified_config_extension_and_resumes(tmp_path) -> None:
    registry = CapabilityRegistry()
    graph = RequirementGraphResolver(registry).resolve([
        CapabilityRequirement(
            requirement_id="req_function_object",
            capability_type="function_object_generator",
            keywords=["phase_difference_probe"],
            description="Need a phase-difference functionObject configuration.",
            mandatory=True,
        )
    ])
    orchestrator = UnknownCapabilityOrchestrator(
        tmp_path,
        registry=registry,
        validation_runner=PassingOpenFOAMRunner(),
    )
    proposed = orchestrator.orchestrate(
        session_id="session-002",
        study_id="study-123",
        draft_version=4,
        scientific_intent={"research_objective": "probe phase lag"},
        simulation_plan={"solver_plan": {"solver": "icoFoam"}},
        requirement_graph=graph,
    )

    executed = orchestrator.execute(proposed.checkpoint.checkpoint_id)

    assert executed.resume.status == "RESUMED"
    assert executed.resume.study_id == "study-123"
    assert executed.resume.draft_version == 4
    assert executed.checkpoint.unresolved_requirement_ids == []
    assert executed.extensions[0].status == "REGISTERED"
    capability_id = executed.registered_capability_ids[0]
    capability = registry.get_capability(capability_id)
    assert capability is not None
    assert capability.status == CapabilityStatus.VERIFIED
    assert capability.verification_artifact.startswith("sha256:")
    assert all(Path(path).exists() for path in executed.verification_artifacts)
    assert all(Path(path).exists() for path in executed.test_manifests)

    reloaded = CapabilityRegistry()
    DynamicCapabilityStore(
        tmp_path / "session-002" / "dynamic_capabilities.json"
    ).load_into(reloaded)
    assert reloaded.get_capability(capability_id) is not None


def test_orchestrator_execute_marks_missing_workstation_as_environment_blocked(tmp_path) -> None:
    registry = CapabilityRegistry()
    graph = RequirementGraphResolver(registry).resolve([
        CapabilityRequirement(
            requirement_id="req_function_object",
            capability_type="function_object_generator",
            keywords=["phase_difference_probe"],
            mandatory=True,
        )
    ])
    store = WorkstationProfileStore(db_path=str(tmp_path / "profiles.db"))

    orchestrator = UnknownCapabilityOrchestrator(
        tmp_path,
        registry=registry,
        validation_runner=RemoteOpenFOAMValidationRunner(store=store),
    )
    proposed = orchestrator.orchestrate(
        session_id="session-003",
        scientific_intent={"research_objective": "probe phase lag"},
        simulation_plan={"solver_plan": {"solver": "icoFoam"}},
        requirement_graph=graph,
    )

    executed = orchestrator.execute(proposed.checkpoint.checkpoint_id)

    assert executed.resume.status == "ENVIRONMENT_BLOCKED"
    assert executed.extensions[0].status == "ENVIRONMENT_BLOCKED"
    assert not executed.registered_capability_ids
