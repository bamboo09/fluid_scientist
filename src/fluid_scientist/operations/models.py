"""Persisted contracts for asynchronous operations."""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from fluid_scientist.compat import UTC, StrEnum

NonEmptyIdentifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation_id: NonEmptyIdentifier
    kind: OperationKind
    project_id: NonEmptyIdentifier
    input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: OperationState = OperationState.QUEUED
    stage: OperationStage = OperationStage.QUEUED
    message: str = "已进入队列"
    result_ref: str | None = None
    safe_error: str | None = None
    cancel_requested: bool = False
    attempt: int = Field(default=1, ge=1)
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("operation timestamps must use UTC")
        return value

    @model_validator(mode="after")
    def require_chronological_timestamps(self) -> "OperationRecord":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be before created_at")
        return self

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
