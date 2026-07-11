"""Config extension executor tests."""

from __future__ import annotations

from pathlib import Path

from fluid_scientist.capabilities import (
    CapabilityRegistry,
    CapabilityRequirement,
    RequirementGraphResolver,
    UnknownCapabilityOrchestrator,
)
from fluid_scientist.capabilities.config_extension import ConfigExtensionExecutor


def _config_extension_record(tmp_path):
    registry = CapabilityRegistry()
    graph = RequirementGraphResolver(registry).resolve([
        CapabilityRequirement(
            requirement_id="req_phase_probe",
            capability_type="function_object_generator",
            keywords=["phase_probe"],
            description="Need a functionObject config for phase probe output.",
            mandatory=True,
        )
    ])
    result = UnknownCapabilityOrchestrator(tmp_path).orchestrate(
        session_id="session-001",
        scientific_intent={"research_objective": "phase lag"},
        simulation_plan={"measurement_plan": {"primitive": "phase difference"}},
        requirement_graph=graph,
    )
    return result.extensions[0]


def test_config_extension_generates_static_valid_minimal_case(tmp_path) -> None:
    record = _config_extension_record(tmp_path)

    result = ConfigExtensionExecutor().execute(record, run_openfoam=False)

    assert result.record.status == "STATIC_VALIDATED"
    case_dir = Path(result.case_dir)
    assert (case_dir / "system" / "controlDict").is_file()
    assert (case_dir / "system" / "blockMeshDict").is_file()
    assert (case_dir / "0" / "U").is_file()
    assert (case_dir / "0" / "p").is_file()
    assert "functions" in (case_dir / "system" / "controlDict").read_text(
        encoding="utf-8"
    )
    checks = result.validation_report["checks"]
    static_errors = [
        check for check in checks
        if (
            not check["passed"]
            and check["severity"] == "error"
            and check["check_name"] != "openfoam_runtime"
        )
    ]
    assert not static_errors


def test_config_extension_does_not_register_or_verify_without_openfoam(tmp_path) -> None:
    record = _config_extension_record(tmp_path)

    result = ConfigExtensionExecutor().execute(record, run_openfoam=True)

    if result.validation_report.get("openfoam_available"):
        assert result.record.status in {"OPENFOAM_TESTED", "FAILED"}
    else:
        assert result.record.status == "FAILED"
        assert not result.verification_artifact
        assert "openfoam_runtime" in result.record.error
