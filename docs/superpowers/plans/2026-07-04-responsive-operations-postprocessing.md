# Responsive Operations and Browser Postprocessing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make planning acknowledge immediately, survive refresh without duplicate model calls, remain independent of target reachability, and make browser postprocessing visibly load and reveal structured results.

**Architecture:** Add a persisted operation contract and a specialized planning-operation service backed by the existing SQL repository and a bounded thread executor. The web client creates and polls operations, while a focused postprocessing module owns fetch, reveal, focus, and rendering behavior. Target health is checked for submission, not for plan creation.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy, `concurrent.futures`, vanilla ES modules, pytest, Node syntax tests.

---

## File structure

- Create `src/fluid_scientist/operations/models.py`: typed operation states and API-safe views.
- Create `src/fluid_scientist/services/planning_operations.py`: idempotent background planning lifecycle.
- Create `src/fluid_scientist/services/target_capabilities.py`: short-lived target health cache with explicit age.
- Modify `src/fluid_scientist/db.py`: persisted operation row.
- Modify `src/fluid_scientist/ports.py`: stored-operation repository contract.
- Modify `src/fluid_scientist/adapters/sql_repository.py`: atomic operation persistence and idempotency lookup.
- Modify `src/fluid_scientist/api/app.py`: 202 create/status/cancel endpoints and target-independent planning.
- Create `apps/web/operation-state.js`: pure operation-to-view state mapping.
- Create `apps/web/postprocess.js`: browser postprocessing controller and renderer.
- Modify `apps/web/app.js`: operation polling, progress card, recovery, and event wiring.
- Modify `apps/web/index.html` and `apps/web/styles.css`: accessible operation and postprocessing states.
- Add focused tests under `tests/operations`, `tests/services`, `tests/api`, and `tests/api/test_web_assets.py`.

### Task 1: Persisted operation contract

**Files:**
- Create: `src/fluid_scientist/operations/__init__.py`
- Create: `src/fluid_scientist/operations/models.py`
- Test: `tests/operations/test_models.py`

- [ ] **Step 1: Write failing model tests**

```python
from fluid_scientist.operations.models import (
    OperationKind,
    OperationRecord,
    OperationStage,
    OperationState,
)


def test_operation_is_terminal_only_after_success_failure_or_cancel() -> None:
    running = OperationRecord.new(
        operation_id="op-1",
        kind=OperationKind.PLAN,
        project_id="project-1",
        input_digest="sha256:" + "a" * 64,
    ).model_copy(
        update={
            "state": OperationState.RUNNING,
            "stage": OperationStage.MODEL_PLANNING,
        }
    )
    assert running.terminal is False
    assert running.model_copy(update={"state": OperationState.SUCCEEDED}).terminal is True


def test_operation_never_serializes_provider_input_or_credentials() -> None:
    fields = OperationRecord.model_fields
    assert "api_key" not in fields
    assert "provider_payload" not in fields
    assert "question" not in fields
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `python -m pytest tests/operations/test_models.py -q`

Expected: collection fails because `fluid_scientist.operations.models` does not exist.

- [ ] **Step 3: Implement the closed operation models**

```python
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
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation_id: str
    kind: OperationKind
    project_id: str
    input_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    state: OperationState = OperationState.QUEUED
    stage: OperationStage = OperationStage.QUEUED
    message: str = "已进入队列"
    result_ref: str | None = None
    safe_error: str | None = None
    cancel_requested: bool = False
    created_at: datetime
    updated_at: datetime

    @property
    def terminal(self) -> bool:
        return self.state in {
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.CANCELLED,
        }
```

Implement `new()` with UTC timestamps and export the types from `operations/__init__.py`.

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/operations/test_models.py -q`

Expected: `2 passed`.

Commit:

```bash
git add src/fluid_scientist/operations tests/operations/test_models.py
git commit -m "feat: define persisted operation contract"
```

### Task 2: SQL operation persistence and idempotency

**Files:**
- Modify: `src/fluid_scientist/db.py`
- Modify: `src/fluid_scientist/ports.py`
- Modify: `src/fluid_scientist/adapters/sql_repository.py`
- Test: `tests/adapters/test_sql_repository.py`

- [ ] **Step 1: Write failing repository tests**

