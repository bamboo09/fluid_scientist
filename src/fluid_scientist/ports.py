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


@dataclass(frozen=True)
class StoredExperimentPlan:
    plan_id: str
    project_id: str | None
    version: int
    provider: str
    model: str
    plan_json: str


@dataclass(frozen=True)
class StoredCompiledExperiment:
    plan_id: str
    plan_version: int
    archive_sha256: str
    archive: bytes
    preview_json: str


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

    def store_experiment_plan(self, plan: StoredExperimentPlan) -> StoredExperimentPlan: ...

    def load_experiment_plan(self, plan_id: str) -> StoredExperimentPlan | None: ...

    def store_compiled_experiment(
        self, compiled: StoredCompiledExperiment
    ) -> StoredCompiledExperiment: ...

    def load_compiled_experiment(
        self, plan_id: str, plan_version: int
    ) -> StoredCompiledExperiment | None: ...
