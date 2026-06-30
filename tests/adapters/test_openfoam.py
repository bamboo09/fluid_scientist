import pytest

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


def test_hagen_poiseuille_benchmark_and_relative_error() -> None:
    analytical = hagen_poiseuille_pressure_drop(
        dynamic_viscosity_pa_s=1.0e-3,
        length_m=2.0,
        mean_velocity_m_s=0.1,
        diameter_m=0.02,
    )

    assert analytical == pytest.approx(16.0)
    assert relative_error_percent(15.84, analytical) == pytest.approx(1.0)
