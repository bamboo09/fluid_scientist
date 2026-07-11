"""Unknown capability orchestration checkpoints."""

from __future__ import annotations

import json

from fluid_scientist.capabilities import (
    CapabilityRegistry,
    CapabilityRequirement,
    RequirementGraphResolver,
    UnknownCapabilityOrchestrator,
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
