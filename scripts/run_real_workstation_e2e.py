"""Run a small real OpenFOAM-13 workstation E2E and print stage evidence."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fluid_scientist.adapters.openfoam import LaminarPipeCase, validate_laminar_pipe
from fluid_scientist.api.app import create_app


def emit(stage: str, **payload) -> None:
    print(json.dumps({"stage": stage, **payload}, ensure_ascii=False), flush=True)


def main() -> None:
    app = create_app()
    targets = list(app.state.execution_targets)
    if not targets:
        raise SystemExit("NO_WORKSTATION_TARGET")
    target = targets[0]
    doctor = target.doctor()
    emit("Doctor", **doctor.model_dump(mode="json"))
    if not doctor.available or doctor.foam_version != "OpenFOAM-13":
        raise SystemExit("DOCTOR_FAILED")

    spec = LaminarPipeCase(
        diameter_m=0.05,
        length_m=0.5,
        mean_velocity_m_s=0.02,
        kinematic_viscosity_m2_s=1e-6,
        density_kg_m3=998.2,
        axial_cells=40,
        radial_cells=8,
    )
    job_id = "codex_e2e_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    job = target.submit(job_id, spec)
    emit(
        "Upload",
        job_id=job.job_id,
        state=job.state.value,
        manifest_file_count=len(job.case_manifest),
        manifest_paths=sorted(job.case_manifest),
    )

    deadline = time.monotonic() + 600
    last_state = None
    while time.monotonic() < deadline:
        job = target.status(job_id)
        if job.state.value != last_state:
            emit("RemoteJob", job_id=job_id, state=job.state.value, error=job.error)
            last_state = job.state.value
        if job.state.value in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(2)
    else:
        raise SystemExit("REMOTE_JOB_TIMEOUT")
    if job.state.value != "succeeded":
        raise SystemExit(f"REMOTE_JOB_FAILED: {job.error}")

    collection = target.collect(job_id)
    emit("Mesh", **collection.mesh.model_dump(mode="json"))
    emit(
        "checkMesh",
        passed=collection.mesh.passed,
        cells=collection.mesh.cells,
        max_non_orthogonality=collection.mesh.max_non_orthogonality,
        max_skewness=collection.mesh.max_skewness,
    )
    emit("Smoke", covered=False, reason="fixed worker protocol currently runs mesh then full solver without a separate smoke gate")
    emit("Solver", **collection.solver.model_dump(mode="json"))
    emit(
        "Postprocess",
        available=collection.post_processing is not None,
        payload=collection.post_processing.model_dump(mode="json") if collection.post_processing else None,
    )
    emit(
        "Collect",
        state=collection.state,
        observables=collection.observables.model_dump(mode="json"),
        analysis=collection.analysis,
        case_manifest_file_count=len(collection.case_manifest),
    )
    validation = validate_laminar_pipe(
        spec,
        pressure_drop_pa=collection.solver.pressure_drop_pa,
        inlet_mass_flow=collection.solver.inlet_mass_flow,
        outlet_mass_flow=collection.solver.outlet_mass_flow,
        final_residuals=collection.solver.final_residuals,
    )
    emit("NumericalPhysicalValidation", **validation.model_dump(mode="json"))
    emit(
        "UI",
        api_payload_ready=True,
        browser_verified=False,
        job_id=job_id,
        summary={
            "mesh_passed": collection.mesh.passed,
            "solver_completed": collection.solver.completed,
            "validation_passed": validation.passed,
        },
    )


if __name__ == "__main__":
    main()
