from datetime import UTC, datetime, timedelta, timezone

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
    assert [(name, item.value) for name, item in OperationKind.__members__.items()] == [
        ("PLAN", "plan"),
        ("CASE_GENERATION", "case_generation"),
    ]
    assert [(name, item.value) for name, item in OperationState.__members__.items()] == [
        ("QUEUED", "queued"),
        ("RUNNING", "running"),
        ("SUCCEEDED", "succeeded"),
        ("FAILED", "failed"),
        ("CANCELLED", "cancelled"),
    ]
    assert [(name, item.value) for name, item in OperationStage.__members__.items()] == [
        ("QUEUED", "queued"),
        ("MODEL_PLANNING", "model_planning"),
        ("SCHEMA_CORRECTION", "schema_correction"),
        ("STORING_PLAN", "storing_plan"),
        ("CASE_MODEL", "case_model"),
        ("STATIC_VALIDATION", "static_validation"),
        ("DETERMINISTIC_PACKAGING", "deterministic_packaging"),
        ("READY_FOR_REVIEW", "ready_for_review"),
        ("TARGET_CHECK", "target_check"),
        ("REMOTE_EXECUTION", "remote_execution"),
        ("COMPLETE", "complete"),
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
def test_terminal_reflects_only_finished_states(state: OperationState, expected: bool) -> None:
    assert make_record(state=state).terminal is expected


def test_operation_record_excludes_sensitive_and_raw_input_fields() -> None:
    assert {"api_key", "provider_payload", "question"}.isdisjoint(OperationRecord.model_fields)


def test_operation_record_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        make_record(api_key="secret")


@pytest.mark.parametrize(
    ("field", "coercive_value"),
    [
        ("cancel_requested", 1),
        ("kind", "plan"),
        ("state", "queued"),
        ("stage", "queued"),
        ("created_at", "2026-07-04T00:00:00Z"),
        ("updated_at", "2026-07-04T00:00:00Z"),
    ],
)
def test_python_validation_rejects_coercive_inputs(field: str, coercive_value: object) -> None:
    with pytest.raises(ValidationError):
        make_record(**{field: coercive_value})


def test_json_round_trip_preserves_strict_operation_record() -> None:
    record = make_record()

    restored = OperationRecord.model_validate_json(record.model_dump_json())

    assert restored == record


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
    assert "�" not in record.message
    assert record.result_ref is None
    assert record.safe_error is None
    assert record.cancel_requested is False
    assert record.attempt == 1
    assert record.created_at.tzinfo is UTC
    assert record.updated_at.tzinfo is UTC
    assert record.created_at == record.updated_at


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_operation_record_rejects_naive_timestamps(field: str) -> None:
    with pytest.raises(ValidationError, match="timezone info"):
        make_record(**{field: datetime(2026, 7, 4)})


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_operation_record_rejects_non_utc_timestamps(field: str) -> None:
    non_utc = timezone(timedelta(hours=8))

    with pytest.raises(ValidationError, match="UTC"):
        make_record(**{field: datetime(2026, 7, 4, tzinfo=non_utc)})


def test_operation_record_rejects_updated_at_before_created_at() -> None:
    with pytest.raises(ValidationError, match="updated_at"):
        make_record(
            created_at=datetime(2026, 7, 5, tzinfo=UTC),
            updated_at=datetime(2026, 7, 4, tzinfo=UTC),
        )


@pytest.mark.parametrize("field", ["operation_id", "project_id"])
@pytest.mark.parametrize("value", ["", " ", "\t\n"])
def test_operation_record_rejects_blank_identifiers(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        make_record(**{field: value})


def test_operation_record_is_frozen() -> None:
    record = make_record()

    with pytest.raises(ValidationError, match="frozen"):
        record.message = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("attempt", [0, -1, True, "1", 1.5])
def test_operation_attempt_is_a_strict_positive_integer(attempt: object) -> None:
    with pytest.raises(ValidationError, match="attempt"):
        make_record(attempt=attempt)
