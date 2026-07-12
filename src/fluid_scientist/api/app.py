"""FastAPI application for Fake demos and persistent research projects."""

import contextlib
import logging
import re
from collections.abc import Callable
from concurrent.futures import Executor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy.exc import IntegrityError

from fluid_scientist import __version__
from fluid_scientist.adapters.custom_openfoam import (
    CustomCaseManifest,
    CustomCaseRejected,
    validate_custom_case_archive,
)
from fluid_scientist.adapters.fakes import build_demo_service
from fluid_scientist.adapters.openai_provider import ExperimentDesign, OpenAIResponsesProvider
from fluid_scientist.adapters.openai_provider import (
    ProviderOutputError as LegacyProviderOutputError,
)
from fluid_scientist.adapters.openai_provider import (
    ProviderRequestError as LegacyProviderRequestError,
)
from fluid_scientist.adapters.openfoam import (
    LaminarPipeCase,
    PipeBenchmarkValidation,
    validate_laminar_pipe,
)
from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.candidate_templates.models import (
    ApproveCandidateRequest,
    CandidateState,
    CandidateTemplateRecord,
    CandidateTransitionError,
    CreateCandidateRequest,
    RejectCandidateRequest,
    assert_transition,
)
from fluid_scientist.compat import UTC
from fluid_scientist.execution.ssh import RemoteExecutionError, SSHTransport
from fluid_scientist.execution_targets.base import (
    ExecutionTargetAdapter,
    ExecutionTargetCapability,
)
from fluid_scientist.execution_targets.workstation import (
    WorkerCollection,
    WorkstationOpenFOAMTarget,
)
from fluid_scientist.experiment_planning import (
    ExperimentDesigner as PlanExperimentDesigner,
)
from fluid_scientist.experiment_planning import (
    ExperimentPlan,
    ProviderAuthenticationError,
    ProviderModelNotFoundError,
    ProviderOutputError,
    ProviderRequestError,
    create_plan_provider,
)
from fluid_scientist.experiment_planning.compilers import (
    CompilationError,
    UnsupportedCompilation,
    compile_plan,
)
from fluid_scientist.experiment_planning.result_analysis import (
    AnalysisEvidenceError,
    AnalysisProviderError,
    ExperimentAnalysis,
    ResultAnalyst,
    create_result_analyst,
)
from fluid_scientist.experiment_spec.compilation import (
    MissingRequiredParameterError,
    SpecNotConfirmedError,
    compile_spec,
    validate_required_parameters,
)
from fluid_scientist.experiment_spec.dependency import (
    change_summary,
    propagate_change,
)
from fluid_scientist.experiment_spec.migration import migrate_plan
from fluid_scientist.experiment_spec.models import (
    ExperimentSpec,
    ExperimentStatus,
)
from fluid_scientist.experiment_spec.state_machine import (
    TransitionError as SpecTransitionError,
)
from fluid_scientist.experiment_spec.state_machine import (
    assert_transition as assert_spec_transition,
)
from fluid_scientist.operations import (
    OperationKind,
    OperationRecord,
    OperationStage,
    OperationState,
)
from fluid_scientist.orchestration.workflow import TransitionError
from fluid_scientist.ports import (
    StoredCandidateTemplate,
    StoredCompiledExperiment,
    StoredExperimentPlan,
    StoredExperimentSpec,
    WorkflowRepository,
)
from fluid_scientist.research import (
    ClarificationRequired,  # noqa: F401
    DraftReady,  # noqa: F401
    IntentEngine,
    ResearchOrchestrator,
    ResearchSession,  # noqa: F401
    ResearchSessionStatus,  # noqa: F401
    ScopeEngine,
    SessionStore,
    UnsupportedRequest,  # noqa: F401
)
from fluid_scientist.services.model_configuration import (
    LegacyExperimentDesigner,
    ModelConfiguration,
    ProviderName,
)
from fluid_scientist.services.planning_operations import PlanningOperationService
from fluid_scientist.services.projects import ProjectService, ProjectView
from fluid_scientist.services.research import DemoResearchResult
from fluid_scientist.services.target_capabilities import (
    TargetCapabilityCache,
    TargetCapabilityStatus,
)
from fluid_scientist.settings import (
    AppSettings,
    NodeSettings,
    OpenAISettings,
    ProviderSettings,
)
from fluid_scientist.worker.service import JobRecord

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = ROOT / "apps" / "web"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoRequest(StrictRequest):
    question: str = Field(min_length=10, max_length=2_000)


class ProjectRequest(StrictRequest):
    question: str = Field(min_length=10, max_length=2_000)


class ExperimentDesignRequest(StrictRequest):
    question: str = Field(min_length=10, max_length=2_000)


class OpenAIConfigurationRequest(StrictRequest):
    api_key: SecretStr = Field(min_length=1)
    planner_model: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ] = "gpt-5.4"
    extractor_model: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ] = "gpt-5.4-mini"

    @field_validator("api_key")
    @classmethod
    def require_nonempty_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must not be empty")
        return value


class OpenAIConfigurationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    planner_model: str
    extractor_model: str


class ModelConfigurationRequest(StrictRequest):
    provider: Literal["openai", "glm", "deepseek"]
    model: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
    ]
    api_key: SecretStr = Field(min_length=1)
    base_url: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
    ] = None

    @field_validator("api_key")
    @classmethod
    def require_nonempty_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must not be empty")
        return value


class ModelConfigurationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configured: bool
    provider: Literal["openai", "glm", "deepseek"] | None
    model: str | None


class ExperimentPlanRequest(StrictRequest):
    question: str = Field(min_length=10, max_length=2_000)
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    target_id: str | None = Field(default=None, min_length=1, max_length=128)


class PlanOperationRequest(StrictRequest):
    project_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=10, max_length=2_000)
    target_id: str = Field(min_length=1, max_length=128)


class OperationView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    kind: OperationKind
    state: OperationState
    stage: OperationStage
    message: str
    result_ref: str | None
    safe_error: str | None
    cancel_requested: bool
    attempt: int
    created_at: datetime
    updated_at: datetime
    terminal: bool


class ExperimentPlanView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "glm", "deepseek"]
    model: str
    plan_id: str
    plan_version: int = Field(ge=1)
    project_id: str | None
    plan: ExperimentPlan


class CompilePreviewView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    plan_version: int = Field(ge=1)
    experiment_type: str
    archive_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifest: CustomCaseManifest
    preprocessing: tuple[str, ...]
    required_outputs: tuple[str, ...]


class ExperimentCapabilityView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_type: Literal[
        "laminar_pipe",
        "cylinder_flow",
        "lid_driven_cavity",
        "custom_openfoam",
    ]
    label: str
    required_outputs: tuple[str, ...]


EXPERIMENT_CAPABILITIES = (
    ExperimentCapabilityView(
        experiment_type="laminar_pipe",
        label="Laminar pipe",
        required_outputs=("pressure_drop", "mass_imbalance", "residuals"),
    ),
    ExperimentCapabilityView(
        experiment_type="cylinder_flow",
        label="Cylinder flow",
        required_outputs=(
            "drag_coefficient",
            "lift_coefficient",
            "strouhal_number",
            "mass_imbalance",
            "residuals",
        ),
    ),
    ExperimentCapabilityView(
        experiment_type="lid_driven_cavity",
        label="Lid-driven cavity",
        required_outputs=(
            "velocity_probes",
            "pressure_probes",
            "mass_imbalance",
            "residuals",
        ),
    ),
    ExperimentCapabilityView(
        experiment_type="custom_openfoam",
        label="Custom OpenFOAM case",
        required_outputs=("solver_logs", "requested_fields"),
    ),
)

PLAN_CAPABILITY_MARKERS = ("OpenFOAM-13", "workstation_openfoam")
PlanProviderFactory = Callable[[ProviderSettings], PlanExperimentDesigner]
ResultAnalystFactory = Callable[[ProviderSettings], ResultAnalyst]
LegacyProviderFactory = Callable[[OpenAISettings], LegacyExperimentDesigner]


class ApprovalRequest(StrictRequest):
    gate: Literal["GATE_1", "GATE_2", "GATE_3"]
    decision: Literal["approve", "reject"]
    actor: str = Field(min_length=1, max_length=128)
    subject_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2_000)
    plan_id: str | None = Field(default=None, min_length=1, max_length=128)
    plan_version: int | None = Field(default=None, ge=1)
    archive_sha256: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def require_complete_gate_two_binding(self) -> "ApprovalRequest":
        binding = (self.plan_id, self.plan_version, self.archive_sha256)
        if any(value is not None for value in binding) and not all(
            value is not None for value in binding
        ):
            raise ValueError("plan_id, plan_version, and archive_sha256 are required together")
        if self.plan_id is not None and (self.gate != "GATE_2" or self.decision != "approve"):
            raise ValueError("artifact binding is only valid for Gate 2 approval")
        return self


class ActionRequest(StrictRequest):
    action: str = Field(min_length=1, max_length=128)
    actor: str = Field(default="system", min_length=1, max_length=128)


class BenchmarkSubmissionRequest(StrictRequest):
    target_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(default="pilot-pipe", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    experiment_name: str = Field(default="laminar-pipe", min_length=1, max_length=80)
    case: LaminarPipeCase
    actor: str = Field(default="researcher", min_length=1, max_length=128)


class BenchmarkSubmissionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectView
    job: JobRecord


class PlannedExperimentSubmissionRequest(StrictRequest):
    target_id: str = Field(min_length=1, max_length=128)
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    actor: str = Field(default="researcher", min_length=1, max_length=128)
    archive_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class BenchmarkResultsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectView
    collection: WorkerCollection
    validation: PipeBenchmarkValidation


class ExperimentResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_type: str
    requested_outputs: tuple[str, ...]
    mesh_passed: bool
    solver_completed: bool
    cells: int
    final_residuals: dict[str, float]
    observables: dict[str, object]


class PlannedExperimentResultsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectView
    collection: WorkerCollection
    summary: ExperimentResultSummary


class ExperimentAnalysisView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "glm", "deepseek"]
    model: str
    summary: ExperimentResultSummary
    analysis: ExperimentAnalysis



class WorkbenchTurnRequest(BaseModel):
    """Request body for the workbench natural language turn endpoint."""

    experiment_id: str
    experiment_version: int
    message: str
    current_spec_hash: str | None = None


class ApplyEditRequest(BaseModel):
    """Request body for applying a confirmed EditProposal."""

    experiment_version: int
    proposal_id: str
    accepted_operation_indices: list[int] = Field(default_factory=list)


def _openai_model_configuration(
    settings: OpenAISettings,
    *,
    plan_provider_factory: PlanProviderFactory,
    legacy_provider_factory: LegacyProviderFactory,
    legacy_designer_override: LegacyExperimentDesigner | None = None,
) -> ModelConfiguration:
    if settings.api_key is None:
        raise ValueError("OpenAI api_key is required")
    plan_settings = ProviderSettings(
        provider="openai",
        api_key=settings.api_key,
        model=settings.planner_model,
        max_retries=settings.max_retries,
        timeout_seconds=settings.timeout_seconds,
    )
    plan_designer = plan_provider_factory(plan_settings)
    legacy_designer = (
        legacy_designer_override
        if legacy_designer_override is not None
        else legacy_provider_factory(settings)
    )
    return ModelConfiguration(
        provider="openai",
        model=settings.planner_model,
        plan_designer=plan_designer,
        result_analyst=create_result_analyst(plan_settings),
        legacy_designer=legacy_designer,
    )


def _build_llm_client(settings: AppSettings) -> Any:
    """构建 LLM 客户端，如果未配置则返回 None。"""
    if not settings.provider or not settings.provider.api_key:
        return None
    provider = settings.provider.provider
    if provider in ("glm", "deepseek"):
        from openai import OpenAI

        base_urls = {
            "glm": "https://open.bigmodel.cn/api/paas/v4/",
            "deepseek": "https://api.deepseek.com",
        }
        return OpenAI(
            api_key=settings.provider.api_key.get_secret_value(),
            base_url=base_urls[provider],
            timeout=settings.provider.timeout_seconds,
            max_retries=0,
        )
    elif provider == "openai" and settings.openai.api_key:
        from openai import OpenAI

        return OpenAI(api_key=settings.openai.api_key.get_secret_value())
    return None


