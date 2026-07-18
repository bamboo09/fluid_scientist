"""SQLAlchemy-backed workflow repository for SQLite and PostgreSQL."""

import hashlib
import json
from datetime import datetime

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from fluid_scientist.adapters.custom_openfoam import (
    CustomCaseRejected,
    validate_custom_case_archive,
)
from fluid_scientist.case_generation.models import GeneratedCaseDraft
from fluid_scientist.case_generation.validation import (
    GeneratedCaseRejected,
    validate_generated_case,
)
from fluid_scientist.compat import UTC
from fluid_scientist.db import (
    ApprovalRow,
    AuditEventRow,
    Base,
    CandidateTemplateRow,
    CompiledExperimentRow,
    ExperimentPlanRow,
    ExperimentSpecRow,
    ExternalJobRow,
    GeneratedCaseDraftRow,
    OperationRow,
    ProjectRow,
    WorkflowSnapshotRow,
)
from fluid_scientist.domain.models import Approval, AuditEvent
from fluid_scientist.operations.models import (
    OperationKind,
    OperationRecord,
    OperationStage,
    OperationState,
)
from fluid_scientist.ports import (
    StoredCandidateTemplate,
    StoredCompiledExperiment,
    StoredExperimentPlan,
    StoredExperimentSpec,
    StoredGeneratedCaseDraft,
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


class OperationIntegrityError(RuntimeError):
    """Raised when an operation row disagrees with its persisted record."""


class GeneratedCaseDraftIntegrityError(RuntimeError):
    """Raised when immutable generated-case persistence fails verification."""


def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _migrate_compiled_experiments_plan_id_impl(engine) -> None:
    """Rename compiled_experiments.plan_id to experiment_id and drop the legacy FK.

    The original ``compiled_experiments`` table was created with
    ``FOREIGN KEY (plan_id) REFERENCES experiment_plans(plan_id)``.  A plain
    ``ALTER TABLE ... RENAME COLUMN plan_id TO experiment_id`` leaves that
    constraint in place (as ``FOREIGN KEY (experiment_id) REFERENCES
    experiment_plans``), which rejects compiled experiments whose
    ``experiment_id`` originates from ``experiment_specs`` instead of
    ``experiment_plans``.  SQLite cannot ``DROP CONSTRAINT``, so the table is
    rebuilt without the foreign key using the standard SQLite rebuild pattern.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if not inspector.has_table("compiled_experiments"):
        return
    columns = {col["name"] for col in inspector.get_columns("compiled_experiments")}
    if "plan_id" in columns and "experiment_id" not in columns:
        # Rebuild the table without the legacy foreign key.  PRAGMA
        # foreign_keys is a no-op inside a transaction, but the rebuild is
        # safe regardless: the freshly created table has no foreign key, so
        # the data copy is never FK-checked, and dropping the renamed child
        # table is always permitted.
        with engine.begin() as conn:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(
                text(
                    "ALTER TABLE compiled_experiments "
                    "RENAME TO compiled_experiments_old"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE compiled_experiments ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "experiment_id VARCHAR(128), "
                    "plan_version INTEGER NOT NULL, "
                    "archive_sha256 VARCHAR(71) NOT NULL, "
                    "archive BLOB NOT NULL, "
                    "preview_json TEXT NOT NULL, "
                    "created_at VARCHAR(64) NOT NULL, "
                    "UNIQUE (experiment_id, plan_version)"
                    ")"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO compiled_experiments "
                    "(experiment_id, plan_version, archive_sha256, archive, "
                    "preview_json, created_at) "
                    "SELECT plan_id, plan_version, archive_sha256, archive, "
                    "preview_json, created_at "
                    "FROM compiled_experiments_old"
                )
            )
            conn.execute(text("DROP TABLE compiled_experiments_old"))
            conn.execute(text("PRAGMA foreign_keys=ON"))


class SQLWorkflowRepository:
    def _migrate_compiled_experiments_plan_id(self) -> None:
        """Rename plan_id column to experiment_id in compiled_experiments table."""
        _migrate_compiled_experiments_plan_id_impl(self._engine)

    def __init__(self, database_url: str) -> None:
        engine_options = {}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            engine_options = {
                "connect_args": {"check_same_thread": False},
                "poolclass": StaticPool,
            }
        self._engine = create_engine(database_url, **engine_options)
        if database_url.startswith("sqlite"):
            event.listen(self._engine, "connect", _enable_sqlite_foreign_keys)
        Base.metadata.create_all(self._engine)
        self._migrate_compiled_experiments_plan_id()
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def save_snapshot(self, project_id: str, snapshot: str, *, expected_version: int) -> int:
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
        try:
            with self._sessions.begin() as session:
                operation_row = session.get(OperationRow, record.operation_id)
                if operation_row is not None:
                    return self._resolve_operation_id_replay(operation_row, record)

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
        except IntegrityError as error:
            return self._resolve_operation_create_race(record, error)

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
        self, record: OperationRecord, *, expected_version: int
    ) -> StoredOperation:
        with self._sessions.begin() as session:
            new_version = expected_version + 1
            result = session.execute(
                update(OperationRow)
                .where(
                    OperationRow.operation_id == record.operation_id,
                    OperationRow.version == expected_version,
                    OperationRow.kind == record.kind.value,
                    OperationRow.project_id == record.project_id,
                    OperationRow.input_digest == record.input_digest,
                    OperationRow.created_at == record.created_at.isoformat(),
                )
                .values(
                    version=new_version,
                    record_json=record.model_dump_json(),
                    updated_at=record.updated_at.isoformat(),
                )
            )
            if result.rowcount == 1:
                return StoredOperation(record=record, version=new_version)

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
            if row.created_at != record.created_at.isoformat():
                raise OperationConflict(f"operation {record.operation_id} created_at is immutable")
            raise ConcurrentUpdateError(
                f"operation {record.operation_id} could not be updated atomically"
            )

    def list_interrupted_operations(self) -> tuple[StoredOperation, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(OperationRow).order_by(OperationRow.created_at, OperationRow.operation_id)
            ).all()
            stored = (self._stored_operation(row) for row in rows)
            return tuple(
                item
                for item in stored
                if item.record.state in {OperationState.QUEUED, OperationState.RUNNING}
            )

    def complete_planning_operation(
        self,
        plan: StoredExperimentPlan,
        record: OperationRecord,
        *,
        expected_version: int,
    ) -> StoredOperation:
        """Atomically persist an accepted plan and its successful operation state."""

        if record.kind is not OperationKind.PLAN:
            raise OperationConflict("only planning operations can store experiment plans")
        if record.state is not OperationState.SUCCEEDED or record.result_ref != plan.plan_id:
            raise OperationConflict("planning completion must reference its accepted plan")
        if plan.project_id != record.project_id:
            raise OperationConflict("planning operation and plan project must match")

        with self._sessions.begin() as session:
            self._require_project(session, record.project_id)
            new_version = expected_version + 1
            result = session.execute(
                update(OperationRow)
                .where(
                    OperationRow.operation_id == record.operation_id,
                    OperationRow.version == expected_version,
                    OperationRow.kind == record.kind.value,
                    OperationRow.project_id == record.project_id,
                    OperationRow.input_digest == record.input_digest,
                    OperationRow.created_at == record.created_at.isoformat(),
                )
                .values(
                    version=new_version,
                    record_json=record.model_dump_json(),
                    updated_at=record.updated_at.isoformat(),
                )
            )
            if result.rowcount != 1:
                raise ConcurrentUpdateError(
                    f"operation {record.operation_id} could not be completed at expected "
                    f"version {expected_version}"
                )
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
            return StoredOperation(record=record, version=new_version)

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
                    CompiledExperimentRow.experiment_id == compiled.experiment_id,
                    CompiledExperimentRow.plan_version == compiled.plan_version,
                )
            )
            if row is not None:
                existing = self._stored_compiled(row)
                if existing != compiled:
                    raise ExperimentArtifactConflict(
                        "compiled experiment "
                        f"{compiled.experiment_id} version {compiled.plan_version} is immutable"
                    )
                return existing
            session.add(
                CompiledExperimentRow(
                    experiment_id=compiled.experiment_id,
                    plan_version=compiled.plan_version,
                    archive_sha256=compiled.archive_sha256,
                    archive=compiled.archive,
                    preview_json=compiled.preview_json,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
            return compiled

    def load_compiled_experiment(
        self, experiment_id: str, plan_version: int
    ) -> StoredCompiledExperiment | None:
        with self._sessions() as session:
            row = session.scalar(
                select(CompiledExperimentRow).where(
                    CompiledExperimentRow.experiment_id == experiment_id,
                    CompiledExperimentRow.plan_version == plan_version,
                )
            )
            return None if row is None else self._stored_compiled(row)

    def store_generated_case_draft(
        self, draft: StoredGeneratedCaseDraft
    ) -> StoredGeneratedCaseDraft:
        try:
            with self._sessions.begin() as session:
                row = session.get(GeneratedCaseDraftRow, draft.draft_id)
                if row is not None:
                    existing = self._stored_generated_case_draft(session, row)
                    if existing != draft:
                        raise ExperimentArtifactConflict(
                            f"generated case draft {draft.draft_id} is immutable"
                        )
                    return existing

                tuple_row = session.scalar(
                    select(GeneratedCaseDraftRow).where(
                        GeneratedCaseDraftRow.plan_id == draft.plan_id,
                        GeneratedCaseDraftRow.plan_version == draft.plan_version,
                        GeneratedCaseDraftRow.version == draft.version,
                    )
                )
                if tuple_row is not None:
                    existing = self._stored_generated_case_draft(session, tuple_row)
                    if existing == draft:
                        return existing
                    raise ExperimentArtifactConflict(
                        "generated case draft plan version already exists"
                    )

                plan = session.get(ExperimentPlanRow, draft.plan_id)
                if plan is None:
                    raise KeyError(f"experiment plan not found: {draft.plan_id}")
                self._require_generated_draft_plan_match(draft, plan)
                self._require_project(session, draft.project_id)
                self._validate_generated_draft_payload(draft)
                session.add(
                    GeneratedCaseDraftRow(
                        draft_id=draft.draft_id,
                        project_id=draft.project_id,
                        plan_id=draft.plan_id,
                        plan_version=draft.plan_version,
                        version=draft.version,
                        provider=draft.provider,
                        model=draft.model,
                        draft_json=draft.draft_json,
                        archive_sha256=draft.archive_sha256,
                        archive=draft.archive,
                        preview_json=draft.preview_json,
                        created_at=datetime.now(UTC).isoformat(),
                    )
                )
                return draft
        except IntegrityError as error:
            return self._resolve_generated_draft_race(draft, error)

    def load_generated_case_draft(self, draft_id: str) -> StoredGeneratedCaseDraft | None:
        with self._sessions() as session:
            row = session.get(GeneratedCaseDraftRow, draft_id)
            return None if row is None else self._stored_generated_case_draft(session, row)

    def find_generated_case_draft(
        self, plan_id: str, plan_version: int, version: int
    ) -> StoredGeneratedCaseDraft | None:
        with self._sessions() as session:
            row = session.scalar(
                select(GeneratedCaseDraftRow).where(
                    GeneratedCaseDraftRow.plan_id == plan_id,
                    GeneratedCaseDraftRow.plan_version == plan_version,
                    GeneratedCaseDraftRow.version == version,
                )
            )
            return None if row is None else self._stored_generated_case_draft(session, row)

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
            experiment_id=row.experiment_id,
            plan_version=row.plan_version,
            archive_sha256=row.archive_sha256,
            archive=row.archive,
            preview_json=row.preview_json,
        )

    # ------------------------------------------------------------------
    # Experiment spec (structured experiment specification)
    # ------------------------------------------------------------------
    def save_experiment_spec(self, spec: StoredExperimentSpec) -> None:
        row = ExperimentSpecRow(
            experiment_id=spec.experiment_id,
            project_id=spec.project_id,
            schema_version=spec.schema_version,
            experiment_version=spec.experiment_version,
            status=spec.status,
            task_type=spec.task_type,
            interaction_mode=spec.interaction_mode,
            spec_json=spec.spec_json,
            created_at=spec.created_at,
            updated_at=spec.updated_at,
        )
        with self._sessions() as session:
            session.add(row)
            session.commit()

    def load_experiment_spec(self, experiment_id: str) -> StoredExperimentSpec | None:
        with self._sessions() as session:
            row = session.scalar(
                select(ExperimentSpecRow).where(
                    ExperimentSpecRow.experiment_id == experiment_id
                )
            )
            if row is None:
                return None
            return self._stored_experiment_spec(row)

    def list_experiment_specs(
        self, *, project_id: str | None = None, status: str | None = None
    ) -> list[StoredExperimentSpec]:
        stmt = select(ExperimentSpecRow).order_by(
            ExperimentSpecRow.created_at.desc()
        )
        if project_id is not None:
            stmt = stmt.where(ExperimentSpecRow.project_id == project_id)
        if status is not None:
            stmt = stmt.where(ExperimentSpecRow.status == status)
        with self._sessions() as session:
            rows = session.scalars(stmt).all()
            return [self._stored_experiment_spec(r) for r in rows]

    def update_experiment_spec_status(
        self,
        experiment_id: str,
        *,
        new_status: str,
        updated_at: str,
    ) -> StoredExperimentSpec:
        with self._sessions() as session:
            row = session.scalar(
                select(ExperimentSpecRow).where(
                    ExperimentSpecRow.experiment_id == experiment_id
                )
            )
            if row is None:
                raise KeyError(f"experiment spec {experiment_id} not found")
            row.status = new_status
            row.updated_at = updated_at
            session.commit()
            session.refresh(row)
            return self._stored_experiment_spec(row)

    def replace_experiment_spec(
        self,
        experiment_id: str,
        *,
        spec_json: str,
        experiment_version: int,
        status: str,
        updated_at: str,
    ) -> StoredExperimentSpec:
        """Replace spec_json and version (used when updating parameters)."""
        with self._sessions() as session:
            row = session.scalar(
                select(ExperimentSpecRow).where(
                    ExperimentSpecRow.experiment_id == experiment_id
                )
            )
            if row is None:
                raise KeyError(f"experiment spec {experiment_id} not found")
            row.spec_json = spec_json
            row.experiment_version = experiment_version
            row.status = status
            row.updated_at = updated_at
            session.commit()
            session.refresh(row)
            return self._stored_experiment_spec(row)

    @staticmethod
    def _stored_experiment_spec(row: ExperimentSpecRow) -> StoredExperimentSpec:
        return StoredExperimentSpec(
            experiment_id=row.experiment_id,
            project_id=row.project_id,
            schema_version=row.schema_version,
            experiment_version=row.experiment_version,
            status=row.status,
            task_type=row.task_type,
            interaction_mode=row.interaction_mode,
            spec_json=row.spec_json,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # ------------------------------------------------------------------
    # Candidate template library
    # ------------------------------------------------------------------
    def save_candidate_template(self, template: StoredCandidateTemplate) -> None:
        """Persist a new candidate template row.

        Raises IntegrityError if the candidate_id or draft_id already exists.
        """
        row = CandidateTemplateRow(
            candidate_id=template.candidate_id,
            draft_id=template.draft_id,
            project_id=template.project_id,
            plan_id=template.plan_id,
            plan_version=template.plan_version,
            draft_version=template.draft_version,
            archive_sha256=template.archive_sha256,
            state=template.state,
            rejection_reason=template.rejection_reason,
            created_at=template.created_at,
            updated_at=template.updated_at,
        )
        with self._sessions() as session:
            session.add(row)
            session.commit()

    def load_candidate_template(self, candidate_id: str) -> StoredCandidateTemplate:
        with self._sessions() as session:
            row = session.scalar(
                select(CandidateTemplateRow).where(
                    CandidateTemplateRow.candidate_id == candidate_id
                )
            )
            if row is None:
                raise KeyError(f"candidate template {candidate_id} not found")
            return self._stored_candidate_template(row)

    def list_candidate_templates(
        self, *, project_id: str | None = None, state: str | None = None
    ) -> list[StoredCandidateTemplate]:
        stmt = select(CandidateTemplateRow).order_by(
            CandidateTemplateRow.created_at.desc()
        )
        if project_id is not None:
            stmt = stmt.where(CandidateTemplateRow.project_id == project_id)
        if state is not None:
            stmt = stmt.where(CandidateTemplateRow.state == state)
        with self._sessions() as session:
            rows = session.scalars(stmt).all()
            return [self._stored_candidate_template(r) for r in rows]

    def update_candidate_template_state(
        self,
        candidate_id: str,
        *,
        new_state: str,
        rejection_reason: str | None,
        updated_at: str,
    ) -> StoredCandidateTemplate:
        with self._sessions() as session:
            row = session.scalar(
                select(CandidateTemplateRow).where(
                    CandidateTemplateRow.candidate_id == candidate_id
                )
            )
            if row is None:
                raise KeyError(f"candidate template {candidate_id} not found")
            row.state = new_state
            row.rejection_reason = rejection_reason
            row.updated_at = updated_at
            session.commit()
            session.refresh(row)
            return self._stored_candidate_template(row)

    @staticmethod
    def _stored_candidate_template(row: CandidateTemplateRow) -> StoredCandidateTemplate:
        return StoredCandidateTemplate(
            candidate_id=row.candidate_id,
            draft_id=row.draft_id,
            project_id=row.project_id,
            plan_id=row.plan_id,
            plan_version=row.plan_version,
            draft_version=row.draft_version,
            archive_sha256=row.archive_sha256,
            state=row.state,
            rejection_reason=row.rejection_reason,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _stored_generated_case_draft(
        self, session: Session, row: GeneratedCaseDraftRow
    ) -> StoredGeneratedCaseDraft:
        try:
            created_at = datetime.fromisoformat(row.created_at)
        except (TypeError, ValueError) as error:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {row.draft_id} has invalid created_at"
            ) from error
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {row.draft_id} created_at must be timezone-aware"
            )
        try:
            stored = StoredGeneratedCaseDraft(
                draft_id=row.draft_id,
                project_id=row.project_id,
                plan_id=row.plan_id,
                plan_version=row.plan_version,
                version=row.version,
                provider=row.provider,
                model=row.model,
                draft_json=row.draft_json,
                archive_sha256=row.archive_sha256,
                archive=bytes(row.archive),
                preview_json=row.preview_json,
            )
        except (TypeError, ValueError) as error:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {row.draft_id} has invalid row metadata: {error}"
            ) from error
        plan = session.get(ExperimentPlanRow, stored.plan_id)
        if plan is None:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {row.draft_id} references a missing plan"
            )
        try:
            self._require_generated_draft_plan_match(stored, plan)
        except ExperimentArtifactConflict as error:
            raise GeneratedCaseDraftIntegrityError(str(error)) from error
        self._validate_generated_draft_payload(stored)
        return stored

    @staticmethod
    def _require_generated_draft_plan_match(
        draft: StoredGeneratedCaseDraft, plan: ExperimentPlanRow
    ) -> None:
        if plan.project_id is None or draft.project_id != plan.project_id:
            raise ExperimentArtifactConflict(
                f"generated case draft project does not own plan {draft.plan_id}"
            )
        if draft.plan_version != plan.version:
            raise ExperimentArtifactConflict(
                f"generated case draft version does not match plan {draft.plan_id}"
            )

    @classmethod
    def _validate_generated_draft_payload(cls, stored: StoredGeneratedCaseDraft) -> None:
        actual_digest = "sha256:" + hashlib.sha256(stored.archive).hexdigest()
        if actual_digest != stored.archive_sha256:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {stored.draft_id} archive digest mismatch"
            )
        draft_payload = cls._load_strict_json(
            stored.draft_id, stored.draft_json, label="draft_json"
        )
        if not isinstance(draft_payload, dict):
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {stored.draft_id} draft_json must be an object"
            )
        try:
            draft = GeneratedCaseDraft.model_validate(draft_payload)
        except ValueError as error:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {stored.draft_id} has invalid draft_json"
            ) from error
        preview = cls._load_strict_json(
            stored.draft_id, stored.preview_json, label="preview_json"
        )
        if not isinstance(preview, (dict, list)):
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {stored.draft_id} preview_json must be an object or array"
            )
        try:
            manifest = validate_custom_case_archive(stored.archive)
            validated = validate_generated_case(draft)
        except (CustomCaseRejected, GeneratedCaseRejected, ValueError) as error:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {stored.draft_id} archive validation failed"
            ) from error
        if manifest.archive_sha256 != stored.archive_sha256:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {stored.draft_id} archive digest mismatch"
            )
        if validated.archive != stored.archive or validated.archive_sha256 != stored.archive_sha256:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {stored.draft_id} archive does not match draft_json"
            )
        expected_preview = [[path, size] for path, size in validated.preview]
        if preview != expected_preview:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {stored.draft_id} preview_json does not match draft_json"
            )

    @staticmethod
    def _load_strict_json(draft_id: str, payload: str, *, label: str) -> object:
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON constant: {value}")

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON object key")
                result[key] = value
            return result

        try:
            value = json.loads(
                payload,
                parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate_keys,
            )
        except (TypeError, ValueError) as error:
            raise GeneratedCaseDraftIntegrityError(
                f"generated case draft {draft_id} has invalid {label}"
            ) from error
        return value

    def _resolve_generated_draft_race(
        self, draft: StoredGeneratedCaseDraft, error: IntegrityError
    ) -> StoredGeneratedCaseDraft:
        with self._sessions() as session:
            row = session.get(GeneratedCaseDraftRow, draft.draft_id)
            if row is None:
                row = session.scalar(
                    select(GeneratedCaseDraftRow).where(
                        GeneratedCaseDraftRow.plan_id == draft.plan_id,
                        GeneratedCaseDraftRow.plan_version == draft.plan_version,
                        GeneratedCaseDraftRow.version == draft.version,
                    )
                )
            if row is not None:
                existing = self._stored_generated_case_draft(session, row)
                if existing == draft:
                    return existing
                raise ExperimentArtifactConflict(
                    "generated case draft plan version already exists with different content"
                ) from error
        raise ExperimentArtifactConflict(
            f"generated case draft {draft.draft_id} insert conflicted without a winner"
        ) from error

    @staticmethod
    def _stored_operation(row: OperationRow) -> StoredOperation:
        record = OperationRecord.model_validate_json(row.record_json)
        try:
            row_created_at = datetime.fromisoformat(row.created_at)
            row_updated_at = datetime.fromisoformat(row.updated_at)
        except ValueError as error:
            raise OperationIntegrityError(
                f"operation {row.operation_id} has an invalid row timestamp"
            ) from error
        authoritative_values = {
            "operation_id": row.operation_id,
            "kind": row.kind,
            "project_id": row.project_id,
            "input_digest": row.input_digest,
            "created_at": row_created_at,
            "updated_at": row_updated_at,
        }
        record_values = {
            "operation_id": record.operation_id,
            "kind": record.kind.value,
            "project_id": record.project_id,
            "input_digest": record.input_digest,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        for field, authoritative_value in authoritative_values.items():
            if record_values[field] != authoritative_value:
                raise OperationIntegrityError(
                    f"operation {row.operation_id} {field} mismatch between row and record_json"
                )
        return StoredOperation(record=record, version=row.version)

    def _resolve_operation_create_race(
        self, record: OperationRecord, error: IntegrityError
    ) -> StoredOperation:
        with self._sessions() as session:
            operation_row = session.get(OperationRow, record.operation_id)
            if operation_row is not None:
                return self._resolve_operation_id_replay(operation_row, record)
            request_row = session.scalar(
                select(OperationRow).where(
                    OperationRow.kind == record.kind.value,
                    OperationRow.project_id == record.project_id,
                    OperationRow.input_digest == record.input_digest,
                )
            )
            if request_row is not None:
                return self._stored_operation(request_row)
        raise OperationConflict(
            f"operation {record.operation_id} insert conflicted but no winner was found"
        ) from error

    def _resolve_operation_id_replay(
        self, row: OperationRow, record: OperationRecord
    ) -> StoredOperation:
        if (
            row.kind != record.kind.value
            or row.project_id != record.project_id
            or row.input_digest != record.input_digest
        ):
            raise OperationConflict(
                f"operation {record.operation_id} already exists with different identity"
            )
        if not self._is_canonical_operation_create(row, record):
            raise OperationConflict(
                f"operation {record.operation_id} replay is not a canonical create payload"
            )
        return self._stored_operation(row)

    @staticmethod
    def _is_canonical_operation_create(row: OperationRow, record: OperationRecord) -> bool:
        return (
            record.state is OperationState.QUEUED
            and record.stage is OperationStage.QUEUED
            and record.message == OperationRecord.model_fields["message"].default
            and record.result_ref is None
            and record.safe_error is None
            and not record.cancel_requested
            and record.attempt == 1
            and record.created_at.isoformat() == row.created_at
            and record.updated_at == record.created_at
        )
