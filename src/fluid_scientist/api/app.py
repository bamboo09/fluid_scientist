"""FastAPI application for Fake demos and persistent research projects."""

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
)

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
from fluid_scientist.orchestration.workflow import TransitionError
from fluid_scientist.ports import WorkflowRepository
from fluid_scientist.services.model_configuration import (
    LegacyExperimentDesigner,
    ModelConfiguration,
    ProviderName,
)
from fluid_scientist.services.projects import ProjectService, ProjectView
from fluid_scientist.services.research import DemoResearchResult
from fluid_scientist.settings import (
    AppSettings,
    NodeSettings,
    OpenAISettings,
    ProviderSettings,
)
from fluid_scientist.worker.service import JobRecord

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


class ExperimentPlanView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["openai", "glm", "deepseek"]
    model: str
    plan: ExperimentPlan


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
LegacyProviderFactory = Callable[[OpenAISettings], LegacyExperimentDesigner]


class ApprovalRequest(StrictRequest):
    gate: Literal["GATE_1", "GATE_2", "GATE_3"]
    decision: Literal["approve", "reject"]
    actor: str = Field(min_length=1, max_length=128)
    subject_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2_000)


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


class BenchmarkResultsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project: ProjectView
    collection: WorkerCollection
    validation: PipeBenchmarkValidation


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
        legacy_designer=legacy_designer,
    )


def create_app(
    repository: WorkflowRepository | None = None,
    execution_targets: tuple[ExecutionTargetAdapter, ...] | None = None,
    *,
    settings: AppSettings | None = None,
    transport_factory: Callable[[NodeSettings], SSHTransport] = SSHTransport,
    experiment_designer: LegacyExperimentDesigner | None = None,
    plan_designer: PlanExperimentDesigner | None = None,
    plan_provider_name: ProviderName | None = None,
    plan_model_name: str | None = None,
    plan_provider_factory: PlanProviderFactory = create_plan_provider,
    legacy_provider_factory: LegacyProviderFactory = OpenAIResponsesProvider,
    model_configuration: ModelConfiguration | None = None,
) -> FastAPI:
    runtime_settings = settings or AppSettings()
    configured_targets = execution_targets
    if configured_targets is None:
        configured_targets = build_execution_targets(runtime_settings, transport_factory)
    coupled_plan_args = (plan_designer, plan_provider_name, plan_model_name)
    if model_configuration is not None and (
        experiment_designer is not None or any(value is not None for value in coupled_plan_args)
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
    application = FastAPI(
        title="Fluid Scientist",
        version="0.2.0",
        description="Evidence-grounded fluid mechanics research workflow",
    )
    application.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")
    application.state.execution_targets = configured_targets
    application.state.model_configuration = configured_models
    target_registry = {target.target_id: target for target in configured_targets}
    project_service = ProjectService(
        repository or SQLWorkflowRepository(runtime_settings.database.url)
    )
    demo_projects: dict[str, DemoResearchResult] = {}

    @application.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": runtime_settings.app_mode.value}

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
            legacy_designer=legacy_designer,
        )
        application.state.model_configuration = configured_models
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

    @application.post("/api/experiment-plans", response_model=ExperimentPlanView)
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
        try:
            plan = designer.design_experiment(
                request.question,
                capabilities=tuple(
                    capability.experiment_type
                    for capability in EXPERIMENT_CAPABILITIES
                )
                + PLAN_CAPABILITY_MARKERS,
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
                detail="Provider returned an invalid experiment plan",
            ) from error
        except ProviderRequestError as error:
            raise HTTPException(
                status_code=502,
                detail="Experiment plan provider request failed",
            ) from error
        return ExperimentPlanView(provider=provider, model=model, plan=plan)

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
            return submit_custom(job_id, payload)
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
            job = status_method(job_id) if existing else submit(job_id, request.case)
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

    @application.get("/api/execution-targets", response_model=tuple[ExecutionTargetCapability, ...])
    def list_execution_targets() -> tuple[ExecutionTargetCapability, ...]:
        return tuple(target.doctor() for target in configured_targets)

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


def _experiment_job_id(project_id: str, experiment_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", experiment_name.lower()).strip("-")
    slug = (slug or "openfoam-experiment")[:48].rstrip("-")
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    project_suffix = project_id.replace("-", "")[:8].lower()
    return f"{timestamp}-{slug}-{project_suffix}"


app = create_app()