```python
def test_operation_create_is_idempotent_by_kind_project_and_digest(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{"name":"SPEC_READY"}', expected_version=0)
    operation = OperationRecord.new(
        operation_id="op-1",
        kind=OperationKind.PLAN,
        project_id="project-1",
        input_digest="sha256:" + "a" * 64,
    )
    assert repo.create_operation(operation).operation_id == "op-1"
    duplicate = operation.model_copy(update={"operation_id": "op-2"})
    assert repo.create_operation(duplicate).operation_id == "op-1"


def test_operation_update_uses_optimistic_version(tmp_path) -> None:
    repo = repository(tmp_path)
    repo.save_snapshot("project-1", '{"name":"SPEC_READY"}', expected_version=0)
    stored = repo.create_operation(
        OperationRecord.new(
            operation_id="op-1",
            kind=OperationKind.PLAN,
            project_id="project-1",
            input_digest="sha256:" + "a" * 64,
        )
    )
    updated = repo.update_operation(
        stored.record.model_copy(update={"state": OperationState.RUNNING}),
        expected_version=stored.version,
    )
    with pytest.raises(ConcurrentUpdateError):
        repo.update_operation(updated, expected_version=stored.version)
```

Use the concrete fields from Task 1 instead of the ellipsis when adding the test.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/adapters/test_sql_repository.py -q`

Expected: failures report missing operation repository methods.

- [ ] **Step 3: Add `OperationRow` and repository methods**

Add a unique constraint on `(kind, project_id, input_digest)`, store `record_json`, and maintain an integer version. Extend `WorkflowRepository` with:

```python
def create_operation(self, operation: OperationRecord) -> StoredOperation:
    raise NotImplementedError

def load_operation(self, operation_id: str) -> StoredOperation | None:
    raise NotImplementedError

def find_operation(
    self, kind: str, project_id: str, input_digest: str
) -> StoredOperation | None:
    raise NotImplementedError

def update_operation(
    self, operation: OperationRecord, *, expected_version: int
) -> StoredOperation:
    raise NotImplementedError

def list_interrupted_operations(self) -> Sequence[StoredOperation]:
    raise NotImplementedError
```

`StoredOperation` contains `record: OperationRecord` and `version: int`. Never persist question text, API keys, or raw provider output in this row.
Import `Sequence` from `collections.abc` in `ports.py` for the interrupted-operation return type.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/adapters/test_sql_repository.py -q`

Expected: all repository tests pass.

Commit:

```bash
git add src/fluid_scientist/db.py src/fluid_scientist/ports.py src/fluid_scientist/adapters/sql_repository.py tests/adapters/test_sql_repository.py
git commit -m "feat: persist idempotent operations"
```

### Task 3: Planning operation service

**Files:**
- Create: `src/fluid_scientist/services/planning_operations.py`
- Modify: `src/fluid_scientist/experiment_planning/providers.py`
- Test: `tests/services/test_planning_operations.py`
- Test: `tests/experiment_planning/test_providers.py`

- [ ] **Step 1: Write controlled-executor tests**

```python
def test_submit_returns_before_designer_runs_and_reuses_duplicate_request(repo) -> None:
    executor = ControlledExecutor()
    service = PlanningOperationService(repo, executor=executor)
    first = service.submit(project_id="project-1", question="Study cavity flow", model=model)
    second = service.submit(project_id="project-1", question="Study cavity flow", model=model)
    assert first.operation_id == second.operation_id
    assert first.state == "queued"
    assert model.calls == []
    executor.run_next()
    assert service.get(first.operation_id).state == "succeeded"


def test_cancelled_operation_discards_late_provider_result(repo) -> None:
    executor = ControlledExecutor()
    service = PlanningOperationService(repo, executor=executor)
    operation = service.submit(
        project_id="project-1",
        question="Study cavity flow",
        model=model,
    )
    service.cancel(operation.operation_id)
    executor.run_next()
    assert service.get(operation.operation_id).state == "cancelled"
    assert service.get(operation.operation_id).result_ref is None


def test_schema_retry_publishes_correction_progress() -> None:
    stages: list[str] = []
    client = FakeClient([invalid_plan_json(), valid_plan_json()])
    provider = OpenAICompatiblePlanProvider(settings(max_retries=1), client=client)
    provider.design_experiment(
        "Study cavity flow",
        capabilities=("lid_driven_cavity",),
        progress=stages.append,
    )
    assert stages == ["model_planning", "schema_correction", "model_planning"]
```

