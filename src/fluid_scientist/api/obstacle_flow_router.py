"""API router for the obstacle flow experiment family.

Exposes REST endpoints for compiling, validating, and post-processing
ConfigurableObstacleFlow2D experiments.

Endpoints:
  POST   /api/v5/obstacle-flow/compile        — Compile spec to OpenFOAM case
  POST   /api/v5/obstacle-flow/validate       — Static validation only
  POST   /api/v5/obstacle-flow/postprocess    — Create PlotSpec
  GET    /api/v5/obstacle-flow/health         — Health check
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from fluid_scientist.obstacle_flow.compiler import CompilationError
from fluid_scientist.obstacle_flow.integration import (
    ObstacleFlowCompilationResult,
    compile_obstacle_flow_spec,
    validate_archive_security,
)
from fluid_scientist.obstacle_flow.models import (
    ObstacleFlowExperimentSpecV1,
)
from fluid_scientist.obstacle_flow.postprocessing import (
    WorkstationObstacleFlowPostprocessor,
)

router = APIRouter(prefix="/api/v5/obstacle-flow", tags=["obstacle-flow"])


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class CompileRequest(BaseModel):
    """Request to compile an obstacle flow experiment spec."""

    spec: dict[str, Any] = Field(..., description="ObstacleFlowExperimentSpecV1 as JSON dict")
    run_security_validation: bool = Field(True, description="Whether to run security validation")


class CompileResponse(BaseModel):
    """Response from compilation."""

    success: bool
    compilation_id: str | None = None
    spec_hash: str | None = None
    case_hash: str | None = None
    flow_mode: str | None = None
    has_cylinder: bool | None = None
    has_bump: bool | None = None
    generated_files: list[str] | None = None
    archive_sha256: str | None = None
    archive_size: int | None = None
    static_validation_passed: bool | None = None
    static_validation_errors: list[str] | None = None
    static_validation_warnings: list[str] | None = None
    security_validation_passed: bool | None = None
    security_validation_details: dict[str, Any] | None = None
    preprocessing: list[str] | None = None
    required_outputs: list[str] | None = None
    error: str | None = None


class ValidateRequest(BaseModel):
    """Request for static validation only."""

    spec: dict[str, Any] = Field(..., description="ObstacleFlowExperimentSpecV1 as JSON dict")


class ValidateResponse(BaseModel):
    """Response from static validation."""

    passed: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    flow_mode: str | None = None
    reynolds_estimate: float | None = None
    is_transient: bool | None = None
    is_turbulent: bool | None = None


class PostprocessRequest(BaseModel):
    """Request to create a PlotSpec."""

    spec: dict[str, Any] = Field(..., description="ObstacleFlowExperimentSpecV1 as JSON dict")
    run_id: str = Field(..., description="Unique run identifier")
    case_path: str = Field(..., description="Path to the case on the workstation")


class PostprocessResponse(BaseModel):
    """Response with PlotSpec and post-processing script."""

    plot_spec: dict[str, Any]
    postprocess_script: str
    n_plots: int
    n_metrics: int


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    module: str = "obstacle_flow"
    version: str = "1.0.0"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check if the obstacle flow module is available."""
    return HealthResponse()


@router.post("/compile", response_model=CompileResponse)
async def compile_spec(request: CompileRequest) -> CompileResponse:
    """Compile an ObstacleFlowExperimentSpecV1 into OpenFOAM case files.

    This endpoint:
    1. Parses the spec JSON into ObstacleFlowExperimentSpecV1
    2. Compiles using ObstacleFlowCompiler
    3. Runs static validation
    4. Optionally runs security validation
    5. Returns the compilation result
    """
    try:
        spec = ObstacleFlowExperimentSpecV1(**request.spec)
    except Exception as e:
        return CompileResponse(
            success=False,
            error=f"Spec validation failed: {e}",
        )

    try:
        result = compile_obstacle_flow_spec(spec)
    except CompilationError as e:
        return CompileResponse(
            success=False,
            error=f"Compilation failed: {e}",
        )
    except Exception as e:
        return CompileResponse(
            success=False,
            error=f"Unexpected error: {e}",
        )

    response = CompileResponse(
        success=True,
        compilation_id=result.manifest.compilation_id,
        spec_hash=result.manifest.spec_hash,
        case_hash=result.manifest.case_hash,
        flow_mode=result.manifest.flow_mode,
        has_cylinder=result.manifest.has_cylinder,
        has_bump=result.manifest.has_bump,
        generated_files=result.manifest.generated_files,
        archive_sha256=result.compiled.archive_sha256,
        archive_size=len(result.compiled.archive),
        static_validation_passed=result.static_validation.passed,
        static_validation_errors=result.static_validation.errors,
        static_validation_warnings=result.static_validation.warnings,
        preprocessing=list(result.compiled.preprocessing),
        required_outputs=list(result.compiled.required_outputs),
    )

    if request.run_security_validation:
        sec_result = validate_archive_security(result.compiled.archive)
        response.security_validation_passed = sec_result.get("passed", False)
        response.security_validation_details = sec_result

    return response


@router.post("/validate", response_model=ValidateResponse)
async def validate_spec(request: ValidateRequest) -> ValidateResponse:
    """Run static validation on a spec without compiling."""
    try:
        spec = ObstacleFlowExperimentSpecV1(**request.spec)
    except Exception as e:
        return ValidateResponse(
            passed=False,
            errors=[f"Spec validation failed: {e}"],
        )

    try:
        result = compile_obstacle_flow_spec(spec)
        return ValidateResponse(
            passed=result.static_validation.passed,
            errors=result.static_validation.errors,
            warnings=result.static_validation.warnings,
            flow_mode=result.manifest.flow_mode,
            reynolds_estimate=spec.estimate_reynolds(),
            is_transient=spec.is_transient,
            is_turbulent=spec.is_turbulent,
        )
    except Exception as e:
        return ValidateResponse(
            passed=False,
            errors=[f"Validation failed: {e}"],
        )


@router.post("/postprocess", response_model=PostprocessResponse)
async def create_postprocess_spec(request: PostprocessRequest) -> PostprocessResponse:
    """Create a PlotSpec and post-processing script from a spec."""
    try:
        spec = ObstacleFlowExperimentSpecV1(**request.spec)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Spec validation failed: {e}",
        )

    pp = WorkstationObstacleFlowPostprocessor()
    plot_spec = pp.create_plot_spec(spec, request.run_id, request.case_path)
    script = pp.generate_postprocess_script(plot_spec)

    return PostprocessResponse(
        plot_spec=plot_spec.to_dict(),
        postprocess_script=script,
        n_plots=len(plot_spec.plots),
        n_metrics=len(plot_spec.metrics),
    )


# ---------------------------------------------------------------------------
# Spec schema endpoint
# ---------------------------------------------------------------------------


@router.get("/schema")
async def get_spec_schema() -> dict[str, Any]:
    """Get the ObstacleFlowExperimentSpecV1 JSON schema."""
    return ObstacleFlowExperimentSpecV1.model_json_schema()
