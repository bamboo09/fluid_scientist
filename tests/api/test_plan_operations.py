from concurrent.futures import Future

from fastapi.testclient import TestClient

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.api.app import create_app
from fluid_scientist.execution_targets.base import ExecutionTargetCapability
from fluid_scientist.experiment_planning.models import ExperimentPlan


class ControlledExecutor:
    def __init__(self) -> None:
        self.pending = []

    def submit(self, fn, /, *args, **kwargs):
        self.pending.append((fn, args, kwargs))
        return Future()

    def run_next(self) -> None:
        fn, args, kwargs = self.pending.pop(0)
        fn(*args, **kwargs)


def valid_plan() -> ExperimentPlan:
    return ExperimentPlan.model_validate(
        {
            "experiment_type": "laminar_pipe",
            "experiment_name": "Async pipe study",
            "objective": "Verify pressure loss.",
            "rationale": "Use an analytical benchmark.",
            "assumptions": ["Steady incompressible flow"],
            "limitations": ["Laminar only"],
            "requested_outputs": ["pressure_drop"],
            "convergence_targets": {
                "residual_tolerance": 1e-6,
                "mass_imbalance_percent": 0.1,
            },
            "case": {
                "diameter_m": 0.02,
                "length_m": 2.0,
                "mean_velocity_m_s": 0.08,
                "kinematic_viscosity_m2_s": 1e-6,
                "density_kg_m3": 998.2,
                "axial_cells": 80,
                "radial_cells": 10,
            },
            "parameter_sweeps": [],
        }
    )


class Planner:
    def __init__(self) -> None:
        self.calls = 0
        self.capabilities = ()

    def design_experiment(self, question, *, capabilities, progress=None):
        self.calls += 1
        self.capabilities = capabilities
        return valid_plan()


class OfflineTarget:
    target_id = "offline-workstation"
    kind = "workstation_openfoam"
    declared_capabilities = ("OpenFOAM-13",)

    def __init__(self) -> None:
        self.doctor_calls = 0

    def doctor(self):
        self.doctor_calls += 1
        return ExecutionTargetCapability(
            target_id=self.target_id,
            kind=self.kind,
            available=False,
            reason="offline",
        )


class OnlineTarget(OfflineTarget):
    target_id = "workstation-openfoam"

    def doctor(self):
        self.doctor_calls += 1
        return ExecutionTargetCapability(
            target_id=self.target_id,
            kind=self.kind,
            available=True,
        )


def configured_client(tmp_path, *, target=None):
    executor = ControlledExecutor()
    planner = Planner()
    repository = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'async.db'}")
    target = target or OnlineTarget()
    targets = (target,)
    application = create_app(
        repository=repository,
        execution_targets=targets,
        plan_designer=planner,
        plan_provider_name="glm",
        plan_model_name="glm-5.1",
        planning_executor=executor,
    )
    client = TestClient(application)
    project_id = client.post(
        "/api/projects", json={"question": "Measure pressure loss in a laminar pipe."}
    ).json()["project_id"]
    return client, executor, planner, project_id, target


def test_post_returns_202_before_model_runs_and_polling_transitions(tmp_path) -> None:
    client, executor, planner, project_id, target = configured_client(tmp_path)

    response = client.post(
        "/api/plan-operations",
        json={
            "project_id": project_id,
            "question": "Design a laminar pressure-loss benchmark.",
            "target_id": target.target_id,
        },
    )

    assert response.status_code == 202
    queued = response.json()
    assert queued["state"] == "queued"
    assert planner.calls == 0
    assert len(executor.pending) == 1
    assert "input_digest" not in queued
    assert "version" not in queued
    assert "Design a laminar" not in response.text
    executor.run_next()
    complete = client.get(f"/api/operations/{queued['operation_id']}")
    assert complete.status_code == 200
    assert complete.json()["state"] == "succeeded"
    assert complete.json()["result_ref"]


def test_duplicate_post_reuses_operation_and_delete_cancels(tmp_path) -> None:
    client, executor, _planner, project_id, target = configured_client(tmp_path)
    payload = {
        "project_id": project_id,
        "question": "Design a laminar pressure-loss benchmark.",
        "target_id": target.target_id,
    }

    first = client.post("/api/plan-operations", json=payload)
    duplicate = client.post("/api/plan-operations", json=payload)
    cancelled = client.delete(f"/api/operations/{first.json()['operation_id']}")

    assert first.status_code == duplicate.status_code == 202
    assert duplicate.json()["operation_id"] == first.json()["operation_id"]
    assert len(executor.pending) == 1
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"


