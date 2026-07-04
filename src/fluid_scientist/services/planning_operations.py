"""Recoverable asynchronous orchestration for model-backed experiment planning."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Callable, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from fluid_scientist.adapters.sql_repository import ConcurrentUpdateError
from fluid_scientist.compat import UTC
from fluid_scientist.experiment_planning.models import ExperimentPlan
from fluid_scientist.experiment_planning.providers import (
    ExperimentDesigner,
    ProviderAuthenticationError,
    ProviderEmptyOutputError,
    ProviderMalformedOutputError,
    ProviderModelNotFoundError,
    ProviderOutputError,
    ProviderRequestError,
    ProviderSchemaError,
)
from fluid_scientist.operations import (
    OperationKind,
    OperationRecord,
    OperationStage,
    OperationState,
)
from fluid_scientist.ports import StoredExperimentPlan, StoredOperation, WorkflowRepository

_MAX_TRANSITION_ATTEMPTS = 5

_PROGRESS = {
    "model_planning": (OperationStage.MODEL_PLANNING, "模型正在设计实验"),
    "schema_correction": (OperationStage.SCHEMA_CORRECTION, "正在修正计划结构"),
}


class PlanningOperationService:
    """Run experiment design off-request while persisting safe progress."""

    def __init__(
        self,
        repository: WorkflowRepository,
        *,
        executor: Executor | None = None,
        executor_factory: Callable[[], Executor] | None = None,
    ) -> None:
        if executor is not None and executor_factory is not None:
            raise ValueError("executor and executor_factory are mutually exclusive")
        self._repository = repository
        if executor is not None:
            self._executor = executor
        elif executor_factory is not None:
            self._executor = executor_factory()
        else:
            self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fluid-planning")
        self._owns_executor = executor is None
        self._lock = RLock()
        self._scheduled: set[str] = set()

    def submit(
        self,
        *,
        project_id: str,
        question: str,
        provider: str,
        model: str,
        designer: ExperimentDesigner,
        capabilities: tuple[str, ...],
    ) -> OperationRecord:
        project_id = self._required("project_id", project_id)
        question = self._canonical_question(question)
        provider = self._required("provider", provider).lower()
        model = self._required("model", model)
        canonical_capabilities = self._capabilities(capabilities)
        digest = self._digest(question, provider, model)

        with self._lock:
            stored = self._repository.find_operation(OperationKind.PLAN, project_id, digest)
            should_schedule = False
            if stored is None:
                operation_id = str(uuid4())
                created = OperationRecord.new(
                    operation_id=operation_id,
                    kind=OperationKind.PLAN,
                    project_id=project_id,
                    input_digest=digest,
                )
                stored = self._repository.create_operation(created)
                should_schedule = stored.record.operation_id == operation_id
            elif stored.record.state in {
                OperationState.FAILED,
                OperationState.CANCELLED,
            }:
                stored, should_schedule = self._claim_terminal_requeue(
                    stored.record.operation_id,
                )

            if should_schedule and stored.record.operation_id not in self._scheduled:
                self._scheduled.add(stored.record.operation_id)
                try:
                    self._executor.submit(
                        self._run,
                        stored.record.operation_id,
                        question,
                        provider,
                        model,
                        designer,
                        canonical_capabilities,
                    )
                except Exception:
                    self._scheduled.discard(stored.record.operation_id)
                    stored = self._transition(
                        stored.record.operation_id,
                        lambda record: self._failed(record, "无法启动实验设计任务，请重试"),
                    )
            return stored.record

    def get(self, operation_id: str) -> OperationRecord:
        stored = self._repository.load_operation(self._required("operation_id", operation_id))
        if stored is None:
            raise KeyError(operation_id)
        return stored.record

    def cancel(self, operation_id: str) -> OperationRecord:
        operation_id = self._required("operation_id", operation_id)
        with self._lock:
            stored = self._repository.load_operation(operation_id)
            if stored is None:
                raise KeyError(operation_id)
            if stored.record.terminal:
                return stored.record
            return self._transition(
                operation_id,
                lambda record: self._updated(
                    record,
                    state=OperationState.CANCELLED,
                    stage=OperationStage.COMPLETE,
                    message="实验设计已取消",
                    safe_error=None,
                    result_ref=None,
                    cancel_requested=True,
                ),
            ).record

    def recover_interrupted(self) -> tuple[OperationRecord, ...]:
        recovered: list[OperationRecord] = []
        with self._lock:
            for item in self._repository.list_interrupted_operations():
                if item.record.kind is not OperationKind.PLAN:
                    continue
                updated = self._transition(
                    item.record.operation_id,
                    lambda record: self._failed(record, "服务重启中断了操作，可安全重试"),
                )
                recovered.append(updated.record)
        return tuple(recovered)

    def _claim_terminal_requeue(self, operation_id: str) -> tuple[StoredOperation, bool]:
        """Atomically claim the right to schedule a terminal operation retry."""

        retryable_states = {OperationState.FAILED, OperationState.CANCELLED}
        for _attempt in range(_MAX_TRANSITION_ATTEMPTS):
            stored = self._repository.load_operation(operation_id)
            if stored is None:
                raise KeyError(operation_id)
            if stored.record.state not in retryable_states:
                return stored, False
            replacement = self._updated(
                stored.record,
                state=OperationState.QUEUED,
                stage=OperationStage.QUEUED,
                message="已进入队列",
                result_ref=None,
                safe_error=None,
                cancel_requested=False,
            )
            try:
                updated = self._repository.update_operation(
                    replacement, expected_version=stored.version
                )
            except ConcurrentUpdateError:
                continue
            return updated, True
        raise ConcurrentUpdateError(
            f"operation {operation_id} could not be claimed after bounded retries"
        )

    def shutdown(self, *, wait: bool = False, cancel_futures: bool = True) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def _run(
        self,
        operation_id: str,
        question: str,
        provider: str,
        model: str,
        designer: ExperimentDesigner,
        capabilities: tuple[str, ...],
    ) -> None:
        try:
            with self._lock:
                current = self.get(operation_id)
                if current.terminal or current.cancel_requested:
                    return
                self._transition(
                    operation_id,
                    lambda record: self._updated(
                        record,
                        state=OperationState.RUNNING,
                        stage=OperationStage.MODEL_PLANNING,
                        message="模型正在设计实验",
                    ),
                )

            plan = designer.design_experiment(
                question,
                capabilities=capabilities,
                progress=lambda stage: self._record_progress(operation_id, stage),
            )
            if not isinstance(plan, ExperimentPlan):
                raise TypeError("designer returned a non-plan value")

            with self._lock:
                current = self.get(operation_id)
                if current.terminal or current.cancel_requested:
                    return
                self._transition(
                    operation_id,
                    lambda record: self._updated(
                        record,
                        state=OperationState.RUNNING,
                        stage=OperationStage.STORING_PLAN,
                        message="正在保存实验计划",
                    ),
                )
                plan_id = str(uuid4())
                self._repository.store_experiment_plan(
                    StoredExperimentPlan(
                        plan_id=plan_id,
                        project_id=current.project_id,
                        version=1,
                        provider=provider,
                        model=model,
                        plan_json=plan.model_dump_json(),
                    )
                )
                self._transition(
                    operation_id,
                    lambda record: self._updated(
                        record,
                        state=OperationState.SUCCEEDED,
                        stage=OperationStage.COMPLETE,
                        message="实验计划已生成",
                        result_ref=plan_id,
                        safe_error=None,
                    ),
                )
        except Exception as error:
            safe_error = self._safe_error(error)
            with self._lock:
                current = self.get(operation_id)
                if not current.terminal and not current.cancel_requested:
                    self._transition(
                        operation_id,
                        lambda record: self._failed(record, safe_error),
                    )
        finally:
            with self._lock:
                self._scheduled.discard(operation_id)

    def _record_progress(self, operation_id: str, stage: str) -> None:
        progress = _PROGRESS.get(stage)
        if progress is None:
            return
        with self._lock:
            current = self.get(operation_id)
            if current.terminal or current.cancel_requested:
                return
            operation_stage, message = progress
            self._transition(
                operation_id,
                lambda record: self._updated(
                    record,
                    state=OperationState.RUNNING,
                    stage=operation_stage,
                    message=message,
                ),
            )

    def _transition(
        self,
        operation_id: str,
        change: Callable[[OperationRecord], OperationRecord],
        *,
        allowed_terminal_states: set[OperationState] | None = None,
    ) -> StoredOperation:
        for _attempt in range(_MAX_TRANSITION_ATTEMPTS):
            stored = self._repository.load_operation(operation_id)
            if stored is None:
                raise KeyError(operation_id)
            if stored.record.terminal and (
                allowed_terminal_states is None
                or stored.record.state not in allowed_terminal_states
            ):
                return stored
            replacement = change(stored.record)
            try:
                return self._repository.update_operation(
                    replacement, expected_version=stored.version
                )
            except ConcurrentUpdateError:
                continue
        raise ConcurrentUpdateError(
            f"operation {operation_id} could not be updated after bounded retries"
        )

    @staticmethod
    def _updated(record: OperationRecord, **updates: object) -> OperationRecord:
        now = datetime.now(UTC)
        if now <= record.updated_at:
            now = record.updated_at + timedelta(microseconds=1)
        return record.model_copy(update={**updates, "updated_at": now})

    @classmethod
    def _failed(cls, record: OperationRecord, safe_error: str) -> OperationRecord:
        return cls._updated(
            record,
            state=OperationState.FAILED,
            stage=OperationStage.COMPLETE,
            message="实验设计未完成",
            result_ref=None,
            safe_error=safe_error,
        )

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, ProviderAuthenticationError):
            return "模型服务认证失败，请检查 API Key"
        if isinstance(error, ProviderModelNotFoundError):
            return "未找到所选模型，请检查模型名称"
        if isinstance(error, ProviderSchemaError):
            return "模型返回的实验计划结构无效，请重试"
        if isinstance(error, (ProviderMalformedOutputError, ProviderEmptyOutputError)):
            return "模型返回的实验计划无法解析，请重试"
        if isinstance(error, ProviderRequestError):
            return "模型服务请求失败，请稍后重试"
        if isinstance(error, ProviderOutputError):
            return "模型返回的实验计划无法接受，请重试"
        return "实验设计失败，请重试"

    @staticmethod
    def _required(field: str, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must not be blank")
        return unicodedata.normalize("NFKC", value.strip())

    @classmethod
    def _canonical_question(cls, question: str) -> str:
        normalized = cls._required("question", question)
        return " ".join(normalized.split())

    @classmethod
    def _capabilities(cls, capabilities: Sequence[str]) -> tuple[str, ...]:
        if not capabilities:
            raise ValueError("capabilities must not be empty")
        normalized = tuple(cls._required("capabilities", item) for item in capabilities)
        if len(set(normalized)) != len(normalized):
            raise ValueError("capabilities must be unique")
        return normalized

    @staticmethod
    def _digest(question: str, provider: str, model: str) -> str:
        payload = json.dumps(
            {"question": question, "provider": provider, "model": model},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(payload).hexdigest()


__all__ = ["PlanningOperationService"]