def create_app(
    repository: WorkflowRepository | None = None,
    execution_targets: tuple[ExecutionTargetAdapter, ...] | None = None,
    *,
    settings: AppSettings | None = None,
    transport_factory: Callable[[NodeSettings], SSHTransport] = SSHTransport,
    experiment_designer: LegacyExperimentDesigner | None = None,
    plan_designer: PlanExperimentDesigner | None = None,
    result_analyst: ResultAnalyst | None = None,
    plan_provider_name: ProviderName | None = None,
    plan_model_name: str | None = None,
    plan_provider_factory: PlanProviderFactory = create_plan_provider,
    result_analyst_factory: ResultAnalystFactory = create_result_analyst,
    legacy_provider_factory: LegacyProviderFactory = OpenAIResponsesProvider,
    model_configuration: ModelConfiguration | None = None,
    planning_executor: Executor | None = None,
    target_capability_cache: TargetCapabilityCache | None = None,
) -> FastAPI:
    runtime_settings = settings or AppSettings()
    configured_targets = execution_targets
    if configured_targets is None:
        configured_targets = build_execution_targets(runtime_settings, transport_factory)
    coupled_plan_args = (plan_designer, plan_provider_name, plan_model_name)
    if model_configuration is not None and (
        experiment_designer is not None
        or result_analyst is not None
        or any(value is not None for value in coupled_plan_args)
    ):
        raise ValueError(
            "model_configuration cannot be combined with designer injection arguments"
        )
    if any(value is not None for value in coupled_plan_args) and not all(
        value is not None for value in coupled_plan_args
    ):
        raise ValueError(
            "plan_designer, plan_provider_name, and plan_model_name must be provided together"
        )
    if model_configuration is not None:
        configured_models = model_configuration
    elif plan_designer is not None:
        if plan_provider_name != "openai" and experiment_designer is not None:
            raise ValueError("non-OpenAI plan configuration cannot use a legacy designer")
        configured_models = ModelConfiguration(
            provider=plan_provider_name,
            model=plan_model_name,
            plan_designer=plan_designer,
            result_analyst=result_analyst,
            legacy_designer=experiment_designer,
        )
    elif runtime_settings.openai.api_key is not None:
        configured_models = _openai_model_configuration(
            runtime_settings.openai,
            plan_provider_factory=plan_provider_factory,
            legacy_provider_factory=legacy_provider_factory,
            legacy_designer_override=experiment_designer,
        )
    else:
        configured_models = ModelConfiguration(legacy_designer=experiment_designer)
    workflow_repository = repository or SQLWorkflowRepository(runtime_settings.database.url)
    planning_service = PlanningOperationService(
        workflow_repository,
        executor=planning_executor,
    )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        from fluid_scientist.capabilities import get_capability_registry

        get_capability_registry().health_check(mutate=True)
        planning_service.recover_interrupted()
        try:
            yield
        finally:
            planning_service.shutdown(wait=False, cancel_futures=True)

    application = FastAPI(
        title="Fluid Scientist",
        version="0.4.0",
        description="Evidence-grounded fluid mechanics research workflow",
        lifespan=lifespan,
    )
    application.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")

    # Prevent browser from caching stale JS/CSS during development
    @application.middleware("http")
    async def no_cache_static(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    # Mount the v5 study-decomposer draft-workflow API
    from fluid_scientist.api.v5_router import router as _v5_router
    application.include_router(_v5_router)
    from fluid_scientist.api.workstation_router import router as workstation_router
    application.include_router(workstation_router)
    application.state.execution_targets = configured_targets
    application.state.model_configuration = configured_models
    application.state.planning_operation_service = planning_service
    target_registry = {target.target_id: target for target in configured_targets}
    capability_cache = target_capability_cache or TargetCapabilityCache()
    application.state.target_capability_cache = capability_cache
    research_session_store = SessionStore()
    from fluid_scientist.code_extension.registry import ExtensionRegistry
    from fluid_scientist.measurement.planner import MetricPlanner
    from fluid_scientist.research.spec_factory import ExperimentSpecFactory

    extension_registry = ExtensionRegistry()
    research_orchestrator = ResearchOrchestrator(
        session_store=research_session_store,
        intent_engine=IntentEngine(
            llm_client=_build_llm_client(runtime_settings),
            model_name=runtime_settings.openai.planner_model,
            provider_name=runtime_settings.provider.provider if runtime_settings.provider else None,
        ),
        scope_engine=ScopeEngine(),
        spec_factory=ExperimentSpecFactory(),
        workflow_repository=workflow_repository,
        metric_planner=MetricPlanner(),
        extension_registry=extension_registry,
    )
    application.state.research_session_store = research_session_store
    application.state.research_orchestrator = research_orchestrator
    application.state.metric_results_store: dict[str, list[dict]] = {}
    application.state.workbench_proposals: dict[str, dict] = {}
    project_service = ProjectService(workflow_repository)
    demo_projects: dict[str, DemoResearchResult] = {}

    @application.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": runtime_settings.app_mode.value}

    @application.get("/api/system/version")
    def get_system_version() -> dict:
        """Return system version information for deployment verification."""
        import subprocess
        try:
            git_sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=Path(__file__).resolve().parent,
            ).decode().strip()
        except Exception:
            git_sha = "unknown"

        return {
            "workflow": "v5",
            "git_commit": git_sha,
            "api_version": "5.0",
            "schema_version": "5.0",
            "native_compile_enabled": True,
            "measurement_plan_compile_enabled": True,
            "package_version": __version__,
            "workflow_v2_enabled": runtime_settings.research_workflow_v2,
        }

    def _pipeline_work_root() -> Path:
        database_url = runtime_settings.database.url
        if database_url.startswith("sqlite:///") and database_url not in {
            "sqlite://",
            "sqlite:///:memory:",
        }:
            database_path = Path(database_url.removeprefix("sqlite:///")).expanduser()
            if not database_path.is_absolute():
                database_path = Path.cwd() / database_path
            return database_path.parent / "compile_ready_pipeline"
        return Path.cwd() / ".fluid_scientist" / "compile_ready_pipeline"

    def _selected_research_text(session, message: str) -> str:
        text = message.strip()
        for prefix in (
            "选择研究任务:",
            "选择研究任务：",
            "选择研究任务",
            "Selected research task:",
        ):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
                break
        return text or session.original_request

    def _run_compile_ready_pipeline_for_session(session, message: str) -> dict:
        import json

        from fluid_scientist.ports import StoredExperimentSpec
        from fluid_scientist.research.models import ResearchSessionStatus
        from fluid_scientist.workflow_pipeline import PipelineStatus, V5WorkflowPipeline

        state = V5WorkflowPipeline(
            work_root=_pipeline_work_root(),
            llm_client=_build_llm_client(runtime_settings),
        ).run(
            user_description=_selected_research_text(session, message),
            session_id=session.session_id,
        )
        pipeline_payload = {
            "status": state.current_stage,
            "stage_history": [s.model_dump(mode="json") for s in state.stage_history],
            "failure": state.failure,
            "case_dir": state.case_dir,
            "validation_report": state.validation_report,
        }
        if state.current_stage != PipelineStatus.COMPILE_READY or state.draft_view is None:
            research_session_store.update(
                session.session_id,
                status=ResearchSessionStatus.UNSUPPORTED,
                accumulated_context={
                    **session.accumulated_context,
                    "compile_ready_pipeline": pipeline_payload,
                },
                updated_at=datetime.now(UTC).isoformat(),
            )
            return {
                "type": "pipeline_failed",
                "session_id": session.session_id,
                **pipeline_payload,
            }

        now = datetime.now(UTC).isoformat()
        draft_id = state.draft_view.get("draft_id") or f"draft-{session.session_id}"
        stored_spec = StoredExperimentSpec(
            experiment_id=draft_id,
            project_id=session.project_id or None,
            schema_version="3.0.0",
            experiment_version=1,
            status=PipelineStatus.COMPILE_READY,
            task_type="new_simulation",
            interaction_mode="standard",
            spec_json=json.dumps(state.draft_view, ensure_ascii=False, default=str),
            created_at=now,
            updated_at=now,
        )
        workflow_repository.save_experiment_spec(stored_spec)
        research_session_store.update(
            session.session_id,
            status=ResearchSessionStatus.DRAFT_READY,
            experiment_spec_id=draft_id,
            accumulated_context={
                **session.accumulated_context,
                "compile_ready_pipeline": pipeline_payload,
            },
            updated_at=datetime.now(UTC).isoformat(),
        )
        return {
            "type": "draft_ready",
            "session_id": session.session_id,
            "experiment_spec_id": draft_id,
            "experiment_version": 1,
            "warnings": [],
            "compile_ready_view": state.draft_view,
            "case_dir": state.case_dir,
        }

    # ===== Research Session API (Workflow V2) =====

    @application.post(
        "/api/research-sessions",
        status_code=status.HTTP_201_CREATED,
        tags=["research-sessions"],
    )
    def create_research_session(body: dict = Body(...)) -> dict:  # noqa: B008
        """创建新的研究会话并处理第一轮输入。"""
        from fluid_scientist.research.models import (
            ResearchSession,
            ResearchSessionStatus,
        )

        project_id = body.get("project_id", "")
        message = body.get("message", "")
        if not message.strip():
            raise HTTPException(status_code=422, detail="message is required")
        # 确保 project 存在（如果 project_id 不为空）
        if project_id:
            try:
                project_service.get(project_id)
            except KeyError as error:
                raise HTTPException(
                    status_code=404, detail="project not found"
                ) from error
        now = datetime.now(UTC).isoformat()
        session_id = uuid4().hex[:12]
        research_session_store.create(
            ResearchSession(
                session_id=session_id,
                project_id=project_id,
                status=ResearchSessionStatus.COLLECTING_REQUIREMENTS,
                original_request=message,
                created_at=now,
                updated_at=now,
            )
        )
        session = research_session_store.get(session_id)
        return _run_compile_ready_pipeline_for_session(session, message)

    @application.post(
        "/api/research-sessions/{session_id}/turns",
        tags=["research-sessions"],
    )
    def continue_research_session(
        session_id: str, body: dict = Body(...)  # noqa: B008
    ) -> dict:
        """继续研究会话，处理用户的后续输入。"""
        message = body.get("message", "")
        if not message.strip():
            raise HTTPException(status_code=422, detail="message is required")
        try:
            session = research_session_store.get(session_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="research session not found"
            ) from error
        if session.experiment_spec_id is None:
            return _run_compile_ready_pipeline_for_session(session, message)
        result = research_orchestrator.handle_turn(
            session_id=session_id,
            user_message=message,
        )
        return result.model_dump()

    @application.get(
        "/api/research-sessions/{session_id}",
        tags=["research-sessions"],
    )
    def get_research_session(session_id: str) -> dict:
        """获取研究会话的当前状态。"""
        try:
            session = research_session_store.get(session_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="research session not found"
            ) from error
        return session.model_dump()

    @application.get(
        "/api/research-sessions/{session_id}/experiment-spec",
        tags=["research-sessions"],
    )
    def get_session_experiment_spec(session_id: str) -> dict:
        """获取研究会话关联的实验规格。"""
        import json

        try:
            session = research_session_store.get(session_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="research session not found"
            ) from error
        if session.experiment_spec_id is None:
            raise HTTPException(
                status_code=404,
                detail="no experiment spec associated with this session",
            )
        try:
            stored = workflow_repository.load_experiment_spec(
                session.experiment_spec_id
            )
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="experiment spec not found"
            ) from error
        return json.loads(stored.spec_json)

    @application.get(
        "/api/research-sessions/{session_id}/missing-capabilities",
        tags=["research-sessions"],
    )
    def get_missing_capabilities(session_id: str) -> dict:
        """获取研究会话中的缺失能力列表。"""
        try:
            session = research_session_store.get(session_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="research session not found"
            ) from error
        return {
            "missing_capabilities": [
                cap.model_dump() for cap in session.missing_capabilities
            ]
        }

    @application.post(
        "/api/demo",
        response_model=DemoResearchResult,
        status_code=status.HTTP_201_CREATED,
    )
    def run_demo(request: DemoRequest) -> DemoResearchResult:
        result = build_demo_service().run_approved_demo(request.question)
        demo_projects[result.project_id] = result
        return result

    @application.post("/api/experiment-designs", response_model=ExperimentDesign)
    def design_experiment(request: ExperimentDesignRequest) -> ExperimentDesign:
        model_snapshot = application.state.model_configuration
        if model_snapshot.provider not in {None, "openai"}:
            raise HTTPException(
                status_code=409,
                detail="Selected provider supports /api/experiment-plans only",
            )
        designer = model_snapshot.legacy_designer
        if designer is None:
            raise HTTPException(
                status_code=503,
                detail="OpenAI experiment designer is not configured",
            )
        try:
            return designer.design_experiment(
                request.question,
                capabilities=(
                    "OpenFOAM-13",
                    "workstation_openfoam",
                    "laminar_pipe",
                    "custom_openfoam",
                ),
            )
        except LegacyProviderOutputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except LegacyProviderRequestError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.post("/api/settings/openai", response_model=OpenAIConfigurationView)
    def configure_openai(
        request: OpenAIConfigurationRequest,
    ) -> OpenAIConfigurationView:
        model_settings = OpenAISettings(
            api_key=request.api_key,
            planner_model=request.planner_model,
            extractor_model=request.extractor_model,
        )
        configured_models = _openai_model_configuration(
            model_settings,
            plan_provider_factory=plan_provider_factory,
            legacy_provider_factory=legacy_provider_factory,
        )
        application.state.model_configuration = configured_models
        return OpenAIConfigurationView(
            configured=True,
            planner_model=model_settings.planner_model,
            extractor_model=model_settings.extractor_model,
        )

    @application.post(
        "/api/model-configurations", response_model=ModelConfigurationView
    )
    def configure_plan_provider(
        request: ModelConfigurationRequest,
    ) -> ModelConfigurationView:
        provider_settings = ProviderSettings(
            provider=request.provider,
            model=request.model,
            api_key=request.api_key,
        )
        designer = plan_provider_factory(provider_settings)
        analyst = result_analyst_factory(provider_settings)
        legacy_designer = None
        if provider_settings.provider == "openai":
            legacy_settings = OpenAISettings(
                api_key=provider_settings.api_key,
                planner_model=provider_settings.model,
                extractor_model=provider_settings.model,
            )
            legacy_designer = legacy_provider_factory(legacy_settings)
        configured_models = ModelConfiguration(
            provider=provider_settings.provider,
            model=provider_settings.model,
            plan_designer=designer,
            result_analyst=analyst,
            legacy_designer=legacy_designer,
        )
        application.state.model_configuration = configured_models
        from fluid_scientist.api import v5_router as _v5_workflow

        _v5_workflow.configure_llm_client(
            provider=provider_settings.provider,
            model=provider_settings.model,
            api_key=provider_settings.api_key.get_secret_value(),
            base_url=request.base_url,
            timeout_seconds=provider_settings.timeout_seconds,
        )
        return ModelConfigurationView(
            configured=True,
            provider=provider_settings.provider,
            model=provider_settings.model,
        )

    @application.get(
        "/api/model-configurations", response_model=ModelConfigurationView
    )
    def get_plan_provider_configuration() -> ModelConfigurationView:
        model_snapshot = application.state.model_configuration
        return ModelConfigurationView(
            configured=model_snapshot.configured,
            provider=model_snapshot.provider,
            model=model_snapshot.model,
        )

    @application.get(
        "/api/experiment-capabilities",
        response_model=tuple[ExperimentCapabilityView, ...],
    )
    def list_experiment_capabilities() -> tuple[ExperimentCapabilityView, ...]:
        return EXPERIMENT_CAPABILITIES

    @application.post(
        "/api/plan-operations",
        response_model=OperationView,
        status_code=status.HTTP_202_ACCEPTED,
        deprecated=True,
        tags=["deprecated"],
    )
    def create_plan_operation(request: PlanOperationRequest) -> OperationView:
        try:
            project_service.get(request.project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="project not found") from error
        target = target_registry.get(request.target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="execution target not found")
        model_snapshot = application.state.model_configuration
        designer = model_snapshot.plan_designer
        provider = model_snapshot.provider
        model = model_snapshot.model
        if designer is None or provider is None or model is None:
            raise HTTPException(
                status_code=503,
                detail="Experiment plan provider is not configured",
            )
        capabilities = _planning_capabilities(target)
        operation = planning_service.submit(
            project_id=request.project_id,
            question=request.question,
            provider=provider,
            model=model,
            designer=designer,
            capabilities=capabilities,
        )
        return _operation_view(operation)

    @application.get("/api/operations/{operation_id}", response_model=OperationView)
    def get_operation(operation_id: str) -> OperationView:
        try:
            return _operation_view(planning_service.get(operation_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="operation not found") from error

    @application.delete("/api/operations/{operation_id}", response_model=OperationView)
    def cancel_operation(operation_id: str) -> OperationView:
        try:
            return _operation_view(planning_service.cancel(operation_id))
        except KeyError as error:
            raise HTTPException(status_code=404, detail="operation not found") from error

    @application.post(
        "/api/experiment-plans",
        response_model=ExperimentPlanView,
        deprecated=True,
        description="Internal compatibility endpoint; use /api/plan-operations.",
    )
    def create_experiment_plan(request: ExperimentPlanRequest) -> ExperimentPlanView:
        model_snapshot = application.state.model_configuration
        designer = model_snapshot.plan_designer
        provider = model_snapshot.provider
        model = model_snapshot.model
        if designer is None or provider is None or model is None:
            raise HTTPException(
                status_code=503,
                detail="Experiment plan provider is not configured",
            )
        capabilities = tuple(
            capability.experiment_type for capability in EXPERIMENT_CAPABILITIES
        ) + PLAN_CAPABILITY_MARKERS
        if request.target_id is not None:
            target = target_registry.get(request.target_id)
            if target is None:
                raise HTTPException(
                    status_code=404, detail="execution target not found"
                )
            capabilities = _planning_capabilities(target)
        try:
            designed_plan = designer.design_experiment(
                request.question,
                capabilities=capabilities,
            )
        except ProviderAuthenticationError as error:
            raise HTTPException(
                status_code=401, detail="Provider authentication failed"
            ) from error
        except ProviderModelNotFoundError as error:
            raise HTTPException(
                status_code=422, detail="Provider model was not found"
            ) from error
        except ProviderOutputError as error:
            raise HTTPException(
                status_code=422,
                detail="模型生成的实验计划未通过严格参数校验；请重试或补充研究条件。",
            ) from error
        except ProviderRequestError as error:
            raise HTTPException(
                status_code=502,
                detail="Experiment plan provider request failed",
            ) from error
        plan = (
            designed_plan
            if isinstance(designed_plan, ExperimentPlan)
            else ExperimentPlan(root=designed_plan)
        )
        plan_id = str(uuid4())
        stored = project_service.store_experiment_plan(
            StoredExperimentPlan(
                plan_id=plan_id,
                project_id=request.project_id,
                version=1,
                provider=provider,
                model=model,
                plan_json=plan.model_dump_json(),
            )
        )
        return ExperimentPlanView(
            provider=provider,
            model=model,
            plan_id=stored.plan_id,
            plan_version=stored.version,
            project_id=stored.project_id,
            plan=plan,
        )

    @application.get(
        "/api/experiment-plans/{plan_id}", response_model=ExperimentPlanView
    )
    def get_experiment_plan(plan_id: str) -> ExperimentPlanView:
        try:
            stored = project_service.load_experiment_plan(plan_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="experiment plan not found"
            ) from error
        return ExperimentPlanView(
            provider=stored.provider,
            model=stored.model,
            plan_id=stored.plan_id,
            plan_version=stored.version,
            project_id=stored.project_id,
            plan=ExperimentPlan.model_validate_json(stored.plan_json),
        )

    @application.post(
        "/api/experiment-plans/{plan_id}/compile",
        response_model=CompilePreviewView,
    )
    def compile_experiment_plan(plan_id: str) -> CompilePreviewView:
        try:
            stored_plan = project_service.load_experiment_plan(plan_id)
            existing = project_service.load_compiled_experiment(
                plan_id, stored_plan.version
            )
            return CompilePreviewView.model_validate_json(existing.preview_json)
        except KeyError:
            pass
        try:
            stored_plan = project_service.load_experiment_plan(plan_id)
            plan = ExperimentPlan.model_validate_json(stored_plan.plan_json)
            compiled = compile_plan(plan)
            preview = CompilePreviewView(
                plan_id=plan_id,
                plan_version=stored_plan.version,
                experiment_type=compiled.experiment_type,
                archive_sha256=compiled.archive_sha256,
                manifest=compiled.manifest,
                preprocessing=compiled.preprocessing,
                required_outputs=compiled.required_outputs,
            )
            project_service.store_compiled_experiment(
                StoredCompiledExperiment(
                    experiment_id=plan_id,
                    plan_version=stored_plan.version,
                    archive_sha256=compiled.archive_sha256,
                    archive=compiled.archive,
                    preview_json=preview.model_dump_json(),
                )
            )
            return preview
        except KeyError as error:
            raise HTTPException(status_code=404, detail="experiment plan not found") from error
        except UnsupportedCompilation as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except CompilationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.post("/api/custom-cases/validate", response_model=CustomCaseManifest)
    def validate_custom_case(
        payload: bytes = Body(media_type="application/gzip"),
    ) -> CustomCaseManifest:
        try:
            return validate_custom_case_archive(payload)
        except CustomCaseRejected as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.post(
        "/api/custom-cases/submit",
        response_model=JobRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def submit_custom_case(
        target_id: str,
        experiment_name: str,
        payload: bytes = Body(media_type="application/gzip"),
    ) -> JobRecord:
        target = target_registry.get(target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="execution target not found")
        submit_custom = getattr(target, "submit_custom", None)
        if not callable(submit_custom):
            raise HTTPException(
                status_code=422,
                detail="execution target does not support custom OpenFOAM cases",
            )
        job_id = _experiment_job_id(str(uuid4()), experiment_name)
        try:
            return _submit_custom_fresh(target, submit_custom, job_id, payload)
        except CustomCaseRejected as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (RemoteExecutionError, OSError) as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.get("/api/custom-cases/{job_id}", response_model=JobRecord)
    def custom_case_status(job_id: str, target_id: str) -> JobRecord:
        target = target_registry.get(target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="execution target not found")
        try:
            return target.status(job_id)
        except RemoteExecutionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.get(
        "/api/custom-cases/{job_id}/results",
        response_model=WorkerCollection,
    )
    def custom_case_results(job_id: str, target_id: str) -> WorkerCollection:
        target = target_registry.get(target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="execution target not found")
        try:
            return target.collect(job_id)
        except RemoteExecutionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.get("/api/demo/{project_id}", response_model=DemoResearchResult)
    def get_demo(project_id: str) -> DemoResearchResult:
        try:
            return demo_projects[project_id]
        except KeyError as error:
            raise HTTPException(status_code=404, detail="demo project not found") from error

    @application.post(
        "/api/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED
    )
    def create_project(request: ProjectRequest) -> ProjectView:
        return project_service.create(request.question)

    @application.get("/api/projects/recent", response_model=ProjectView)
    def recent_project() -> ProjectView:
        try:
            return project_service.recent()
        except KeyError as error:
            raise HTTPException(status_code=404, detail="no projects exist") from error

    @application.get("/api/projects/{project_id}", response_model=ProjectView | DemoResearchResult)
    def get_project(project_id: str) -> ProjectView | DemoResearchResult:
        if project_id in demo_projects:
            return demo_projects[project_id]
        try:
            return project_service.get(project_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="project not found") from error

    @application.post("/api/projects/{project_id}/approvals", response_model=ProjectView)
    def decide_approval(project_id: str, request: ApprovalRequest) -> ProjectView:
        try:
            return project_service.decide(project_id, **request.model_dump())
        except KeyError as error:
            raise HTTPException(status_code=404, detail="project not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except TransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.post("/api/projects/{project_id}/actions", response_model=ProjectView)
    def apply_action(project_id: str, request: ActionRequest) -> ProjectView:
        try:
            return project_service.act(project_id, request.action, actor=request.actor)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="project not found") from error
        except TransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @application.post(
        "/api/projects/{project_id}/benchmarks",
        response_model=BenchmarkSubmissionView,
        status_code=status.HTTP_201_CREATED,
    )
    def submit_benchmark(
        project_id: str, request: BenchmarkSubmissionRequest
    ) -> BenchmarkSubmissionView:
        target = target_registry.get(request.target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="execution target not found")
        try:
            existing = project_service.prepare_pilot_submission(project_id, request.case_id)
            submit = getattr(target, "submit", None)
            status_method = getattr(target, "status", None)
            if not callable(submit) or not callable(status_method):
                raise ValueError("execution target does not support benchmark jobs")
            job_id = existing or _experiment_job_id(project_id, request.experiment_name)
            if existing:
                job = status_method(job_id)
            else:
                _require_fresh_target(target)
                job = submit(job_id, request.case)
            project = project_service.record_pilot_submission(
                project_id,
                case_id=request.case_id,
                job_id=job.job_id,
                target_id=request.target_id,
                actor=request.actor,
            )
            return BenchmarkSubmissionView(project=project, job=job)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="project not found") from error
        except TransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RemoteExecutionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.post(
        "/api/projects/{project_id}/experiment-plans/{plan_id}/submit",
        response_model=BenchmarkSubmissionView,
        status_code=status.HTTP_201_CREATED,
        deprecated=True,
        tags=["deprecated"],
    )
    def submit_planned_experiment(
        project_id: str,
        plan_id: str,
        request: PlannedExperimentSubmissionRequest,
    ) -> BenchmarkSubmissionView:
        if runtime_settings.research_workflow_v2:
            logger.warning(
                "Old plan-based endpoint called while V2 is enabled: %s",
                "submit",
            )
        target = target_registry.get(request.target_id)
        if target is None:
            raise HTTPException(status_code=404, detail="execution target not found")
        submit_custom = getattr(target, "submit_custom", None)
        status_method = getattr(target, "status", None)
        if not callable(submit_custom) or not callable(status_method):
            raise HTTPException(
                status_code=422,
                detail="execution target does not support compiled OpenFOAM cases",
            )
        try:
            existing, stored_plan, compiled = (
                project_service.prepare_bound_experiment_submission(
                    project_id,
                    plan_id=plan_id,
                    case_id=request.case_id,
                    archive_sha256=request.archive_sha256,
                )
            )
            plan = ExperimentPlan.model_validate_json(stored_plan.plan_json)
            experiment_name = plan.root.experiment_name
            gate_two = next(
                approval
                for approval in project_service.get(project_id).approvals
                if approval.gate == "GATE_2"
            )
            job_id = existing or _experiment_job_id(
                project_id,
                experiment_name,
                timestamp=gate_two.approved_at,
            )
            job = (
                status_method(job_id)
                if existing
                else _submit_custom_fresh(target, submit_custom, job_id, compiled.archive)
            )
            project = project_service.record_pilot_submission(
                project_id,
                case_id=request.case_id,
                job_id=job.job_id,
                target_id=request.target_id,
                actor=request.actor,
            )
            return BenchmarkSubmissionView(project=project, job=job)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except TransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RemoteExecutionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.get(
        "/api/projects/{project_id}/experiment-plans/{plan_id}/results",
        response_model=PlannedExperimentResultsView,
        deprecated=True,
        tags=["deprecated"],
    )
    def planned_experiment_results(
        project_id: str,
        plan_id: str,
        target_id: str,
        case_id: str,
    ) -> PlannedExperimentResultsView:
        if runtime_settings.research_workflow_v2:
            logger.warning(
                "Old plan-based endpoint called while V2 is enabled: %s",
                "results",
            )
        target, job_id = _bound_benchmark(
            project_service, target_registry, project_id, case_id, target_id
        )
        collect = getattr(target, "collect", None)
        if not callable(collect):
            raise HTTPException(status_code=422, detail="execution target cannot collect results")
        try:
            stored_plan = project_service.load_experiment_plan(plan_id)
            if stored_plan.project_id != project_id:
                raise TransitionError("experiment plan belongs to a different project")
            collection = collect(job_id)
            if collection.state != "succeeded":
                raise TransitionError("experiment results are not ready")
            plan = ExperimentPlan.model_validate_json(stored_plan.plan_json)
            summary = ExperimentResultSummary(
                experiment_type=plan.root.experiment_type,
                requested_outputs=plan.root.requested_outputs,
                mesh_passed=collection.mesh.passed,
                solver_completed=collection.solver.completed,
                cells=collection.mesh.cells,
                final_residuals=collection.solver.final_residuals,
                observables=collection.observables.model_dump(
                    exclude_none=True,
                    exclude_defaults=True,
                ),
            )
            project = project_service.verify_pilot(
                project_id,
                case_id=case_id,
                validation=summary.model_dump(mode="json"),
                actor="validator",
            )
            return PlannedExperimentResultsView(
                project=project,
                collection=collection,
                summary=summary,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except TransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RemoteExecutionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.post(
        "/api/projects/{project_id}/experiment-plans/{plan_id}/analysis",
        response_model=ExperimentAnalysisView,
        deprecated=True,
        tags=["deprecated"],
    )
    def analyze_planned_experiment(
        project_id: str,
        plan_id: str,
        target_id: str,
        case_id: str,
    ) -> ExperimentAnalysisView:
        if runtime_settings.research_workflow_v2:
            logger.warning(
                "Old plan-based endpoint called while V2 is enabled: %s",
                "analysis",
            )
        model_snapshot = application.state.model_configuration
        analyst = model_snapshot.result_analyst
        if analyst is None or model_snapshot.provider is None or model_snapshot.model is None:
            raise HTTPException(status_code=503, detail="Result analyst is not configured")
        target, job_id = _bound_benchmark(
            project_service, target_registry, project_id, case_id, target_id
        )
        collect = getattr(target, "collect", None)
        if not callable(collect):
            raise HTTPException(status_code=422, detail="execution target cannot collect results")
        try:
            stored_plan = project_service.load_experiment_plan(plan_id)
            if stored_plan.project_id != project_id:
                raise TransitionError("experiment plan belongs to a different project")
            plan = ExperimentPlan.model_validate_json(stored_plan.plan_json)
            collection = collect(job_id)
            summary = ExperimentResultSummary(
                experiment_type=plan.root.experiment_type,
                requested_outputs=plan.root.requested_outputs,
                mesh_passed=collection.mesh.passed,
                solver_completed=collection.solver.completed,
                cells=collection.mesh.cells,
                final_residuals=collection.solver.final_residuals,
                observables=collection.observables.model_dump(
                    exclude_none=True,
                    exclude_defaults=True,
                ),
            )
            evidence = {
                "mesh": collection.mesh.model_dump(mode="json"),
                "solver": collection.solver.model_dump(mode="json"),
                "observables": summary.observables,
                "plan": {
                    "experiment_type": summary.experiment_type,
                    "requested_outputs": list(summary.requested_outputs),
                },
            }
            analysis = analyst.analyze(
                evidence,
                evidence_keys=tuple(sorted(_leaf_evidence_keys(evidence))),
            )
            return ExperimentAnalysisView(
                provider=model_snapshot.provider,
                model=model_snapshot.model,
                summary=summary,
                analysis=analysis,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except TransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except AnalysisEvidenceError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except AnalysisProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except RemoteExecutionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.get("/api/projects/{project_id}/benchmarks/{case_id}", response_model=JobRecord)
    def benchmark_status(project_id: str, case_id: str, target_id: str) -> JobRecord:
        target, job_id = _bound_benchmark(
            project_service, target_registry, project_id, case_id, target_id
        )
        status_method = getattr(target, "status", None)
        if not callable(status_method):
            raise HTTPException(status_code=422, detail="execution target has no job status")
        try:
            return status_method(job_id)
        except RemoteExecutionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    @application.get(
        "/api/projects/{project_id}/benchmarks/{case_id}/results",
        response_model=BenchmarkResultsView,
    )
    def benchmark_results(project_id: str, case_id: str, target_id: str) -> BenchmarkResultsView:
        target, job_id = _bound_benchmark(
            project_service, target_registry, project_id, case_id, target_id
        )
        collect = getattr(target, "collect", None)
        if not callable(collect):
            raise HTTPException(status_code=422, detail="execution target has no result collection")
        try:
            job = target.status(job_id)
            collection = collect(job_id)
            validation = validate_laminar_pipe(
                job.spec,
                pressure_drop_pa=collection.solver.pressure_drop_pa,
                inlet_mass_flow=collection.solver.inlet_mass_flow,
                outlet_mass_flow=collection.solver.outlet_mass_flow,
                final_residuals=collection.solver.final_residuals,
            )
            project = project_service.get(project_id)
            if validation.passed:
                project = project_service.verify_pilot(
                    project_id,
                    case_id=case_id,
                    validation=validation.model_dump(),
                )
            return BenchmarkResultsView(
                project=project,
                collection=collection,
                validation=validation,
            )
        except RemoteExecutionError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.get(
        "/api/execution-targets",
        response_model=tuple[TargetCapabilityStatus, ...],
    )
    def list_execution_targets() -> tuple[TargetCapabilityStatus, ...]:
        return tuple(capability_cache.get(target) for target in configured_targets)

    @application.get("/api/workstation/status")
    def get_workstation_status() -> dict:
        """Return workstation connection status for frontend display."""
        ws_settings = runtime_settings.workstation
        host = ws_settings.hosts[0] if ws_settings.hosts else None

        status = {
            "connected": False,
            "host": host,
            "username": ws_settings.username,
            "port": ws_settings.port,
            "foam_version": None,
            "cpu_count": None,
            "memory_gb": None,
            "disk_free_gb": None,
            "last_checked": None,
            "error": None,
        }

        for target in configured_targets:
            if target.kind == "workstation_openfoam":
                cap = capability_cache.get(target)
                status["connected"] = cap.available
                status["foam_version"] = cap.foam_version
                status["cpu_count"] = cap.cpu_count
                status["memory_gb"] = cap.memory_gb
                status["disk_free_gb"] = cap.disk_free_gb
                status["last_checked"] = cap.checked_at
                if not cap.available and cap.reason:
                    status["error"] = cap.reason
                break

        return status

    @application.post("/api/workstation/reconnect")
    def reconnect_workstation() -> dict:
        """Force a fresh doctor() call to reconnect the workstation."""
        ws_settings = runtime_settings.workstation
        host = ws_settings.hosts[0] if ws_settings.hosts else None
        for target in configured_targets:
            if target.kind == "workstation_openfoam":
                cap = capability_cache.get(target, force_refresh=True)
                return {
                    "connected": cap.available,
                    "host": host,
                    "username": ws_settings.username,
                    "port": ws_settings.port,
                    "foam_version": cap.foam_version,
                    "cpu_count": cap.cpu_count,
                    "memory_gb": cap.memory_gb,
                    "disk_free_gb": cap.disk_free_gb,
                    "last_checked": cap.checked_at,
                    "error": None
                    if cap.available
                    else (cap.reason or "Connection failed"),
                }
        return {
            "connected": False,
            "host": host,
            "error": "No workstation target configured",
        }

    @application.post("/api/workstation/test-ssh")
    def test_workstation_ssh() -> dict:
        """Run a quick SSH test to verify workstation connectivity."""
        import subprocess

        ws = runtime_settings.workstation
        if not ws.hosts or not ws.username:
            return {"success": False, "error": "Workstation not configured"}

        host = ws.hosts[0]
        cmd = [
            "ssh",
            "-p",
            str(ws.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={ws.known_hosts_file}",
            "-o",
            "ConnectTimeout=10",
        ]
        if ws.identity_file:
            cmd.extend(["-i", ws.identity_file])
        cmd.append(f"{ws.username}@{host}")
        cmd.extend(["echo", "SSH_OK"])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15
            )
            success = result.returncode == 0 and "SSH_OK" in result.stdout
            return {
                "success": success,
                "host": host,
                "error": None if success else result.stderr[:200],
            }
        except Exception as e:  # noqa: BLE001
            return {"success": False, "host": host, "error": str(e)[:200]}

    @application.get("/api/workstation/detect")
    def detect_workstation_config() -> dict:
        """Auto-detect existing SSH keys and known_hosts for workstation setup."""
        from pathlib import Path

        ssh_dir = Path.home() / ".ssh"
        # Look for fluid_scientist-specific keys first, then generic keys
        key_candidates = [
            ssh_dir / "fluid_scientist_ed25519",
            ssh_dir / "id_ed25519",
            ssh_dir / "id_rsa",
        ]
        known_hosts_candidates = [
            ssh_dir / "fluid_scientist_known_hosts",
            ssh_dir / "known_hosts",
        ]

        detected_key = None
        for key_path in key_candidates:
            if key_path.exists():
                detected_key = str(key_path).replace("\\", "/")
                break

        detected_known_hosts = None
        detected_hosts: list[str] = []
        for kh_path in known_hosts_candidates:
            if kh_path.exists():
                detected_known_hosts = str(kh_path).replace("\\", "/")
                try:
                    content = kh_path.read_text(encoding="utf-8")
                    for line in content.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            # Extract hostname (first field, may have multiple comma-separated)
                            host_part = line.split()[0].split(",")[0]
                            if host_part and not host_part.startswith("|"):
                                detected_hosts.append(host_part)
                except Exception:
                    pass
                break

        return {
            "identity_file": detected_key,
            "known_hosts_file": detected_known_hosts,
            "detected_hosts": detected_hosts,
            "ssh_dir": str(ssh_dir).replace("\\", "/"),
        }

    @application.post("/api/workstation/configure")
    def configure_workstation(
        host: str = Body(..., embed=True),
        username: str = Body(..., embed=True),
        port: int = Body(22, embed=True),
        identity_file: str = Body("", embed=True),
        known_hosts_file: str = Body("", embed=True),
    ) -> dict:
        """Configure workstation connection by writing .env and reloading targets.

        This endpoint writes the workstation settings to the .env file and
        rebuilds the execution targets so the new configuration takes effect
        immediately without a server restart.
        """
        from pathlib import Path as _Path

        project_root = _Path(__file__).resolve().parents[3]
        env_file = project_root / ".env"

        # Read existing .env content (if any) and preserve non-workstation lines
        preserved_lines: list[str] = []
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if any(
                    stripped.startswith(prefix)
                    for prefix in [
                        "FLUID_WORKSTATION__",
                        "FLUID_APP_MODE",
                    ]
                ):
                    continue
                preserved_lines.append(line)

        # Build new .env content
        new_lines = [
            "# Fluid Scientist configuration — auto-generated by workstation configure",
            "FLUID_APP_MODE=fake",
            f'FLUID_WORKSTATION__HOSTS=["{host}"]',
            f"FLUID_WORKSTATION__USERNAME={username}",
            f"FLUID_WORKSTATION__PORT={port}",
        ]
        if identity_file:
            new_lines.append(
                f"FLUID_WORKSTATION__IDENTITY_FILE={identity_file}"
            )
        if known_hosts_file:
            new_lines.append(
                f"FLUID_WORKSTATION__KNOWN_HOSTS_FILE={known_hosts_file}"
            )
        new_lines.extend(preserved_lines)

        env_file.write_text(
            "\n".join(new_lines) + "\n", encoding="utf-8"
        )

        # Reload settings and rebuild execution targets
        nonlocal runtime_settings, configured_targets
        runtime_settings = AppSettings()
        configured_targets = build_execution_targets(runtime_settings)
        application.state.execution_targets = configured_targets

        # Update target_registry
        target_registry.clear()
        for t in configured_targets:
            target_registry[t.target_id] = t

        # Try to connect immediately
        for target in configured_targets:
            if target.kind == "workstation_openfoam":
                cap = capability_cache.get(target, force_refresh=True)
                return {
                    "configured": True,
                    "connected": cap.available,
                    "host": host,
                    "username": username,
                    "port": port,
                    "foam_version": cap.foam_version,
                    "cpu_count": cap.cpu_count,
                    "memory_gb": cap.memory_gb,
                    "disk_free_gb": cap.disk_free_gb,
                    "error": None if cap.available else (cap.reason or "Connection failed"),
                }

        return {
            "configured": True,
            "connected": False,
            "host": host,
            "error": (
                "Configuration saved but no workstation target was created."
                " Check SSH key and known_hosts paths."
            ),
        }


    # ------------------------------------------------------------------
    # Candidate template library
    # ------------------------------------------------------------------
    @application.post(
        "/api/projects/{project_id}/candidates",
        response_model=CandidateTemplateRecord,
        status_code=status.HTTP_201_CREATED,
        tags=["candidate-templates"],
    )
    def create_candidate(
        project_id: str,
        request: CreateCandidateRequest,
    ) -> CandidateTemplateRecord:
        """Create a candidate template from a generated case draft."""
        draft = workflow_repository.load_generated_case_draft(request.draft_id)
        if draft is None:
            raise HTTPException(
                status_code=404, detail="generated case draft not found"
            )
        if draft.project_id != project_id:
            raise HTTPException(
                status_code=400,
                detail="draft does not belong to this project",
            )
        candidate_id = f"cand-{uuid4().hex[:16]}"
        now = datetime.now(UTC).isoformat()
        template = StoredCandidateTemplate(
            candidate_id=candidate_id,
            draft_id=draft.draft_id,
            project_id=draft.project_id,
            plan_id=draft.plan_id,
            plan_version=draft.plan_version,
            draft_version=draft.version,
            archive_sha256=draft.archive_sha256,
            state=CandidateState.DRAFT.value,
            rejection_reason=None,
            created_at=now,
            updated_at=now,
        )
        try:
            workflow_repository.save_candidate_template(template)
        except IntegrityError as error:
            raise HTTPException(
                status_code=409, detail="candidate already exists"
            ) from error
        return _candidate_record(template)

    @application.get(
        "/api/projects/{project_id}/candidates",
        response_model=list[CandidateTemplateRecord],
        tags=["candidate-templates"],
    )
    def list_candidates(
        project_id: str,
        state: str | None = None,
    ) -> list[CandidateTemplateRecord]:
        """List candidate templates for a project, optionally filtered by state."""
        templates = workflow_repository.list_candidate_templates(
            project_id=project_id,
            state=state,
        )
        return [_candidate_record(t) for t in templates]

    @application.get(
        "/api/projects/{project_id}/candidates/{candidate_id}",
        response_model=CandidateTemplateRecord,
        tags=["candidate-templates"],
    )
    def get_candidate(
        project_id: str,
        candidate_id: str,
    ) -> CandidateTemplateRecord:
        """Get a single candidate template by id."""
        try:
            template = workflow_repository.load_candidate_template(candidate_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            ) from error
        if template.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            )
        return _candidate_record(template)

    @application.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/validate-static",
        response_model=CandidateTemplateRecord,
        tags=["candidate-templates"],
    )
    def validate_candidate_static(
        project_id: str,
        candidate_id: str,
    ) -> CandidateTemplateRecord:
        """Advance candidate from DRAFT to STATIC_VALIDATED after re-running
        the static safety scanner on the stored archive."""
        try:
            template = workflow_repository.load_candidate_template(candidate_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            ) from error
        if template.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            )
        current = CandidateState(template.state)
        try:
            assert_transition(current, CandidateState.STATIC_VALIDATED)
        except CandidateTransitionError as error:
            raise HTTPException(
                status_code=422, detail=str(error)
            ) from error
        # Re-run the static safety scanner on the stored archive
        try:
            draft = workflow_repository.load_generated_case_draft(template.draft_id)
        except KeyError as error:
            raise HTTPException(
                status_code=500,
                detail="source draft no longer exists",
            ) from error
        try:
            validate_custom_case_archive(draft.archive)
        except CustomCaseRejected as error:
            with contextlib.suppress(KeyError):
                workflow_repository.update_candidate_template_state(
                    candidate_id,
                    new_state=CandidateState.REJECTED.value,
                    rejection_reason=f"Static validation failed: {error}",
                    updated_at=datetime.now(UTC).isoformat(),
                )
            raise HTTPException(
                status_code=422,
                detail=f"Static validation failed: {error}",
            ) from error
        updated = workflow_repository.update_candidate_template_state(
            candidate_id,
            new_state=CandidateState.STATIC_VALIDATED.value,
            rejection_reason=None,
            updated_at=datetime.now(UTC).isoformat(),
        )
        return _candidate_record(updated)

    @application.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/submit-pilot",
        response_model=CandidateTemplateRecord,
        tags=["candidate-templates"],
    )
    def submit_candidate_pilot(
        project_id: str,
        candidate_id: str,
        target_id: str,
    ) -> CandidateTemplateRecord:
        """Submit a pilot run for the candidate and advance to PILOT_PASSED
        when the trial run completes successfully."""
        try:
            template = workflow_repository.load_candidate_template(candidate_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            ) from error
        if template.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            )
        current = CandidateState(template.state)
        try:
            assert_transition(current, CandidateState.PILOT_PASSED)
        except CandidateTransitionError as error:
            raise HTTPException(
                status_code=422, detail=str(error)
            ) from error
        target = target_registry.get(target_id)
        if target is None:
            raise HTTPException(
                status_code=404, detail="execution target not found"
            )
        submit_custom = getattr(target, "submit_custom", None)
        if not callable(submit_custom):
            raise HTTPException(
                status_code=422,
                detail="execution target does not support custom cases",
            )
        try:
            draft = workflow_repository.load_generated_case_draft(template.draft_id)
        except KeyError as error:
            raise HTTPException(
                status_code=500, detail="source draft no longer exists"
            ) from error
        job_id = f"pilot-{candidate_id[:16]}"
        try:
            submit_custom(job_id, draft.archive)
        except (RemoteExecutionError, OSError, CustomCaseRejected) as error:
            with contextlib.suppress(KeyError):
                workflow_repository.update_candidate_template_state(
                    candidate_id,
                    new_state=CandidateState.REJECTED.value,
                    rejection_reason=f"Pilot submission failed: {error}",
                    updated_at=datetime.now(UTC).isoformat(),
                )
            raise HTTPException(
                status_code=502, detail=f"Pilot submission failed: {error}"
            ) from error
        updated = workflow_repository.update_candidate_template_state(
            candidate_id,
            new_state=CandidateState.PILOT_PASSED.value,
            rejection_reason=None,
            updated_at=datetime.now(UTC).isoformat(),
        )
        return _candidate_record(updated)

    @application.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/approve",
        response_model=CandidateTemplateRecord,
        tags=["candidate-templates"],
    )
    def approve_candidate(
        project_id: str,
        candidate_id: str,
        request: ApproveCandidateRequest,
    ) -> CandidateTemplateRecord:
        """Advance candidate from PILOT_PASSED to CANDIDATE_APPROVED."""
        return _transition_candidate(
            project_id, candidate_id, CandidateState.CANDIDATE_APPROVED
        )

    @application.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/publish",
        response_model=CandidateTemplateRecord,
        tags=["candidate-templates"],
    )
    def publish_candidate(
        project_id: str,
        candidate_id: str,
    ) -> CandidateTemplateRecord:
        """Advance candidate from REGRESSION_PASSED to PUBLISHED."""
        return _transition_candidate(
            project_id, candidate_id, CandidateState.PUBLISHED
        )

    @application.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/reject",
        response_model=CandidateTemplateRecord,
        tags=["candidate-templates"],
    )
    def reject_candidate(
        project_id: str,
        candidate_id: str,
        request: RejectCandidateRequest,
    ) -> CandidateTemplateRecord:
        """Reject a candidate template at any pre-terminal state."""
        try:
            template = workflow_repository.load_candidate_template(candidate_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            ) from error
        if template.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            )
        current = CandidateState(template.state)
        try:
            assert_transition(current, CandidateState.REJECTED)
        except CandidateTransitionError as error:
            raise HTTPException(
                status_code=422, detail=str(error)
            ) from error
        updated = workflow_repository.update_candidate_template_state(
            candidate_id,
            new_state=CandidateState.REJECTED.value,
            rejection_reason=request.reason,
            updated_at=datetime.now(UTC).isoformat(),
        )
        return _candidate_record(updated)

    def _transition_candidate(
        project_id: str,
        candidate_id: str,
        target: CandidateState,
    ) -> CandidateTemplateRecord:
        try:
            template = workflow_repository.load_candidate_template(candidate_id)
        except KeyError as error:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            ) from error
        if template.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="candidate template not found"
            )
        current = CandidateState(template.state)
        try:
            assert_transition(current, target)
        except CandidateTransitionError as error:
            raise HTTPException(
                status_code=422, detail=str(error)
            ) from error
        updated = workflow_repository.update_candidate_template_state(
            candidate_id,
            new_state=target.value,
            rejection_reason=None,
            updated_at=datetime.now(UTC).isoformat(),
        )
        return _candidate_record(updated)

    def _candidate_record(
        template: StoredCandidateTemplate,
    ) -> CandidateTemplateRecord:
        return CandidateTemplateRecord(
            candidate_id=template.candidate_id,
            draft_id=template.draft_id,
            project_id=template.project_id,
            plan_id=template.plan_id,
            plan_version=template.plan_version,
            draft_version=template.draft_version,
            archive_sha256=template.archive_sha256,
            state=CandidateState(template.state),
            rejection_reason=template.rejection_reason,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )

    # ------------------------------------------------------------------
    # Experiment Spec (structured parameter workbench)
    # ------------------------------------------------------------------
    @application.post(
        "/api/projects/{project_id}/experiment-specs",
        status_code=status.HTTP_201_CREATED,
        tags=["experiment-specs"],
    )
    def create_experiment_spec(
        project_id: str,
        body: dict,
    ) -> dict:
        """Create a new structured experiment spec from a plan or scratch."""
        import json
        from uuid import uuid4

        experiment_id = f"exp-{uuid4().hex[:16]}"
        now = datetime.now(UTC).isoformat()

        # If plan_id provided, migrate from existing plan
        plan_id = body.get("plan_id")
        if plan_id:
            stored = workflow_repository.load_experiment_plan(plan_id)
            if stored is None:
                raise HTTPException(status_code=404, detail="plan not found")
            from fluid_scientist.experiment_planning.models import ExperimentPlan
            plan = ExperimentPlan.model_validate_json(stored.plan_json)
            spec = migrate_plan(plan, experiment_id, project_id)
        else:
            # Create from body directly
            try:
                spec = ExperimentSpec(**body)
                spec = spec.model_copy(update={
                "experiment_id": experiment_id,
            })
            except Exception as e:
                raise HTTPException(status_code=422, detail=str(e)) from e

        stored_spec = StoredExperimentSpec(
            experiment_id=experiment_id,
            project_id=project_id,
            schema_version=spec.schema_version,
            experiment_version=spec.experiment_version,
            status=spec.status.value,
            task_type=spec.task_type.value,
            interaction_mode=spec.interaction_mode.value,
            spec_json=spec.model_dump_json(),
            created_at=now,
            updated_at=now,
        )
        try:
            workflow_repository.save_experiment_spec(stored_spec)
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return json.loads(stored_spec.spec_json)

    @application.get(
        "/api/projects/{project_id}/experiment-specs",
        tags=["experiment-specs"],
    )
    def list_experiment_specs(
        project_id: str,
        status_filter: str | None = None,
    ) -> list[dict]:
        """List experiment specs for a project."""
        import json
        specs = workflow_repository.list_experiment_specs(
            project_id=project_id, status=status_filter
        )
        return [json.loads(s.spec_json) for s in specs]

    @application.get(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}",
        tags=["experiment-specs"],
    )
    def get_experiment_spec(
        project_id: str,
        experiment_id: str,
    ) -> dict:
        """Get a single experiment spec."""
        import json
        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(status_code=404, detail="experiment spec not found")
        return json.loads(stored.spec_json)

    @application.get(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/metric-plan",
        tags=["experiment-specs"],
    )
    def get_metric_plan(
        project_id: str,
        experiment_id: str,
    ) -> dict:
        """Get the metric plan for an experiment spec."""
        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="experiment spec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored.spec_json)
        # Return the metrics from the spec
        metrics = spec.metrics if isinstance(spec.metrics, list) else []
        return {
            "experiment_id": experiment_id,
            "metrics": metrics,
            "metric_count": len(metrics),
        }

    @application.patch(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters/{parameter_id}",
        tags=["experiment-specs"],
    )
    def update_parameter(
        project_id: str,
        experiment_id: str,
        parameter_id: str,
        body: dict,
    ) -> dict:
        """Update a single parameter and propagate dependencies."""
        import json
        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(status_code=404, detail="experiment spec not found")

        spec = ExperimentSpec.model_validate_json(stored.spec_json)

        # Check if spec is editable
        from fluid_scientist.experiment_spec.state_machine import is_editable
        status_val = spec.status.value if hasattr(spec.status, 'value') else str(spec.status)
        if not is_editable(status_val):
            raise HTTPException(
                status_code=422,
                detail="experiment spec is not editable in current state"
            )

        new_value = body.get("value")
        if new_value is None:
            raise HTTPException(status_code=422, detail="value is required")

        try:
            updated_spec, result = propagate_change(spec, parameter_id, new_value)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        new_version = (
            stored.experiment_version + 1
            if result.needs_new_version
            else stored.experiment_version
        )
        now = datetime.now(UTC).isoformat()
        updated_stored = workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=updated_spec.model_dump_json(),
            experiment_version=new_version,
            status=stored.status,
            updated_at=now,
        )
        response = json.loads(updated_stored.spec_json)
        response["_propagation"] = {
            "directly_modified": result.directly_modified,
            "auto_recomputed": result.auto_recomputed,
            "requires_choice": result.requires_choice,
            "stale_artifacts": result.stale_artifacts,
            "new_warnings": result.new_warnings,
            "needs_new_version": result.needs_new_version,
            "summary": change_summary(result),
        }
        return response

    @application.patch(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/parameters",
        tags=["experiment-specs"],
    )
    def batch_update_parameters(
        project_id: str,
        experiment_id: str,
        body: dict,
    ) -> dict:
        """Batch update multiple parameters and propagate dependencies."""
        import json
        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(status_code=404, detail="experiment spec not found")

        # Version check
        client_version = body.get("experiment_version")
        if client_version is not None and client_version != stored.experiment_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "current_version": stored.experiment_version,
                    "client_version": client_version,
                    "message": "实验参数已被其他操作修改，请刷新后再提交。",
                },
            )

        spec = ExperimentSpec.model_validate_json(stored.spec_json)

        # Check if spec is editable
        from fluid_scientist.experiment_spec.state_machine import is_editable
        status_val = spec.status.value if hasattr(spec.status, 'value') else str(spec.status)
        if not is_editable(status_val):
            raise HTTPException(
                status_code=422,
                detail="experiment spec is not editable in current state"
            )

        updates = body.get("updates", [])
        if not updates:
            raise HTTPException(
                status_code=422,
                detail="updates list is required and must not be empty",
            )

        # Apply each update sequentially and accumulate results
        updated_spec = spec
        updated_parameters = []
        all_auto_recomputed = []
        all_stale_artifacts = []
        all_warnings = []
        all_requires_choice = []
        derived_updates = []
        direct_updates = []
        needs_new_version = False

        for update in updates:
            param_id = update.get("parameter_id")
            new_value = update.get("value")
            if param_id is None:
                raise HTTPException(status_code=422, detail="each update must have parameter_id")
            if new_value is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"value is required for parameter {param_id}",
                )

            # Capture old value before update
            old_param = updated_spec.get_parameter(param_id)
            old_value = old_param.value if old_param else None

            try:
                updated_spec, result = propagate_change(updated_spec, param_id, new_value)
            except KeyError as e:
                raise HTTPException(status_code=404, detail=str(e)) from e
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e

            updated_parameters.append(param_id)
            # Track direct update with old/new values
            direct_updates.append({
                "parameter_id": param_id,
                "old_value": old_value,
                "new_value": new_value,
            })
            all_auto_recomputed.extend(result.auto_recomputed)
            all_stale_artifacts.extend(result.stale_artifacts)
            all_warnings.extend(result.new_warnings)
            all_requires_choice.extend(result.requires_choice)
            if result.needs_new_version:
                needs_new_version = True

            # Track derived updates with old/new values
            for rec_id in result.auto_recomputed:
                rec_param = updated_spec.get_parameter(rec_id)
                if rec_param:
                    derived_updates.append({
                        "parameter_id": rec_id,
                        "new_value": rec_param.value,
                        "reason": f"由 {param_id} 修改联动更新",
                    })

        # Deduplicate stale artifacts
        seen_stale = set()
        deduped_stale = []
        for s in all_stale_artifacts:
            if s not in seen_stale:
                seen_stale.add(s)
                deduped_stale.append(s)

        # Deduplicate auto_recomputed
        seen_recomputed = set()
        deduped_recomputed = []
        for r in all_auto_recomputed:
            if r not in seen_recomputed:
                seen_recomputed.add(r)
                deduped_recomputed.append(r)

        new_version = (
            stored.experiment_version + 1
            if needs_new_version
            else stored.experiment_version
        )
        now = datetime.now(UTC).isoformat()
        updated_stored = workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=updated_spec.model_dump_json(),
            experiment_version=new_version,
            status=stored.status,
            updated_at=now,
        )
        response = json.loads(updated_stored.spec_json)
        response["_batch_propagation"] = {
            "updated_parameters": updated_parameters,
            "direct_updates": direct_updates,
            "derived_updates": derived_updates,
            "auto_recomputed": deduped_recomputed,
            "requires_choice": all_requires_choice,
            "invalidated": deduped_stale,
            "warnings": all_warnings,
            "needs_new_version": needs_new_version,
            "summary": f"已保存 {len(updated_parameters)} 个参数"
                       + (
                           f"，{len(deduped_recomputed)} 个派生参数已更新"
                           if deduped_recomputed
                           else ""
                       )
                       + (f"，{len(deduped_stale)} 个对象已失效" if deduped_stale else ""),
        }
        return response


    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/accept-recommendations",
        tags=["experiment-specs"],
    )
    def accept_all_recommendations(
        project_id: str,
        experiment_id: str,
    ) -> dict:
        """Accept all system_recommended parameters and compute derived values.

        - system_recommended -> status=ACCEPTED
        - derived parameters computed (mean_velocity, reynolds_number)
        - unknown_required parameters remain unchanged
        - Returns updated spec with acceptance summary
        """
        import json
        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(status_code=404, detail="experiment spec not found")

        spec = ExperimentSpec.model_validate_json(stored.spec_json)

        from fluid_scientist.experiment_spec.derivation import (
            accept_all_recommendations as do_accept,
        )
        updated_spec = do_accept(spec)

        # Count what was accepted
        accepted = [
            p.parameter_id for p in updated_spec.parameters
            if p.source.type.value == "system_recommended"
            and p.status.value == "accepted"
        ]
        derived = [
            p.parameter_id for p in updated_spec.parameters
            if p.source.type.value == "derived"
        ]
        still_unknown = [
            p.parameter_id for p in updated_spec.parameters
            if p.source.type.value == "unknown"
        ]

        now = datetime.now(UTC).isoformat()
        updated_stored = workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=updated_spec.model_dump_json(),
            experiment_version=stored.experiment_version,
            status=stored.status,
            updated_at=now,
        )
        response = json.loads(updated_stored.spec_json)
        response["_acceptance_summary"] = {
            "accepted_recommendations": accepted,
            "derived_parameters": derived,
            "still_unknown_required": still_unknown,
            "summary": (
                f"已接受 {len(accepted)} 个推荐值"
                + (f"，{len(derived)} 个参数已推导" if derived else "")
                + (f"，{len(still_unknown)} 个参数仍需确认" if still_unknown else "")
            ),
        }
        return response

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/natural-language-edit",
        tags=["experiment-specs"],
    )
    def natural_language_edit(
        project_id: str,
        experiment_id: str,
        body: dict,
    ) -> dict:
        """Parse a natural language instruction into proposed parameter changes.

        Does NOT apply changes — returns proposed changes for user confirmation.
        User confirms by calling the batch PATCH endpoint.
        """
        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(status_code=404, detail="experiment spec not found")

        spec = ExperimentSpec.model_validate_json(stored.spec_json)

        # Version check
        client_version = body.get("experiment_version")
        if client_version is not None and client_version != stored.experiment_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "current_version": stored.experiment_version,
                    "client_version": client_version,
                    "message": "实验参数已被其他操作修改，请刷新后再提交。",
                },
            )

        instruction = body.get("instruction", "").strip()
        if not instruction:
            raise HTTPException(status_code=422, detail="instruction is required")

        from fluid_scientist.experiment_spec.nl_parser import parse_nl_instruction
        result = parse_nl_instruction(instruction, spec)

        # Build preview of derived updates (without applying)
        derived_preview: list[dict[str, Any]] = []
        for change in result.proposed_changes:
            param = spec.get_parameter(change.parameter_id)
            if param:
                for p in spec.parameters:
                    if change.parameter_id in p.dependencies.depends_on:
                        derived_preview.append({
                            "parameter_id": p.parameter_id,
                            "display_name": p.display_name,
                            "current_value": p.value,
                            "reason": f"依赖 {change.parameter_id} 的修改",
                        })

        return {
            "experiment_id": experiment_id,
            "experiment_version": stored.experiment_version,
            "proposed_changes": [
                {
                    "parameter_id": c.parameter_id,
                    "display_name": c.display_name,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "unit": c.unit,
                    "matched_term": c.matched_term,
                }
                for c in result.proposed_changes
            ],
            "derived_updates_preview": derived_preview,
            "unmatched_segments": result.unmatched_segments,
            "requires_confirmation": result.requires_confirmation,
        }

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/transition",
        tags=["experiment-specs"],
    )
    def transition_experiment_spec(
        project_id: str,
        experiment_id: str,
        body: dict,
    ) -> dict:
        """Transition experiment spec to a new status."""
        import json
        target = body.get("target_status")
        if not target:
            raise HTTPException(status_code=422, detail="target_status is required")

        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(status_code=404, detail="experiment spec not found")

        try:
            assert_spec_transition(stored.status, target)
        except SpecTransitionError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e

        # If transitioning to ready, check critical params
        if target == "ready":
            spec = ExperimentSpec.model_validate_json(stored.spec_json)
            unresolved = spec.critical_unresolved()
            if unresolved:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"cannot transition to ready: {len(unresolved)}"
                        " critical parameters unresolved: "
                        + ", ".join(p.parameter_id for p in unresolved)
                    )
                )

        # Parse spec, update its internal status, and save both the
        # StoredExperimentSpec.status and the ExperimentSpec.status inside spec_json.
        spec = ExperimentSpec.model_validate_json(stored.spec_json)
        updated_spec = spec.model_copy(
            update={"status": ExperimentStatus(target)}
        )
        updated = workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=updated_spec.model_dump_json(),
            experiment_version=stored.experiment_version,
            status=target,
            updated_at=datetime.now(UTC).isoformat(),
        )
        return json.loads(updated.spec_json)


    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/compile",
        tags=["experiment-specs"],
    )
    def compile_experiment_spec(
        project_id: str,
        experiment_id: str,
    ) -> dict:
        """Compile a confirmed ExperimentSpec into a runnable OpenFOAM case.

        Only specs in the ``confirmed`` state can be compiled.  The endpoint
        builds an ExperimentPlan from the confirmed parameter values, calls
        the deterministic Simulation Compiler, stores the compiled archive,
        and transitions the spec to ``compiling``.
        """
        import json

        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="experiment spec not found"
            )

        spec = ExperimentSpec.model_validate_json(stored.spec_json)

        # Hard gate: validate required parameters before attempting compilation.
        try:
            validate_required_parameters(spec)
        except MissingRequiredParameterError as error:
            raise HTTPException(
                status_code=422, detail=str(error)
            ) from error

        try:
            compiled, compilation_manifest = compile_spec(spec)
        except MissingRequiredParameterError as error:
            raise HTTPException(
                status_code=422, detail=str(error)
            ) from error
        except SpecNotConfirmedError as error:
            raise HTTPException(
                status_code=409, detail=str(error)
            ) from error
        except (ValueError, CompilationError) as error:
            raise HTTPException(
                status_code=422, detail=str(error)
            ) from error
        except UnsupportedCompilation as error:
            raise HTTPException(
                status_code=422, detail=str(error)
            ) from error

        preview = {
            "experiment_id": experiment_id,
            "experiment_version": stored.experiment_version,
            "experiment_type": compiled.experiment_type,
            "archive_sha256": compiled.archive_sha256,
            "manifest": compiled.manifest.model_dump(mode="json"),
            "preprocessing": list(compiled.preprocessing),
            "required_outputs": list(compiled.required_outputs),
            "compilation_manifest": {
                "compilation_id": compilation_manifest.compilation_id,
                "spec_hash": compilation_manifest.spec_hash,
                "case_hash": compilation_manifest.case_hash,
                "compiler_id": compilation_manifest.compiler_id,
                "compiler_version": compilation_manifest.compiler_version,
            },
        }

        workflow_repository.store_compiled_experiment(
            StoredCompiledExperiment(
                experiment_id=experiment_id,
                plan_version=stored.experiment_version,
                archive_sha256=compiled.archive_sha256,
                archive=compiled.archive,
                preview_json=json.dumps(preview, ensure_ascii=False),
            )
        )

        # Transition spec to compiling — update both stored status and
        # the ExperimentSpec.status inside spec_json for consistency.
        spec_compiling = spec.model_copy(
            update={"status": ExperimentStatus.COMPILING}
        )
        workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=spec_compiling.model_dump_json(),
            experiment_version=stored.experiment_version,
            status="compiling",
            updated_at=datetime.now(UTC).isoformat(),
        )

        return {
            "experiment_id": spec.experiment_id,
            "experiment_version": spec.experiment_version,
            "compilation_manifest": {
                "compilation_id": compilation_manifest.compilation_id,
                "spec_hash": compilation_manifest.spec_hash,
                "case_hash": compilation_manifest.case_hash,
                "compiler_id": compilation_manifest.compiler_id,
                "compiler_version": compilation_manifest.compiler_version,
            },
            "archive_sha256": compiled.archive_sha256,
            "archive_size": len(compiled.archive),
            "entry_point": "system/controlDict",
        }

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/clone",
        status_code=status.HTTP_201_CREATED,
        tags=["experiment-specs"],
    )
    def clone_experiment_spec(
        project_id: str,
        experiment_id: str,
    ) -> dict:
        """Clone an immutable experiment spec into a new editable draft.

        Creates a brand-new ``experiment_id`` with ``status="draft"``,
        copying all parameters, physics, and research info, while
        incrementing ``experiment_version`` by one.  The original spec is
        left untouched so confirmed results stay immutable.
        """
        import json

        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(status_code=404, detail="experiment spec not found")

        spec = ExperimentSpec.model_validate_json(stored.spec_json)

        new_experiment_id = f"exp-{uuid4().hex[:16]}"
        new_version = stored.experiment_version + 1
        now = datetime.now(UTC).isoformat()

        cloned_spec = spec.model_copy(
            update={
                "experiment_id": new_experiment_id,
                "experiment_version": new_version,
                "status": ExperimentStatus.DRAFT,
                "created_at": now,
                "updated_at": now,
            }
        )

        new_stored = StoredExperimentSpec(
            experiment_id=new_experiment_id,
            project_id=project_id,
            schema_version=cloned_spec.schema_version,
            experiment_version=new_version,
            status=cloned_spec.status.value,
            task_type=cloned_spec.task_type.value,
            interaction_mode=cloned_spec.interaction_mode.value,
            spec_json=cloned_spec.model_dump_json(),
            created_at=now,
            updated_at=now,
        )
        try:
            workflow_repository.save_experiment_spec(new_stored)
        except Exception as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return json.loads(new_stored.spec_json)

    @application.get(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/pre-check",
        tags=["experiment-specs"],
    )
    def pre_check_experiment_spec(
        project_id: str,
        experiment_id: str,
    ) -> dict:
        """Pre-compile validation for an experiment spec.

        Checks performed before allowing Case generation:
        1. ``spec.status`` is ``confirmed`` (blocking otherwise).
        2. No parameters with ``source.type == "unknown"`` (blocking).
        3. No missing/unapproved code capabilities (blocking).

        Returns ``can_compile`` plus structured ``blocking_issues`` and
        informational ``warnings``.
        """
        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None or stored.project_id != project_id:
            raise HTTPException(status_code=404, detail="experiment spec not found")

        spec = ExperimentSpec.model_validate_json(stored.spec_json)

        blocking_issues: list[dict[str, Any]] = []
        warnings: list[str] = []

        status_val = (
            spec.status.value if hasattr(spec.status, "value") else str(spec.status)
        )

        # Check 1: status must be confirmed before compiling.
        if status_val != "confirmed":
            blocking_issues.append(
                {
                    "type": "status",
                    "message": (
                        f"实验规格当前状态为「{status_val}」，"
                        "需要先确认实验版本后再生成 Case。"
                    ),
                }
            )

        # Check 2: parameters with unknown source block compilation.
        for param in spec.parameters:
            source_type = (
                param.source.type.value
                if hasattr(param.source.type, "value")
                else str(param.source.type)
            )
            if source_type == "unknown":
                blocking_issues.append(
                    {
                        "type": "unknown_required",
                        "parameter_id": param.parameter_id,
                        "message": (
                            f"参数「{param.display_name}」来源未知，"
                            "需先确认后再生成 Case。"
                        ),
                    }
                )

        # Check 3: missing / unapproved code capabilities block compilation.
        for ext in spec.code_extensions:
            approval_state = ext.get("approval_state") or ext.get("status")
            if approval_state in (None, "pending", "missing", "required", "rejected"):
                capability_id = (
                    ext.get("capability_id")
                    or ext.get("id")
                    or ext.get("name", "unknown")
                )
                blocking_issues.append(
                    {
                        "type": "missing_capability",
                        "capability_id": capability_id,
                        "message": (
                            f"缺少能力「{capability_id}」，"
                            "需先完成代码扩展审批。"
                        ),
                    }
                )

        # Warnings: critical parameters that still lack a value.
        for param in spec.critical_unresolved():
            warnings.append(
                f"关键参数「{param.display_name}」尚未确认。"
            )

        can_compile = len(blocking_issues) == 0
        return {
            "experiment_id": experiment_id,
            "can_compile": can_compile,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------ #
    # Analysis main flow: Ingestor -> MetricExecutor -> ScientificAnalyzer
    # ------------------------------------------------------------------ #

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/ingest",
        tags=["experiment-specs"],
    )
    def ingest_experiment_results(
        project_id: str,
        experiment_id: str,
        case_path: str = Body(..., embed=True),
    ) -> dict:
        """Ingest OpenFOAM results from a case directory.

        Calls OpenFOAMResultIngestor to parse real result files.
        """
        from pathlib import Path

        from fluid_scientist.results.ingestor import OpenFOAMResultIngestor

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404,
                detail=f"ExperimentSpec '{experiment_id}' not found",
            )

        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        # Reconstruct MeasurementPlan if available
        measurement_plan = None
        if spec.metrics:
            from fluid_scientist.measurement.models import MeasurementPlan

            with contextlib.suppress(Exception):
                measurement_plan = MeasurementPlan.model_validate(spec.metrics[0])

        # Ingest results
        ingestor = OpenFOAMResultIngestor()
        try:
            sim_data = ingestor.ingest(
                case_path=Path(case_path),
                measurement_plan=measurement_plan,
            )
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404, detail=str(error)
            ) from error
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=f"Ingestion failed: {error}"
            ) from error

        return {
            "experiment_id": experiment_id,
            "simulation_data": sim_data.model_dump(),
            "missing_data": sim_data.missing_data,
            "warnings": sim_data.warnings,
        }

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/analyze",
        tags=["experiment-specs"],
    )
    def analyze_experiment_results(
        project_id: str,
        experiment_id: str,
        case_path: str = Body(..., embed=True),
        metric_ids: Annotated[list[str] | None, Body(embed=True)] = None,
    ) -> dict:
        """Calculate metrics from simulation results.

        Calls OpenFOAMResultIngestor then MetricExecutor.
        """
        from pathlib import Path

        from fluid_scientist.results.ingestor import OpenFOAMResultIngestor
        from fluid_scientist.results.metric_executor import MetricExecutor

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404,
                detail=f"ExperimentSpec '{experiment_id}' not found",
            )

        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        # Reconstruct MeasurementPlan
        measurement_plan = None
        if spec.metrics:
            from fluid_scientist.measurement.models import MeasurementPlan

            with contextlib.suppress(Exception):
                measurement_plan = MeasurementPlan.model_validate(spec.metrics[0])

        # Ingest
        ingestor = OpenFOAMResultIngestor()
        try:
            sim_data = ingestor.ingest(
                case_path=Path(case_path),
                measurement_plan=measurement_plan,
            )
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404, detail=str(error)
            ) from error
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=f"Ingestion failed: {error}"
            ) from error

        # Determine which metrics to calculate
        if metric_ids is None:
            # Extract from spec's metric plan
            metric_ids = []
            if measurement_plan:
                metric_ids = [
                    b.metric_id for b in measurement_plan.metric_bindings
                ]
            if not metric_ids:
                # Default based on experiment type
                param_ids = {p.parameter_id for p in spec.parameters}
                if "reynolds_number" in param_ids:
                    metric_ids = [
                        "drag_coefficient",
                        "lift_coefficient",
                        "strouhal_number",
                    ]
                elif "length" in param_ids:
                    metric_ids = [
                        "pressure_drop",
                        "friction_factor",
                        "mass_flow_rate",
                    ]
                else:
                    metric_ids = ["residual_tolerance", "max_courant"]

        # Extract parameters for metric calculation
        parameters = {p.parameter_id: p.value for p in spec.parameters}

        # Execute metrics
        executor = MetricExecutor()
        try:
            results = executor.execute_all(
                metric_ids, sim_data, parameters=parameters
            )
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=f"Metric execution failed: {error}",
            ) from error

        stored_metric_results = [r.model_dump() for r in results]
        application.state.metric_results_store[experiment_id] = stored_metric_results
        return {
            "experiment_id": experiment_id,
            "metric_results": stored_metric_results,
            "missing_data": sim_data.missing_data,
        }

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/scientific-report",
        tags=["experiment-specs"],
    )
    def generate_scientific_report(
        project_id: str,
        experiment_id: str,
        case_path: str = Body(..., embed=True),
    ) -> dict:
        """Generate full scientific report: ingest -> metrics -> analysis.

        Calls OpenFOAMResultIngestor -> MetricExecutor -> ScientificAnalyzer.
        """
        from pathlib import Path

        from fluid_scientist.results.analysis import ScientificAnalyzer
        from fluid_scientist.results.ingestor import OpenFOAMResultIngestor
        from fluid_scientist.results.metric_executor import MetricExecutor

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404,
                detail=f"ExperimentSpec '{experiment_id}' not found",
            )

        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        # Reconstruct MeasurementPlan
        measurement_plan = None
        if spec.metrics:
            from fluid_scientist.measurement.models import MeasurementPlan

            with contextlib.suppress(Exception):
                measurement_plan = MeasurementPlan.model_validate(spec.metrics[0])

        # Step 1: Ingest
        ingestor = OpenFOAMResultIngestor()
        try:
            sim_data = ingestor.ingest(
                case_path=Path(case_path),
                measurement_plan=measurement_plan,
            )
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404, detail=str(error)
            ) from error
        except Exception as error:
            raise HTTPException(
                status_code=500, detail=f"Ingestion failed: {error}"
            ) from error

        # Step 2: Calculate metrics
        metric_ids = []
        if measurement_plan:
            metric_ids = [
                b.metric_id for b in measurement_plan.metric_bindings
            ]
        if not metric_ids:
            param_ids = {p.parameter_id for p in spec.parameters}
            if "reynolds_number" in param_ids:
                metric_ids = [
                    "drag_coefficient",
                    "lift_coefficient",
                    "strouhal_number",
                ]
            elif "length" in param_ids:
                metric_ids = [
                    "pressure_drop",
                    "friction_factor",
                    "mass_flow_rate",
                ]
            else:
                metric_ids = ["residual_tolerance", "max_courant"]

        parameters = {p.parameter_id: p.value for p in spec.parameters}

        executor = MetricExecutor()
        try:
            metric_results = executor.execute_all(
                metric_ids, sim_data, parameters=parameters
            )
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=f"Metric execution failed: {error}",
            ) from error

        # Step 3: Scientific analysis
        analyzer = ScientificAnalyzer()
        try:
            analysis = analyzer.analyze(
                metric_results=metric_results,
                simulation_data=sim_data,
                experiment_spec=spec,
            )
        except Exception as error:
            raise HTTPException(
                status_code=500,
                detail=f"Scientific analysis failed: {error}",
            ) from error

        stored_metric_results = [r.model_dump() for r in metric_results]
        application.state.metric_results_store[experiment_id] = stored_metric_results
        return {
            "experiment_id": experiment_id,
            "metric_results": stored_metric_results,
            "scientific_analysis": analysis.model_dump(),
            "missing_data": sim_data.missing_data,
            "warnings": sim_data.warnings,
        }

    @application.get(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/metric-results",
        tags=["experiment-specs"],
    )
    def get_metric_results(
        project_id: str,
        experiment_id: str,
    ) -> dict:
        """Retrieve stored metric results for an experiment.

        Metric results are generated on-demand via POST /analyze or
        POST /scientific-report and cached in memory.  If no analysis
        has been performed yet, an empty list is returned.
        """
        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404,
                detail=f"ExperimentSpec '{experiment_id}' not found",
            )
        # Results are generated on-demand via POST /analyze and cached
        # in application.state.metric_results_store.
        cached = application.state.metric_results_store.get(experiment_id, [])
        return {
            "experiment_id": experiment_id,
            "metric_results": cached,
        }

    # ------------------------------------------------------------------ #
    # Code Extension user loop (Commit 9)
    # ------------------------------------------------------------------ #

    @application.get(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions",
        tags=["experiment-specs"],
    )
    def list_code_extensions(
        project_id: str,
        experiment_id: str,
    ) -> dict:
        """List all code extensions for an experiment spec."""
        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)
        return {
            "experiment_id": experiment_id,
            "code_extensions": spec.code_extensions,
        }

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions",
        status_code=status.HTTP_201_CREATED,
        tags=["experiment-specs"],
    )
    def create_code_extension(
        project_id: str,
        experiment_id: str,
        extension_data: dict = Body(...),  # noqa: B008
    ) -> dict:
        """Create a new code extension for an experiment spec."""
        from fluid_scientist.code_extension.models import (
            CodeExtensionSpec,
            CodeExtensionType,
            ExtensionStatus,
        )

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )

        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        # Create extension
        extension = CodeExtensionSpec(
            extension_id=extension_data.get(
                "extension_id", f"ext-{uuid4().hex[:12]}"
            ),
            name=extension_data["name"],
            description=extension_data.get("description", ""),
            extension_type=CodeExtensionType(extension_data["extension_type"]),
            code=extension_data.get("source_code")
            or extension_data.get("code", "def placeholder():\n    pass\n"),
            status=ExtensionStatus.DRAFT,
        )

        # Add to spec
        spec.code_extensions.append(extension.model_dump(mode="json"))

        # Save
        workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=spec.model_dump_json(),
            experiment_version=stored_spec.experiment_version,
            status=stored_spec.status,
            updated_at=datetime.now(UTC).isoformat(),
        )

        return {
            "experiment_id": experiment_id,
            "code_extension": extension.model_dump(mode="json"),
        }

    @application.get(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}",
        tags=["experiment-specs"],
    )
    def get_code_extension(
        project_id: str,
        experiment_id: str,
        extension_id: str,
    ) -> dict:
        """Get a specific code extension."""
        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        for ext in spec.code_extensions:
            if ext.get("extension_id") == extension_id:
                return {
                    "experiment_id": experiment_id,
                    "code_extension": ext,
                }
        raise HTTPException(
            status_code=404,
            detail=f"CodeExtension '{extension_id}' not found",
        )

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/approve",
        tags=["experiment-specs"],
    )
    def approve_code_extension(
        project_id: str,
        experiment_id: str,
        extension_id: str,
        reviewer: str = Body(..., embed=True),  # noqa: B008
        notes: str = Body("", embed=True),  # noqa: B008
    ) -> dict:
        """Approve a code extension that has passed auto-testing.

        Transitions the extension from auto_tested to approved.
        If the spec was in AWAITING_CODE_APPROVAL status and all extensions
        are approved, transitions the spec back to confirmed.
        """
        from fluid_scientist.code_extension.models import ExtensionStatus

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        # Find and update the extension
        found = False
        for i, ext in enumerate(spec.code_extensions):
            if ext.get("extension_id") == extension_id:
                if ext.get("status") != ExtensionStatus.AUTO_TESTED.value:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Extension must be in auto_tested status, "
                            f"got {ext.get('status')}"
                        ),
                    )
                ext["status"] = ExtensionStatus.APPROVED.value
                ext["review_notes"] = f"Approved by {reviewer}. {notes}".strip()
                spec.code_extensions[i] = ext
                found = True
                break

        if not found:
            raise HTTPException(
                status_code=404,
                detail=f"CodeExtension '{extension_id}' not found",
            )

        # State recovery: if all extensions are approved, transition
        # spec back to confirmed
        all_approved = all(
            ext.get("status")
            in (
                ExtensionStatus.APPROVED.value,
                ExtensionStatus.REGISTERED.value,
            )
            for ext in spec.code_extensions
        )
        if all_approved and spec.status == ExperimentStatus.AWAITING_CODE_APPROVAL:
            spec = spec.model_copy(
                update={"status": ExperimentStatus.CONFIRMED}
            )

        new_status = (
            spec.status.value
            if hasattr(spec.status, "value")
            else str(spec.status)
        )
        workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=spec.model_dump_json(),
            experiment_version=stored_spec.experiment_version,
            status=new_status,
            updated_at=datetime.now(UTC).isoformat(),
        )

        return {
            "experiment_id": experiment_id,
            "code_extension": spec.code_extensions,
            "spec_status": new_status,
        }

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/reject",
        tags=["experiment-specs"],
    )
    def reject_code_extension(
        project_id: str,
        experiment_id: str,
        extension_id: str,
        reason: str = Body(..., embed=True),  # noqa: B008
    ) -> dict:
        """Reject a code extension."""
        from fluid_scientist.code_extension.models import ExtensionStatus

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        found = False
        for i, ext in enumerate(spec.code_extensions):
            if ext.get("extension_id") == extension_id:
                ext["status"] = ExtensionStatus.REJECTED.value
                ext["review_notes"] = f"Rejected: {reason}"
                spec.code_extensions[i] = ext
                found = True
                break

        if not found:
            raise HTTPException(
                status_code=404,
                detail=f"CodeExtension '{extension_id}' not found",
            )

        workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=spec.model_dump_json(),
            experiment_version=stored_spec.experiment_version,
            status=stored_spec.status,
            updated_at=datetime.now(UTC).isoformat(),
        )

        return {
            "experiment_id": experiment_id,
            "code_extension": spec.code_extensions,
        }

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/register",
        tags=["experiment-specs"],
    )
    def register_code_extension(
        project_id: str,
        experiment_id: str,
        extension_id: str,
    ) -> dict:
        """Register an approved code extension as an active plugin."""
        from fluid_scientist.code_extension.models import ExtensionStatus

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        found = False
        for i, ext in enumerate(spec.code_extensions):
            if ext.get("extension_id") == extension_id:
                if ext.get("status") != ExtensionStatus.APPROVED.value:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Extension must be in approved status, "
                            f"got {ext.get('status')}"
                        ),
                    )
                ext["status"] = ExtensionStatus.REGISTERED.value
                spec.code_extensions[i] = ext
                found = True
                break

        if not found:
            raise HTTPException(
                status_code=404,
                detail=f"CodeExtension '{extension_id}' not found",
            )

        workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=spec.model_dump_json(),
            experiment_version=stored_spec.experiment_version,
            status=stored_spec.status,
            updated_at=datetime.now(UTC).isoformat(),
        )

        return {
            "experiment_id": experiment_id,
            "code_extension": spec.code_extensions,
        }

    @application.get(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/history",
        tags=["experiment-specs"],
    )
    def get_extension_history(
        project_id: str,
        experiment_id: str,
        extension_id: str,
    ) -> dict:
        """Get the change history for a code extension."""
        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        for ext in spec.code_extensions:
            if ext.get("extension_id") == extension_id:
                history = ext.get("history", [])
                return {
                    "experiment_id": experiment_id,
                    "extension_id": extension_id,
                    "history": history,
                }
        raise HTTPException(
            status_code=404,
            detail=f"CodeExtension '{extension_id}' not found",
        )

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/sandbox-test",
        tags=["experiment-specs"],
    )
    def sandbox_test_code_extension(
        project_id: str,
        experiment_id: str,
        extension_id: str,
    ) -> dict:
        """Run sandbox testing for a code extension.

        Transitions the extension from DRAFT to SANDBOX_TESTED (or REJECTED
        if the sandbox test fails).  Calls ``sandbox_test_extension()``
        from the code_extension registry module.

        Returns the updated extension and the sandbox test result.
        """
        from fluid_scientist.code_extension.models import (
            CodeExtensionSpec,
            CodeExtensionType,
            ExtensionStatus,
        )
        from fluid_scientist.code_extension.registry import (
            ExtensionRegistry,
            sandbox_test_extension,
        )

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        # Find the extension
        ext_data = None
        ext_index = -1
        for i, ext in enumerate(spec.code_extensions):
            if ext.get("extension_id") == extension_id:
                ext_data = ext
                ext_index = i
                break

        if ext_data is None:
            raise HTTPException(
                status_code=404,
                detail=f"CodeExtension '{extension_id}' not found",
            )

        # Build a CodeExtensionSpec and run sandbox test via registry
        extension = CodeExtensionSpec(
            extension_id=ext_data["extension_id"],
            name=ext_data["name"],
            description=ext_data.get("description", ""),
            extension_type=CodeExtensionType(ext_data["extension_type"]),
            code=ext_data.get("code", ""),
            language=ext_data.get("language", "python"),
            dependencies=tuple(ext_data.get("dependencies", [])),
            openfoam_files=tuple(ext_data.get("openfoam_files", [])),
            tests=tuple(ext_data.get("tests", [])),
            status=ExtensionStatus(ext_data.get("status", "draft")),
            version=ext_data.get("version", "1.0.0"),
            author=ext_data.get("author", "system"),
            review_notes=ext_data.get("review_notes", ""),
            created_at=ext_data.get("created_at", ""),
            updated_at=ext_data.get("updated_at", ""),
        )

        registry = ExtensionRegistry()
        registry.extensions[extension.extension_id] = extension

        try:
            updated_ext, sandbox_result = sandbox_test_extension(
                registry, extension_id
            )
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=str(e),
            ) from e

        # Persist the updated status back into the spec
        spec.code_extensions[ext_index] = updated_ext.model_dump(mode="json")
        workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=spec.model_dump_json(),
            experiment_version=stored_spec.experiment_version,
            status=stored_spec.status,
            updated_at=datetime.now(UTC).isoformat(),
        )

        return {
            "experiment_id": experiment_id,
            "code_extension": updated_ext.model_dump(mode="json"),
            "sandbox_result": {
                "success": sandbox_result.success,
                "error": sandbox_result.error,
                "execution_time_s": sandbox_result.execution_time_s,
                "stdout": sandbox_result.stdout,
            },
        }

    @application.post(
        "/api/projects/{project_id}/experiment-specs/{experiment_id}/code-extensions/{extension_id}/auto-test",
        tags=["experiment-specs"],
    )
    def auto_test_code_extension(
        project_id: str,
        experiment_id: str,
        extension_id: str,
    ) -> dict:
        """Run automatic tests for a code extension.

        Transitions the extension from SANDBOX_TESTED to AUTO_TESTED (or
        REJECTED if any test fails).  Calls ``auto_test_extension()``
        from the code_extension registry module.

        Returns the updated extension and the test results.
        """
        from fluid_scientist.code_extension.models import (
            CodeExtensionSpec,
            CodeExtensionType,
            ExtensionStatus,
        )
        from fluid_scientist.code_extension.registry import (
            ExtensionRegistry,
            auto_test_extension,
        )

        stored_spec = workflow_repository.load_experiment_spec(experiment_id)
        if stored_spec is None or stored_spec.project_id != project_id:
            raise HTTPException(
                status_code=404, detail="ExperimentSpec not found"
            )
        spec = ExperimentSpec.model_validate_json(stored_spec.spec_json)

        # Find the extension
        ext_data = None
        ext_index = -1
        for i, ext in enumerate(spec.code_extensions):
            if ext.get("extension_id") == extension_id:
                ext_data = ext
                ext_index = i
                break

        if ext_data is None:
            raise HTTPException(
                status_code=404,
                detail=f"CodeExtension '{extension_id}' not found",
            )

        # Build a CodeExtensionSpec and run auto tests via registry
        extension = CodeExtensionSpec(
            extension_id=ext_data["extension_id"],
            name=ext_data["name"],
            description=ext_data.get("description", ""),
            extension_type=CodeExtensionType(ext_data["extension_type"]),
            code=ext_data.get("code", ""),
            language=ext_data.get("language", "python"),
            dependencies=tuple(ext_data.get("dependencies", [])),
            openfoam_files=tuple(ext_data.get("openfoam_files", [])),
            tests=tuple(ext_data.get("tests", [])),
            status=ExtensionStatus(ext_data.get("status", "sandbox_tested")),
            version=ext_data.get("version", "1.0.0"),
            author=ext_data.get("author", "system"),
            review_notes=ext_data.get("review_notes", ""),
            created_at=ext_data.get("created_at", ""),
            updated_at=ext_data.get("updated_at", ""),
        )

        registry = ExtensionRegistry()
        registry.extensions[extension.extension_id] = extension

        try:
            updated_ext, test_results = auto_test_extension(
                registry, extension_id
            )
        except ValueError as e:
            raise HTTPException(
                status_code=400,
                detail=str(e),
            ) from e

        # Persist the updated status back into the spec
        spec.code_extensions[ext_index] = updated_ext.model_dump(mode="json")
        workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=spec.model_dump_json(),
            experiment_version=stored_spec.experiment_version,
            status=stored_spec.status,
            updated_at=datetime.now(UTC).isoformat(),
        )

        return {
            "experiment_id": experiment_id,
            "code_extension": updated_ext.model_dump(mode="json"),
            "test_results": [r.model_dump() for r in test_results],
        }


    # ------------------------------------------------------------------
    # Workbench Agent API (natural language editing)
    # ------------------------------------------------------------------
    @application.post(
        "/api/research-sessions/{session_id}/workbench-turn",
        tags=["workbench"],
    )
    def workbench_turn(
        session_id: str,
        payload: WorkbenchTurnRequest,
    ) -> dict:
        """Process a workbench natural language turn.

        Accepts a user message and the current experiment spec, then
        returns an EditProposal for user confirmation.  The proposal is
        stored in app state for later apply via /apply-edit.
        """
        import json

        from fluid_scientist.workbench.workbench_agent import WorkbenchAgent

        stored = workflow_repository.load_experiment_spec(
            payload.experiment_id
        )
        if stored is None:
            raise HTTPException(
                status_code=404, detail="experiment spec not found"
            )

        # Version check
        if payload.experiment_version != stored.experiment_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "current_version": stored.experiment_version,
                    "client_version": payload.experiment_version,
                    "message": "实验参数已被其他操作修改，请刷新后再提交。",
                },
            )

        spec_dict = json.loads(stored.spec_json)

        # Create WorkbenchAgent (fake mode if no LLM client configured)
        llm_client = getattr(application.state, "llm_client", None)
        agent = WorkbenchAgent(llm_client=llm_client)

        # Process the turn
        proposal = agent.process_turn(
            payload.message, spec_dict
        )

        # Store EditProposal in app state for later apply
        proposal_dict = proposal.model_dump()
        application.state.workbench_proposals[proposal.proposal_id] = (
            proposal_dict
        )

        return proposal_dict

    @application.post(
        "/api/experiment-specs/{experiment_id}/apply-edit",
        tags=["workbench"],
    )
    def apply_edit(
        experiment_id: str,
        payload: ApplyEditRequest,
    ) -> dict:
        """Apply a confirmed EditProposal to the experiment spec.

        Loads the stored EditProposal, applies the accepted operations
        deterministically via SpecEditExecutor, and returns the updated
        spec with a change summary.
        """
        import json

        from fluid_scientist.experiment_spec.state_machine import (
            is_immutable,
        )
        from fluid_scientist.workbench.edit_executor import SpecEditExecutor
        from fluid_scientist.workbench.edit_models import EditProposal

        # 1. Load proposal from app state
        proposal_data = application.state.workbench_proposals.get(
            payload.proposal_id
        )
        if proposal_data is None:
            raise HTTPException(
                status_code=404, detail="proposal not found"
            )

        # 2. Load current spec from repository
        stored = workflow_repository.load_experiment_spec(experiment_id)
        if stored is None:
            raise HTTPException(
                status_code=404, detail="experiment spec not found"
            )

        # 3. Verify version matches
        if stored.experiment_version != payload.experiment_version:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "current_version": stored.experiment_version,
                    "client_version": payload.experiment_version,
                    "message": "实验参数已被其他操作修改，请刷新后再提交。",
                },
            )

        spec_dict = json.loads(stored.spec_json)
        proposal = EditProposal.model_validate(proposal_data)

        # 4. Create SpecEditExecutor, call apply()
        executor = SpecEditExecutor()
        updated_spec, change_summary = executor.apply(
            spec_dict,
            proposal,
            payload.accepted_operation_indices,
        )

        # 5. Save updated spec (increment version if spec was immutable)
        spec_status = stored.status
        new_version = (
            stored.experiment_version + 1
            if is_immutable(spec_status)
            else stored.experiment_version
        )
        now = datetime.now(UTC).isoformat()
        updated_stored = workflow_repository.replace_experiment_spec(
            experiment_id,
            spec_json=json.dumps(updated_spec, ensure_ascii=False),
            experiment_version=new_version,
            status=spec_status,
            updated_at=now,
        )

        # 6. Return updated spec + change_summary
        result = json.loads(updated_stored.spec_json)
        result["_change_summary"] = change_summary.model_dump()
        return result

    return application


