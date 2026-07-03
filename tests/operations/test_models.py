from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fluid_scientist.operations import (
    OperationKind,
    OperationRecord,
    OperationStage,
    OperationState,
)

VALID_DIGEST = "sha256:" + "a" * 64


def make_record(**updates: object) -> OperationRecord:
    values = {
        "operation_id": "operation-1",
        "kind": OperationKind.PLAN,
        "project_id": "project-1",
        "input_digest": VALID_DIGEST,
        "created_at": datetime(2026, 7, 4, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 4, tzinfo=UTC),
    }
    return OperationRecord(**(values | updates))


def test_operation_enums_expose_persisted_values() -> None:
    assert {kind.value for kind in OperationKind} == {"plan", "case_generation"}
    assert {state.value for state in OperationState} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }
    assert [stage.value for stage in OperationStage] == [
        "queued",
        "model_planning",
        "schema_correction",
        "storing_plan",
        "case_model",
        "static_validation",
        "deterministic_packaging",
        "ready_for_review",
        "target_check",
        "remote_execution",
        "complete",
    ]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (OperationState.QUEUED, False),
        (OperationState.RUNNING, False),
        (OperationState.SUCCEEDED, True),
        (OperationState.FAILED, True),
        (OperationState.CANCELLED, True),
    ],
)
def test_terminal_reflects_only_finished_states(
    state: OperationState, expected: bool
) -> None:
    assert make_record(state=state).terminal is expected


def test_operation_record_excludes_sensitive_and_raw_input_fields() -> None:
    assert {"api_key", "provider_payload", "question"}.isdisjoint(
        OperationRecord.model_fields
    )


def test_operation_record_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        make_record(api_key="secret")


@pytest.mark.parametrize(
    "digest",
    [
        "a" * 64,
        "sha256:" + "A" * 64,
        "sha256:" + "a" * 63,
        "sha512:" + "a" * 64,
    ],
)
def test_operation_record_rejects_invalid_input_digest(digest: str) -> None:
    with pytest.raises(ValidationError):
        make_record(input_digest=digest)


def test_new_operation_has_safe_defaults_and_utc_timestamps() -> None:
    record = OperationRecord.new(
        operation_id="operation-1",
        kind=OperationKind.CASE_GENERATION,
        project_id="project-1",
        input_digest=VALID_DIGEST,
    )

    assert record.state is OperationState.QUEUED
    assert record.stage is OperationStage.QUEUED
    assert record.message == "已进入队列"
    assert record.result_ref is None
    assert record.safe_error is None
    assert record.cancel_requested is False
    assert record.created_at.tzinfo is UTC
    assert record.updated_at.tzinfo is UTC
    assert record.created_at == record.updated_at


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_operation_record_rejects_naive_timestamps(field: str) -> None:
    with pytest.raises(ValidationError, match="timezone info"):
        make_record(**{field: datetime(2026, 7, 4)})


@pytest.mark.parametrize("field", ["operation_id", "project_id"])
def test_operation_record_rejects_empty_identifiers(field: str) -> None:
    with pytest.raises(ValidationError):
        make_record(**{field: ""})


def test_operation_record_is_frozen() -> None:
    record = make_record()

    with pytest.raises(ValidationError, match="frozen"):
        record.message = "changed"  # type: ignore[misc]
