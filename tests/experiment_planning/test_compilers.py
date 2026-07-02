import gzip
import io
import tarfile
from dataclasses import FrozenInstanceError, replace

import pytest

from fluid_scientist.adapters.custom_openfoam import validate_custom_case_archive
from fluid_scientist.experiment_planning.compilers import (
    CompiledCase,
    UnsupportedCompilation,
    compile_plan,
)
from fluid_scientist.experiment_planning.models import (
    CavityExperimentPlan,
    CustomExperimentPlan,
    CylinderExperimentPlan,
    PipeExperimentPlan,
)


def common(experiment_type: str) -> dict[str, object]:
    return {
        "experiment_name": "Deterministic case",
        "experiment_type": experiment_type,
        "objective": "Resolve the requested incompressible flow response.",
        "rationale": "This bounded benchmark has reproducible numerical inputs.",
        "assumptions": ("Newtonian incompressible fluid",),
        "limitations": ("Two-dimensional or axisymmetric idealization",),
        "requested_outputs": ("residuals",),
        "convergence_targets": {
            "residual_tolerance": 1e-6,
            "mass_imbalance_percent": 0.1,
        },
    }


def pipe_plan(**case_updates: object) -> PipeExperimentPlan:
    case = {
        "diameter_m": 0.02,
        "length_m": 1.0,
        "mean_velocity_m_s": 0.05,
        "kinematic_viscosity_m2_s": 1e-6,
        "density_kg_m3": 998.2,
        "axial_cells": 80,
        "radial_cells": 10,
    } | case_updates
    return PipeExperimentPlan.model_validate(
        common("laminar_pipe")
        | {"requested_outputs": ("pressure_drop", "mass_imbalance"), "case": case}
    )


def cylinder_plan(**case_updates: object) -> CylinderExperimentPlan:
    case = {
        "diameter_m": 0.1,
        "reynolds_number": 100.0,
        "domain_upstream_diameters": 10.0,
        "domain_downstream_diameters": 20.0,
        "domain_transverse_diameters": 10.0,
        "cells_radial": 32,
        "cells_wake": 120,
        "end_time_s": 20.0,
        "time_step_s": 0.002,
        "density_kg_m3": 1.0,
        "kinematic_viscosity_m2_s": 0.001,
        "mean_velocity_m_s": 1.0,
    } | case_updates
    return CylinderExperimentPlan.model_validate(
        common("cylinder_flow")
        | {
            "requested_outputs": (
                "drag_coefficient",
                "lift_coefficient",
                "strouhal_number",
            ),
            "case": case,
        }
    )


def cavity_plan(**case_updates: object) -> CavityExperimentPlan:
    case = {
        "side_length_m": 1.0,
        "lid_velocity_m_s": 1.0,
        "kinematic_viscosity_m2_s": 0.001,
        "density_kg_m3": 1.0,
        "cells_per_side": 64,
        "end_time_s": 10.0,
    } | case_updates
    return CavityExperimentPlan.model_validate(
        common("lid_driven_cavity")
        | {"requested_outputs": ("velocity_probes", "residuals"), "case": case}
    )


def custom_plan() -> CustomExperimentPlan:
    return CustomExperimentPlan.model_validate(
        common("custom_openfoam")
        | {
            "requested_outputs": ("outlet_velocity",),
            "case": {
                "geometry": "A bounded custom three-dimensional diffuser geometry.",
                "boundary_conditions": ("fixed inlet velocity", "fixed outlet pressure"),
                "mesh_strategy": "Hex mesh supplied in a separately reviewed archive.",
                "run_strategy": "Steady solve followed by reviewed metric extraction.",
            },
        }
    )


def archive_files(payload: bytes) -> dict[str, str]:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        return {
            member.name: archive.extractfile(member).read().decode("utf-8")
            for member in archive.getmembers()
            if member.isfile()
        }


@pytest.mark.parametrize("plan", [pipe_plan(), cylinder_plan(), cavity_plan()])
def test_compilation_is_deterministic_safe_and_immutable(plan: object) -> None:
    first = compile_plan(plan)
    second = compile_plan(plan)
    validated = validate_custom_case_archive(first.archive)

    assert isinstance(first, CompiledCase)
    assert first.archive == second.archive
    assert first.archive_sha256 == second.archive_sha256 == validated.archive_sha256
    assert first.manifest == validated
    assert first.archive_sha256.startswith("sha256:")
    with pytest.raises(FrozenInstanceError):
        first.experiment_type = "changed"  # type: ignore[misc]


