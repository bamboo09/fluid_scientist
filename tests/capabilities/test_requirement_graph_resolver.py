"""Tests for requirement-graph based capability resolution."""

from __future__ import annotations

from fluid_scientist.capabilities import (
    Capability,
    CapabilityRegistry,
    CapabilityRequirement,
    CapabilityStatus,
    RequirementGraphResolver,
)


def test_resolver_uses_exact_verified_capability() -> None:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            capability_id="test.postprocessor.force_spectrum",
            capability_type="postprocessor",
            name="Force Spectrum",
            implementation_entrypoint="math:sqrt",
            test_manifest=["tests/capabilities/test_requirement_graph_resolver.py"],
            verification_artifact="sha256:test-artifact",
            status=CapabilityStatus.VERIFIED,
        )
    )
    requirement = CapabilityRequirement(
        requirement_id="req_force_spectrum",
        capability_type="postprocessor",
        capability_id="test.postprocessor.force_spectrum",
        scientific_reason="Need force spectrum from force coefficients.",
    )

    graph = RequirementGraphResolver(registry).resolve([requirement])

    assert not graph.unresolved
    resolution = graph.resolutions[0]
    assert resolution.status == "RESOLVED"
    assert resolution.strategy == "EXACT_MATCH"
    assert resolution.selected_capabilities[0].capability_id == requirement.capability_id


def test_resolver_composes_verified_capabilities_by_keywords() -> None:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            capability_id="test.boundary.velocity_inlet",
            capability_type="boundary_writer",
            name="Velocity Inlet",
            implementation_entrypoint="math:sqrt",
            test_manifest=["tests/capabilities/test_requirement_graph_resolver.py"],
            verification_artifact="sha256:test-artifact",
            status=CapabilityStatus.VERIFIED,
        )
    )
    registry.register(
        Capability(
            capability_id="test.boundary.pressure_outlet",
            capability_type="boundary_writer",
            name="Pressure Outlet",
            implementation_entrypoint="math:sqrt",
            test_manifest=["tests/capabilities/test_requirement_graph_resolver.py"],
            verification_artifact="sha256:test-artifact",
            status=CapabilityStatus.VERIFIED,
        )
    )
    requirement = CapabilityRequirement(
        requirement_id="req_bc_combo",
        capability_type="boundary_writer",
        keywords=["velocity_inlet", "pressure_outlet"],
        scientific_reason="Need a combined inlet/outlet patch plan.",
    )

    graph = RequirementGraphResolver(registry).resolve([requirement])

    assert not graph.unresolved
    resolution = graph.resolutions[0]
    assert resolution.status == "COMPOSED"
    assert resolution.strategy == "COMPOSED_VERIFIED_CAPABILITIES"
    assert len(resolution.selected_capabilities) == 2


def test_resolver_returns_extension_required_for_unknown_mandatory_capability() -> None:
    registry = CapabilityRegistry()
    requirement = CapabilityRequirement(
        requirement_id="req_new_motion",
        capability_type="motion_compiler",
        keywords=["nonexistent_motion_mode"],
        mandatory=True,
        scientific_reason="Need a motion compiler not present in registry.",
    )

    graph = RequirementGraphResolver(registry).resolve([requirement])

    assert len(graph.unresolved) == 1
    resolution = graph.unresolved[0]
    assert resolution.status == "CODE_EXTENSION_REQUIRED"
    assert resolution.strategy == "NEW_EXTENSION_SPEC"
    assert resolution.extension_required is True


def test_config_extension_pending_is_unresolved_not_extended() -> None:
    registry = CapabilityRegistry()
    requirement = CapabilityRequirement(
        requirement_id="req_custom_function_object",
        capability_type="function_object_generator",
        keywords=["custom_phase_difference_probe"],
        mandatory=True,
        scientific_reason="Need a new functionObject dictionary composition.",
    )

    graph = RequirementGraphResolver(registry).resolve([requirement])

    assert len(graph.unresolved) == 1
    resolution = graph.unresolved[0]
    assert resolution.status == "CONFIG_EXTENSION_PENDING"
    assert resolution.strategy == "OPENFOAM_CONFIG_EXTENSION"
    assert resolution.extension_required is True
