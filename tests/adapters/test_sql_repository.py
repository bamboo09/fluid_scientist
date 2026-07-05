import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from inspect import Parameter, signature
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from fluid_scientist.adapters.sql_repository import (
    ConcurrentUpdateError,
    ExperimentArtifactConflict,
    ExternalJobConflict,
    GeneratedCaseDraftIntegrityError,
    OperationConflict,
    OperationIntegrityError,
    SQLWorkflowRepository,
)
from fluid_scientist.case_generation.models import GeneratedCaseDraft
from fluid_scientist.case_generation.validation import validate_generated_case
from fluid_scientist.domain.models import Approval, AuditEvent
from fluid_scientist.operations.models import (
    OperationKind,
    OperationRecord,
    OperationStage,
    OperationState,
)
from fluid_scientist.ports import (
    StoredExperimentPlan,
    StoredGeneratedCaseDraft,
    WorkflowRepository,
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


def generated_case_draft() -> GeneratedCaseDraft:
    def header(class_name: str, object_name: str) -> str:
        return (
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
            f"    class {class_name};\n    object {object_name};\n}}\n"
        )

    return GeneratedCaseDraft.model_validate(
        {
            "experiment_name": "Generated cavity pilot",
            "objective": "Build a bounded generated OpenFOAM case for isolated validation.",
            "solver": "incompressibleFluid",
            "preprocessing": ["blockMesh", "checkMesh"],
            "parameters": [],
            "files": [
                {"path": "0/U", "content": header("volVectorField", "U")},
                {"path": "0/p", "content": header("volScalarField", "p")},
                {
                    "path": "constant/physicalProperties",
                    "content": header("dictionary", "physicalProperties")
                    + "nu [0 2 -1 0 0 0 0] 1e-5;\n",
                },
                {
                    "path": "system/controlDict",
                    "content": header("dictionary", "controlDict")
                    + "application incompressibleFluid;\nendTime 1;\n",
                },
                {
                    "path": "system/fvSchemes",
                    "content": header("dictionary", "fvSchemes")
                    + "ddtSchemes { default steadyState; }\n",
                },
                {
                    "path": "system/fvSolution",
                    "content": header("dictionary", "fvSolution") + "solvers {}\n",
                },
                {
                    "path": "system/blockMeshDict",
                    "content": header("dictionary", "blockMeshDict")
                    + "convertToMeters 1;\nvertices ();\n",
                },
            ],
            "requested_outputs": ["velocity_probes"],
            "assumptions": ["Two-dimensional incompressible pilot"],
            "limitations": ["Requires isolated pilot validation"],
        }
    )


def seed_generated_draft(repo: SQLWorkflowRepository) -> StoredGeneratedCaseDraft:
    repo.save_snapshot("project-1", "{}", expected_version=0)
    repo.store_experiment_plan(
        StoredExperimentPlan(
            plan_id="plan-1",
            project_id="project-1",
            version=2,
            provider="glm",
            model="glm-5.1",
            plan_json='{"experiment_type":"custom_openfoam"}',
        )
    )
    draft = generated_case_draft()
    validated = validate_generated_case(draft)
    return StoredGeneratedCaseDraft(
        draft_id="draft-1",
        project_id="project-1",
        plan_id="plan-1",
        plan_version=2,
        version=1,
        provider="deepseek",
        model="deepseek-chat",
        draft_json=draft.model_dump_json(),
        archive_sha256=validated.archive_sha256,
        archive=validated.archive,
        preview_json=json.dumps(validated.preview),
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
    repo.save_snapshot("project-1", "{}", expected_version=0)
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
    repo.save_snapshot("project-1", "{}", expected_version=0)
    original = operation()
    duplicate_request = operation("operation-2")

    first = repo.create_operation(original)
    duplicate = repo.create_operation(duplicate_request)

    assert duplicate == first
    assert repo.load_operation("operation-2") is None


def test_concurrent_operation_create_returns_unique_constraint_winner(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    insert_barrier = Barrier(2)

    def synchronize_operation_inserts(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO OPERATIONS"):
            insert_barrier.wait(timeout=5)

    event.listen(repo._engine, "before_cursor_execute", synchronize_operation_inserts)
    requests = [operation("operation-1"), operation("operation-2")]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(repo.create_operation, record) for record in requests]
        results = [future.result(timeout=10) for future in futures]

    assert results[0] == results[1]
    assert results[0].record.operation_id in {"operation-1", "operation-2"}
    stored = [repo.load_operation(record.operation_id) for record in requests]
    assert sum(item is not None for item in stored) == 1


def test_same_operation_id_replay_returns_advanced_record(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    original = operation()
    repo.create_operation(original)
    running = original.model_copy(
        update={
            "state": OperationState.RUNNING,
            "updated_at": original.updated_at + timedelta(seconds=1),
        }
    )
    advanced = repo.update_operation(running, expected_version=1)

    replayed = repo.create_operation(original)

    assert replayed == advanced


@pytest.mark.parametrize(
    "changes",
    [
        {"state": OperationState.RUNNING},
        {"stage": OperationStage.MODEL_PLANNING},
        {"message": "altered create payload"},
        {"result_ref": "result-1"},
        {"safe_error": "altered"},
        {"cancel_requested": True},
        {"attempt": 2},
        {"updated_at": datetime(2026, 7, 1, 0, 0, 1, tzinfo=UTC)},
        {
            "created_at": datetime(2026, 7, 1, 0, 0, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 7, 1, 0, 0, 1, tzinfo=UTC),
        },
    ],
)
def test_same_operation_id_rejects_noncanonical_mutable_payload(tmp_path, changes) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    original = operation()
    stored = repo.create_operation(original)
    altered = original.model_copy(update=changes)

    with pytest.raises(OperationConflict, match="canonical"):
        repo.create_operation(altered)

    assert repo.load_operation(original.operation_id) == stored


def test_operation_id_collision_rejects_different_content(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    repo.create_operation(operation())
    changed = operation(project_id="missing-project")

    with pytest.raises(OperationConflict, match="operation-1"):
        repo.create_operation(changed)


def test_operation_update_increments_version_and_rejects_stale_write(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
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


def test_operation_update_uses_version_in_atomic_update_predicate(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    original = operation()
    repo.create_operation(original)
    updated_record = original.model_copy(
        update={"updated_at": original.updated_at + timedelta(seconds=1)}
    )
    statements = []
    event.listen(
        repo._engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _executemany: statements.append(
            statement
        ),
    )

    repo.update_operation(updated_record, expected_version=1)

    update_sql = next(
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("UPDATE OPERATIONS")
    )
    where_clause = update_sql.upper().split(" WHERE ", maxsplit=1)[1]
    assert "OPERATIONS.VERSION" in where_clause


def test_operation_update_expected_version_is_keyword_only() -> None:
    implementation_parameter = signature(SQLWorkflowRepository.update_operation).parameters[
        "expected_version"
    ]
    protocol_parameter = signature(WorkflowRepository.update_operation).parameters[
        "expected_version"
    ]

    assert implementation_parameter.kind is Parameter.KEYWORD_ONLY
    assert protocol_parameter.kind is Parameter.KEYWORD_ONLY


def test_generated_case_draft_round_trip_is_exact_and_secret_free(tmp_path) -> None:
    repo = repository(tmp_path)
    draft = seed_generated_draft(repo)

    assert repo.store_generated_case_draft(draft) == draft
    assert repo.load_generated_case_draft(draft.draft_id) == draft
    assert repo.find_generated_case_draft("plan-1", 2, 1) == draft
    assert repo.load_generated_case_draft("missing") is None
    assert repo.find_generated_case_draft("plan-1", 2, 999) is None
    assert "api_key" not in {field.name for field in fields(StoredGeneratedCaseDraft)}
    assert "raw_response" not in {field.name for field in fields(StoredGeneratedCaseDraft)}
    assert "host_path" not in {field.name for field in fields(StoredGeneratedCaseDraft)}


@pytest.mark.parametrize(
    "change",
    [
        {"archive": b"changed"},
        {"provider": "openai"},
        {"draft_json": "{}"},
        {"preview_json": "[]"},
    ],
)
def test_generated_case_draft_id_replay_is_idempotent_or_conflicts(
    tmp_path, change
) -> None:
    repo = repository(tmp_path)
    draft = seed_generated_draft(repo)
    assert repo.store_generated_case_draft(draft) == draft
    assert repo.store_generated_case_draft(draft) == draft

    with pytest.raises(ExperimentArtifactConflict):
        repo.store_generated_case_draft(replace(draft, **change))


def test_generated_case_draft_requires_exact_project_and_plan_version(tmp_path) -> None:
    repo = repository(tmp_path)
    draft = seed_generated_draft(repo)

    with pytest.raises(KeyError, match="plan"):
        repo.store_generated_case_draft(replace(draft, plan_id="missing-plan"))
    with pytest.raises(ExperimentArtifactConflict, match="project"):
        repo.store_generated_case_draft(replace(draft, project_id="project-2"))
    with pytest.raises(ExperimentArtifactConflict, match="version"):
        repo.store_generated_case_draft(replace(draft, plan_version=1))


def test_generated_case_draft_rejects_bad_digest_and_unique_tuple_collision(tmp_path) -> None:
    repo = repository(tmp_path)
    draft = seed_generated_draft(repo)

    with pytest.raises(GeneratedCaseDraftIntegrityError, match="digest"):
        repo.store_generated_case_draft(
            replace(draft, archive_sha256=f"sha256:{'0' * 64}")
        )

    repo.store_generated_case_draft(draft)
    with pytest.raises(ExperimentArtifactConflict, match="plan version"):
        repo.store_generated_case_draft(replace(draft, draft_id="draft-2"))


def test_concurrent_identical_generated_case_draft_insert_is_idempotent(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.db'}"
    seed_repo = SQLWorkflowRepository(database_url)
    draft = seed_generated_draft(seed_repo)
    barrier = Barrier(2)

    def write() -> StoredGeneratedCaseDraft:
        repo = SQLWorkflowRepository(database_url)
        barrier.wait(timeout=5)
        return repo.store_generated_case_draft(draft)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: write(), range(2)))

    assert results == [draft, draft]


def test_concurrent_different_generated_drafts_for_same_version_have_one_winner(
    tmp_path,
) -> None:
    database_url = f"sqlite:///{tmp_path / 'workflow.db'}"
    seed_repo = SQLWorkflowRepository(database_url)
    first = seed_generated_draft(seed_repo)
    changed_payload = generated_case_draft().model_dump()
    changed_payload["objective"] = (
        "Build a different but bounded generated OpenFOAM case for isolated validation."
    )
    second = replace(
        first,
        draft_id="draft-2",
        draft_json=GeneratedCaseDraft.model_validate(changed_payload).model_dump_json(),
    )
    barrier = Barrier(2)

    def write(draft: StoredGeneratedCaseDraft):
        repo = SQLWorkflowRepository(database_url)
        barrier.wait(timeout=5)
        try:
            return repo.store_generated_case_draft(draft)
        except ExperimentArtifactConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, (first, second)))

    assert sum(isinstance(result, StoredGeneratedCaseDraft) for result in results) == 1
    assert sum(isinstance(result, ExperimentArtifactConflict) for result in results) == 1
    with seed_repo._engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM generated_case_drafts")) == 1


def test_generated_case_draft_sqlite_foreign_keys_reject_orphans_and_cascade(tmp_path) -> None:
    repo = repository(tmp_path)
    draft = seed_generated_draft(repo)
    repo.store_generated_case_draft(draft)

    with repo._engine.begin() as connection, pytest.raises(SAIntegrityError):
        connection.execute(
            text(
                """INSERT INTO generated_case_drafts
                (draft_id, project_id, plan_id, plan_version, version, provider, model,
                 draft_json, archive_sha256, archive, preview_json, created_at)
                VALUES
                ('orphan', 'missing-project', 'missing-plan', 1, 1, 'glm', 'model',
                 '{}', :digest, :archive, '[]', :created_at)"""
            ),
            {
                "digest": f"sha256:{'0' * 64}",
                "archive": b"x",
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    with repo._engine.begin() as connection:
        connection.execute(text("DELETE FROM experiment_plans WHERE plan_id = 'plan-1'"))
    assert repo.load_generated_case_draft(draft.draft_id) is None

    second_repo = SQLWorkflowRepository(f"sqlite:///{tmp_path / 'second.db'}")
    second = seed_generated_draft(second_repo)
    second_repo.store_generated_case_draft(second)
    with second_repo._engine.begin() as connection:
        connection.execute(text("DELETE FROM projects WHERE project_id = 'project-1'"))
        assert connection.scalar(text("SELECT count(*) FROM generated_case_drafts")) == 0


@pytest.mark.parametrize(
    ("field_name", "oversized"),
    [
        pytest.param("draft_json", "x" * (9 * 1024 * 1024 + 1), id="draft-json"),
        pytest.param("preview_json", "x" * (1024 * 1024 + 1), id="preview-json"),
        pytest.param("archive", b"x" * (16 * 1024 * 1024 + 1), id="archive"),
        pytest.param("draft_id", "x" * 129, id="draft-id"),
        pytest.param("provider", "x" * 33, id="provider"),
        pytest.param("model", "x" * 129, id="model"),
    ],
)
def test_generated_case_draft_rejects_oversized_boundaries_before_store(
    tmp_path, field_name, oversized
) -> None:
    repo = repository(tmp_path)
    draft = seed_generated_draft(repo)

    with pytest.raises(ValueError, match=field_name):
        replace(draft, **{field_name: oversized})


@pytest.mark.parametrize(
    ("column", "oversized", "message"),
    [
        pytest.param(
            "draft_json", "x" * (9 * 1024 * 1024 + 1), "draft_json", id="draft-json"
        ),
        pytest.param(
            "preview_json", "x" * (1024 * 1024 + 1), "preview_json", id="preview-json"
        ),
        pytest.param(
            "archive", b"x" * (16 * 1024 * 1024 + 1), "archive", id="archive"
        ),
    ],
)
def test_load_generated_case_draft_rejects_oversized_row_before_validation(
    tmp_path, column, oversized, message, monkeypatch
) -> None:
    database_path = tmp_path / "workflow.db"
    repo = repository(tmp_path)
    draft = seed_generated_draft(repo)
    repo.store_generated_case_draft(draft)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            f"UPDATE generated_case_drafts SET {column} = ? WHERE draft_id = ?",
            (oversized, draft.draft_id),
        )
    monkeypatch.setattr(
        "fluid_scientist.adapters.sql_repository.validate_custom_case_archive",
        lambda *_args, **_kwargs: pytest.fail("expensive validation must not run"),
    )

    with pytest.raises(GeneratedCaseDraftIntegrityError, match=message):
        repo.load_generated_case_draft(draft.draft_id)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("draft_json", "{}", "draft_json"),
        ("project_id", "wrong-project", "project"),
        ("plan_version", 1, "version"),
        ("archive_sha256", f"sha256:{'0' * 64}", "digest"),
        ("archive", b"corrupt", "digest"),
        ("preview_json", "NaN", "preview_json"),
        ("preview_json", "[]", "preview_json"),
        ("created_at", "not-a-time", "created_at"),
        ("provider", "", "metadata"),
    ],
)
def test_load_generated_case_draft_rejects_corrupt_rows(
    tmp_path, column, value, message
) -> None:
    database_path = tmp_path / "workflow.db"
    repo = repository(tmp_path)
    draft = seed_generated_draft(repo)
    repo.store_generated_case_draft(draft)
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            f"UPDATE generated_case_drafts SET {column} = ? WHERE draft_id = ?",
            (value, draft.draft_id),
        )

    with pytest.raises(GeneratedCaseDraftIntegrityError, match=message):
        repo.load_generated_case_draft(draft.draft_id)


def test_complete_planning_operation_atomically_stores_plan_and_success(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    original = operation()
    created = repo.create_operation(original)
    plan = StoredExperimentPlan(
        plan_id="plan-1",
        project_id="project-1",
        version=1,
        provider="glm",
        model="glm-5.1",
        plan_json='{"accepted":true}',
    )
    succeeded = original.model_copy(
        update={
            "state": OperationState.SUCCEEDED,
            "stage": OperationStage.COMPLETE,
            "message": "实验计划已生成",
            "result_ref": plan.plan_id,
            "updated_at": original.updated_at + timedelta(seconds=1),
        }
    )

    stored = repo.complete_planning_operation(plan, succeeded, expected_version=created.version)

    assert stored.record == succeeded
    assert repo.load_operation(original.operation_id) == stored
    assert repo.load_experiment_plan(plan.plan_id) == plan


def test_complete_planning_operation_stale_version_leaves_no_orphan_plan(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    original = operation()
    repo.create_operation(original)
    cancelled = original.model_copy(
        update={
            "state": OperationState.CANCELLED,
            "stage": OperationStage.COMPLETE,
            "cancel_requested": True,
            "updated_at": original.updated_at + timedelta(seconds=1),
        }
    )
    repo.update_operation(cancelled, expected_version=1)
    plan = StoredExperimentPlan(
        plan_id="stale-plan",
        project_id="project-1",
        version=1,
        provider="glm",
        model="glm-5.1",
        plan_json='{"stale":true}',
    )
    stale_success = original.model_copy(
        update={
            "state": OperationState.SUCCEEDED,
            "stage": OperationStage.COMPLETE,
            "result_ref": plan.plan_id,
            "updated_at": original.updated_at + timedelta(seconds=2),
        }
    )

    with pytest.raises(ConcurrentUpdateError):
        repo.complete_planning_operation(plan, stale_success, expected_version=1)

    assert repo.load_experiment_plan(plan.plan_id) is None
    assert repo.load_operation(original.operation_id).record == cancelled


def test_operation_update_rejects_nonexistent_operation(tmp_path) -> None:
    repo = repository(tmp_path)

    with pytest.raises(ConcurrentUpdateError, match="does not exist"):
        repo.update_operation(operation(), expected_version=1)


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
    repo.save_snapshot("project-1", "{}", expected_version=0)
    repo.save_snapshot("project-2", "{}", expected_version=0)
    original = operation()
    repo.create_operation(original)
    changed = original.model_copy(update={field: value})

    with pytest.raises(OperationConflict, match="identity"):
        repo.update_operation(changed, expected_version=1)


def test_operation_update_rejects_created_at_change_without_corrupting_row(
    tmp_path,
) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    original = operation()
    stored = repo.create_operation(original)
    changed = original.model_copy(update={"created_at": original.created_at - timedelta(days=1)})

    with pytest.raises(OperationConflict, match="created_at"):
        repo.update_operation(changed, expected_version=1)

    assert repo.load_operation(original.operation_id) == stored


def test_find_operation_returns_exact_match_or_none(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    record = operation()
    stored = repo.create_operation(record)

    assert repo.find_operation(record.kind, record.project_id, record.input_digest) == stored
    assert repo.find_operation(record.kind, record.project_id, f"sha256:{'b' * 64}") is None


def test_list_interrupted_operations_filters_and_orders_records(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
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
    repo.save_snapshot("project-1", "{}", expected_version=0)
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


def test_load_operation_rejects_persisted_type_coercion(tmp_path) -> None:
    database_path = tmp_path / "workflow.db"
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    repo.create_operation(operation())
    with sqlite3.connect(database_path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT record_json FROM operations WHERE operation_id = ?",
                ("operation-1",),
            ).fetchone()[0]
        )
        payload["cancel_requested"] = "false"
        connection.execute(
            "UPDATE operations SET record_json = ? WHERE operation_id = ?",
            (json.dumps(payload), "operation-1"),
        )

    with pytest.raises(ValidationError, match="cancel_requested"):
        repo.load_operation("operation-1")


def test_load_operation_rejects_coercive_attempt(tmp_path) -> None:
    database_path = tmp_path / "workflow.db"
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    repo.create_operation(operation())
    with sqlite3.connect(database_path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT record_json FROM operations WHERE operation_id = ?",
                ("operation-1",),
            ).fetchone()[0]
        )
        payload["attempt"] = "1"
        connection.execute(
            "UPDATE operations SET record_json = ? WHERE operation_id = ?",
            (json.dumps(payload), "operation-1"),
        )

    with pytest.raises(ValidationError, match="attempt"):
        repo.load_operation("operation-1")


def test_load_legacy_operation_without_attempt_defaults_to_first_attempt(tmp_path) -> None:
    database_path = tmp_path / "workflow.db"
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    repo.create_operation(operation())
    with sqlite3.connect(database_path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT record_json FROM operations WHERE operation_id = ?",
                ("operation-1",),
            ).fetchone()[0]
        )
        payload.pop("attempt")
        connection.execute(
            "UPDATE operations SET record_json = ? WHERE operation_id = ?",
            (json.dumps(payload), "operation-1"),
        )

    restored = repo.load_operation("operation-1")

    assert restored is not None
    assert restored.record.attempt == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_id", "operation-corrupt"),
        ("kind", "case_generation"),
        ("project_id", "project-corrupt"),
        ("input_digest", f"sha256:{'b' * 64}"),
        ("created_at", "2026-06-30T00:00:00Z"),
        ("updated_at", "2026-07-02T00:00:00Z"),
    ],
)
def test_load_operation_rejects_json_row_mismatch(tmp_path, field, value) -> None:
    database_path = tmp_path / "workflow.db"
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", "{}", expected_version=0)
    repo.create_operation(operation())
    with sqlite3.connect(database_path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT record_json FROM operations WHERE operation_id = ?",
                ("operation-1",),
            ).fetchone()[0]
        )
        payload[field] = value
        connection.execute(
            "UPDATE operations SET record_json = ? WHERE operation_id = ?",
            (json.dumps(payload), "operation-1"),
        )

    with pytest.raises(OperationIntegrityError, match=f"{field}.*mismatch"):
        repo.load_operation("operation-1")
