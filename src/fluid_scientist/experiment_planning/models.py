"""Provider-neutral contracts for deterministic CFD experiment planning."""

from collections.abc import Callable
from enum import Enum
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, RootModel, model_validator

TupleItem = TypeVar("TupleItem")


def _json_list_to_tuple(value: object) -> object:
    """Accept JSON's sole sequence type without weakening element validation."""

    return tuple(value) if isinstance(value, list) else value


JsonTuple = Annotated[tuple[TupleItem, ...], BeforeValidator(_json_list_to_tuple)]


class StrictModel(BaseModel):
    """Base for closed planning contracts."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class ConvergenceTargets(StrictModel):
    """Solver-independent numerical credibility targets."""

    residual_tolerance: float = Field(gt=0.0, le=1e-2)
    mass_imbalance_percent: float = Field(ge=0.0, le=5.0)


class ParameterSweep(StrictModel):
    """A small, explicit one-factor sweep for deterministic compilation."""

    parameter: str = Field(min_length=1, max_length=64)
    values: JsonTuple[float] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def require_positive_increasing_values(self) -> "ParameterSweep":
        if any(value <= 0.0 for value in self.values):
            raise ValueError("parameter sweep values must be positive")
        pairs = zip(self.values, self.values[1:], strict=False)
        if any(left >= right for left, right in pairs):
            raise ValueError("parameter sweep values must be strictly increasing")
        return self


SweepUpdates = Callable[[StrictModel, str, float], dict[str, float]]


def _validate_case_sweeps(
    case: StrictModel,
    sweeps: tuple[ParameterSweep, ...],
    *,
    allowed_parameters: set[str],
    label: str,
    bounds_context: str = "case bounds",
    coupled_updates: SweepUpdates | None = None,
) -> None:
    """Revalidate every sweep point through its case model's own constraints."""

    if any(sweep.parameter not in allowed_parameters for sweep in sweeps):
        raise ValueError(f"unsupported {label} sweep parameter")
    case_values = case.model_dump()
    try:
        for sweep in sweeps:
            for value in sweep.values:
                updates = {sweep.parameter: value}
                if coupled_updates is not None:
                    updates.update(coupled_updates(case, sweep.parameter, value))
                type(case).model_validate(case_values | updates)
    except ValueError as error:
        raise ValueError(f"{label} sweep values must remain within {bounds_context}") from error


class PipeOutput(str, Enum):
    PRESSURE_DROP = "pressure_drop"
    MASS_IMBALANCE = "mass_imbalance"
    RESIDUALS = "residuals"


class CylinderOutput(str, Enum):
    DRAG_COEFFICIENT = "drag_coefficient"
    LIFT_COEFFICIENT = "lift_coefficient"
    STROUHAL_NUMBER = "strouhal_number"
    MASS_IMBALANCE = "mass_imbalance"
    RESIDUALS = "residuals"


class CavityOutput(str, Enum):
    VELOCITY_PROBES = "velocity_probes"
    PRESSURE_PROBES = "pressure_probes"
    MASS_IMBALANCE = "mass_imbalance"
    RESIDUALS = "residuals"


PipeOutputValue = Annotated[PipeOutput, Field(strict=False)]
CylinderOutputValue = Annotated[CylinderOutput, Field(strict=False)]
CavityOutputValue = Annotated[CavityOutput, Field(strict=False)]


class PlanBase(StrictModel):
    experiment_name: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=10)
    rationale: str = Field(min_length=10)
    assumptions: JsonTuple[str] = Field(min_length=1)
    limitations: JsonTuple[str] = Field(min_length=1)
    requested_outputs: JsonTuple[str] = Field(min_length=1)
    convergence_targets: ConvergenceTargets


class LaminarPipeCase(StrictModel):
    """Compiler inputs compatible with the existing laminar pipe renderer."""

    diameter_m: float = Field(gt=0.0, le=100.0)
    length_m: float = Field(gt=0.0, le=100_000.0)
    mean_velocity_m_s: float = Field(gt=0.0, le=1_000.0)
    kinematic_viscosity_m2_s: float = Field(gt=0.0, le=1.0)
    density_kg_m3: float = Field(default=998.2, gt=0.0, le=100_000.0)
    axial_cells: int = Field(default=80, ge=10, le=10_000)
    radial_cells: int = Field(default=10, ge=3, le=500)

    @property
    def reynolds_number(self) -> float:
        return self.mean_velocity_m_s * self.diameter_m / self.kinematic_viscosity_m2_s

    @model_validator(mode="after")
    def require_laminar_regime(self) -> "LaminarPipeCase":
        if self.reynolds_number >= 2_300.0:
            raise ValueError("laminar pipe plan requires Reynolds number below 2300")
        return self


class PipeExperimentPlan(PlanBase):
    experiment_type: Literal["laminar_pipe"]
    requested_outputs: JsonTuple[PipeOutputValue] = Field(min_length=1)
    case: LaminarPipeCase
    parameter_sweeps: JsonTuple[ParameterSweep] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def require_meaningful_sweeps(self) -> "PipeExperimentPlan":
        _validate_case_sweeps(
            self.case,
            self.parameter_sweeps,
            allowed_parameters={
                "diameter_m",
                "length_m",
                "mean_velocity_m_s",
                "kinematic_viscosity_m2_s",
            },
            label="laminar pipe",
            bounds_context="the laminar regime and case bounds",
        )
        return self


