from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass

import pytest

from fluid_scientist.adapters.sql_repository import SQLWorkflowRepository
from fluid_scientist.experiment_planning.models import ExperimentPlan
from fluid_scientist.experiment_planning.providers import (
    ExperimentDesigner,
    ProviderAuthenticationError,
    ProviderMalformedOutputError,
    ProviderModelNotFoundError,
    ProviderRequestError,
    ProviderSchemaError,
)
from fluid_scientist.operations import OperationState
from fluid_scientist.ports import StoredOperation
from fluid_scientist.services.planning_operations import PlanningOperationService


def valid_plan() -> ExperimentPlan:
    return ExperimentPlan.model_validate(
        {
            "experiment_type": "laminar_pipe",
            "experiment_name": "Pipe pressure-loss benchmark",
            "objective": "Measure pressure loss in fully developed laminar pipe flow.",
            "rationale": "This case provides an analytical benchmark for verification.",
            "assumptions": ["Steady incompressible Newtonian flow"],
            "limitations": ["Only the laminar range is represented"],
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


class ControlledExecutor:
    def __init__(self, *, fail_submit: bool = False) -> None:
        self.pending: list[tuple[object, tuple[object, ...], dict[str, object]]] = []
        self.fail_submit = fail_submit

    def submit(self, fn: object, /, *args: object, **kwargs: object) -> Future[object]:
        if self.fail_submit:
            raise RuntimeError("executor internal secret")
        self.pending.append((fn, args, kwargs))
        return Future()

    def run_next(self) -> None:
        fn, args, kwargs = self.pending.pop(0)
        assert callable(fn)
        fn(*args, **kwargs)


class RecordingRepository(SQLWorkflowRepository):
    def __init__(self) -> None:
        super().__init__("sqlite://")
        self.transitions: list[tuple[str, str, str]] = []

    def update_operation(self, record, *, expected_version: int) -> StoredOperation:
        stored = super().update_operation(record, expected_version=expected_version)
        self.transitions.append((record.state.value, record.stage.value, record.message))
        return stored


@dataclass
class FakeDesigner:
    outcome: object
    calls: int = 0

    def design_experiment(self, question: str, *, capabilities: tuple[str, ...], progress=None):
        self.calls += 1
        assert question
        assert capabilities == ("laminar_pipe",)
        if progress is not None:
            progress("model_planning")
            progress("schema_correction")
            progress("model_planning")
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def repository() -> SQLWorkflowRepository:
    repo = SQLWorkflowRepository("sqlite://")
    repo.save_snapshot("project-1", "{}", expected_version=0)
    return repo


def submit(
    service: PlanningOperationService,
    designer: ExperimentDesigner,
    question: str = " test  pipe ",
):
    return service.submit(
        project_id="project-1",
        question=question,
        provider="glm",
        model="glm-5.1",
        designer=designer,
        capabilities=("laminar_pipe",),
    )


def test_submit_returns_queued_before_designer_runs_and_duplicate_schedules_once() -> None:
    executor = ControlledExecutor()
    designer = FakeDesigner(valid_plan())
    service = PlanningOperationService(repository(), executor=executor)

    first = submit(service, designer)
    duplicate = submit(service, designer, "test pipe")

    assert first.state is OperationState.QUEUED
    assert first.message == "已进入队列"
    assert duplicate.operation_id == first.operation_id
    assert designer.calls == 0
    assert len(executor.pending) == 1
    assert "test pipe" not in first.model_dump_json()


def test_success_persists_only_accepted_plan_and_progress_sequence() -> None:
    repo = RecordingRepository()
    repo.save_snapshot("project-1", "{}", expected_version=0)
    executor = ControlledExecutor()
    service = PlanningOperationService(repo, executor=executor)
    operation = submit(service, FakeDesigner(valid_plan()))

    executor.run_next()

    complete = service.get(operation.operation_id)
    assert complete.state is OperationState.SUCCEEDED
    assert complete.stage.value == "complete"
    assert complete.message == "实验计划已生成"
    assert complete.result_ref is not None
    stored = repo.load_experiment_plan(complete.result_ref)
    assert stored is not None
    assert stored.plan_id == complete.result_ref
    assert stored.project_id == "project-1"
    assert stored.provider == "glm"
    assert stored.model == "glm-5.1"
    assert stored.version == 1
    assert ExperimentPlan.model_validate_json(stored.plan_json) == valid_plan()
    assert repo.transitions == [
        ("running", "model_planning", "模型正在设计实验"),
        ("running", "model_planning", "模型正在设计实验"),
        ("running", "schema_correction", "正在修正计划结构"),
        ("running", "model_planning", "模型正在设计实验"),
        ("running", "storing_plan", "正在保存实验计划"),
        ("succeeded", "complete", "实验计划已生成"),
    ]


def test_cancel_is_idempotent_and_discards_late_result() -> None:
    repo = repository()
    executor = ControlledExecutor()
    service = PlanningOperationService(repo, executor=executor)
    operation_id: dict[str, str] = {}

    class CancelWhileRunningDesigner:
        def design_experiment(self, question, *, capabilities, progress=None):
            assert progress is not None
            progress("model_planning")
            service.cancel(operation_id["value"])
            return valid_plan()

    operation = submit(service, CancelWhileRunningDesigner())
    operation_id["value"] = operation.operation_id

    executor.run_next()

    cancelled = service.get(operation.operation_id)
    assert service.cancel(operation.operation_id) == cancelled

    final = service.get(operation.operation_id)
    assert final.state is OperationState.CANCELLED
    assert final.cancel_requested is True
    assert final.result_ref is None


@pytest.mark.parametrize("initial_state", ["failed", "cancelled"])
def test_resubmit_terminal_operation_reuses_id_and_schedules_once(initial_state: str) -> None:
    executor = ControlledExecutor()
    service = PlanningOperationService(repository(), executor=executor)
    if initial_state == "failed":
        first = submit(service, FakeDesigner(RuntimeError("boom")))
        executor.run_next()
    else:
        first = submit(service, FakeDesigner(valid_plan()))
        service.cancel(first.operation_id)
        executor.run_next()

    retried = submit(service, FakeDesigner(valid_plan()), "test pipe")

    assert retried.operation_id == first.operation_id
    assert retried.state is OperationState.QUEUED
    assert retried.safe_error is None
    assert retried.cancel_requested is False
    assert len(executor.pending) == 1


def test_recover_interrupted_marks_operations_failed_and_retryable() -> None:
    executor = ControlledExecutor()
    service = PlanningOperationService(repository(), executor=executor)
    operation = submit(service, FakeDesigner(valid_plan()))

    recovered = service.recover_interrupted()

    assert [item.operation_id for item in recovered] == [operation.operation_id]
    final = service.get(operation.operation_id)
    assert final.state is OperationState.FAILED
    assert final.safe_error == "服务重启中断了操作，可安全重试"
    assert final.result_ref is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ProviderAuthenticationError("x", provider="glm", model="m"),
            "模型服务认证失败，请检查 API Key",
        ),
        (
            ProviderModelNotFoundError("x", provider="glm", model="m"),
            "未找到所选模型，请检查模型名称",
        ),
        (
            ProviderSchemaError("secret", provider="glm", model="m"),
            "模型返回的实验计划结构无效，请重试",
        ),
        (
            ProviderMalformedOutputError("secret", provider="glm", model="m"),
            "模型返回的实验计划无法解析，请重试",
        ),
        (
            ProviderRequestError("secret", provider="glm", model="m"),
            "模型服务请求失败，请稍后重试",
        ),
        (RuntimeError("raw question and secret"), "实验设计失败，请重试"),
    ],
)
def test_provider_errors_are_classified_without_persisting_details(
    error: Exception, expected: str
) -> None:
    executor = ControlledExecutor()
    service = PlanningOperationService(repository(), executor=executor)
    operation = submit(service, FakeDesigner(error), question="raw question secret")

    executor.run_next()

    final = service.get(operation.operation_id)
    assert final.state is OperationState.FAILED
    assert final.safe_error == expected
    assert "secret" not in final.model_dump_json()
    assert "raw question" not in final.model_dump_json()


def test_executor_submission_failure_marks_operation_failed_safely() -> None:
    service = PlanningOperationService(repository(), executor=ControlledExecutor(fail_submit=True))

    operation = submit(service, FakeDesigner(valid_plan()))

    assert operation.state is OperationState.FAILED
    assert operation.safe_error == "无法启动实验设计任务，请重试"
    assert "secret" not in operation.model_dump_json()


@pytest.mark.parametrize("field", ["project_id", "question", "provider", "model"])
def test_submit_rejects_blank_canonical_inputs(field: str) -> None:
    values = {
        "project_id": "project-1",
        "question": "question",
        "provider": "glm",
        "model": "glm-5.1",
    }
    values[field] = "  "
    service = PlanningOperationService(repository(), executor=ControlledExecutor())

    with pytest.raises(ValueError, match=field):
        service.submit(
            **values,
            designer=FakeDesigner(valid_plan()),
            capabilities=("laminar_pipe",),
        )


def test_get_and_cancel_unknown_operation_raise_key_error() -> None:
    service = PlanningOperationService(repository(), executor=ControlledExecutor())

    with pytest.raises(KeyError):
        service.get("missing")
    with pytest.raises(KeyError):
        service.cancel("missing")
