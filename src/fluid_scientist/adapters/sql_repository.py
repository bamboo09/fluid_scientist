"""SQLAlchemy-backed workflow repository for SQLite and PostgreSQL."""

import json
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fluid_scientist.compat import UTC
from fluid_scientist.db import (
    ApprovalRow,
    AuditEventRow,
    Base,
    CompiledExperimentRow,
    ExperimentPlanRow,
    ExternalJobRow,
    OperationRow,
    ProjectRow,
    WorkflowSnapshotRow,
)
from fluid_scientist.domain.models import Approval, AuditEvent
from fluid_scientist.operations.models import OperationKind, OperationRecord, OperationState
from fluid_scientist.ports import (
    StoredCompiledExperiment,
    StoredExperimentPlan,
    StoredOperation,
    StoredWorkflow,
)


class ConcurrentUpdateError(RuntimeError):
    """Raised when a caller tries to overwrite a newer workflow snapshot."""


class ExternalJobConflict(RuntimeError):
    """Raised when a case is rebound to a different external job."""


class ExperimentArtifactConflict(RuntimeError):
    """Raised when immutable plan or compiled bytes are replaced."""


class OperationConflict(RuntimeError):
    """Raised when an operation's immutable identity conflicts."""


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

    def latest_project_id(self) -> str | None:
        with self._sessions() as session:
            project_id = session.scalar(
                select(WorkflowSnapshotRow.project_id)
                .order_by(WorkflowSnapshotRow.updated_at.desc())
                .limit(1)
            )
            return project_id

    def create_operation(self, record: OperationRecord) -> StoredOperation:
        with self._sessions.begin() as session:
            operation_row = session.get(OperationRow, record.operation_id)
            if operation_row is not None:
                existing = self._stored_operation(operation_row)
                if existing.record != record:
                    raise OperationConflict(
                        f"operation {record.operation_id} already exists with different content"
                    )
                return existing

            self._require_project(session, record.project_id)
            request_row = session.scalar(
                select(OperationRow).where(
                    OperationRow.kind == record.kind.value,
                    OperationRow.project_id == record.project_id,
                    OperationRow.input_digest == record.input_digest,
                )
            )
            if request_row is not None:
                return self._stored_operation(request_row)

            session.add(
                OperationRow(
                    operation_id=record.operation_id,
                    kind=record.kind.value,
                    project_id=record.project_id,
                    input_digest=record.input_digest,
                    version=1,
                    record_json=record.model_dump_json(),
                    created_at=record.created_at.isoformat(),
                    updated_at=record.updated_at.isoformat(),
                )
            )
            return StoredOperation(record=record, version=1)

    def load_operation(self, operation_id: str) -> StoredOperation | None:
        with self._sessions() as session:
            row = session.get(OperationRow, operation_id)
            return None if row is None else self._stored_operation(row)

    def find_operation(
        self, kind: OperationKind, project_id: str, input_digest: str
    ) -> StoredOperation | None:
        with self._sessions() as session:
            row = session.scalar(
                select(OperationRow).where(
                    OperationRow.kind == kind.value,
                    OperationRow.project_id == project_id,
                    OperationRow.input_digest == input_digest,
                )
            )
            return None if row is None else self._stored_operation(row)

    def update_operation(
        self, record: OperationRecord, expected_version: int
    ) -> StoredOperation:
        with self._sessions.begin() as session:
            row = session.get(OperationRow, record.operation_id)
            if row is None:
                raise ConcurrentUpdateError(
                    f"operation {record.operation_id} does not exist at expected version "
                    f"{expected_version}"
                )
            if row.version != expected_version:
                raise ConcurrentUpdateError(
                    f"operation {record.operation_id} is version {row.version}, "
                    f"expected {expected_version}"
                )
            if (
                row.kind != record.kind.value
                or row.project_id != record.project_id
                or row.input_digest != record.input_digest
            ):
                raise OperationConflict(
                    f"operation {record.operation_id} identity fields are immutable"
                )
            row.version += 1
            row.record_json = record.model_dump_json()
            row.updated_at = record.updated_at.isoformat()
            return StoredOperation(record=record, version=row.version)

    def list_interrupted_operations(self) -> tuple[StoredOperation, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(OperationRow).order_by(
                    OperationRow.created_at, OperationRow.operation_id
                )
            ).all()
            stored = (self._stored_operation(row) for row in rows)
            return tuple(
                item
                for item in stored
                if item.record.state in {OperationState.QUEUED, OperationState.RUNNING}
            )

    def store_experiment_plan(self, plan: StoredExperimentPlan) -> StoredExperimentPlan:
        with self._sessions.begin() as session:
            if plan.project_id is not None:
                self._require_project(session, plan.project_id)
            row = session.get(ExperimentPlanRow, plan.plan_id)
            if row is not None:
                existing = self._stored_plan(row)
                if existing != plan:
                    raise ExperimentArtifactConflict(
                        f"plan {plan.plan_id} is immutable and already exists"
                    )
                return existing
            session.add(
                ExperimentPlanRow(
                    plan_id=plan.plan_id,
                    project_id=plan.project_id,
                    version=plan.version,
                    provider=plan.provider,
                    model=plan.model,
                    plan_json=plan.plan_json,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
            return plan

    def load_experiment_plan(self, plan_id: str) -> StoredExperimentPlan | None:
        with self._sessions() as session:
            row = session.get(ExperimentPlanRow, plan_id)
            return None if row is None else self._stored_plan(row)

    def store_compiled_experiment(
        self, compiled: StoredCompiledExperiment
    ) -> StoredCompiledExperiment:
        with self._sessions.begin() as session:
            row = session.scalar(
                select(CompiledExperimentRow).where(
                    CompiledExperimentRow.plan_id == compiled.plan_id,
                    CompiledExperimentRow.plan_version == compiled.plan_version,
                )
            )
            if row is not None:
                existing = self._stored_compiled(row)
                if existing != compiled:
                    raise ExperimentArtifactConflict(
                        "compiled plan "
                        f"{compiled.plan_id} version {compiled.plan_version} is immutable"
                    )
                return existing
            session.add(
                CompiledExperimentRow(
                    plan_id=compiled.plan_id,
                    plan_version=compiled.plan_version,
                    archive_sha256=compiled.archive_sha256,
                    archive=compiled.archive,
                    preview_json=compiled.preview_json,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
            return compiled

    def load_compiled_experiment(
        self, plan_id: str, plan_version: int
    ) -> StoredCompiledExperiment | None:
        with self._sessions() as session:
            row = session.scalar(
                select(CompiledExperimentRow).where(
                    CompiledExperimentRow.plan_id == plan_id,
                    CompiledExperimentRow.plan_version == plan_version,
                )
            )
            return None if row is None else self._stored_compiled(row)

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

    @staticmethod
    def _stored_plan(row: ExperimentPlanRow) -> StoredExperimentPlan:
        return StoredExperimentPlan(
            plan_id=row.plan_id,
            project_id=row.project_id,
            version=row.version,
            provider=row.provider,
            model=row.model,
            plan_json=row.plan_json,
        )

    @staticmethod
    def _stored_compiled(row: CompiledExperimentRow) -> StoredCompiledExperiment:
        return StoredCompiledExperiment(
            plan_id=row.plan_id,
            plan_version=row.plan_version,
            archive_sha256=row.archive_sha256,
            archive=row.archive,
            preview_json=row.preview_json,
        )

    @staticmethod
    def _stored_operation(row: OperationRow) -> StoredOperation:
        return StoredOperation(
            record=OperationRecord.model_validate_json(row.record_json),
            version=row.version,
        )