Define `ControlledExecutor.submit()` to retain callables and `run_next()` to execute one synchronously.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/services/test_planning_operations.py tests/experiment_planning/test_providers.py -q`

Expected: import failure for `PlanningOperationService`.

- [ ] **Step 3: Implement bounded background planning**

Extend the provider-neutral `ExperimentDesigner.design_experiment()` contract with an optional `progress: Callable[[str], None] | None` callback. Both native and compatible providers emit `model_planning` before each provider request and `schema_correction` before a bounded schema retry; fakes emit the same stages deterministically.

The service computes `sha256(normalized_question + provider + model)`, creates or reuses the persisted operation, and submits one closure to a `ThreadPoolExecutor(max_workers=2)`. The closure maps provider progress into persisted stages, then transitions through `storing_plan` and `succeeded`. It stores only the accepted `StoredExperimentPlan`; `result_ref` is its plan ID. Map provider exceptions to existing safe Chinese messages.

Implement `recover_interrupted()` to transition startup-time `queued` or `running` records to `failed` with `safe_error="服务重启中断了操作，可安全重试"`. Do not claim that an in-process callable resumed.
When `submit()` finds a failed or cancelled matching operation, transition that same operation ID back to `queued` with a new repository version and schedule one new callable; do not insert a row that violates the idempotency constraint.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/services/test_planning_operations.py tests/experiment_planning/test_providers.py -q`

Expected: all planning-operation tests pass.

Commit:

```bash
git add src/fluid_scientist/services/planning_operations.py src/fluid_scientist/experiment_planning/providers.py tests/services/test_planning_operations.py tests/experiment_planning/test_providers.py
git commit -m "feat: run planning as recoverable operations"
```

### Task 4: 202 planning-operation API and offline-target decoupling

**Files:**
- Create: `src/fluid_scientist/services/target_capabilities.py`
- Modify: `src/fluid_scientist/api/app.py`
- Test: `tests/api/test_app.py`
- Test: `tests/api/test_execution_targets.py`
- Test: `tests/services/test_target_capabilities.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_plan_operation_returns_202_before_model_completion(tmp_path) -> None:
    client, executor = operation_client(tmp_path)
    response = client.post(
        "/api/plan-operations",
        json={"project_id": "project-1", "question": "Study cavity flow", "target_id": "offline"},
    )
    assert response.status_code == 202
    assert response.json()["state"] == "queued"
    assert executor.pending == 1


def test_offline_target_does_not_block_planning_but_blocks_submission(tmp_path) -> None:
    client, executor = operation_client(tmp_path, target=UnavailableTarget())
    created = client.post(
        "/api/plan-operations",
        json={
            "project_id": "project-1",
            "question": "Study lid-driven cavity centerline velocity.",
            "target_id": "offline",
        },
    )
    assert created.status_code == 202
    executor.run_next()
    assert client.get(f"/api/operations/{created.json()['operation_id']}").json()["state"] == "succeeded"


def test_target_capability_cache_avoids_repeated_ssh_doctor_calls() -> None:
    target = CountingTarget()
    clock = FakeClock()
    cache = TargetCapabilityCache(ttl_seconds=30, clock=clock)
    cache.get(target)
    cache.get(target)
    assert target.doctor_calls == 1
    clock.advance(31)
    cache.get(target)
    assert target.doctor_calls == 2
```

Fill the request with the same concrete project/question/target values used by the fixture.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/api/test_app.py tests/api/test_execution_targets.py tests/services/test_target_capabilities.py -q`

Expected: 404 for `/api/plan-operations`.

- [ ] **Step 3: Add operation endpoints**

Add:

```python
POST /api/plan-operations -> 202 OperationView
GET /api/operations/{operation_id} -> OperationView
DELETE /api/operations/{operation_id} -> OperationView
```

Validate that `target_id` exists but do not call `doctor` during planning. Include declared target kind/ID in provider capabilities. Preserve synchronous `/api/experiment-plans` for compatibility but remove its target reachability check; mark it internal/deprecated in OpenAPI.

Implement `TargetCapabilityCache` with an injected monotonic clock, a 30-second TTL, and a response field `checked_at`. Cache both available and unavailable results for the TTL so repeated page loads do not repeat SSH timeouts. Submission bypasses stale cache and performs a fresh target check. Register executor shutdown in the FastAPI lifespan handler with `wait=False, cancel_futures=True`.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/api/test_app.py tests/api/test_execution_targets.py tests/services/test_target_capabilities.py -q`

Expected: all selected tests pass.

Commit:

```bash
git add src/fluid_scientist/services/target_capabilities.py src/fluid_scientist/api/app.py tests/api/test_app.py tests/api/test_execution_targets.py tests/services/test_target_capabilities.py
git commit -m "feat: expose asynchronous plan operations"
```

### Task 5: Frontend operation state and recovery

**Files:**
- Create: `apps/web/operation-state.js`
- Modify: `apps/web/app.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/styles.css`
- Test: `tests/api/test_web_assets.py`

- [ ] **Step 1: Write failing asset contract tests**

```python
def test_planning_uses_operation_endpoint_and_persists_operation_identity() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")
    state = read_asset("apps/web/operation-state.js")
    assert 'id="active-operation"' in html
    assert '"/api/plan-operations"' in script
    assert "storageKeys.operationId" in script
    assert "/api/operations/${operationId}" in script
    assert "elapsed" in state
    assert 'aria-busy' in html
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/api/test_web_assets.py -q`

