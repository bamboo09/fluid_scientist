"""Deterministic OpenFOAM mesh, solver, and benchmark parsers."""

import re
from dataclasses import dataclass


class OpenFOAMFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class CheckMeshResult:
    passed: bool
    cells: int
    max_aspect_ratio: float
    max_non_orthogonality: float
    average_non_orthogonality: float
    max_skewness: float


@dataclass(frozen=True)
class SolverLogResult:
    completed: bool
    final_residuals: dict[str, float]
    global_continuity_error: float | None
    cumulative_continuity_error: float | None
    inlet_mass_flow: float | None
    outlet_mass_flow: float | None
    pressure_drop_pa: float | None


_NUMBER = r"[-+]?(?:[0-9]*\.?[0-9]+)(?:[eE][-+]?[0-9]+)?"


def _required(pattern: str, text: str, name: str) -> re.Match[str]:
    match = re.search(pattern, text, re.IGNORECASE)
    if match is None:
        raise OpenFOAMFailure(f"could not parse {name}")
    return match


def parse_check_mesh(text: str, *, require_passed: bool = True) -> CheckMeshResult:
    lower = text.lower()
    passed = (
        "mesh ok" in lower
        and "failed" not in lower
        and "negative volume" not in lower
    )
    if require_passed and not passed:
        raise OpenFOAMFailure("mesh quality checks failed")
    cells = int(_required(r"cells:\s*([0-9]+)", text, "cell count").group(1))
    aspect = float(
        _required(rf"Max aspect ratio\s*=\s*({_NUMBER})", text, "aspect ratio").group(1)
    )
    non_orthogonal = _required(
        rf"non-orthogonality Max:\s*({_NUMBER})\s+average:\s*({_NUMBER})",
        text,
        "non-orthogonality",
    )
    skewness = float(
        _required(rf"Max skewness\s*=\s*({_NUMBER})", text, "skewness").group(1)
    )
    return CheckMeshResult(
        passed=passed,
        cells=cells,
        max_aspect_ratio=aspect,
        max_non_orthogonality=float(non_orthogonal.group(1)),
        average_non_orthogonality=float(non_orthogonal.group(2)),
        max_skewness=skewness,
    )


def parse_solver_log(text: str) -> SolverLogResult:
    lower = text.lower()
    if re.search(r"floating point exception(?!\s+trapping)", lower):
        raise OpenFOAMFailure("solver failed with floating point exception")
    if "foam fatal error" in lower:
        raise OpenFOAMFailure("solver reported a FOAM fatal error")

    residuals: dict[str, float] = {}
    residual_pattern = re.compile(
        rf"Solving for ([A-Za-z0-9_]+), Initial residual = {_NUMBER}, "
        rf"Final residual = ({_NUMBER})",
        re.IGNORECASE,
    )
    for match in residual_pattern.finditer(text):
        residuals[match.group(1)] = float(match.group(2))

    continuity_matches = list(
        re.finditer(
            rf"continuity errors\s*:\s*sum local = {_NUMBER},\s*global = ({_NUMBER}),\s*"
            rf"cumulative = ({_NUMBER})",
            text,
            re.IGNORECASE,
        )
    )
    global_error = cumulative_error = None
    if continuity_matches:
        global_error = float(continuity_matches[-1].group(1))
        cumulative_error = float(continuity_matches[-1].group(2))

    return SolverLogResult(
        completed=bool(re.search(r"^End\s*$", text, re.MULTILINE)),
        final_residuals=residuals,
        global_continuity_error=global_error,
        cumulative_continuity_error=cumulative_error,
        inlet_mass_flow=_optional_metric(text, "inlet massFlow"),
        outlet_mass_flow=_optional_metric(text, "outlet massFlow"),
        pressure_drop_pa=_optional_metric(text, "pressureDrop"),
    )


def _optional_metric(text: str, name: str) -> float | None:
    match = re.search(rf"{re.escape(name)}\s*=\s*({_NUMBER})", text, re.IGNORECASE)
    return float(match.group(1)) if match else None


def hagen_poiseuille_pressure_drop(
    *,
    dynamic_viscosity_pa_s: float,
    length_m: float,
    mean_velocity_m_s: float,
    diameter_m: float,
) -> float:
    if min(dynamic_viscosity_pa_s, length_m, mean_velocity_m_s, diameter_m) <= 0:
        raise ValueError("Hagen-Poiseuille inputs must be positive")
    return 32.0 * dynamic_viscosity_pa_s * length_m * mean_velocity_m_s / diameter_m**2


def relative_error_percent(numerical: float, reference: float) -> float:
    if reference == 0:
        raise ValueError("reference must be non-zero")
    return abs(numerical - reference) / abs(reference) * 100.0
