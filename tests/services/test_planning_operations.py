from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, Event

import pytest
from sqlalchemy import select

from fluid_scientist.adapters.sql_repository import ConcurrentUpdateError, SQLWorkflowRepository
from fluid_scientist.db import ExperimentPlanRow
from fluid_scientist.experiment_planning.models import ExperimentPlan
from fluid_scientist.experiment_planning.providers import (
    ExperimentDesigner,
    ProviderAuthenticationError,
    ProviderMalformedOutputError,
    ProviderModelNotFoundError,
    ProviderRequestError,
    ProviderSchemaError,
)
from fluid_scientist.operations import OperationKind, OperationRecord, OperationState
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


def named_plan(name: str) -> ExperimentPlan:
    payload = valid_plan().model_dump()
    payload["experiment_name"] = name
    return ExperimentPlan.model_validate(payload)


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


class ShutdownRecordingExecutor(ControlledExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.shutdown_calls: list[tuple[bool, bool]] = []

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append((wait, cancel_futures))


class TrackingThreadExecutor:
    def __init__(self, max_workers: int = 2) -> None:
        self.pool = ThreadPoolExecutor(max_workers=max_workers)
        self.futures: list[Future[object]] = []

    def submit(self, fn: object, /, *args: object, **kwargs: object) -> Future[object]:
        assert callable(fn)
        future = self.pool.submit(fn, *args, **kwargs)
        self.futures.append(future)
        return future

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True, cancel_futures=True)


class ConflictRepository(SQLWorkflowRepository):
    def __init__(self, conflicts: int) -> None:
        super().__init__("sqlite://")
        self.conflicts = conflicts
        self.update_attempts = 0

    def update_operation(self, record, *, expected_version: int) -> StoredOperation:
        self.update_attempts += 1
        if self.conflicts:
            self.conflicts -= 1
            raise ConcurrentUpdateError("synthetic optimistic conflict secret")
        return super().update_operation(record, expected_version=expected_version)


class ConcurrentRequeueRepository(SQLWorkflowRepository):
    def __init__(self, database_url: str) -> None:
        super().__init__(database_url)
        self.requeue_barrier = Barrier(2, timeout=2)
        self.block_requeues = False

    def load_operation(self, operation_id: str) -> StoredOperation | None:
        stored = super().load_operation(operation_id)
        if (
            self.block_requeues
            and stored is not None
            and stored.record.state in {OperationState.FAILED, OperationState.CANCELLED}
        ):
            self.requeue_barrier.wait()
        return stored


class BlockingCompletionRepository(SQLWorkflowRepository):
    def __init__(self, database_url: str) -> None:
        super().__init__(database_url)
        self.completion_entered = Event()
        self.release_completion = Event()

    def complete_planning_operation(self, plan, record, *, expected_version: int):
        self.completion_entered.set()
        assert self.release_completion.wait(timeout=5)
        return super().complete_planning_operation(plan, record, expected_version=expected_version)


class RecordingRepository(SQLWorkflowRepository):
    def __init__(self) -> None:
        super().__init__("sqlite://")
        self.transitions: list[tuple[str, str, str]] = []

    def update_operation(self, record, *, expected_version: int) -> StoredOperation:
        stored = super().update_operation(record, expected_version=expected_version)
        self.transitions.append((record.state.value, record.stage.value, record.message))
        return stored

    def complete_planning_operation(self, plan, record, *, expected_version: int):
        stored = super().complete_planning_operation(
            plan, record, expected_version=expected_version
        )
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


def test_idempotency_digest_includes_canonical_capability_profile() -> None:
    executor = ControlledExecutor()
    designer = FakeDesigner(valid_plan())
    service = PlanningOperationService(repository(), executor=executor)
    common = {
        "project_id": "project-1",
        "question": "test pipe",
        "provider": "glm",
        "model": "glm-5.1",
        "designer": designer,
    }

    workstation = service.submit(
        **common,
        capabilities=("laminar_pipe", "workstation_openfoam", "target-a"),
    )
    same_profile_reordered = service.submit(
        **common,
        capabilities=("target-a", "laminar_pipe", "workstation_openfoam"),
    )
    hpc = service.submit(
        **common,
        capabilities=("laminar_pipe", "hpc_slurm", "target-b"),
    )

    assert same_profile_reordered.operation_id == workstation.operation_id
    assert hpc.operation_id != workstation.operation_id
    assert len(executor.pending) == 2


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
    assert retried.attempt == first.attempt + 1
    assert len(executor.pending) == 1


def test_cancel_immediate_resubmit_isolated_by_persisted_attempt(tmp_path) -> None:
    repo = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'attempts.db'}")
    repo.save_snapshot("project-1", "{}", expected_version=0)
    executor = TrackingThreadExecutor()
    service = PlanningOperationService(repo, executor=executor)
    old_started = Event()
    release_old = Event()

    class BlockingOldDesigner:
        def design_experiment(self, question, *, capabilities, progress=None):
            old_started.set()
            assert release_old.wait(timeout=5)
            return named_plan("stale attempt")

    try:
        first = submit(service, BlockingOldDesigner())
        assert old_started.wait(timeout=5)
        cancelled = service.cancel(first.operation_id)
        retried = submit(service, FakeDesigner(named_plan("winning attempt")))

        assert cancelled.attempt == 1
        assert retried.attempt == 2
        assert len(executor.futures) == 2
        executor.futures[1].result(timeout=5)
        release_old.set()
        executor.futures[0].result(timeout=5)

        final = service.get(first.operation_id)
        assert final.state is OperationState.SUCCEEDED
        assert final.attempt == 2
        stored = repo.load_experiment_plan(final.result_ref)
        assert stored is not None
        assert ExperimentPlan.model_validate_json(stored.plan_json) == named_plan("winning attempt")
        with repo._sessions() as session:
            plans = session.scalars(select(ExperimentPlanRow)).all()
        assert len(plans) == 1
    finally:
        release_old.set()
        executor.shutdown()


