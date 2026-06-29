"""Convergence, conservation, and grid uncertainty calculations."""

from dataclasses import dataclass
from math import isclose, log


@dataclass(frozen=True)
class GCIResult:
    observed_order: float
    extrapolated_value: float
    fine_gci_percent: float


def mass_imbalance_percent(inlet_mass_flow: float, outlet_mass_flow: float) -> float:
    reference = max(abs(inlet_mass_flow), abs(outlet_mass_flow))
    if reference == 0:
        raise ValueError("reference flow must be non-zero")
    return abs(inlet_mass_flow + outlet_mass_flow) / reference * 100.0


def residuals_converged(residuals: dict[str, list[float]], target: float) -> bool:
    if target <= 0:
        raise ValueError("target must be positive")
    if not residuals or any(not values for values in residuals.values()):
        return False
    return all(values[-1] <= target for values in residuals.values())


def monitor_stable(values: list[float], *, relative_band: float) -> bool:
    if relative_band < 0:
        raise ValueError("relative_band must be non-negative")
    if len(values) < 2:
        return False
    reference = abs(sum(values) / len(values))
    if reference == 0:
        return max(values) == min(values)
    return (max(values) - min(values)) / reference <= relative_band


def grid_convergence_index(
    grid_sizes: list[float], values: list[float], *, safety_factor: float = 1.25
) -> GCIResult:
    if len(grid_sizes) != 3 or len(values) != 3:
        raise ValueError("GCI requires exactly three grids and values")
    if any(size <= 0 for size in grid_sizes):
        raise ValueError("grid sizes must be positive")
    if not grid_sizes[0] > grid_sizes[1] > grid_sizes[2]:
        raise ValueError("grid sizes must strictly decrease from coarse to fine")

    r_coarse = grid_sizes[0] / grid_sizes[1]
    r_fine = grid_sizes[1] / grid_sizes[2]
    if not isclose(r_coarse, r_fine, rel_tol=1e-6):
        raise ValueError("uniform refinement ratio is required")

    coarse_delta = values[1] - values[0]
    fine_delta = values[2] - values[1]
    if coarse_delta == 0 or fine_delta == 0 or coarse_delta * fine_delta <= 0:
        raise ValueError("values must show monotonic non-zero convergence")

    observed_order = log(abs(coarse_delta / fine_delta)) / log(r_fine)
    denominator = r_fine**observed_order - 1.0
    extrapolated = values[2] + fine_delta / denominator
    fine_gci = safety_factor * abs(fine_delta / values[2]) / denominator * 100.0
    return GCIResult(
        observed_order=observed_order,
        extrapolated_value=extrapolated,
        fine_gci_percent=fine_gci,
    )
