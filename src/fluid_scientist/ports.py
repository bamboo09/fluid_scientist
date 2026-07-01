"""Dependency-inversion ports for models, evidence, HPC, simulation, and persistence."""

from dataclasses import dataclass
from typing import Protocol

from fluid_scientist.domain.models import (
    AnalysisResult,
    CaseManifest,
    EvidenceLinkedClaim,
    EvidencePackage,
    ExperimentPlan,
    ResearchReport,
    ResearchSpec,
    ValidationResult,
)


@dataclass(frozen=True)
class SimulationResult:
    case_id: str
    grid_size: float
    pressure_drop_pa: float
    inlet_mass_flow: float
    outlet_mass_flow: float
    residuals: dict[str, list[float]]
    monitor_values: list[float]
    artifact_id: str


@dataclass(frozen=True)
class StoredWorkflow:
    project_id: str
    snapshot: str
    version: int


class LLMProvider(Protocol):
    def interpret(self, question: str) -> ResearchSpec: ...

    def analyze(
        self,
        analysis: AnalysisResult,
        evidence: EvidencePackage,
        simulations: tuple[SimulationResult, ...],
    ) -> tuple[EvidenceLinkedClaim, ...]: ...

    def review(self, report: ResearchReport, validation: ValidationResult) -> bool: ...


class EvidenceRetriever(Protocol):
    def retrieve(self, spec: ResearchSpec) -> EvidencePackage: ...


class SimulatorAdapter(Protocol):
    def design_pilot(self, project_id: str, spec: ResearchSpec) -> ExperimentPlan: ...

    def render_cases(
        self, project_id: str, spec: ResearchSpec, plan: ExperimentPlan
    ) -> tuple[CaseManifest, ...]: ...

    def run(self, case: CaseManifest) -> SimulationResult: ...


class JobScheduler(Protocol):
    def submit(self, case: CaseManifest) -> str: ...

    def result(self, job_id: str) -> SimulationResult: ...


class ArtifactStore(Protocol):
    def put_json(self, artifact_id: str, payload: str) -> str: ...

    def get_text(self, artifact_id: str) -> str: ...


class WorkflowRepository(Protocol):
    def save_snapshot(self, project_id: str, snapshot: str, *, expected_version: int) -> int: ...

    def load_snapshot(self, project_id: str) -> StoredWorkflow | None: ...

    def latest_project_id(self) -> str | None: ...

    def bind_external_job(self, project_id: str, case_id: str, job_id: str) -> str: ...