def test_same_question_on_different_targets_schedules_distinct_operations(tmp_path) -> None:
    class HPCTarget(OnlineTarget):
        target_id = "hpc-login"
        kind = "hpc_slurm"
        declared_capabilities = ("OpenFOAM-13", "slurm")

    executor = ControlledExecutor()
    planner = Planner()
    repository = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'targets.db'}")
    workstation = OnlineTarget()
    hpc = HPCTarget()
    client = TestClient(
        create_app(
            repository=repository,
            execution_targets=(workstation, hpc),
            plan_designer=planner,
            plan_provider_name="glm",
            plan_model_name="glm-5.1",
            planning_executor=executor,
        )
    )
    project_id = client.post(
        "/api/projects", json={"question": "Measure pressure loss in a laminar pipe."}
    ).json()["project_id"]
    payload = {
        "project_id": project_id,
        "question": "Design a laminar pressure-loss benchmark.",
        "target_id": workstation.target_id,
    }

    first = client.post("/api/plan-operations", json=payload)
    duplicate = client.post("/api/plan-operations", json=payload)
    payload["target_id"] = hpc.target_id
    second_target = client.post("/api/plan-operations", json=payload)

    assert duplicate.json()["operation_id"] == first.json()["operation_id"]
    assert second_target.json()["operation_id"] != first.json()["operation_id"]
    assert len(executor.pending) == 2
    executor.run_next()
    executor.run_next()
    assert planner.calls == 2


def test_offline_target_does_not_block_or_run_doctor_during_planning(tmp_path) -> None:
    target = OfflineTarget()
    client, executor, planner, project_id, _target = configured_client(tmp_path, target=target)

    response = client.post(
        "/api/plan-operations",
        json={
            "project_id": project_id,
            "question": "Design a laminar pressure-loss benchmark.",
            "target_id": target.target_id,
        },
    )
    executor.run_next()

    assert response.status_code == 202
    assert target.doctor_calls == 0
    assert client.get(f"/api/operations/{response.json()['operation_id']}").json()[
        "state"
    ] == "succeeded"
    assert "laminar_pipe" in planner.capabilities
    assert target.target_id in planner.capabilities
    assert target.kind in planner.capabilities
    assert "OpenFOAM-13" in planner.capabilities


def test_async_planning_rejects_unknowns_and_unconfigured_model(tmp_path) -> None:
    client, _executor, _planner, project_id, target = configured_client(tmp_path)
    payload = {
        "project_id": project_id,
        "question": "Design a laminar pressure-loss benchmark.",
        "target_id": "missing",
    }
    assert client.post("/api/plan-operations", json=payload).status_code == 404
    payload["target_id"] = target.target_id
    payload["project_id"] = "missing"
    assert client.post("/api/plan-operations", json=payload).status_code == 404
    assert client.get("/api/operations/missing").status_code == 404
    assert client.delete("/api/operations/missing").status_code == 404

    unconfigured = TestClient(create_app(execution_targets=(target,)))
    project = unconfigured.post(
        "/api/projects", json={"question": "Measure pressure loss in a laminar pipe."}
    ).json()
    payload["project_id"] = project["project_id"]
    payload["target_id"] = target.target_id
    assert unconfigured.post("/api/plan-operations", json=payload).status_code == 503


def test_lifespan_recovers_once_and_requests_safe_shutdown(tmp_path) -> None:
    executor = ControlledExecutor()
    target = OnlineTarget()
    application = create_app(
        repository=SQLWorkflowRepository(f"sqlite:///{tmp_path / 'lifecycle.db'}"),
        execution_targets=(target,),
        plan_designer=Planner(),
        plan_provider_name="glm",
        plan_model_name="glm-5.1",
        planning_executor=executor,
    )
    service = application.state.planning_operation_service
    recover_calls = []
    shutdown_calls = []
    original_recover = service.recover_interrupted
    original_shutdown = service.shutdown

    def recover():
        recover_calls.append(True)
        return original_recover()

    def shutdown(*, wait=False, cancel_futures=True):
        shutdown_calls.append((wait, cancel_futures))
        return original_shutdown(wait=wait, cancel_futures=cancel_futures)

    service.recover_interrupted = recover
    service.shutdown = shutdown
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        assert recover_calls == [True]

    assert shutdown_calls == [(False, True)]


def test_synchronous_planning_endpoint_is_marked_deprecated() -> None:
    schema = TestClient(create_app(execution_targets=())).get("/openapi.json").json()

    operation = schema["paths"]["/api/experiment-plans"]["post"]
    assert operation["deprecated"] is True
    assert "compatibility" in operation["description"]
