"""Strict domain models for research planning, execution, and reporting."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GeometryType(StrEnum):
    PIPE = "pipe"
    BEND_90 = "bend_90"


class GeometrySpec(FrozenModel):
    type: GeometryType
    diameter_m: float = Field(gt=0)
    curvature_ratio: float | None = Field(default=None, gt=0)
    upstream_length_d: float = Field(default=20.0, ge=0)
    downstream_length_d: float = Field(default=30.0, ge=0)

    @model_validator(mode="after")
    def require_bend_curvature(self) -> "GeometrySpec":
        if self.type == GeometryType.BEND_90 and self.curvature_ratio is None:
            raise ValueError("curvature_ratio is required for bend_90")
        return self


class FluidSpec(FrozenModel):
    name: str = Field(default="water", min_length=1)
    density_kg_m3: float = Field(default=998.2, gt=0)
    dynamic_viscosity_pa_s: float = Field(default=1.002e-3, gt=0)
    temperature_k: float = Field(default=293.15, gt=0)
    phase: Literal["single_phase"] = "single_phase"
    compressibility: Literal["incompressible"] = "incompressible"


class VariableRange(FrozenModel):
    name: str = Field(min_length=1)
    minimum: float
    maximum: float
    scale: Literal["linear", "log"] = "linear"

    @model_validator(mode="after")
    def maximum_must_exceed_minimum(self) -> "VariableRange":
        if self.maximum <= self.minimum:
            raise ValueError("maximum must exceed minimum")
        if self.scale == "log" and self.minimum <= 0:
            raise ValueError("log ranges require a positive minimum")
        return self


class SimulationBudget(FrozenModel):
    max_cases: int = Field(default=60, ge=1, le=10_000)
    max_parallel: int = Field(default=8, ge=1, le=1_000)
    max_cpu_hours: float | None = Field(default=None, gt=0)


class ResearchSpec(StrictModel):
    question: str = Field(min_length=10)
    geometry: GeometrySpec
    fluid: FluidSpec
    independent_variables: tuple[VariableRange, ...] = ()
    responses: tuple[str, ...] = ("pressure_drop",)
    constraints: tuple[str, ...] = ("steady_state", "incompressible")
    simulation_budget: SimulationBudget = Field(default_factory=SimulationBudget)


class EvidenceItem(FrozenModel):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reviewed: bool = False


class EvidencePackage(FrozenModel):
    query: str = Field(min_length=1)
    items: tuple[EvidenceItem, ...] = ()
    coverage: dict[str, bool] = Field(default_factory=dict)
    conflicts: tuple[str, ...] = ()


class ExperimentPlan(FrozenModel):
    plan_id: str = Field(min_length=1)
    design_type: str = Field(min_length=1)
    pilot_case_ids: tuple[str, ...] = Field(min_length=1)
    mesh_levels: tuple[Literal["coarse", "medium", "fine"], ...] = (
        "coarse",
        "medium",
        "fine",
    )
    estimated_cpu_hours: float = Field(ge=0)


class CaseManifest(FrozenModel):
    case_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    template_id: str = Field(min_length=1)
    template_git_commit: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    solver: Literal["simpleFoam"]
    software_version: str = Field(min_length=1)
    artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    geometry: GeometrySpec
    physics: dict[str, float]
    resources: dict[str, int]
    expected_outputs: tuple[str, ...] = Field(min_length=1)
    created_at: datetime


class ValidationResult(FrozenModel):
    case_id: str = Field(min_length=1)
    iterative_convergence: float = Field(ge=0, le=1)
    mass_imbalance_percent: float = Field(ge=0)
    mass_conservation_passed: bool
    mesh_independence: float | None = Field(default=None, ge=0, le=1)
    benchmark_agreement: float | None = Field(default=None, ge=0, le=1)
    warnings: tuple[str, ...] = ()


class AnalysisResult(FrozenModel):
    project_id: str = Field(min_length=1)
    sample_count: int = Field(ge=1)
    metrics: dict[str, float]
    observations: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()


class ClaimLevel(StrEnum):
    DIRECT_OBSERVATION = "direct_observation"
    STATISTICAL_INFERENCE = "statistical_inference"
    LITERATURE_SUPPORT = "literature_support"
    MODEL_EXTRAPOLATION = "model_extrapolation"
    UNVERIFIED_HYPOTHESIS = "unverified_hypothesis"


class EvidenceLinkedClaim(FrozenModel):
    text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    level: ClaimLevel


class ResearchReport(FrozenModel):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    claims: tuple[EvidenceLinkedClaim, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = ()


class Approval(FrozenModel):
    gate: Literal["GATE_1", "GATE_2", "GATE_3"]
    approved_by: str = Field(min_length=1)
    approved_at: datetime
    subject_version: int = Field(ge=1)


class AuditEvent(FrozenModel):
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime
    actor: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
