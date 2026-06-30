"""SQLAlchemy-backed workflow repository for SQLite and PostgreSQL."""

import json
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fluid_scientist.db import (
    ApprovalRow,
    AuditEventRow,
    Base,
    ExternalJobRow,
    ProjectRow,
    WorkflowSnapshotRow,
)
from fluid_scientist.domain.models import Approval, AuditEvent
from fluid_scientist.ports import StoredWorkflow


class ConcurrentUpdateError(RuntimeError):
    """Raised when a caller tries to overwrite a newer workflow snapshot."""


class ExternalJobConflict(RuntimeError):
    """Raised when a case is rebound to a different external job."""


class SQLWorkflowRepository:
    def __init__(self, database_url: str) -> None:
        engine_options = {}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            engine_options = {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        self._engine = create_engine(database_url, **engine_options)
        Base.metadata.create_all(self._engine)
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def save_snapshot(
        self, project_id: str, snapshot: str, *, expected_version: int
    ) -> int:
        now = datetime.now(UTC).isoformat()
        with self._sessions.begin() as session:
            project = session.get(ProjectRow, project_id)
            row = session.get(WorkflowSnapshotRow, project_id)
            if row is None:
                if expected_version != 0:
                    raise ConcurrentUpdateError(
                        f"project {project_id} has no snapshot at version {expected_version}"
                    )
                if project is None:
                    session.add(ProjectRow(project_id=project_id, created_at=now))
                row = WorkflowSnapshotRow(
                    project_id=project_id,
                    version=1,
                    snapshot=snapshot,
                    updated_at=now,
                )
                session.add(row)
                return 1
            if row.version != expected_version:
                raise ConcurrentUpdateError(
                    f"project {project_id} is version {row.version}, expected {expected_version}"
                )
            row.version += 1
            row.snapshot = snapshot
            row.updated_at = now
            return row.version

    def load_snapshot(self, project_id: str) -> StoredWorkflow | None:
        with self._sessions() as session:
            row = session.get(WorkflowSnapshotRow, project_id)
            if row is None:
                return None
            return StoredWorkflow(
                project_id=project_id,
                snapshot=row.snapshot,
                version=row.version,
            )

    def record_approval(self, project_id: str, approval: Approval) -> None:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            session.add(
                ApprovalRow(
                    project_id=project_id,
                    gate=approval.gate,
                    approved_by=approval.approved_by,
                    approved_at=approval.approved_at.isoformat(),
                    subject_version=approval.subject_version,
                )
            )

    def list_approvals(self, project_id: str) -> tuple[Approval, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ApprovalRow)
                .where(ApprovalRow.project_id == project_id)
                .order_by(ApprovalRow.id)
            ).all()
            return tuple(
                Approval(
                    gate=row.gate,
                    approved_by=row.approved_by,
                    approved_at=datetime.fromisoformat(row.approved_at),
                    subject_version=row.subject_version,
                )
                for row in rows
            )

    def bind_external_job(self, project_id: str, case_id: str, job_id: str) -> str:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            row = session.scalar(
                select(ExternalJobRow).where(
                    ExternalJobRow.project_id == project_id,
                    ExternalJobRow.case_id == case_id,
                )
            )
            if row is not None:
                if row.job_id != job_id:
                    raise ExternalJobConflict(
                        f"{case_id} already bound to external job {row.job_id}"
                    )
                return row.job_id
            session.add(ExternalJobRow(project_id=project_id, case_id=case_id, job_id=job_id))
            return job_id

    def list_external_jobs(self, project_id: str) -> dict[str, str]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ExternalJobRow).where(ExternalJobRow.project_id == project_id)
            ).all()
            return {row.case_id: row.job_id for row in rows}

    def append_audit_event(self, project_id: str, event: AuditEvent) -> None:
        with self._sessions.begin() as session:
            self._require_project(session, project_id)
            session.add(
                AuditEventRow(
                    event_id=event.event_id,
                    project_id=project_id,
                    event_type=event.event_type,
                    occurred_at=event.occurred_at.isoformat(),
                    actor=event.actor,
                    payload_json=json.dumps(event.payload, sort_keys=True),
                )
            )

    def list_audit_events(self, project_id: str) -> tuple[AuditEvent, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(AuditEventRow)
                .where(AuditEventRow.project_id == project_id)
                .order_by(AuditEventRow.occurred_at, AuditEventRow.event_id)
            ).all()
            return tuple(
                AuditEvent(
                    event_id=row.event_id,
                    event_type=row.event_type,
                    occurred_at=datetime.fromisoformat(row.occurred_at),
                    actor=row.actor,
                    payload=json.loads(row.payload_json),
                )
                for row in rows
            )

    @staticmethod
    def _require_project(session: Session, project_id: str) -> None:
        if session.get(ProjectRow, project_id) is None:
            raise KeyError(f"project not found: {project_id}")
