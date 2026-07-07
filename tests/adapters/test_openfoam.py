import pytest

from fluid_scientist.adapters.openfoam import (
    LaminarPipeCase,
    OpenFOAM13CaseRenderer,
    validate_laminar_pipe,
)
from fluid_scientist.adapters.openfoam_parsers import (
    OpenFOAMFailure,
    hagen_poiseuille_pressure_drop,
    parse_check_mesh,
    parse_solver_log,
    relative_error_percent,
)

CHECK_MESH_OK = """
Mesh stats
    cells:            12000
Max aspect ratio = 2.3 OK.
Mesh non-orthogonality Max: 18 average: 3.2
Max skewness = 0.41 OK.
Mesh OK.
"""


SOLVER_LOG = """
smoothSolver:  Solving for Ux, Initial residual = 0.001, Final residual = 1e-07, No Iterations 2
GAMG:  Solving for p, Initial residual = 0.01, Final residual = 3e-06, No Iterations 4
time step continuity errors : sum local = 1e-08, global = -2e-09, cumulative = 4e-08
inlet massFlow = 0.0314
outlet massFlow = -0.03139
pressureDrop = 12.8
End
"""


def test_check_mesh_parser_extracts_quality_and_pass_state() -> None:
    result = parse_check_mesh(CHECK_MESH_OK)

    assert result.passed is True
    assert result.cells == 12_000
    assert result.max_aspect_ratio == pytest.approx(2.3)
    assert result.max_non_orthogonality == pytest.approx(18)
    assert result.max_skewness == pytest.approx(0.41)


def test_check_mesh_rejects_failed_mesh() -> None:
    with pytest.raises(OpenFOAMFailure, match="mesh quality"):
        parse_check_mesh("Failed 1 mesh checks.\nnegative volume cells: 2")


def test_check_mesh_can_collect_metrics_from_a_failed_quality_report() -> None:
    report = CHECK_MESH_OK.replace("Mesh OK.", "Failed 1 mesh checks.")

    result = parse_check_mesh(report, require_passed=False)

    assert result.passed is False
    assert result.cells == 12_000
    assert result.max_non_orthogonality == pytest.approx(18)


def test_solver_parser_extracts_credibility_inputs() -> None:
    result = parse_solver_log(SOLVER_LOG)

    assert result.completed is True
    assert result.final_residuals == {"Ux": 1e-7, "p": 3e-6}
    assert result.global_continuity_error == pytest.approx(-2e-9)
    assert result.inlet_mass_flow == pytest.approx(0.0314)
    assert result.outlet_mass_flow == pytest.approx(-0.03139)
    assert result.pressure_drop_pa == pytest.approx(12.8)


def test_solver_parser_classifies_floating_point_failure() -> None:
    with pytest.raises(OpenFOAMFailure, match="floating point"):
        parse_solver_log("FOAM FATAL ERROR: Floating point exception")


def test_solver_parser_ignores_sigfpe_trapping_banner() -> None:
    result = parse_solver_log(
        "sigFpe : Enabling floating point exception trapping (FOAM_SIGFPE).\n"
        "smoothSolver:  Solving for Ux, Initial residual = 1e-5, "
        "Final residual = 1e-9, No Iterations 1\n"
        "End\n"
    )

    assert result.completed is True
    assert result.final_residuals == {"Ux": 1e-9}


def test_hagen_poiseuille_benchmark_and_relative_error() -> None:
    analytical = hagen_poiseuille_pressure_drop(
        dynamic_viscosity_pa_s=1.0e-3,
        length_m=2.0,
        mean_velocity_m_s=0.1,
        diameter_m=0.02,
    )

    assert analytical == pytest.approx(16.0)
    assert relative_error_percent(15.84, analytical) == pytest.approx(1.0)


