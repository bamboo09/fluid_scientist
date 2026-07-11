"""Capability registry health checks."""

from __future__ import annotations

from fluid_scientist.capabilities import (
    Capability,
    CapabilityRegistry,
    CapabilityStatus,
)


def test_health_check_reports_invalid_verified_entrypoint() -> None:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            capability_id="test.invalid_verified",
            capability_type="postprocessor",
            implementation_entrypoint="fluid_scientist.nope:missing",
            status=CapabilityStatus.VERIFIED,
        )
    )

    report = registry.health_check(mutate=False)
    record = next(
        item for item in report.records
        if item.capability_id == "test.invalid_verified"
    )

    assert not record.healthy
    assert record.status_before == CapabilityStatus.VERIFIED
    assert record.status_after == CapabilityStatus.VERIFIED
    assert record.issues[0].issue_code == "entrypoint_import_failed"


def test_health_check_can_degrade_invalid_verified_capability() -> None:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            capability_id="test.invalid_verified",
            capability_type="boundary_writer",
            implementation_entrypoint="fluid_scientist.nope:missing",
            status=CapabilityStatus.VERIFIED,
        )
    )

    report = registry.health_check(mutate=True)
    capability = registry.get_capability("test.invalid_verified")
    record = next(
        item for item in report.records
        if item.capability_id == "test.invalid_verified"
    )

    assert capability is not None
    assert capability.status == CapabilityStatus.UNVERIFIED
    assert record.status_before == CapabilityStatus.VERIFIED
    assert record.status_after == CapabilityStatus.UNVERIFIED
    assert report.degraded >= 1


def test_health_check_degrades_verified_without_evidence() -> None:
    registry = CapabilityRegistry()
    registry.register(
        Capability(
            capability_id="test.no_evidence",
            capability_type="postprocessor",
            implementation_entrypoint="math:sqrt",
            status=CapabilityStatus.VERIFIED,
        )
    )

    report = registry.health_check(mutate=True)
    capability = registry.get_capability("test.no_evidence")
    record = next(
        item for item in report.records
        if item.capability_id == "test.no_evidence"
    )

    assert capability is not None
    assert capability.status == CapabilityStatus.UNVERIFIED
    assert record.status_after == CapabilityStatus.UNVERIFIED
    assert {
        issue.issue_code for issue in record.issues
    } >= {"missing_test_manifest", "missing_verification_artifact"}