Expected: missing operation asset and endpoint assertions fail.

- [ ] **Step 3: Implement operation UI**

Add a persistent operation card with stage, message, elapsed time, cancel button, and retry action. `designExperimentFromPrompt()` must render the question, call `/api/plan-operations`, persist only `operationId`, and start capped polling. On success, fetch `result_ref` through `/api/experiment-plans/{plan_id}` and render the review card. On refresh, restore and poll the operation before considering a new submission. Clear the elapsed interval on terminal state and unload. In `init()`, bind events first, load model configuration and restore the active operation without waiting for target `doctor`; load target capabilities independently and update the rail when that request completes.

Use `operation-state.js` for pure label/tone/progress mapping so Node-compatible source tests can cover every state without a browser.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/api/test_web_assets.py -q`

Run: `node --check apps/web/app.js && node --check apps/web/operation-state.js`

Expected: both commands pass.

Commit:

```bash
git add apps/web/operation-state.js apps/web/app.js apps/web/index.html apps/web/styles.css tests/api/test_web_assets.py
git commit -m "feat: show recoverable planning progress"
```

### Task 6: Browser postprocessing controller

**Files:**
- Create: `apps/web/postprocess.js`
- Modify: `apps/web/app.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/styles.css`
- Test: `tests/api/test_web_assets.py`

- [ ] **Step 1: Write failing postprocessing tests**

```python
def test_both_postprocess_buttons_use_one_reveal_controller() -> None:
    html = read_asset("apps/web/index.html")
    app = read_asset("apps/web/app.js")
    controller = read_asset("apps/web/postprocess.js")
    assert 'id="view-postprocess"' in html
    assert 'byId("view-postprocess")?.addEventListener' in app
    assert "revealPostprocess" in app
    for behavior in ("scrollIntoView", "focus", "aria-busy", "fetchResults"):
        assert behavior in controller
    assert "renderCavityCenterlineProfile" in controller
    assert "renderCylinderForceHistory" in controller
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/api/test_web_assets.py::test_both_postprocess_buttons_use_one_reveal_controller -q`

Expected: failure because the static button has no event binding.

- [ ] **Step 3: Implement fetch, reveal, focus, and rendering**

Export `revealPostprocess({root, button, results, fetchResults})`. It sets button and panel busy states, fetches only when `results` is absent, renders mesh metrics, residual table, numeric times, observables, and `.foam` marker, unhides the panel, sets `tabindex="-1"`, calls `focus({preventScroll: true})`, then `scrollIntoView({behavior: "smooth", block: "start"})`. On error, show a typed inline message and leave the button enabled for retry. Render accessible SVG plots only when matching numeric evidence exists: cavity centerline velocity profiles and cylinder drag/lift histories. Every SVG includes a title, axis labels, and a text fallback table; missing series produce an explicit “当前结果未包含该曲线” note rather than invented points.

Bind both `#view-postprocess` and dynamically created result buttons to this controller. Remove the unconditional `renderPostprocessResults(results)` call from `renderResultsCard`; the content appears only after the user action.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/api/test_web_assets.py -q`

Run: `node --check apps/web/app.js && node --check apps/web/postprocess.js`

Expected: all pass.

Commit:

```bash
git add apps/web/postprocess.js apps/web/app.js apps/web/index.html apps/web/styles.css tests/api/test_web_assets.py
git commit -m "fix: make browser postprocessing responsive"
```

### Task 7: Full verification and live acceptance

**Files:**
- Modify: `docs/acceptance/2026-07-04-responsive-operations.md`

- [ ] **Step 1: Run full automated verification**

Run: `python -m pytest -q`

Run: `python -m ruff check .`

Run: `node --check apps/web/app.js && node --check apps/web/workbench-state.js && node --check apps/web/operation-state.js && node --check apps/web/postprocess.js`

Expected: all tests and checks pass; only the documented local OpenFOAM skips remain.

- [ ] **Step 2: Restart and run live checks**

Configure GLM in memory, create a cavity plan operation while the workstation is offline, verify the initial response is 202 in under one second, observe stage polling, and confirm the accepted plan contains a `cells_per_side` sweep. Restore a retained completed job and click browser postprocessing; verify focus moves and structured data appears.

- [ ] **Step 3: Record evidence and commit**

Record operation ID, timestamps, accepted plan ID, postprocessing fields, test counts, and the explicit limitation that offline-target planning does not prove remote execution.

Commit:

```bash
git add docs/acceptance/2026-07-04-responsive-operations.md
git commit -m "docs: accept responsive planning and postprocessing"
```