def test_openfoam13_renderer_builds_a_complete_laminar_pipe_case(tmp_path) -> None:
    renderer = OpenFOAM13CaseRenderer(tmp_path)
    spec = LaminarPipeCase(
        diameter_m=0.02,
        length_m=2.0,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1.0e-6,
        axial_cells=100,
        radial_cells=12,
    )

    manifest = renderer.render("pipe-study-001", spec)

    expected = {
        "0/U",
        "0/p",
        "constant/momentumTransport",
        "constant/physicalProperties",
        "system/blockMeshDict",
        "system/controlDict",
        "system/fvSchemes",
        "system/fvSolution",
    }
    assert set(manifest.files) == expected
    assert all(len(digest) == 64 for digest in manifest.files.values())
    case_root = tmp_path / "pipe-study-001"
    assert "solver          incompressibleFluid;" in (case_root / "system/controlDict").read_text()
    control = (case_root / "system/controlDict").read_text()
    assert "type            fieldValueDelta;" in control
    assert "patch           inlet;" in control
    assert "patch           outlet;" in control
    assert "fields          (phi);" in control
    assert "simulationType laminar;" in (case_root / "constant/momentumTransport").read_text()
    assert "1e-06" in (case_root / "constant/physicalProperties").read_text()
    assert "(100 1 12)" in (case_root / "system/blockMeshDict").read_text()
    block_mesh = (case_root / "system/blockMeshDict").read_text()
    assert block_mesh.count("type wedge;") == 2
    assert "type cyclic;" not in block_mesh
    assert "bounded Gauss limitedLinearV 1" in (
        case_root / "system/fvSchemes"
    ).read_text()
    fv_solution = (case_root / "system/fvSolution").read_text()
    u_solver = fv_solution.split("    U\n    {", maxsplit=1)[1].split("    }", maxsplit=1)[0]
    assert "relTol          0;" in u_solver
    inlet_velocity = (case_root / "0/U").read_text()
    assert "type            codedFixedValue;" in inlet_velocity
    assert "name            fullyDevelopedPipeInlet;" in inlet_velocity
    assert "const scalar radius = 0.01;" in inlet_velocity
    assert "const scalar meanVelocity = 0.1;" in inlet_velocity
    assert "2.0*meanVelocity" in inlet_velocity


def test_openfoam13_renderer_rejects_unsafe_case_id(tmp_path) -> None:
    renderer = OpenFOAM13CaseRenderer(tmp_path)
    spec = LaminarPipeCase(
        diameter_m=0.02,
        length_m=2.0,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1.0e-6,
    )

    with pytest.raises(ValueError, match="case id"):
        renderer.render("../outside", spec)


def test_laminar_pipe_case_enforces_laminar_regime() -> None:
    with pytest.raises(ValueError, match="laminar"):
        LaminarPipeCase(
            diameter_m=0.1,
            length_m=1.0,
            mean_velocity_m_s=1.0,
            kinematic_viscosity_m2_s=1.0e-6,
        )


def test_pipe_benchmark_validation_combines_analytical_mass_and_residual_checks() -> None:
    spec = LaminarPipeCase(
        diameter_m=0.02,
        length_m=2,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1e-6,
        density_kg_m3=1000,
    )

    result = validate_laminar_pipe(
        spec,
        pressure_drop_pa=15.84,
        inlet_mass_flow=0.0314159,
        outlet_mass_flow=-0.0314158,
        final_residuals={"Ux": 1e-8, "p": 2e-8},
    )

    assert result.analytical_pressure_drop_pa == pytest.approx(16.0)
    assert result.pressure_drop_error_percent == pytest.approx(1.0)
    assert result.mass_imbalance_percent < 0.001
    assert result.passed is True


def test_pipe_benchmark_validation_fails_missing_credibility_metric() -> None:
    spec = LaminarPipeCase(
        diameter_m=0.02,
        length_m=2,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1e-6,
    )

    with pytest.raises(ValueError, match="required"):
        validate_laminar_pipe(
            spec,
            pressure_drop_pa=None,
            inlet_mass_flow=0.03,
            outlet_mass_flow=-0.03,
            final_residuals={"Ux": 1e-8},
        )
