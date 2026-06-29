"""SI-unit fluid mechanics calculations that must not be delegated to an LLM."""

from math import pi, sqrt


def _require_positive(name: str, value: float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def area(diameter: float) -> float:
    _require_positive("diameter", diameter)
    return pi * diameter**2 / 4.0


def reynolds_number(*, rho: float, velocity: float, diameter: float, mu: float) -> float:
    _require_positive("rho", rho)
    _require_positive("velocity", velocity)
    _require_positive("diameter", diameter)
    _require_positive("mu", mu)
    return rho * velocity * diameter / mu


def velocity_from_reynolds(
    *, reynolds: float, rho: float, diameter: float, mu: float
) -> float:
    _require_positive("reynolds", reynolds)
    _require_positive("rho", rho)
    _require_positive("diameter", diameter)
    _require_positive("mu", mu)
    return reynolds * mu / (rho * diameter)


def dean_number(*, reynolds: float, curvature_ratio: float) -> float:
    _require_positive("reynolds", reynolds)
    _require_positive("curvature_ratio", curvature_ratio)
    return reynolds / sqrt(2.0 * curvature_ratio)

