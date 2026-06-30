from datetime import UTC, datetime

import pytest

from fluid_scientist.adapters.sql_repository import (
    ConcurrentUpdateError,
    ExternalJobConflict,
    SQLWorkflowRepository,
)
from fluid_scientist.domain.models import Approval, AuditEvent


def repository(tmp_path) -> SQLWorkflowRepository:
    return SQLWorkflowRepository(f"sqlite:///{tmp_path / 'workflow.db'}")


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
