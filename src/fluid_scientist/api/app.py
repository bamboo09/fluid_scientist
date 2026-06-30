"""FastAPI application for Fake demos and persistent research projects."""

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.adapters.fakes import build_demo_service
from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.execution_targets.base import (
    ExecutionTargetAdapter,
    ExecutionTargetCapability,
)
from fluid_scientist.orchestration.workflow import TransitionError
from fluid_scientist.ports import WorkflowRepository
from fluid_scientist.services.projects import ProjectService, ProjectView
from fluid_scientist.services.research import DemoResearchResult

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


def create_app(
    repository: WorkflowRepository | None = None,
    execution_targets: tuple[ExecutionTargetAdapter, ...] = (),
) -> FastAPI:
    application = FastAPI(
        title="Fluid Scientist",
        version="0.2.0",
        description="Evidence-grounded fluid mechanics research workflow",
    )
    application.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")
    project_service = ProjectService(repository or SQLWorkflowRepository("sqlite://"))
    demo_projects: dict[str, DemoResearchResult] = {}

    @application.get("/", include_in_schema=False)
    def workbench() -> FileResponse:
        return FileResponse(WEB_ROOT / "index.html")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": "fake"}

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

    @application.get(
        "/api/projects/{project_id}", response_model=ProjectView | DemoResearchResult
    )
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

    @application.get(
        "/api/execution-targets", response_model=tuple[ExecutionTargetCapability, ...]
    )
    def list_execution_targets() -> tuple[ExecutionTargetCapability, ...]:
        return tuple(target.doctor() for target in execution_targets)

    return application


app = create_app()
