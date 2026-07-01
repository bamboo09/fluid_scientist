"""FastAPI application for Fake demos and persistent research projects."""

from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.adapters.fakes import build_demo_service
from fluid_scientist.adapters.openfoam import (
    LaminarPipeCase,
    PipeBenchmarkValidation,
    validate_laminar_pipe,
)
from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.execution.ssh import RemoteExecutionError, SSHTransport
from fluid_scientist.execution_targets.base import (
    ExecutionTargetAdapter,
    ExecutionTargetCapability,
)
from fluid_scientist.execution_targets.workstation import (
    WorkerCollection,
    WorkstationOpenFOAMTarget,
)
from fluid_scientist.orchestration.workflow import TransitionError
from fluid_scientist.ports import WorkflowRepository
from fluid_scientist.services.projects import ProjectService, ProjectView
from fluid_scientist.services.research import DemoResearchResult
from fluid_scientist.settings import AppSettings, NodeSettings
from fluid_scientist.worker.service import JobRecord

ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = ROOT / "apps" / "web"


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DemoRequest(StrictRequest):
    question: str = Field(min_length=10, max_length=2_000)


class ProjectRequest(StrictRequest):
    question: str = Field(min_length=10, max_length=2_000)


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


def create_app(
    repository: WorkflowRepository | None = None,
    execution_targets: tuple[ExecutionTargetAdapter, ...] | None = None,
    *,
    settings: AppSettings | None = None,
    transport_factory: Callable[[NodeSettings], SSHTransport] = SSHTransport,
) -> FastAPI:
    runtime_settings = settings or AppSettings()
    configured_targets = execution_targets
    if configured_targets is None:
        configured_targets = build_execution_targets(runtime_settings, transport_factory)
    application = FastAPI(
        title="Fluid Scientist",
        version="0.2.0",
        description="Evidence-grounded fluid mechanics research workflow",
    )
    application.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")
    application.state.execution_targets = configured_targets
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
            job_id = existing or f"{project_id}-{request.case_id}"
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


app = create_app()