def build_execution_targets(
    settings: AppSettings,
    transport_factory: Callable[[NodeSettings], SSHTransport] = SSHTransport,
) -> tuple[ExecutionTargetAdapter, ...]:
    workstation = settings.workstation
    if not workstation.hosts or not workstation.username or not workstation.known_hosts_file:
        return ()
    candidates = tuple(
        (
            f"candidate-{index}",
            transport_factory(
                NodeSettings(
                    host=host,
                    username=workstation.username,
                    port=workstation.port,
                    identity_file=workstation.identity_file,
                    known_hosts_file=workstation.known_hosts_file,
                )
            ),
        )
        for index, host in enumerate(workstation.hosts, start=1)
    )
    return (
        WorkstationOpenFOAMTarget(
            target_id="workstation-openfoam",
            candidates=candidates,
        ),
    )


def _operation_view(record: OperationRecord) -> OperationView:
    return OperationView(
        operation_id=record.operation_id,
        kind=record.kind,
        state=record.state,
        stage=record.stage,
        message=record.message,
        result_ref=record.result_ref,
        safe_error=record.safe_error,
        cancel_requested=record.cancel_requested,
        attempt=record.attempt,
        created_at=record.created_at,
        updated_at=record.updated_at,
        terminal=record.terminal,
    )