def test_cross_service_cancel_wins_before_atomic_plan_persistence(tmp_path) -> None:
    repo = BlockingCompletionRepository(f"sqlite:///{tmp_path / 'cancel-store.db'}")
    repo.save_snapshot("project-1", "{}", expected_version=0)
    executor = TrackingThreadExecutor(max_workers=1)
    worker_service = PlanningOperationService(repo, executor=executor)
    cancelling_service = PlanningOperationService(repo, executor=ControlledExecutor())

    try:
        operation = submit(worker_service, FakeDesigner(named_plan("must not persist")))
        assert repo.completion_entered.wait(timeout=5)
        cancelled = cancelling_service.cancel(operation.operation_id)
        repo.release_completion.set()
        executor.futures[0].result(timeout=5)

        assert cancelled.state is OperationState.CANCELLED
        assert worker_service.get(operation.operation_id).state is OperationState.CANCELLED
        with repo._sessions() as session:
            plans = session.scalars(select(ExperimentPlanRow)).all()
        assert plans == []
    finally:
        repo.release_completion.set()
        executor.shutdown()


def test_designer_identity_mismatch_is_rejected_before_operation_creation() -> None:
    class MiswiredDesigner(FakeDesigner):
        provider_name = "deepseek"
        model_name = "deepseek-chat"

    repo = repository()
    service = PlanningOperationService(repo, executor=ControlledExecutor())

    with pytest.raises(ValueError, match="designer provider"):
        submit(service, MiswiredDesigner(valid_plan()))

    assert repo.list_interrupted_operations() == ()


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


def test_recover_interrupted_skips_case_generation_operations() -> None:
    repo = repository()
    service = PlanningOperationService(repo, executor=ControlledExecutor())
    plan = submit(service, FakeDesigner(valid_plan()))
    case = OperationRecord.new(
        operation_id="case-generation-1",
        kind=OperationKind.CASE_GENERATION,
        project_id="project-1",
        input_digest="sha256:" + "a" * 64,
    )
    repo.create_operation(case)

    recovered = service.recover_interrupted()

    assert [record.operation_id for record in recovered] == [plan.operation_id]
    unchanged = repo.load_operation(case.operation_id)
    assert unchanged is not None
    assert unchanged.record == case


def test_two_services_atomically_claim_one_terminal_requeue(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'operations.db').as_posix()}"
    repo = ConcurrentRequeueRepository(database_url)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    first_executor = ControlledExecutor()
    first_service = PlanningOperationService(repo, executor=first_executor)
    initial = submit(first_service, FakeDesigner(RuntimeError("fail once")))
    first_executor.run_next()
    assert first_service.get(initial.operation_id).state is OperationState.FAILED

    executor_a = ControlledExecutor()
    executor_b = ControlledExecutor()
    service_a = PlanningOperationService(repo, executor=executor_a)
    service_b = PlanningOperationService(repo, executor=executor_b)
    repo.block_requeues = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        result_a = pool.submit(submit, service_a, FakeDesigner(valid_plan()))
        result_b = pool.submit(submit, service_b, FakeDesigner(valid_plan()))
        records = (result_a.result(), result_b.result())

    assert {record.operation_id for record in records} == {initial.operation_id}
    assert sum((len(executor_a.pending), len(executor_b.pending))) == 1


def test_optimistic_transition_conflicts_are_retried() -> None:
    repo = ConflictRepository(conflicts=0)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    service = PlanningOperationService(repo, executor=ControlledExecutor())
    operation = submit(service, FakeDesigner(valid_plan()))
    repo.conflicts = 2

    cancelled = service.cancel(operation.operation_id)

    assert cancelled.state is OperationState.CANCELLED
    assert repo.update_attempts == 3


def test_optimistic_transition_conflict_exhaustion_is_bounded() -> None:
    repo = ConflictRepository(conflicts=0)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    service = PlanningOperationService(repo, executor=ControlledExecutor())
    operation = submit(service, FakeDesigner(valid_plan()))
    repo.conflicts = 99
    baseline = repo.update_attempts

    with pytest.raises(ConcurrentUpdateError, match="bounded retries"):
        service.cancel(operation.operation_id)

    assert repo.update_attempts - baseline == 5


def test_shutdown_only_closes_owned_executor_with_safe_defaults() -> None:
    owned = ShutdownRecordingExecutor()
    owned_service = PlanningOperationService(repository(), executor_factory=lambda: owned)
    injected = ShutdownRecordingExecutor()
    injected_service = PlanningOperationService(repository(), executor=injected)

    owned_service.shutdown()
    injected_service.shutdown()

    assert owned.shutdown_calls == [(False, True)]
    assert injected.shutdown_calls == []


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
