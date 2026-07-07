import gzip
import io
import json
import re
import tarfile
from dataclasses import FrozenInstanceError, replace

import pytest

from fluid_scientist.adapters.custom_openfoam import validate_custom_case_archive
from fluid_scientist.experiment_planning.compilers import (
    CompilationError,
    CompiledCase,
    UnsupportedCompilation,
    compile_plan,
)
from fluid_scientist.experiment_planning.models import (
    CavityExperimentPlan,
    ConvergenceTargets,
    CustomExperimentPlan,
    CylinderExperimentPlan,
    ParameterSweep,
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
        "fluidScientist/plan.json",
        "system/blockMeshDict",
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
    }
    assert "pressureDrop" in files["system/controlDict"]
    assert "inletFlow" in files["system/controlDict"]
    assert "outletFlow" in files["system/controlDict"]
    assert "nonuniform List<vector>" in files["0/U"]
    assert "codedFixedValue" not in files["0/U"]
    assert compiled.preprocessing == ("blockMesh", "checkMesh")


@pytest.mark.parametrize("plan", [pipe_plan(), cylinder_plan(), cavity_plan()])
def test_builtin_archives_have_no_runtime_dictionary_directives(plan: object) -> None:
    files = archive_files(compile_plan(plan).archive)

    combined = "\n".join(files.values())
    for forbidden in ("#includeEtc", "#include", "#calc", "#neg"):
        assert forbidden not in combined


@pytest.mark.parametrize(
    ("plan", "expected_libraries"),
    [
        (
            pipe_plan(),
            {"libfieldFunctionObjects.so", "libutilityFunctionObjects.so"},
        ),
        (cylinder_plan(), {"libforces.so", "libutilityFunctionObjects.so"}),
        (cavity_plan(), {"libsampling.so", "libutilityFunctionObjects.so"}),
    ],
)
def test_builtin_archives_retain_trusted_foundation_libraries_and_revalidate(
    plan: object, expected_libraries: set[str]
) -> None:
    compiled = compile_plan(plan)
    combined = "\n".join(archive_files(compiled.archive).values())

    assert all(f'libs ("{library}");' in combined for library in expected_libraries)
    assert validate_custom_case_archive(compiled.archive).archive_sha256 == compiled.digest


def test_pipe_inlet_is_face_matched_area_averaged_parabolic_profile() -> None:
    plan = pipe_plan(radial_cells=10, mean_velocity_m_s=0.05)
    velocity = archive_files(compile_plan(plan).archive)["0/U"]
    profile = [
        float(value)
        for value in re.findall(r"\(([-+0-9.eE]+) 0 0\)", velocity)[1:]
    ]

    assert "type fixedValue;" in velocity
    assert "nonuniform List<vector>" in velocity
    assert len(profile) == 10
    assert profile[0] == pytest.approx(0.0995)
    assert profile[-1] == pytest.approx(0.0095)
    area_weights = [2 * index + 1 for index in range(10)]
    weighted_mean = sum(v * w for v, w in zip(profile, area_weights, strict=True)) / sum(
        area_weights
    )
    assert weighted_mean == pytest.approx(0.05)


def test_pipe_convergence_targets_are_compiled_into_case_and_digest() -> None:
    original = pipe_plan()
    changed = original.model_copy(
        update={
            "convergence_targets": ConvergenceTargets(
                residual_tolerance=2e-6,
                mass_imbalance_percent=0.25,
            )
        }
    )

    before = compile_plan(original)
    after = compile_plan(changed)
    files = archive_files(after.archive)

    assert before.archive_sha256 != after.archive_sha256
    assert "p               2e-06;" in files["system/fvSolution"]
    assert "U               2e-06;" in files["system/fvSolution"]
    metadata = json.loads(files["fluidScientist/plan.json"])
    assert metadata["convergence_targets"] == {
        "mass_imbalance_percent": 0.25,
        "residual_tolerance": 2e-6,
    }
    assert "convergenceTargets" not in files["system/controlDict"]


def test_parameter_sweeps_are_recorded_but_base_case_is_compiled() -> None:
    original = pipe_plan()
    swept = original.model_copy(
        update={
            "parameter_sweeps": (
                ParameterSweep(parameter="length_m", values=(1.5, 2.0)),
            )
        }
    )

    before = compile_plan(original)
    after = compile_plan(swept)
    metadata = json.loads(archive_files(after.archive)["fluidScientist/plan.json"])

    assert before.archive_sha256 != after.archive_sha256
    assert metadata["compilation"] == {
        "mode": "approved_base_case",
        "sweep_expansion_owner": "task7",
    }
    assert metadata["parameter_sweeps"] == [
        {"parameter": "length_m", "values": [1.5, 2.0]}
    ]


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
    assert "planeType       pointAndNormal;" in files["system/mirrorMeshDict"]
    assert not {"0/k", "0/omega", "0/nut"} & files.keys()


def test_mirror_mesh_uses_foundation_13_top_level_plane_syntax() -> None:
    mirror = archive_files(compile_plan(cylinder_plan()).archive)["system/mirrorMeshDict"]

    assert "planeType       pointAndNormal;" in mirror
    assert "pointAndNormalDict\n{" in mirror
    assert "point       (0 0 0);" in mirror
    assert "normal      (0 1 0);" in mirror
    assert "planeTolerance  0.0001;" in mirror
    assert "mirrorPlane" not in mirror


