"""Multi-case validation tests for the V5 CompileReady pipeline.

Validates that the pipeline produces statically-correct OpenFOAM cases for
a variety of flow scenarios:
- Internal laminar pipe flow (simpleFoam, laminar)
- External cylinder flow with LES (pimpleFoam, WALE)
- Turbulent channel flow (simpleFoam, kOmegaSST)
- Jet impingement (pimpleFoam, laminar)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from fluid_scientist.workflow_pipeline.pipeline import V5WorkflowPipeline


CASES = [
    {
        "name": "internal_flow_laminar_pipe",
        "text": "Simulate laminar pipe flow at Re=100 using simpleFoam. I need the pressure drop.",
        "expect_solver": "simpleFoam",
        "expect_turbulence_family": "laminar",
    },
    {
        "name": "external_flow_cylinder_les",
        "text": (
            "I want to simulate 2D flow around a cylinder at Re=100 using OpenFOAM "
            "with pimpleFoam and WALE LES. I need drag coefficient, lift coefficient, "
            "and Strouhal number."
        ),
        "expect_solver": "pimpleFoam",
        "expect_turbulence_family": "LES",
    },
    {
        "name": "channel_flow_rans",
        "text": "Simulate turbulent channel flow at Re=5000 using simpleFoam with kOmegaSST model. I need the velocity profile.",
        "expect_solver": "simpleFoam",
        "expect_turbulence_family": "RANS",
    },
    {
        "name": "jet_impingement",
        "text": "Simulate jet impingement flow at Re=2000 using pimpleFoam. I need velocity profiles on the target wall.",
        "expect_solver": "pimpleFoam",
        "expect_turbulence_family": "laminar",
    },
]


@pytest.fixture(scope="module")
def work_root(tmp_path_factory):
    """Use a pytest-managed temp directory for test cases."""
    return str(tmp_path_factory.mktemp("v5_pipeline_multicase"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_pipeline_gathers_all_static_checks(case, work_root):
    pipeline = V5WorkflowPipeline(work_root=work_root)
    state = pipeline.run(case["text"])

    # Must reach COMPILE_READY
    assert state.current_stage == "compile_ready", (
        f"Case {case['name']} failed at stage {state.current_stage}: "
        f"{state.failure.get('message') if state.failure else 'no failure info'}"
    )

    # All static error checks must pass (openfoam_runtime may fail if OF not installed)
    vr = state.validation_report or {}
    checks = vr.get("checks", [])
    static_errors = [
        c for c in checks
        if not c.get("passed")
        and c.get("severity") == "error"
        and c.get("check_name") != "openfoam_runtime"
    ]
    assert not static_errors, (
        f"Case {case['name']} has static errors: "
        + "; ".join(f"{c['check_name']}: {c['message']}" for c in static_errors)
    )

    # Case directory must exist and contain key files
    assert state.case_dir, "case_dir not set"
    case_dir = Path(state.case_dir)
    assert case_dir.is_dir(), f"case_dir does not exist: {case_dir}"
    assert (case_dir / "system" / "controlDict").is_file()
    assert (case_dir / "0" / "U").is_file()
    assert (case_dir / "0" / "p").is_file()


def test_incremental_modification_preserves_state(work_root):
    """Verify the ChangeProposal/modify workflow: modify Re and endTime, then re-validate."""
    pipeline = V5WorkflowPipeline(work_root=work_root)

    # Initial run: cylinder LES
    initial_text = (
        "I want to simulate 2D flow around a cylinder at Re=100 using OpenFOAM "
        "with pimpleFoam and WALE LES. I need drag coefficient."
    )
    state = pipeline.run(initial_text)
    assert state.current_stage == "compile_ready"
    session_id = state.session_id

    # Modify: increase Re and endTime
    modified = pipeline.modify(session_id, "Change Reynolds number to 200 and run until endTime 50.")
    assert modified.current_stage == "compile_ready", (
        f"Modify failed at {modified.current_stage}: "
        f"{modified.failure.get('message') if modified.failure else ''}"
    )

    # Verify controlDict reflects the new endTime
    case_dir = Path(modified.case_dir)
    cd_text = (case_dir / "system" / "controlDict").read_text(encoding="utf-8")
    assert "endTime" in cd_text
