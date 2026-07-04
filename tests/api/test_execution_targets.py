import io
import re
import tarfile
import time
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import update

from fluid_scientist.adapters.custom_openfoam import validate_custom_case_archive
from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.db import CompiledExperimentRow
from fluid_scientist.execution_targets.base import ExecutionTargetCapability
from fluid_scientist.execution_targets.workstation import WorkerCollection
from fluid_scientist.experiment_planning.models import PipeExperimentPlan
from fluid_scientist.experiment_planning.result_analysis import ExperimentAnalysis
from fluid_scientist.settings import AppSettings
from fluid_scientist.worker.service import CustomCaseSpec, JobRecord, JobState


class StaticTarget:
    target_id = "workstation-openfoam"
    kind = "workstation_openfoam"
    declared_capabilities = ("OpenFOAM-13",)

    def doctor(self):
        return ExecutionTargetCapability(
            target_id=self.target_id,
            kind="workstation_openfoam",
            available=True,
            selected_candidate="primary",
            foam_version="OpenFOAM-v2312",
            cpu_count=32,
            memory_gb=128,
            disk_free_gb=500,
            commands=("blockMesh", "checkMesh", "simpleFoam", "postProcess"),
            worker_protocol=1,
        )


def test_execution_targets_api_lists_capabilities() -> None:
    client = TestClient(create_app(execution_targets=(StaticTarget(),)))

    response = client.get("/api/execution-targets")

    assert response.status_code == 200
    assert response.json()[0]["kind"] == "workstation_openfoam"
    assert response.json()[0]["available"] is True
    assert 'id="execution-target"' in client.get("/").text


def test_execution_targets_api_reuses_cached_doctor_result() -> None:
    class CountingTarget(StaticTarget):
        def __init__(self) -> None:
            self.calls = 0

        def doctor(self):
            self.calls += 1
            return super().doctor()

    target = CountingTarget()
    client = TestClient(create_app(execution_targets=(target,)))

    first = client.get("/api/execution-targets")
    second = client.get("/api/execution-targets")

    assert target.calls == 1
    assert first.json()[0]["cached"] is False
    assert second.json()[0]["cached"] is True
    assert second.json()[0]["checked_at"] == first.json()[0]["checked_at"]


def test_app_builds_workstation_candidates_from_runtime_settings_only() -> None:
    created_nodes = []

    class RecordingTransport:
        def __init__(self, node) -> None:
            created_nodes.append(node)

        def execute(self, program, args, *, timeout):
            raise RuntimeError("not used")

    settings = AppSettings(
        app_mode="fake",
        workstation={
            "hosts": ("workstation-a.internal", "workstation-b.internal"),
            "username": "ls",
            "identity_file": "runtime-key",
            "known_hosts_file": "runtime-known-hosts",
        },
    )

    application = create_app(settings=settings, transport_factory=RecordingTransport)
    targets = application.state.execution_targets

    assert len(targets) == 1
    assert [node.host for node in created_nodes] == [
        "workstation-a.internal",
        "workstation-b.internal",
    ]
    assert targets[0]._candidates[0][0] == "candidate-1"


class BenchmarkTarget(StaticTarget):
    def __init__(self) -> None:
        self.submissions = []

    def submit(self, job_id: str, spec: LaminarPipeCase) -> JobRecord:
        self.submissions.append((job_id, spec))
        return self._record(job_id, spec)

    def status(self, job_id: str) -> JobRecord:
        return self._record(job_id, pipe_spec())

    def collect(self, job_id: str) -> WorkerCollection:
        return WorkerCollection.model_validate(
            {
                "job_id": job_id,
                "state": "succeeded",
                "mesh": {
                    "passed": True,
                    "cells": 8000,
                    "max_aspect_ratio": 2,
                    "max_non_orthogonality": 3,
                    "average_non_orthogonality": 0.5,
                    "max_skewness": 0.2,
                },
                "solver": {
                    "completed": True,
                    "final_residuals": {"Ux": 1e-8},
                    "global_continuity_error": 2e-10,
                    "cumulative_continuity_error": 3e-9,
                    "inlet_mass_flow": 0.031359,
                    "outlet_mass_flow": -0.0313589,
                    "pressure_drop_pa": 15.9712,
                },
                "case_manifest": {"system/controlDict": "a" * 64},
                "post_processing": {
                    "case_path": f"jobs/{job_id}/case",
                    "paraview_file": f"{job_id}.foam",
                    "time_directories": ["0", "2000"],
                },
            }
        )

    @staticmethod
    def _record(job_id: str, spec: LaminarPipeCase) -> JobRecord:
        return JobRecord(
            job_id=job_id,
            state=JobState.RUNNING,
            spec=spec,
            case_manifest={"system/controlDict": "a" * 64},
            submitted_at="2026-06-30T00:00:00Z",
            pid=123,
        )


