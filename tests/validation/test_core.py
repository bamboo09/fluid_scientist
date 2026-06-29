import pytest

from fluid_scientist.validation.core import (
    grid_convergence_index,
    mass_imbalance_percent,
    monitor_stable,
    residuals_converged,
)


def test_mass_imbalance_uses_absolute_reference() -> None:
    assert mass_imbalance_percent(10.0, -9.99) == pytest.approx(0.1)


def test_mass_imbalance_rejects_zero_reference_flow() -> None:
    with pytest.raises(ValueError, match="reference flow"):
        mass_imbalance_percent(0.0, 0.0)


def test_gci_returns_fine_grid_uncertainty() -> None:
    result = grid_convergence_index(
        grid_sizes=[0.1, 0.05, 0.025],
        values=[1.0, 1.1, 1.125],
    )

    assert result.observed_order == pytest.approx(2.0)
    assert result.fine_gci_percent == pytest.approx(0.9259259)


def test_gci_rejects_non_monotonic_grid_sizes() -> None:
    with pytest.raises(ValueError, match="strictly decrease"):
        grid_convergence_index([0.1, 0.09, 0.12], [1.0, 0.9, 0.8])


def test_residuals_require_all_final_values_below_target() -> None:
    assert residuals_converged({"p": [1e-2, 1e-6], "U": [1e-2, 5e-6]}, 1e-5)
    assert not residuals_converged({"p": [1e-2, 2e-5], "U": [1e-2, 5e-6]}, 1e-5)


def test_monitor_stability_uses_relative_band() -> None:
    assert monitor_stable([100.0, 100.01, 99.99], relative_band=0.001)
    assert not monitor_stable([100.0, 101.0, 99.0], relative_band=0.001)