class CylinderFlowCase(StrictModel):
    diameter_m: float = Field(gt=0.0, le=100.0)
    reynolds_number: float = Field(gt=0.0, le=300.0)
    domain_upstream_diameters: float = Field(default=10.0, ge=5.0, le=30.0)
    domain_downstream_diameters: float = Field(default=20.0, ge=10.0, le=60.0)
    domain_transverse_diameters: float = Field(default=10.0, ge=4.0, le=40.0)
    cells_radial: int = Field(default=40, ge=16, le=400)
    cells_wake: int = Field(default=120, ge=40, le=2_000)
    end_time_s: float = Field(gt=0.0, le=1_000_000.0)
    time_step_s: float | None = Field(default=None, gt=0.0, le=10_000.0)
    max_courant: float | None = Field(default=None, gt=0.0, le=1.0)
    density_kg_m3: float = Field(gt=0.0, le=100_000.0)
    kinematic_viscosity_m2_s: float = Field(gt=0.0, le=1.0)
    mean_velocity_m_s: float = Field(gt=0.0, le=1_000.0)

    @model_validator(mode="after")
    def require_consistent_transient_specification(self) -> "CylinderFlowCase":
        if (self.time_step_s is None) == (self.max_courant is None):
            raise ValueError("provide exactly one of time_step_s or max_courant")
        calculated = (
            self.mean_velocity_m_s * self.diameter_m / self.kinematic_viscosity_m2_s
        )
        relative_error = abs(calculated - self.reynolds_number) / self.reynolds_number
        if relative_error > 0.01:
            raise ValueError(
                "Reynolds number is inconsistent with diameter, velocity, and viscosity"
            )
        return self


def _cylinder_coupled_sweep_updates(
    case: StrictModel, parameter: str, value: float
) -> dict[str, float]:
    """Keep the cylinder Reynolds definition coherent at each sweep point."""

    if not isinstance(case, CylinderFlowCase):
        raise TypeError("cylinder sweep coupling requires CylinderFlowCase")
    if parameter == "reynolds_number":
        velocity = value * case.kinematic_viscosity_m2_s / case.diameter_m
        return {"mean_velocity_m_s": velocity}
    if parameter == "mean_velocity_m_s":
        reynolds = value * case.diameter_m / case.kinematic_viscosity_m2_s
        return {"reynolds_number": reynolds}
    if parameter == "diameter_m":
        velocity = case.reynolds_number * case.kinematic_viscosity_m2_s / value
        return {"mean_velocity_m_s": velocity}
    return {}


class CylinderExperimentPlan(PlanBase):
    experiment_type: Literal["cylinder_flow"]
    requested_outputs: JsonTuple[CylinderOutputValue] = Field(min_length=1)
    case: CylinderFlowCase
    parameter_sweeps: JsonTuple[ParameterSweep] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def require_meaningful_sweeps(self) -> "CylinderExperimentPlan":
        _validate_case_sweeps(
            self.case,
            self.parameter_sweeps,
            allowed_parameters={"diameter_m", "reynolds_number", "mean_velocity_m_s"},
            label="cylinder flow",
            bounds_context="case bounds and Reynolds consistency",
            coupled_updates=_cylinder_coupled_sweep_updates,
        )
        return self


class LidDrivenCavityCase(StrictModel):
    side_length_m: float = Field(gt=0.0, le=100.0)
    lid_velocity_m_s: float = Field(gt=0.0, le=1_000.0)
    kinematic_viscosity_m2_s: float = Field(gt=0.0, le=1.0)
    density_kg_m3: float = Field(gt=0.0, le=100_000.0)
    cells_per_side: int = Field(default=64, ge=8, le=4_096)
    end_time_s: float = Field(gt=0.0, le=1_000_000.0)


class CavityExperimentPlan(PlanBase):
    experiment_type: Literal["lid_driven_cavity"]
    requested_outputs: JsonTuple[CavityOutputValue] = Field(min_length=1)
    case: LidDrivenCavityCase
    parameter_sweeps: JsonTuple[ParameterSweep] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def require_meaningful_sweeps(self) -> "CavityExperimentPlan":
        _validate_case_sweeps(
            self.case,
            self.parameter_sweeps,
            allowed_parameters={
                "side_length_m",
                "lid_velocity_m_s",
                "kinematic_viscosity_m2_s",
            },
            label="lid-driven cavity",
        )
        return self


CustomOutput = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]


class CustomOpenFOAMCase(StrictModel):
    geometry: str = Field(min_length=10)
    boundary_conditions: JsonTuple[str] = Field(min_length=2)
    mesh_strategy: str = Field(min_length=10)
    run_strategy: str = Field(min_length=10)


class CustomExperimentPlan(PlanBase):
    experiment_type: Literal["custom_openfoam"]
    requested_outputs: JsonTuple[CustomOutput] = Field(min_length=1)
    case: CustomOpenFOAMCase


PlanVariant = Annotated[
    PipeExperimentPlan
    | CylinderExperimentPlan
    | CavityExperimentPlan
    | CustomExperimentPlan,
    Field(discriminator="experiment_type"),
]


class ExperimentPlan(RootModel[PlanVariant]):
    """Discriminated provider-neutral experiment plan root."""


# Descriptive aliases retained for callers that prefer geometry-first names.
LaminarPipePlan = PipeExperimentPlan
CylinderFlowPlan = CylinderExperimentPlan
LidDrivenCavityPlan = CavityExperimentPlan
CustomOpenFOAMPlan = CustomExperimentPlan