def pipe_spec() -> LaminarPipeCase:
    return LaminarPipeCase(
        diameter_m=0.02,
        length_m=2,
        mean_velocity_m_s=0.1,
        kinematic_viscosity_m2_s=1e-6,
    )


def advance_to_pilot_ready(client: TestClient) -> dict:
    project = client.post(
        "/api/projects",
        json={"question": "How accurately can OpenFOAM reproduce laminar pipe pressure loss?"},
    ).json()
    project = client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_1",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
        },
    ).json()
    project = client.post(
        f"/api/projects/{project['project_id']}/actions",
        json={"action": "RETRIEVE_EVIDENCE", "actor": "researcher"},
    ).json()
    return client.post(
        f"/api/projects/{project['project_id']}/actions",
        json={"action": "DESIGN_PILOT", "actor": "researcher"},
    ).json()


def test_gate_two_submission_calls_selected_target_and_persists_job(tmp_path) -> None:
    target = BenchmarkTarget()
    client = TestClient(
        create_app(
            repository=SQLWorkflowRepository(f"sqlite:///{tmp_path / 'workflow.db'}"),
            execution_targets=(target,),
        )
    )
    project = advance_to_pilot_ready(client)
    project = client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_2",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
        },
    ).json()

    response = client.post(
        f"/api/projects/{project['project_id']}/benchmarks",
        json={
            "target_id": "workstation-openfoam",
            "case_id": "pilot-pipe",
            "experiment_name": "Laminar Pipe Pressure Loss",
            "case": pipe_spec().model_dump(),
            "actor": "researcher",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["project"]["workflow_state"] == "PILOT_RUNNING"
    assert body["project"]["external_jobs"]["pilot-pipe"] == body["job"]["job_id"]
    assert re.fullmatch(
        r"\d{8}-\d{6}-laminar-pipe-pressure-loss-[0-9a-f]{8}",
        target.submissions[0][0],
    )


def test_submission_uses_fresh_doctor_and_blocks_unavailable_target(tmp_path) -> None:
    class OfflineBenchmarkTarget(BenchmarkTarget):
        def __init__(self) -> None:
            super().__init__()
            self.doctor_calls = 0
            self.available = True

        def doctor(self):
            self.doctor_calls += 1
            return ExecutionTargetCapability(
                target_id=self.target_id,
                kind="workstation_openfoam",
                available=self.available,
                reason=None if self.available else "offline",
            )

    target = OfflineBenchmarkTarget()
    client = TestClient(
        create_app(
            repository=SQLWorkflowRepository(f"sqlite:///{tmp_path / 'offline.db'}"),
            execution_targets=(target,),
        )
    )
    project = advance_to_pilot_ready(client)
    project = client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_2",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
        },
    ).json()
    cached = client.get("/api/execution-targets")
    assert cached.json()[0]["available"] is True
    target.available = False

    response = client.post(
        f"/api/projects/{project['project_id']}/benchmarks",
        json={
            "target_id": target.target_id,
            "case_id": "pilot-pipe",
            "experiment_name": "Laminar Pipe Pressure Loss",
            "case": pipe_spec().model_dump(),
            "actor": "researcher",
        },
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "execution target is unavailable"}
    assert target.doctor_calls == 2
    assert target.submissions == []


def test_benchmark_submission_is_blocked_until_gate_two(tmp_path) -> None:
    target = BenchmarkTarget()
    client = TestClient(
        create_app(
            repository=SQLWorkflowRepository(f"sqlite:///{tmp_path / 'workflow.db'}"),
            execution_targets=(target,),
        )
    )
    project = advance_to_pilot_ready(client)

    response = client.post(
        f"/api/projects/{project['project_id']}/benchmarks",
        json={
            "target_id": "workstation-openfoam",
            "case_id": "pilot-pipe",
            "case": pipe_spec().model_dump(),
        },
    )

    assert response.status_code == 409
    assert target.submissions == []


