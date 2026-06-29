import pytest

from fluid_scientist.physics.calculations import dean_number, reynolds_number


def test_reynolds_number_is_deterministic() -> None:
    assert reynolds_number(
        rho=998.2,
        velocity=2.0,
        diameter=0.2,
        mu=1.002e-3,
    ) == pytest.approx(398_483.0339, rel=1e-9)


def test_dean_number_rejects_nonpositive_curvature_ratio() -> None:
    with pytest.raises(ValueError, match="curvature_ratio"):
        dean_number(reynolds=50_000, curvature_ratio=0)

