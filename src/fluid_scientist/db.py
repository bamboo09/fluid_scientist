"""SQLAlchemy table definitions for durable workflow state."""

from sqlalchemy import ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class WorkflowSnapshotRow(Base):
    __tablename__ = "workflow_snapshots"

    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class OperationRow(Base):
    __tablename__ = "operations"
    __table_args__ = (UniqueConstraint("kind", "project_id", "input_digest"),)

    operation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True
    )
    input_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    record_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True
    )
    gate: Mapped[str] = mapped_column(String(16), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_at: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_version: Mapped[int] = mapped_column(Integer, nullable=False)


class ExternalJobRow(Base):
    __tablename__ = "external_jobs"
    __table_args__ = (UniqueConstraint("project_id", "case_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class ExperimentPlanRow(Base):
    __tablename__ = "experiment_plans"

    plan_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class CompiledExperimentRow(Base):
    __tablename__ = "compiled_experiments"
    __table_args__ = (UniqueConstraint("plan_id", "plan_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("experiment_plans.plan_id", ondelete="CASCADE"), index=True
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    archive: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    preview_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)


class ExperimentSpecRow(Base):
    __tablename__ = "experiment_specs"

    experiment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    experiment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    interaction_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    spec_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class CandidateTemplateRow(Base):
    __tablename__ = "candidate_templates"

    candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        ForeignKey("generated_case_drafts.draft_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(64), nullable=False)


class GeneratedCaseDraftRow(Base):
    __tablename__ = "generated_case_drafts"
    __table_args__ = (UniqueConstraint("plan_id", "plan_version", "version"),)

    draft_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("experiment_plans.plan_id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    draft_json: Mapped[str] = mapped_column(Text, nullable=False)
    archive_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    archive: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    preview_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False)