def test_bound_benchmark_exposes_status_and_collected_results(tmp_path) -> None:
    target = BenchmarkTarget()
    client = TestClient(
        create_app(
            repository=SQLWorkflowRepository(f"sqlite:///{tmp_path / 'workflow.db'}"),
            execution_targets=(target,),
        )
    )
    project = advance_to_pilot_ready(client)
    project = client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_2",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
        },
    ).json()
    client.post(
        f"/api/projects/{project['project_id']}/benchmarks",
        json={
            "target_id": "workstation-openfoam",
            "case_id": "pilot-pipe",
            "case": pipe_spec().model_dump(),
        },
    )

    status_response = client.get(
        f"/api/projects/{project['project_id']}/benchmarks/pilot-pipe",
        params={"target_id": "workstation-openfoam"},
    )
    results_response = client.get(
        f"/api/projects/{project['project_id']}/benchmarks/pilot-pipe/results",
        params={"target_id": "workstation-openfoam"},
    )

    assert status_response.status_code == 200
    assert status_response.json()["state"] == "running"
    assert results_response.status_code == 200
    results = results_response.json()
    assert results["collection"]["mesh"]["cells"] == 8000
    assert results["validation"]["passed"] is True
    assert results["project"]["workflow_state"] == "PILOT_VERIFIED"


class CustomCaseTarget(BenchmarkTarget):
    def submit_custom(self, job_id: str, archive: bytes) -> JobRecord:
        self.submissions.append((job_id, archive))
        return JobRecord(
            job_id=job_id,
            state=JobState.RUNNING,
            spec=CustomCaseSpec(
                archive_sha256="sha256:" + "a" * 64,
                solver="incompressibleFluid",
                needs_block_mesh=True,
            ),
            case_manifest={"system/controlDict": "a" * 64},
            submitted_at="2026-07-01T00:00:00Z",
            pid=456,
        )


def custom_archive() -> bytes:
    output = io.BytesIO()
    files = {
        "0/U": "internalField uniform (0 0 0);",
        "constant/physicalProperties": "nu 1e-6;",
        "system/controlDict": "solver incompressibleFluid; endTime 100;",
        "system/fvSchemes": "ddtSchemes {}",
        "system/fvSolution": "solvers {}",
        "system/blockMeshDict": "vertices ();",
    }
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        for name, text in files.items():
            data = text.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
    return output.getvalue()


def test_custom_case_can_be_submitted_polled_and_collected() -> None:
    target = CustomCaseTarget()
    client = TestClient(create_app(execution_targets=(target,)))
    archive = custom_archive()

    submitted = client.post(
        "/api/custom-cases/submit",
        params={
            "target_id": "workstation-openfoam",
            "experiment_name": "Cylinder Flow Re 100",
        },
        content=archive,
        headers={"Content-Type": "application/gzip"},
    )

    assert submitted.status_code == 201
    job_id = submitted.json()["job_id"]
    assert re.fullmatch(r"\d{8}-\d{6}-cylinder-flow-re-100-[0-9a-f]{8}", job_id)
    assert target.submissions == [(job_id, archive)]
    status = client.get(
        f"/api/custom-cases/{job_id}", params={"target_id": target.target_id}
    )
    results = client.get(
        f"/api/custom-cases/{job_id}/results", params={"target_id": target.target_id}
    )
    assert status.status_code == 200
    assert results.status_code == 200
    assert results.json()["post_processing"]["paraview_file"].endswith(".foam")