def test_pipe_adapts_renderer_and_preserves_analytical_function_objects() -> None:
    compiled = compile_plan(pipe_plan())
    files = archive_files(compiled.archive)

    assert set(files) == {
        "0/U",
        "0/p",
        "constant/momentumTransport",
        "constant/physicalProperties",
        "system/blockMeshDict",
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
    }
    assert "pressureDrop" in files["system/controlDict"]
    assert "inletFlow" in files["system/controlDict"]
    assert "outletFlow" in files["system/controlDict"]
    assert "flowRateInletVelocity" in files["0/U"]
    assert "codedFixedValue" not in files["0/U"]
    assert compiled.preprocessing == ("blockMesh", "checkMesh")


def test_cylinder_contains_mirrored_laminar_case_and_force_outputs() -> None:
    compiled = compile_plan(cylinder_plan())
    files = archive_files(compiled.archive)

    assert compiled.manifest.needs_block_mesh is True
    assert compiled.manifest.needs_mirror_mesh is True
    assert compiled.preprocessing == ("blockMesh", "mirrorMesh", "checkMesh")
    assert "solver          incompressibleFluid;" in files["system/controlDict"]
    assert "forces" in files["system/controlDict"]
    assert "forceCoeffs" in files["system/controlDict"]
    assert "residuals" in files["system/controlDict"]
    assert "simulationType laminar;" in files["constant/momentumTransport"]
    assert "mirrorPlane" in files["system/mirrorMeshDict"]
    assert not {"0/k", "0/omega", "0/nut"} & files.keys()


def test_cavity_contains_moving_lid_probes_without_mirror_mesh() -> None:
    compiled = compile_plan(cavity_plan())
    files = archive_files(compiled.archive)

    assert compiled.manifest.needs_mirror_mesh is False
    assert "system/mirrorMeshDict" not in files
    assert "movingLid" in files["0/U"]
    assert "uniform (1 0 0)" in files["0/U"]
    assert "probes" in files["system/controlDict"]
    assert "residuals" in files["system/controlDict"]


@pytest.mark.parametrize(
    ("original", "changed", "member", "needle"),
    [
        (pipe_plan(), pipe_plan(length_m=2.0), "system/blockMeshDict", "length 2"),
        (
            cylinder_plan(),
            cylinder_plan(
                reynolds_number=200.0,
                mean_velocity_m_s=2.0,
            ),
            "0/U",
            "uniform (2 0 0)",
        ),
        (cavity_plan(), cavity_plan(lid_velocity_m_s=2.0), "0/U", "uniform (2 0 0)"),
    ],
)
def test_plan_parameter_changes_relevant_text_and_digest(
    original: object, changed: object, member: str, needle: str
) -> None:
    before = compile_plan(original)
    after = compile_plan(changed)

    assert before.archive_sha256 != after.archive_sha256
    assert needle in archive_files(after.archive)[member]


@pytest.mark.parametrize("plan", [pipe_plan(), cylinder_plan(), cavity_plan()])
def test_archive_names_metadata_and_line_endings_are_canonical(plan: object) -> None:
    compiled = compile_plan(plan)
    assert gzip.decompress(compiled.archive)
    with tarfile.open(fileobj=io.BytesIO(compiled.archive), mode="r:gz") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(member.name for member in members)
        for member in members:
            assert member.isfile()
            assert not member.name.startswith(("/", "../"))
            assert "/../" not in member.name
            assert member.mode == 0o644
            assert member.uid == member.gid == member.mtime == 0
            assert member.uname == member.gname == ""
            raw = archive.extractfile(member).read()
            assert b"\r" not in raw
            raw.decode("utf-8")


def test_custom_plan_cannot_be_compiled_as_a_builtin() -> None:
    with pytest.raises(UnsupportedCompilation, match="upload"):
        compile_plan(custom_plan())


def test_compiled_case_rejects_mutation_by_replacement_only() -> None:
    compiled = compile_plan(cavity_plan())

    changed = replace(compiled, experiment_type="lid_driven_cavity")

    assert changed == compiled
