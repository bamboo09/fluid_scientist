from fastapi.testclient import TestClient

from fluid_scientist.adapters.openfoam import LaminarPipeCase
from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.execution_targets.base import ExecutionTargetCapability
from fluid_scientist.execution_targets.workstation import WorkerCollection
from fluid_scientist.settings import AppSettings
from fluid_scientist.worker.service import JobRecord, JobState


class StaticTarget:
    target_id = "workstation-openfoam"

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
            "case": pipe_spec().model_dump(),
            "actor": "researcher",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["project"]["workflow_state"] == "PILOT_RUNNING"
    assert body["project"]["external_jobs"]["pilot-pipe"] == body["job"]["job_id"]
    assert target.submissions[0][0].endswith("-pilot-pipe")


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