def test_custom_case_submit_bypasses_cached_health_and_blocks_freshly_offline_target() -> None:
    class ChangingTarget(CustomCaseTarget):
        def __init__(self) -> None:
            super().__init__()
            self.available = True
            self.doctor_calls = 0

        def doctor(self):
            self.doctor_calls += 1
            return ExecutionTargetCapability(
                target_id=self.target_id,
                kind="workstation_openfoam",
                available=self.available,
                reason=None if self.available else "offline",
            )

    target = ChangingTarget()
    client = TestClient(create_app(execution_targets=(target,)))
    archive = custom_archive()
    cached = client.get("/api/execution-targets")
    assert cached.status_code == 200
    assert cached.json()[0]["available"] is True
    target.available = False

    response = client.post(
        "/api/custom-cases/submit",
        params={
            "target_id": target.target_id,
            "experiment_name": "Fresh doctor gate",
        },
        content=archive,
        headers={"Content-Type": "application/gzip"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "execution target is unavailable"}
    assert target.doctor_calls == 2
    assert target.submissions == []


class StaticPipePlanner:
    def design_experiment(self, question: str, *, capabilities: tuple[str, ...]):
        return PipeExperimentPlan.model_validate(
            {
                "experiment_name": "Laminar pipe verification",
                "experiment_type": "laminar_pipe",
                "objective": "Verify pressure loss for fully developed laminar pipe flow.",
                "rationale": "The analytical Hagen-Poiseuille result provides a benchmark.",
                "assumptions": ["Newtonian incompressible fluid"],
                "limitations": ["Laminar single-phase regime only"],
                "requested_outputs": ["pressure_drop", "mass_imbalance"],
                "convergence_targets": {
                    "residual_tolerance": 1e-6,
                    "mass_imbalance_percent": 0.5,
                },
                "case": {
                    "diameter_m": 0.02,
                    "length_m": 1.0,
                    "mean_velocity_m_s": 0.05,
                    "kinematic_viscosity_m2_s": 1e-6,
                    "density_kg_m3": 998.2,
                    "axial_cells": 80,
                    "radial_cells": 10,
                },
            }
        )


class StaticResultAnalyst:
    def analyze(self, summary, *, evidence_keys):
        assert "mesh.cells" in evidence_keys
        return ExperimentAnalysis.model_validate(
            {
                "title": "Laminar pipe smoke analysis",
                "executive_summary": "The mesh and solver completed successfully.",
                "claims": [
                    {
                        "text": "The mesh contains 8000 cells.",
                        "level": "direct_observation",
                        "evidence_keys": ["mesh.cells"],
                    }
                ],
                "credibility_assessment": ["This is a smoke-level run."],
                "limitations": ["No grid-independence study was performed."],
                "recommended_next_steps": ["Run coarse, medium, and fine meshes."],
            }
        )


def planning_client(tmp_path, target: CustomCaseTarget):
    return TestClient(
        create_app(
            repository=SQLWorkflowRepository(f"sqlite:///{tmp_path / 'planning.db'}"),
            execution_targets=(target,),
            plan_designer=StaticPipePlanner(),
            plan_provider_name="glm",
            plan_model_name="glm-5.1",
            result_analyst=StaticResultAnalyst(),
        )
    )


def create_and_compile_plan(client: TestClient, project_id: str) -> tuple[dict, dict]:
    created = client.post(
        "/api/experiment-plans",
        json={
            "question": "Design a laminar pipe pressure-loss verification experiment.",
            "project_id": project_id,
        },
    )
    assert created.status_code == 200
    plan = created.json()
    compiled = client.post(
        f"/api/experiment-plans/{plan['plan_id']}/compile"
    )
    assert compiled.status_code == 200
    return plan, compiled.json()


def test_gate_two_binds_plan_version_and_archive_digest(tmp_path) -> None:
    target = CustomCaseTarget()
    client = planning_client(tmp_path, target)
    project = advance_to_pilot_ready(client)
    plan, preview = create_and_compile_plan(client, project["project_id"])

    approved = client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_2",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "archive_sha256": preview["archive_sha256"],
        },
    )

    assert approved.status_code == 200
    assert approved.json()["approved_artifacts"][plan["plan_id"]] == {
        "plan_version": 1,
        "archive_sha256": preview["archive_sha256"],
    }


def test_gate_two_rejects_digest_that_was_not_compiled(tmp_path) -> None:
    target = CustomCaseTarget()
    client = planning_client(tmp_path, target)
    project = advance_to_pilot_ready(client)
    plan, _ = create_and_compile_plan(client, project["project_id"])

    response = client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_2",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "archive_sha256": "sha256:" + "0" * 64,
        },
    )

    assert response.status_code == 409
    assert "digest" in response.json()["detail"].lower()
    assert target.submissions == []