def test_cylinder_force_reference_area_uses_diameter_times_extrusion_span() -> None:
    files = archive_files(compile_plan(cylinder_plan()).archive)

    assert "lRef 0.1;" in files["system/controlDict"]
    assert "Aref 0.001;" in files["system/controlDict"]
    assert "extrusionSpan 0.01;" in files["system/blockMeshDict"]


def test_cylinder_mesh_assigns_requested_wake_and_radial_resolutions() -> None:
    mesh = archive_files(compile_plan(cylinder_plan()).archive)["system/blockMeshDict"]

    assert "radialCells 32;" in mesh
    assert "wakeCells 120;" in mesh
    assert "(40 32 1)" in mesh
    assert "(80 32 1)" in mesh


def test_cylinder_declares_only_y_zero_faces_as_mirror_plane_patch() -> None:
    files = archive_files(compile_plan(cylinder_plan()).archive)
    mesh = files["system/blockMeshDict"]

    assert "mirrorPlane\n    {\n        type symmetryPlane;" in mesh
    for face in (
        "(3 4 18 17)",
        "(1 15 16 2)",
        "(4 5 19 18)",
        "(5 6 20 19)",
        "(1 0 14 15)",
    ):
        assert face in mesh
    assert "frontAndBack\n    {\n        type empty;" in mesh
    assert "mirrorPlane { type symmetryPlane; }" in files["0/U"]
    assert "mirrorPlane { type symmetryPlane; }" in files["0/p"]


def test_explicit_cylinder_step_keeps_adaptive_courant_control_enabled() -> None:
    control = archive_files(compile_plan(cylinder_plan()).archive)["system/controlDict"]

    assert "adjustTimeStep  yes;" in control
    assert "maxCo           1;" in control


def test_cylinder_courant_guard_accounts_for_graded_near_wall_cell() -> None:
    with pytest.raises(CompilationError, match="Courant"):
        compile_plan(
            cylinder_plan(
                domain_upstream_diameters=30.0,
                domain_downstream_diameters=60.0,
                domain_transverse_diameters=40.0,
                cells_radial=400,
                cells_wake=2000,
                time_step_s=0.0002,
            )
        )


def test_cavity_contains_moving_lid_probes_without_mirror_mesh() -> None:
    compiled = compile_plan(cavity_plan())
    files = archive_files(compiled.archive)

    assert compiled.manifest.needs_mirror_mesh is False
    assert "system/mirrorMeshDict" not in files
    assert "movingLid" in files["0/U"]
    assert "uniform (1 0 0)" in files["0/U"]
    assert "probes" in files["system/controlDict"]
    assert "residuals" in files["system/controlDict"]
    assert "pRefCell 0;" in files["system/fvSolution"]
    assert "pRefValue 0;" in files["system/fvSolution"]
    assert "(0.25 0.5 0.0078125)" in files["system/controlDict"]


@pytest.mark.parametrize("plan", [cylinder_plan(), cavity_plan()])
def test_transient_foundation13_solver_has_required_final_field_solvers(plan) -> None:
    solution = archive_files(compile_plan(plan).archive)["system/fvSolution"]

    assert "pFinal" in solution
    assert "$p;" in solution
    assert "UFinal" in solution
    assert "$U;" in solution
    assert solution.count("relTol 0;") >= 2


@pytest.mark.parametrize(
    ("plan", "expected"),
    [
        (pipe_plan(), ("pressure_drop", "mass_imbalance")),
        (
            cylinder_plan(),
            ("drag_coefficient", "lift_coefficient", "strouhal_number"),
        ),
        (cavity_plan(), ("velocity_probes", "residuals")),
    ],
)
def test_compiled_required_outputs_are_exactly_requested(
    plan: object, expected: tuple[str, ...]
) -> None:
    compiled = compile_plan(plan)
    files = archive_files(compiled.archive)

    assert compiled.required_outputs == expected
    assert "residuals" in files["system/controlDict"]
    if compiled.experiment_type == "cylinder_flow":
        metadata = json.loads(files["fluidScientist/plan.json"])
        assert metadata["output_derivations"]["strouhal_number"] == (
            "derived from forceCoeffs lift history and shedding frequency"
        )


def test_explicit_cylinder_time_step_above_initial_courant_limit_is_rejected() -> None:
    with pytest.raises(CompilationError, match="Courant"):
        compile_plan(cylinder_plan(time_step_s=0.004))


def test_explicit_cylinder_time_step_below_graded_courant_limit_is_accepted() -> None:
    compiled = compile_plan(cylinder_plan(time_step_s=0.002))

    assert "deltaT          0.002;" in archive_files(compiled.archive)[
        "system/controlDict"
    ]


def test_adaptive_cylinder_unrepresentable_time_step_is_rejected() -> None:
    with pytest.raises(CompilationError, match="time step"):
        compile_plan(cylinder_plan(time_step_s=None, max_courant=1e-12))


def test_extreme_cavity_resolution_that_needs_unrepresentable_step_is_rejected() -> None:
    with pytest.raises(CompilationError, match="time step"):
        compile_plan(
            cavity_plan(
                side_length_m=1e-12,
                lid_velocity_m_s=1000.0,
                cells_per_side=4096,
            )
        )


@pytest.mark.parametrize(
    ("original", "changed", "member", "needle"),
    [
        (pipe_plan(), pipe_plan(length_m=2.0), "system/blockMeshDict", "length 2"),
        (
            cylinder_plan(),
            cylinder_plan(
                reynolds_number=200.0,
                mean_velocity_m_s=2.0,
                time_step_s=0.001,
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