def _planning_capabilities(target: ExecutionTargetAdapter) -> tuple[str, ...]:
    """Use declared software/type support without contacting the remote host."""

    experiment_types = tuple(
        capability.experiment_type for capability in EXPERIMENT_CAPABILITIES
    )
    declared = tuple(getattr(target, "declared_capabilities", ()))
    kind = getattr(target, "kind", None)
    target_markers = (target.target_id,) if kind is None else (kind, target.target_id)
    return tuple(dict.fromkeys(experiment_types + declared + target_markers))


def _require_fresh_target(target: ExecutionTargetAdapter) -> ExecutionTargetCapability:
    """Gate a remote mutation on a non-cached doctor result."""

    try:
        capability = target.doctor()
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="execution target capability check failed",
        ) from error
    if not capability.available:
        raise HTTPException(status_code=503, detail="execution target is unavailable")
    return capability


def _submit_custom_fresh(
    target: ExecutionTargetAdapter,
    submit: Callable[[str, bytes], JobRecord],
    job_id: str,
    archive: bytes,
) -> JobRecord:
    _require_fresh_target(target)
    return submit(job_id, archive)


def _bound_benchmark(
    project_service: ProjectService,
    target_registry: dict[str, ExecutionTargetAdapter],
    project_id: str,
    case_id: str,
    target_id: str,
) -> tuple[ExecutionTargetAdapter, str]:
    target = target_registry.get(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="execution target not found")
    try:
        project = project_service.get(project_id)
        job_id = project.external_jobs[case_id]
    except KeyError as error:
        raise HTTPException(status_code=404, detail="benchmark job not found") from error
    return target, job_id


def _experiment_job_id(
    project_id: str,
    experiment_name: str,
    *,
    timestamp: datetime | None = None,
) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", experiment_name.lower()).strip("-")
    slug = (slug or "openfoam-experiment")[:48].rstrip("-")
    timestamp_text = (timestamp or datetime.now(UTC)).astimezone(UTC).strftime(
        "%Y%m%d-%H%M%S"
    )
    project_suffix = project_id.replace("-", "")[:8].lower()
    return f"{timestamp_text}-{slug}-{project_suffix}"


def _leaf_evidence_keys(value: object, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        keys: set[str] = set()
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            keys.update(_leaf_evidence_keys(item, child))
        return keys
    if isinstance(value, (list, tuple)):
        return {prefix} if prefix else set()
    return {prefix} if prefix else set()




app = create_app()
