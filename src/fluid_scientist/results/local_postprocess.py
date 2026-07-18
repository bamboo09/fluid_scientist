"""Local post-processing service — runs on the API server.

Instead of relying on the remote worker to generate visualizations,
this service:
1. Calls collect() to get basic results (mesh, solver, observables)
2. Downloads result files from the workstation via SCP
3. Generates visualizations locally using matplotlib
4. Runs analysis locally using existing modules
5. Stores visualizations in a local directory for serving

When the workstation is offline, the service returns a clear error
but still attempts to produce whatever output is possible.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fluid_scientist.results.models import SimulationData

logger = logging.getLogger(__name__)

# Local storage for generated visualizations
_VIZ_BASE_DIR = Path(tempfile.gettempdir()) / "fluid_scientist_viz"


def _get_viz_dir(job_id: str) -> Path:
    """Get the local visualization directory for a job."""
    viz_dir = _VIZ_BASE_DIR / job_id / "visualizations"
    viz_dir.mkdir(parents=True, exist_ok=True)
    return viz_dir


def _list_remote_files(target: Any, job_id: str) -> list[str]:
    """List result files on the remote workstation via SSH.

    Tries to list postProcessing and log files. Returns empty list on failure.
    """
    # RemoteArg doesn't allow spaces/special chars, so we skip complex shell commands.
    # Instead, we rely on known file paths in _download_result_files.
    return []


def _download_remote_file(target: Any, remote_path: str) -> bytes | None:
    """Download a single file from the remote workstation."""
    try:
        transport = target._transport()
        return transport.download_file(remote_path, timeout=30.0)
    except Exception as e:
        logger.warning("Failed to download %s: %s", remote_path, e)
        return None


def _download_result_files(target: Any, job_id: str) -> dict[str, bytes]:
    """Download key result files from the workstation.

    Returns a dict mapping relative path -> file bytes.
    """
    remote_base = f".local/share/fluid-scientist/jobs/{job_id}"
    files_to_download = [
        # Solver log
        f"{remote_base}/log.foamRun",
        f"{remote_base}/log.pimpleFoam",
        # Force coefficients
        f"{remote_base}/postProcessing/forceCoeffs/0/forceCoeffs.dat",
        f"{remote_base}/postProcessing/forces/0/forces.dat",
        # Surface field values
        f"{remote_base}/postProcessing/surfaceFieldValue/0/surfaceFieldValue.dat",
    ]

    # Also try to list and find more files
    remote_files = _list_remote_files(target, job_id)
    for rf in remote_files:
        if rf not in files_to_download:
            files_to_download.append(rf)

    downloaded: dict[str, bytes] = {}
    for remote_path in files_to_download[:50]:  # Limit to 50 files
        data = _download_remote_file(target, remote_path)
        if data:
            # Store with a simplified key
            rel_path = remote_path.replace(f"{remote_base}/", "")
            downloaded[rel_path] = data

    return downloaded


def _parse_force_coefficients(content: str) -> dict[str, list[float]]:
    """Parse forceCoeffs.dat file content.

    Format: # Time Cd Cl Cm
    """
    result: dict[str, list[float]] = {"Cd": [], "Cl": [], "Cm": []}
    times: list[float] = []

    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 4:
            try:
                times.append(float(parts[0]))
                result["Cd"].append(float(parts[1]))
                result["Cl"].append(float(parts[2]))
                result["Cm"].append(float(parts[3]))
            except (ValueError, IndexError):
                pass

    if times:
        result["_time"] = times  # type: ignore

    # Remove empty lists
    return {k: v for k, v in result.items() if v}


def _parse_residuals_from_log(content: str) -> dict[str, list[float]]:
    """Parse residual history from solver log."""
    residuals: dict[str, list[float]] = {}

    for line in content.split("\n"):
        # Look for residual lines like: "PIMPLE: convergence criterion found"
        # or "GAMG: Solving for p, Initial residual = 0.1, Final residual = 0.01"
        match = re.search(
            r"Solving for (\w+).*Initial residual = ([\d.eE+-]+)", line
        )
        if match:
            var = match.group(1)
            val = float(match.group(2))
            if var not in residuals:
                residuals[var] = []
            residuals[var].append(val)

    return residuals


def _parse_courant_from_log(content: str) -> list[float]:
    """Parse Courant number history from solver log."""
    courants: list[float] = []
    for line in content.split("\n"):
        match = re.search(r"Courant Number mean:\s*[\d.eE+-]+\s+max:\s*([\d.eE+-]+)", line)
        if match:
            courants.append(float(match.group(1)))
    return courants


def _parse_continuity_from_log(content: str) -> list[float]:
    """Parse continuity errors from solver log."""
    errors: list[float] = []
    for line in content.split("\n"):
        match = re.search(r"continuity errors :.*global = ([\d.eE+-]+)", line)
        if match:
            errors.append(float(match.group(1)))
    return errors


def _build_simulation_data(
    collection: Any,
    downloaded_files: dict[str, bytes],
) -> SimulationData:
    """Build SimulationData from collect() results and downloaded files."""
    sim_data = SimulationData()

    # From collection - basic data
    if hasattr(collection, "solver") and collection.solver:
        s = collection.solver
        sim_data.final_residuals = dict(s.final_residuals) if s.final_residuals else {}
        if s.pressure_drop_pa is not None:
            sim_data.surface_field_values["pressure_drop"] = [s.pressure_drop_pa]
        if s.inlet_mass_flow is not None and s.outlet_mass_flow is not None:
            sim_data.surface_field_values["inlet_mass_flow"] = [s.inlet_mass_flow]
            sim_data.surface_field_values["outlet_mass_flow"] = [s.outlet_mass_flow]

    if hasattr(collection, "observables") and collection.observables:
        obs = collection.observables
        if obs.drag_coefficient is not None:
            sim_data.force_coefficients["Cd"] = [obs.drag_coefficient]
        if obs.lift_coefficient is not None:
            sim_data.force_coefficients["Cl"] = [obs.lift_coefficient]
        if obs.moment_coefficient is not None:
            sim_data.force_coefficients["Cm"] = [obs.moment_coefficient]

    if hasattr(collection, "mesh") and collection.mesh:
        m = collection.mesh
        sim_data.mesh_cells = m.cells
        sim_data.mesh_max_aspect_ratio = m.max_aspect_ratio
        sim_data.mesh_max_non_orthogonality = m.max_non_orthogonality

    # From downloaded files - time series data
    for rel_path, content_bytes in downloaded_files.items():
        content = content_bytes.decode("utf-8", errors="replace")

        # Force coefficients
        if "forceCoeffs" in rel_path and rel_path.endswith(".dat"):
            fc = _parse_force_coefficients(content)
            if fc:
                sim_data.force_coefficients.update(fc)
                if "_time" in fc:
                    sim_data.time_values["forceCoeffs"] = fc["_time"]  # type: ignore

        # Solver log
        elif rel_path.startswith("log.") or rel_path.endswith(".log"):
            residuals = _parse_residuals_from_log(content)
            if residuals:
                sim_data.residuals.update(residuals)

            courants = _parse_courant_from_log(content)
            if courants:
                sim_data.courant_numbers = courants
                sim_data.max_courant = max(courants) if courants else None

            continuity = _parse_continuity_from_log(content)
            if continuity:
                sim_data.continuity_errors = continuity
                sim_data.final_continuity_error = continuity[-1] if continuity else None

            # Check convergence
            if "converged" in content.lower() or "Final residual" in content:
                sim_data.converged = True

        # Surface field values
        elif "surfaceFieldValue" in rel_path and rel_path.endswith(".dat"):
            for line in content.strip().split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        val = float(parts[-1])
                        sim_data.surface_field_values.setdefault(
                            "surface_values", []
                        ).append(val)
                    except ValueError:
                        pass

    # Mark missing data
    if not sim_data.force_coefficients:
        sim_data.missing_data.append("forceCoeffs")
    if not sim_data.residuals:
        sim_data.missing_data.append("residuals")

    return sim_data


def _run_analysis(sim_data: SimulationData) -> dict[str, Any]:
    """Run the analysis pipeline locally."""
    try:
        from fluid_scientist.results.metric_executor import MetricExecutor
        from fluid_scientist.results.analysis import ScientificAnalyzer

        # Execute metrics — use execute_all with relevant metric IDs
        executor = MetricExecutor()

        # Determine which metrics to calculate based on available data
        metric_ids: list[str] = []
        if sim_data.force_coefficients:
            if "Cd" in sim_data.force_coefficients:
                metric_ids.append("drag_coefficient")
            if "Cl" in sim_data.force_coefficients:
                metric_ids.append("lift_coefficient")
        if sim_data.surface_field_values:
            metric_ids.append("pressure_drop")
        if sim_data.courant_numbers:
            metric_ids.append("max_courant")
        if sim_data.residuals:
            metric_ids.append("residual_tolerance")
        # Always try these
        metric_ids.extend(["reynolds_number", "mass_flow_rate"])

        metric_results = executor.execute_all(metric_ids, sim_data)

        # Run scientific analysis
        analyzer = ScientificAnalyzer()
        analysis = analyzer.analyze(metric_results, sim_data)

        return {
            "metrics": [r.model_dump() for r in metric_results],
            "scientific_analysis": analysis.model_dump(),
            "simulation_data_summary": {
                "converged": sim_data.converged,
                "max_courant": sim_data.max_courant,
                "mesh_cells": sim_data.mesh_cells,
                "n_residual_steps": (
                    max(len(v) for v in sim_data.residuals.values())
                    if sim_data.residuals
                    else 0
                ),
                "force_coefficient_points": (
                    len(sim_data.force_coefficients.get("Cd", []))
                    if sim_data.force_coefficients
                    else 0
                ),
            },
            "warnings": sim_data.warnings + sim_data.missing_data,
        }
    except Exception as e:
        logger.warning("Analysis pipeline failed: %s", e)
        import traceback
        traceback.print_exc()
        return {
            "metrics": [],
            "scientific_analysis": {},
            "simulation_data_summary": {
                "converged": sim_data.converged,
                "mesh_cells": sim_data.mesh_cells,
            },
            "warnings": [f"Analysis pipeline error: {e}"],
        }


def _generate_visualizations(
    sim_data: SimulationData,
    job_id: str,
) -> list[dict[str, Any]]:
    """Generate visualizations locally and save to disk."""
    from fluid_scientist.results.visualizer import PostprocessVisualizer

    viz_dir = _get_viz_dir(job_id)
    visualizer = PostprocessVisualizer(output_dir=viz_dir)

    artifacts = visualizer.generate_all(sim_data, case_path=None)

    # Save artifacts to disk and build response
    result: list[dict[str, Any]] = []
    for i, art in enumerate(artifacts):
        ext = art.format
        filename = f"{art.type}_{art.field}_{i:03d}.{ext}"
        filepath = viz_dir / filename
        filepath.write_bytes(art.data)

        result.append({
            "type": art.type,
            "field": art.field,
            "format": art.format,
            "filename": filename,
            "title": art.title,
            "time_step": art.time_step,
        })

    return result


def run_local_postprocess(target: Any, job_id: str) -> dict[str, Any]:
    """Run the full post-processing pipeline locally.

    Args:
        target: WorkstationOpenFOAMTarget instance.
        job_id: The job ID to post-process.

    Returns:
        Dict with analysis, visualizations, mesh, solver, and state.
    """
    # 1. Collect basic results from the workstation
    collection = None
    collect_error: str | None = None
    original_timeout = target._doctor_timeout

    try:
        # Use a longer timeout for collect
        target._doctor_timeout = max(original_timeout, 60.0)
        collection = target.collect(job_id)
    except Exception as e:
        collect_error = str(e)
    finally:
        target._doctor_timeout = original_timeout

    # If collect failed, return error
    if collection is None:
        return {
            "job_id": job_id,
            "state": "unknown",
            "error": f"无法连接工作站执行结果收集: {collect_error}",
            "analysis": {},
            "visualizations": [],
            "mesh": None,
            "solver": None,
            "workstation_online": False,
        }

    # 2. Download result files for time series data
    downloaded_files: dict[str, bytes] = {}
    try:
        downloaded_files = _download_result_files(target, job_id)
    except Exception as e:
        logger.warning("Failed to download result files: %s", e)

    # 3. Build SimulationData
    sim_data = _build_simulation_data(collection, downloaded_files)

    # 4. Run analysis
    analysis = _run_analysis(sim_data)

    # 5. Generate visualizations
    visualizations = _generate_visualizations(sim_data, job_id)

    # 6. Return results
    return {
        "job_id": job_id,
        "state": collection.state,
        "analysis": analysis,
        "visualizations": visualizations,
        "mesh": collection.mesh.model_dump() if hasattr(collection.mesh, "model_dump") else {},
        "solver": collection.solver.model_dump() if hasattr(collection.solver, "model_dump") else {},
        "workstation_online": True,
        "downloaded_files": list(downloaded_files.keys()),
    }


def get_local_visualization(job_id: str, filename: str) -> bytes | None:
    """Get a visualization file from local storage.

    Args:
        job_id: The job ID.
        filename: The visualization filename.

    Returns:
        File bytes, or None if not found.
    """
    # Validate filename to prevent path traversal
    if not re.fullmatch(r"[A-Za-z0-9_\-\.]+", filename):
        return None

    viz_dir = _get_viz_dir(job_id)
    filepath = viz_dir / filename

    if filepath.exists():
        return filepath.read_bytes()

    return None


__all__ = [
    "run_local_postprocess",
    "get_local_visualization",
]
