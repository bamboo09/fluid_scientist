"""Result models for simulation output tracking and metric execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from fluid_scientist.compat import UTC


class ResultManifest(BaseModel):
    """Result manifest — binds simulation results to spec and case versions.

    Every remote run must produce a ResultManifest that ties the output
    files back to the ExperimentSpec version and compiled Case hash
    that produced them.
    """

    run_id: str
    experiment_id: str
    experiment_version: int
    spec_hash: str
    case_hash: str
    remote_job_id: str | None = None
    remote_host: str | None = None
    solver_exit_code: int = 0
    result_paths: list[str] = Field(default_factory=list)
    downloaded_paths: list[str] = Field(default_factory=list)
    started_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None


class SimulationData(BaseModel):
    """Parsed simulation data from OpenFOAM result files.

    This is the structured data that MetricExecutor consumes to
    calculate metrics. Each field corresponds to a specific
    OpenFOAM output type.
    """

    # Solver log
    solver_name: str | None = None
    solver_version: str | None = None
    end_time: float | None = None
    converged: bool = False

    # Residuals
    residuals: dict[str, list[float]] = Field(default_factory=dict)
    final_residuals: dict[str, float] = Field(default_factory=dict)

    # Continuity
    continuity_errors: list[float] = Field(default_factory=list)
    final_continuity_error: float | None = None

    # Courant
    courant_numbers: list[float] = Field(default_factory=list)
    max_courant: float | None = None

    # Force coefficients (forceCoeffs)
    force_coefficients: dict[str, list[float]] = Field(default_factory=dict)
    # e.g., {"Cd": [...], "Cl": [...], "Cm": [...]}

    # Forces
    forces: dict[str, list[float]] = Field(default_factory=dict)

    # Probes
    probe_data: dict[str, list[float]] = Field(default_factory=dict)
    # e.g., {"U_at_point_0": [...], "p_at_point_0": [...]}

    # Surface field values
    surface_field_values: dict[str, list[float]] = Field(default_factory=dict)
    # e.g., {"pressure_inlet_average": [...]}

    # Field averages
    field_averages: dict[str, float] = Field(default_factory=dict)

    # Sample data
    sample_data: dict[str, Any] = Field(default_factory=dict)

    # Mesh quality
    mesh_cells: int | None = None
    mesh_max_aspect_ratio: float | None = None
    mesh_max_non_orthogonality: float | None = None

    # Missing data tracking
    missing_data: list[str] = Field(default_factory=list)
    # e.g., ["forceCoeffs", "probes"] — expected but not found

    # Source files
    source_files: list[str] = Field(default_factory=list)

    # Warnings
    warnings: list[str] = Field(default_factory=list)


class MetricResult(BaseModel):
    """Result of executing a single metric calculation.

    Produced by MetricExecutor from SimulationData.
    """

    metric_id: str
    metric_version: str = "1.0.0"
    value: float | list | dict | None = None
    unit: str = ""
    time_range: tuple[float, float] | None = None
    spatial_scope: str | None = None
    quality_checks: list[dict[str, Any]] = Field(default_factory=list)
    confidence: str = "high"  # high, medium, low, failed
    warnings: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    algorithm_version: str = "1.0.0"
    data_missing: bool = False
    missing_reason: str | None = None


__all__ = ["MetricResult", "ResultManifest", "SimulationData"]
