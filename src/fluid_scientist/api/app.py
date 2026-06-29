"""Credential-free FastAPI application wired to deterministic demo adapters."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from fluid_scientist.adapters.fakes import build_demo_service
from fluid_scientist.services.research import DemoResearchResult

ROOT = Path(__file__).resolve().parents[3]
WEB_ROOT = ROOT / "apps" / "web"

app = FastAPI(
    title="Fluid Scientist",
    version="0.1.0",
    description="Evidence-grounded fluid mechanics research workflow",
)
app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")

_projects: dict[str, DemoResearchResult] = {}


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=10, max_length=2_000)


@app.get("/", include_in_schema=False)
def workbench() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "fake"}


@app.post(
    "/api/demo",
    response_model=DemoResearchResult,
    status_code=status.HTTP_201_CREATED,
)
def run_demo(request: DemoRequest) -> DemoResearchResult:
    result = build_demo_service().run_approved_demo(request.question)
    _projects[result.project_id] = result
    return result


@app.get("/api/projects/{project_id}", response_model=DemoResearchResult)
def get_project(project_id: str) -> DemoResearchResult:
    try:
        return _projects[project_id]
    except KeyError as error:
        raise HTTPException(status_code=404, detail="project not found") from error

