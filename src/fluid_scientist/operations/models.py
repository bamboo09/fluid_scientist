"""Persisted contracts for asynchronous operations."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from fluid_scientist.compat import UTC, StrEnum


class OperationKind(StrEnum):
    PLAN = "plan"
    CASE_GENERATION = "case_generation"


class OperationState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationStage(StrEnum):
    QUEUED = "queued"
    MODEL_PLANNING = "model_planning"
    SCHEMA_CORRECTION = "schema_correction"
    STORING_PLAN = "storing_plan"
    CASE_MODEL = "case_model"
    STATIC_VALIDATION = "static_validation"
    DETERMINISTIC_PACKAGING = "deterministic_packaging"
    READY_FOR_REVIEW = "ready_for_review"
    TARGET_CHECK = "target_check"
    REMOTE_EXECUTION = "remote_execution"
    COMPLETE = "complete"


class OperationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1)
    kind: OperationKind
    project_id: str = Field(min_length=1)
    input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: OperationState = OperationState.QUEUED
    stage: OperationStage = OperationStage.QUEUED
    message: str = "已进入队列"
    result_ref: str | None = None
    safe_error: str | None = None
    cancel_requested: bool = False
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @classmethod
    def new(
        cls,
        operation_id: str,
        kind: OperationKind,
        project_id: str,
        input_digest: str,
    ) -> "OperationRecord":
        now = datetime.now(UTC)
        return cls(
            operation_id=operation_id,
            kind=kind,
            project_id=project_id,
            input_digest=input_digest,
            created_at=now,
            updated_at=now,
        )

    @property
    def terminal(self) -> bool:
        return self.state in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
        }


__all__ = ["OperationKind", "OperationRecord", "OperationStage", "OperationState"]
