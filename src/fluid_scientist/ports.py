"""Dependency-inversion ports for models, evidence, HPC, simulation, and persistence."""

import re
from collections.abc import Sequence
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
from fluid_scientist.operations.models import OperationKind, OperationRecord

MAX_GENERATED_CASE_ARCHIVE_BYTES = 16 * 1024 * 1024
MAX_GENERATED_CASE_DRAFT_JSON_BYTES = 9 * 1024 * 1024
MAX_GENERATED_CASE_PREVIEW_JSON_BYTES = 1024 * 1024


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
class StoredOperation:
    record: OperationRecord
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


@dataclass(frozen=True)
class StoredGeneratedCaseDraft:
    """Exact immutable result of a separately audited Case Builder call."""

    draft_id: str
    project_id: str
    plan_id: str
    plan_version: int
    version: int
    provider: str
    model: str
    draft_json: str
    archive_sha256: str
    archive: bytes
    preview_json: str

    def __post_init__(self) -> None:
        text_limits = {
            "draft_id": 128,
            "project_id": 128,
            "plan_id": 128,
            "provider": 32,
            "model": 128,
        }
        for field_name, maximum in text_limits.items():
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            if len(value) > maximum:
                raise ValueError(f"{field_name} exceeds its storage boundary")
        for field_name in ("plan_version", "version"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be an integer greater than or equal to 1")
        if not isinstance(self.draft_json, str) or not self.draft_json:
            raise ValueError("draft_json must be a non-empty string")
        self._require_utf8_size(
            "draft_json", self.draft_json, MAX_GENERATED_CASE_DRAFT_JSON_BYTES
        )
        if not isinstance(self.preview_json, str) or not self.preview_json:
            raise ValueError("preview_json must be a non-empty string")
        self._require_utf8_size(
            "preview_json", self.preview_json, MAX_GENERATED_CASE_PREVIEW_JSON_BYTES
        )
        if not isinstance(self.archive, bytes) or not self.archive:
            raise ValueError("archive must be non-empty bytes")
        if len(self.archive) > MAX_GENERATED_CASE_ARCHIVE_BYTES:
            raise ValueError("archive exceeds its storage boundary")
        if not isinstance(self.archive_sha256, str) or re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.archive_sha256
        ) is None:
            raise ValueError("archive_sha256 must be a lowercase sha256 digest")

    @staticmethod
    def _require_utf8_size(field_name: str, value: str, maximum: int) -> None:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError(f"{field_name} must be valid UTF-8") from error
        if size > maximum:
            raise ValueError(f"{field_name} exceeds its storage boundary")


@dataclass(frozen=True)
class StoredCandidateTemplate:
    """Immutable persistence record for a candidate template."""

    candidate_id: str
    draft_id: str
    project_id: str
    plan_id: str
    plan_version: int
    draft_version: int
    archive_sha256: str
    state: str
    rejection_reason: str | None
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        text_limits = {
            "candidate_id": 128,
            "draft_id": 128,
            "project_id": 128,
            "plan_id": 128,
        }
        for field_name, maximum in text_limits.items():
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
            if len(value) > maximum:
                raise ValueError(f"{field_name} exceeds its storage boundary")
        for field_name in ("plan_version", "draft_version"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if not isinstance(self.state, str) or not self.state.strip():
            raise ValueError("state must be a non-empty string")
        if self.rejection_reason is not None and (
            not isinstance(self.rejection_reason, str) or not self.rejection_reason.strip()
        ):
            raise ValueError("rejection_reason must be a non-empty string or None")


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

    def create_operation(self, record: OperationRecord) -> StoredOperation: ...

    def load_operation(self, operation_id: str) -> StoredOperation | None: ...

    def find_operation(
        self, kind: OperationKind, project_id: str, input_digest: str
    ) -> StoredOperation | None: ...

    def update_operation(
        self, record: OperationRecord, *, expected_version: int
    ) -> StoredOperation: ...

    def complete_planning_operation(
        self,
        plan: StoredExperimentPlan,
        record: OperationRecord,
        *,
        expected_version: int,
    ) -> StoredOperation: ...

    def list_interrupted_operations(self) -> Sequence[StoredOperation]: ...

    def bind_external_job(self, project_id: str, case_id: str, job_id: str) -> str: ...

    def store_experiment_plan(self, plan: StoredExperimentPlan) -> StoredExperimentPlan: ...

    def load_experiment_plan(self, plan_id: str) -> StoredExperimentPlan | None: ...

    def store_compiled_experiment(
        self, compiled: StoredCompiledExperiment
    ) -> StoredCompiledExperiment: ...

    def load_compiled_experiment(
        self, plan_id: str, plan_version: int
    ) -> StoredCompiledExperiment | None: ...

    def store_generated_case_draft(
        self, draft: StoredGeneratedCaseDraft
    ) -> StoredGeneratedCaseDraft: ...

    def load_generated_case_draft(self, draft_id: str) -> StoredGeneratedCaseDraft | None: ...

    def find_generated_case_draft(
        self, plan_id: str, plan_version: int, version: int
    ) -> StoredGeneratedCaseDraft | None: ...