def test_bound_experiment_submission_uses_exact_approved_archive(tmp_path) -> None:
    target = CustomCaseTarget()
    client = planning_client(tmp_path, target)
    project = advance_to_pilot_ready(client)
    plan, preview = create_and_compile_plan(client, project["project_id"])
    approval = client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_2",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "archive_sha256": preview["archive_sha256"],
        },
    ).json()
    gate_two_approved_at = next(
        item["approved_at"] for item in approval["approvals"] if item["gate"] == "GATE_2"
    )
    time.sleep(1.1)

    submitted = client.post(
        f"/api/projects/{project['project_id']}/experiment-plans/{plan['plan_id']}/submit",
        json={
            "target_id": "workstation-openfoam",
            "case_id": "planned-pipe",
            "actor": "researcher",
            "archive_sha256": preview["archive_sha256"],
        },
    )

    assert submitted.status_code == 201
    assert submitted.json()["project"]["workflow_state"] == "PILOT_RUNNING"
    approved_timestamp = datetime.fromisoformat(gate_two_approved_at).strftime("%Y%m%d-%H%M%S")
    assert submitted.json()["job"]["job_id"].startswith(approved_timestamp)
    assert target.submissions
    submitted_archive = target.submissions[0][1]
    assert validate_custom_case_archive(submitted_archive).archive_sha256 == preview[
        "archive_sha256"
    ]
    assert approval["approved_artifacts"][plan["plan_id"]]["archive_sha256"] == preview[
        "archive_sha256"
    ]

    results = client.get(
        f"/api/projects/{project['project_id']}/experiment-plans/{plan['plan_id']}/results",
        params={"target_id": "workstation-openfoam", "case_id": "planned-pipe"},
    )
    assert results.status_code == 200
    assert results.json()["project"]["workflow_state"] == "PILOT_VERIFIED"
    assert results.json()["summary"] == {
        "experiment_type": "laminar_pipe",
        "requested_outputs": ["pressure_drop", "mass_imbalance"],
        "mesh_passed": True,
        "solver_completed": True,
        "cells": 8000,
        "final_residuals": {"Ux": 1e-8},
        "observables": {},
    }

    analysis = client.post(
        f"/api/projects/{project['project_id']}/experiment-plans/{plan['plan_id']}/analysis",
        params={"target_id": "workstation-openfoam", "case_id": "planned-pipe"},
    )
    assert analysis.status_code == 200
    assert analysis.json()["provider"] == "glm"
    assert analysis.json()["model"] == "glm-5.1"
    assert analysis.json()["analysis"]["claims"][0]["evidence_keys"] == ["mesh.cells"]


def test_bound_submission_rejects_client_digest_different_from_gate_two(tmp_path) -> None:
    target = CustomCaseTarget()
    client = planning_client(tmp_path, target)
    project = advance_to_pilot_ready(client)
    plan, preview = create_and_compile_plan(client, project["project_id"])
    client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_2",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "archive_sha256": preview["archive_sha256"],
        },
    )

    response = client.post(
        f"/api/projects/{project['project_id']}/experiment-plans/{plan['plan_id']}/submit",
        json={
            "target_id": "workstation-openfoam",
            "case_id": "planned-pipe",
            "actor": "researcher",
            "archive_sha256": "sha256:" + "f" * 64,
        },
    )

    assert response.status_code == 409
    assert "digest" in response.json()["detail"].lower()
    assert target.submissions == []


def test_bound_submission_rehashes_stored_archive_before_remote_submit(tmp_path) -> None:
    target = CustomCaseTarget()
    repository = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'tampered.db'}")
    client = TestClient(
        create_app(
            repository=repository,
            execution_targets=(target,),
            plan_designer=StaticPipePlanner(),
            plan_provider_name="glm",
            plan_model_name="glm-5.1",
        )
    )
    project = advance_to_pilot_ready(client)
    plan, preview = create_and_compile_plan(client, project["project_id"])
    client.post(
        f"/api/projects/{project['project_id']}/approvals",
        json={
            "gate": "GATE_2",
            "decision": "approve",
            "actor": "researcher",
            "subject_version": project["version"],
            "plan_id": plan["plan_id"],
            "plan_version": plan["plan_version"],
            "archive_sha256": preview["archive_sha256"],
        },
    )
    with repository._engine.begin() as connection:
        connection.execute(update(CompiledExperimentRow).values(archive=b"tampered"))

    response = client.post(
        f"/api/projects/{project['project_id']}/experiment-plans/{plan['plan_id']}/submit",
        json={
            "target_id": "workstation-openfoam",
            "case_id": "planned-pipe",
            "actor": "researcher",
            "archive_sha256": preview["archive_sha256"],
        },
    )

    assert response.status_code == 409
    assert "digest" in response.json()["detail"].lower()
    assert target.submissions == []
