import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from fluid_scientist.adapters.sql_repository import (
    ConcurrentUpdateError,
    ExternalJobConflict,
    OperationConflict,
    SQLWorkflowRepository,
)
from fluid_scientist.domain.models import Approval, AuditEvent
from fluid_scientist.operations.models import (
    OperationKind,
    OperationRecord,
    OperationStage,
    OperationState,
)


def repository(tmp_path) -> SQLWorkflowRepository:
    return SQLWorkflowRepository(f"sqlite:///{tmp_path / 'workflow.db'}")


def operation(
    operation_id: str = "operation-1",
    *,
    kind: OperationKind = OperationKind.PLAN,
    project_id: str = "project-1",
    input_digest: str = f"sha256:{'a' * 64}",
    state: OperationState = OperationState.QUEUED,
    created_at: datetime = datetime(2026, 7, 1, tzinfo=UTC),
) -> OperationRecord:
    return OperationRecord(
        operation_id=operation_id,
        kind=kind,
        project_id=project_id,
        input_digest=input_digest,
        state=state,
        stage=OperationStage.QUEUED,
        created_at=created_at,
        updated_at=created_at,
    )


def test_snapshot_save_load_and_optimistic_version(tmp_path) -> None:
    repo = repository(tmp_path)

    version = repo.save_snapshot("project-1", '{"name":"CREATED"}', expected_version=0)
    stored = repo.load_snapshot("project-1")

    assert version == 1
    assert stored.snapshot == '{"name":"CREATED"}'
    assert stored.version == 1

    assert repo.save_snapshot("project-1", '{"name":"SPEC_READY"}', expected_version=1) == 2
    with pytest.raises(ConcurrentUpdateError):
        repo.save_snapshot("project-1", '{"name":"STALE"}', expected_version=1)


def test_approval_and_audit_survive_repository_recreation(tmp_path) -> None:
    db_url = f"sqlite:///{tmp_path / 'workflow.db'}"
    repo = SQLWorkflowRepository(db_url)
    repo.save_snapshot("project-1", '{"name":"SPEC_READY"}', expected_version=0)
    approval = Approval(
        gate="GATE_1",
        approved_by="researcher",
        approved_at=datetime(2026, 6, 30, tzinfo=UTC),
        subject_version=1,
    )
    audit = AuditEvent(
        event_id="event-1",
        event_type="APPROVAL_GRANTED",
        occurred_at=datetime(2026, 6, 30, tzinfo=UTC),
        actor="researcher",
        payload={"gate": "GATE_1"},
    )
    repo.record_approval("project-1", approval)
    repo.append_audit_event("project-1", audit)

    reopened = SQLWorkflowRepository(db_url)

    assert reopened.list_approvals("project-1") == (approval,)
    assert reopened.list_audit_events("project-1") == (audit,)


def test_external_job_binding_is_idempotent_and_rejects_mismatch(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{"name":"PILOT_RUNNING"}', expected_version=0)

    assert repo.bind_external_job("project-1", "case-1", "123") == "123"
    assert repo.bind_external_job("project-1", "case-1", "123") == "123"
    assert repo.list_external_jobs("project-1") == {"case-1": "123"}

    with pytest.raises(ExternalJobConflict, match="already bound"):
        repo.bind_external_job("project-1", "case-1", "456")


def test_operation_create_and_load_round_trip(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{}', expected_version=0)
    record = operation()

    stored = repo.create_operation(record)

    assert stored.record == record
    assert stored.version == 1
    assert repo.load_operation(record.operation_id) == stored


def test_operation_create_requires_existing_project(tmp_path) -> None:
    repo = repository(tmp_path)

    with pytest.raises(KeyError, match="project not found"):
        repo.create_operation(operation())


def test_operation_create_is_idempotent_by_request_identity(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{}', expected_version=0)
    original = operation()
    duplicate_request = operation("operation-2")

    first = repo.create_operation(original)
    duplicate = repo.create_operation(duplicate_request)

    assert duplicate == first
    assert repo.load_operation("operation-2") is None


def test_operation_id_collision_rejects_different_content(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{}', expected_version=0)
    repo.create_operation(operation())
    changed = operation(project_id="missing-project")

    with pytest.raises(OperationConflict, match="operation-1"):
        repo.create_operation(changed)


def test_operation_update_increments_version_and_rejects_stale_write(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{}', expected_version=0)
    original = operation()
    repo.create_operation(original)
    updated_record = original.model_copy(
        update={
            "state": OperationState.RUNNING,
            "stage": OperationStage.MODEL_PLANNING,
            "updated_at": original.updated_at + timedelta(seconds=1),
        }
    )

    updated = repo.update_operation(updated_record, expected_version=1)

    assert updated.record == updated_record
    assert updated.version == 2
    with pytest.raises(ConcurrentUpdateError, match="expected 1"):
        repo.update_operation(updated_record, expected_version=1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", OperationKind.CASE_GENERATION),
        ("project_id", "project-2"),
        ("input_digest", f"sha256:{'b' * 64}"),
    ],
)
def test_operation_update_rejects_identity_changes(tmp_path, field, value) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{}', expected_version=0)
    repo.save_snapshot("project-2", '{}', expected_version=0)
    original = operation()
    repo.create_operation(original)
    changed = original.model_copy(update={field: value})

    with pytest.raises(OperationConflict, match="identity"):
        repo.update_operation(changed, expected_version=1)


def test_find_operation_returns_exact_match_or_none(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{}', expected_version=0)
    record = operation()
    stored = repo.create_operation(record)

    assert repo.find_operation(record.kind, record.project_id, record.input_digest) == stored
    assert repo.find_operation(record.kind, record.project_id, f"sha256:{'b' * 64}") is None


def test_list_interrupted_operations_filters_and_orders_records(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{}', expected_version=0)
    later = datetime(2026, 7, 2, tzinfo=UTC)
    earlier = datetime(2026, 7, 1, tzinfo=UTC)
    records = [
        operation("z-running", state=OperationState.RUNNING, created_at=earlier),
        operation(
            "terminal",
            input_digest=f"sha256:{'b' * 64}",
            state=OperationState.SUCCEEDED,
            created_at=earlier,
        ),
        operation(
            "later-queued",
            input_digest=f"sha256:{'c' * 64}",
            created_at=later,
        ),
        operation(
            "a-queued",
            input_digest=f"sha256:{'d' * 64}",
            created_at=earlier,
        ),
    ]
    for record in records:
        repo.create_operation(record)

    interrupted = repo.list_interrupted_operations()

    assert [item.record.operation_id for item in interrupted] == [
        "a-queued",
        "z-running",
        "later-queued",
    ]


def test_load_operation_strictly_validates_persisted_json(tmp_path) -> None:
    database_path = tmp_path / "workflow.db"
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{}', expected_version=0)
    repo.create_operation(operation())
    with sqlite3.connect(database_path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT record_json FROM operations WHERE operation_id = ?",
                ("operation-1",),
            ).fetchone()[0]
        )
        payload["unexpected"] = True
        connection.execute(
            "UPDATE operations SET record_json = ? WHERE operation_id = ?",
            (json.dumps(payload), "operation-1"),
        )

    with pytest.raises(ValidationError, match="unexpected"):
        repo.load_operation("operation-1")
