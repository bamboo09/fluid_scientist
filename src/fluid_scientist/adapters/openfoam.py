"""Versioned OpenFOAM Foundation 13 case rendering."""

import hashlib
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fluid_scientist.adapters.openfoam_parsers import (
    hagen_poiseuille_pressure_drop,
    relative_error_percent,
)
from fluid_scientist.validation.core import mass_imbalance_percent


class LaminarPipeCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    diameter_m: float = Field(gt=0)
    length_m: float = Field(gt=0)
    mean_velocity_m_s: float = Field(gt=0)
    kinematic_viscosity_m2_s: float = Field(gt=0)
    density_kg_m3: float = Field(default=998.2, gt=0)
    axial_cells: int = Field(default=80, ge=10, le=10_000)
    radial_cells: int = Field(default=10, ge=3, le=500)

    @property
    def reynolds_number(self) -> float:
        return self.mean_velocity_m_s * self.diameter_m / self.kinematic_viscosity_m2_s

    @model_validator(mode="after")
    def require_laminar_regime(self) -> "LaminarPipeCase":
        if self.reynolds_number >= 2_300:
            raise ValueError("laminar pipe benchmark requires Reynolds number below 2300")
        return self


@dataclass(frozen=True)
class CaseManifest:
    case_id: str
    files: dict[str, str]


class PipeBenchmarkValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analytical_pressure_drop_pa: float
    numerical_pressure_drop_pa: float
    pressure_drop_error_percent: float
    mass_imbalance_percent: float
    maximum_final_residual: float
    passed: bool


_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TOKEN = re.compile(r"\{\{([a-z_]+)\}\}")
_TEMPLATE_FILES = (
    "0/U",
    "0/p",
    "constant/momentumTransport",
    "constant/physicalProperties",
    "system/blockMeshDict",
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
)


class OpenFOAM13CaseRenderer:
    def __init__(self, work_root: Path) -> None:
        self._work_root = work_root.resolve()

    def render(self, case_id: str, spec: LaminarPipeCase) -> CaseManifest:
        if not _CASE_ID.fullmatch(case_id):
            raise ValueError("case id contains forbidden characters")
        case_root = (self._work_root / case_id).resolve()
        if case_root.parent != self._work_root:
            raise ValueError("case id escapes work root")

        values = {
            "axial_cells": str(spec.axial_cells),
            "diameter": _foam_number(spec.diameter_m),
            "length": _foam_number(spec.length_m),
            "nu": _foam_number(spec.kinematic_viscosity_m2_s),
            "radial_cells": str(spec.radial_cells),
            "radius": _foam_number(spec.diameter_m / 2.0),
            "velocity": _foam_number(spec.mean_velocity_m_s),
        }
        package = files("fluid_scientist.templates.openfoam13.laminar_pipe")
        digests: dict[str, str] = {}
        for relative in _TEMPLATE_FILES:
            template = package.joinpath(relative).read_text(encoding="utf-8")
            rendered = _render_template(template, values)
            destination = case_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered, encoding="utf-8", newline="\n")
            digests[relative] = hashlib.sha256(rendered.encode()).hexdigest()
        return CaseManifest(case_id=case_id, files=digests)


def _render_template(template: str, values: dict[str, str]) -> str:
    required = set(_TOKEN.findall(template))
    missing = required - values.keys()
    if missing:
        raise ValueError(f"missing OpenFOAM template values: {sorted(missing)}")
    rendered = _TOKEN.sub(lambda match: values[match.group(1)], template)
    if _TOKEN.search(rendered):
        raise ValueError("unresolved OpenFOAM template value")
    return rendered


def _foam_number(value: float) -> str:
    return f"{value:.12g}"


def validate_laminar_pipe(
    spec: LaminarPipeCase,
    *,
    pressure_drop_pa: float | None,
    inlet_mass_flow: float | None,
    outlet_mass_flow: float | None,
    final_residuals: dict[str, float],
    pressure_error_limit_percent: float = 5.0,
    mass_imbalance_limit_percent: float = 0.1,
    residual_limit: float = 1e-6,
) -> PipeBenchmarkValidation:
    if pressure_drop_pa is None or inlet_mass_flow is None or outlet_mass_flow is None:
        raise ValueError("pressure drop and mass-flow credibility metrics are required")
    if not final_residuals:
        raise ValueError("final residual credibility metrics are required")
    analytical = hagen_poiseuille_pressure_drop(
        dynamic_viscosity_pa_s=(spec.kinematic_viscosity_m2_s * spec.density_kg_m3),
        length_m=spec.length_m,
        mean_velocity_m_s=spec.mean_velocity_m_s,
        diameter_m=spec.diameter_m,
    )
    pressure_error = relative_error_percent(pressure_drop_pa, analytical)
    imbalance = mass_imbalance_percent(inlet_mass_flow, outlet_mass_flow)
    maximum_residual = max(final_residuals.values())
    return PipeBenchmarkValidation(
        analytical_pressure_drop_pa=analytical,
        numerical_pressure_drop_pa=pressure_drop_pa,
        pressure_drop_error_percent=pressure_error,
        mass_imbalance_percent=imbalance,
        maximum_final_residual=maximum_residual,
        passed=(
            pressure_error <= pressure_error_limit_percent
            and imbalance <= mass_imbalance_limit_percent
            and maximum_residual <= residual_limit
        ),
    )
