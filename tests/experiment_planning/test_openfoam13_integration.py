"""OpenFOAM 13 smoke tests; skipped when the Foundation toolchain is absent."""

import io
import os
import re
import shutil
import subprocess
import tarfile

import pytest

from fluid_scientist.adapters.openfoam_parsers import parse_check_mesh
from fluid_scientist.experiment_planning.compilers import compile_plan
from fluid_scientist.experiment_planning.models import ExperimentPlan

REQUIRED_COMMANDS = ("blockMesh", "mirrorMesh", "checkMesh", "foamRun")


def _foundation_13_status() -> tuple[bool, str]:
    missing = [command for command in REQUIRED_COMMANDS if shutil.which(command) is None]
    if missing:
        return False, "OpenFOAM Foundation 13 commands unavailable: " + ", ".join(missing)
    raw_version = os.environ.get("WM_PROJECT_VERSION", "").strip()
    if not raw_version:
        if shutil.which("foamVersion") is None:
            return False, "OpenFOAM Foundation version unavailable: foamVersion not found"
        result = subprocess.run(
            ("foamVersion",), capture_output=True, text=True, check=False
        )
        raw_version = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    version = "OpenFOAM-13" if raw_version == "13" else raw_version
    if version != "OpenFOAM-13":
        return False, f"OpenFOAM Foundation 13 required, found {version or 'unknown'}"
    return True, "OpenFOAM-13 toolchain available"


TOOLCHAIN_READY, TOOLCHAIN_REASON = _foundation_13_status()
openfoam13_only = pytest.mark.skipif(not TOOLCHAIN_READY, reason=TOOLCHAIN_REASON)


def _common(experiment_type: str) -> dict[str, object]:
    return {
        "experiment_name": "OpenFOAM smoke",
        "experiment_type": experiment_type,
        "objective": "Verify generated dictionaries with Foundation OpenFOAM 13.",
        "rationale": "Executable integration catches dictionary and topology defects.",
        "assumptions": ("Newtonian incompressible fluid",),
        "limitations": ("Smoke-run duration only",),
        "requested_outputs": ("residuals",),
        "convergence_targets": {
            "residual_tolerance": 1e-5,
            "mass_imbalance_percent": 1.0,
        },
    }


def _plans() -> tuple[ExperimentPlan, ...]:
    pipe = _common("laminar_pipe") | {
        "case": {
            "diameter_m": 0.02,
            "length_m": 0.2,
            "mean_velocity_m_s": 0.02,
            "kinematic_viscosity_m2_s": 1e-6,
            "axial_cells": 10,
            "radial_cells": 3,
        }
    }
    cylinder = _common("cylinder_flow") | {
        "requested_outputs": ("drag_coefficient",),
        "case": {
            "diameter_m": 0.1,
            "reynolds_number": 100.0,
            "domain_upstream_diameters": 5.0,
            "domain_downstream_diameters": 10.0,
            "domain_transverse_diameters": 4.0,
            "cells_radial": 16,
            "cells_wake": 40,
            "end_time_s": 0.01,
            "time_step_s": 0.001,
            "density_kg_m3": 1.0,
            "kinematic_viscosity_m2_s": 0.001,
            "mean_velocity_m_s": 1.0,
        },
    }
    cavity = _common("lid_driven_cavity") | {
        "case": {
            "side_length_m": 1.0,
            "lid_velocity_m_s": 1.0,
            "kinematic_viscosity_m2_s": 0.01,
            "density_kg_m3": 1.0,
            "cells_per_side": 8,
            "end_time_s": 0.01,
        }
    }
    return tuple(ExperimentPlan.model_validate(payload) for payload in (pipe, cylinder, cavity))


@pytest.mark.parametrize("plan", _plans(), ids=lambda plan: plan.root.experiment_type)
@openfoam13_only
def test_generated_case_runs_foundation_13_smoke_only_not_acceptance(plan, tmp_path) -> None:
    compiled = compile_plan(plan)
    case_root = tmp_path / plan.root.experiment_type
    case_root.mkdir()
    with tarfile.open(fileobj=io.BytesIO(compiled.archive), mode="r:gz") as archive:
        archive.extractall(case_root, filter="data")

    commands = [("blockMesh",)]
    if compiled.manifest.needs_mirror_mesh:
        commands.append(("mirrorMesh",))
    for command in commands:
        subprocess.run(command, cwd=case_root, check=True, capture_output=True, text=True)
    check_mesh = subprocess.run(
        ("checkMesh", "-allGeometry", "-allTopology"),
        cwd=case_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert parse_check_mesh(check_mesh.stdout, require_passed=True).passed is True

    control_path = case_root / "system" / "controlDict"
    control = control_path.read_text(encoding="utf-8")
    delta_match = re.search(r"\bdeltaT\s+([^;]+);", control)
    assert delta_match is not None
    control = re.sub(r"\bendTime\s+[^;]+;", f"endTime {delta_match.group(1)};", control)
    control_path.write_text(control, encoding="utf-8", newline="\n")
    subprocess.run(
        ("foamRun", "-solver", "incompressibleFluid"),
        cwd=case_root,
        check=True,
        capture_output=True,
        text=True,
    )
