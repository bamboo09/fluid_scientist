# Generated OpenFOAM Case Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a separately configured Case Builder model author a restricted OpenFOAM 13 case for unsupported experiments, then require deterministic validation, digest-bound human approval, and an isolated worker pilot before execution is trusted.

**Architecture:** The Case Builder returns a strict file-manifest contract rather than an archive or commands. Trusted code validates paths and dictionary content, packages deterministic bytes, reuses the existing compiled-artifact/Gate 2 binding, and submits through the existing idempotent `submit_custom` worker path. Case generation uses the persisted operation infrastructure from the responsive-operations plan.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, OpenAI-compatible provider adapters, deterministic tar/gzip packaging, existing `fluid-worker`, SQLAlchemy, pytest.

---

## File structure

- Create `src/fluid_scientist/case_generation/models.py`: strict generated-file and draft contracts.
- Create `src/fluid_scientist/case_generation/providers.py`: OpenAI/GLM/DeepSeek Case Builder adapters.
- Create `src/fluid_scientist/case_generation/rendering.py`: constrained scalar placeholder rendering.
- Create `src/fluid_scientist/case_generation/validation.py`: path/content allow-list and deterministic packaging.
- Create `src/fluid_scientist/services/case_generation.py`: operation lifecycle and immutable draft storage.
- Modify `src/fluid_scientist/services/model_configuration.py`: separate in-memory Case Builder snapshot.
- Modify `src/fluid_scientist/db.py`, `ports.py`, and `adapters/sql_repository.py`: draft persistence.
- Modify `src/fluid_scientist/api/app.py`: configuration, generation, review, approval, and submission APIs.
- Modify `apps/web/app.js`, `index.html`, and `styles.css`: unsupported-template generation and file review flow.
- Add tests under `tests/case_generation`, `tests/services`, `tests/api`, `tests/worker`, and web asset tests.

### Task 1: Strict generated-case draft schema

**Files:**
- Create: `src/fluid_scientist/case_generation/__init__.py`
- Create: `src/fluid_scientist/case_generation/models.py`
- Test: `tests/case_generation/test_models.py`

- [ ] **Step 1: Write failing contract tests**

```python
def valid_draft_payload() -> dict[str, object]:
    return {
        "experiment_name": "Backward-facing step study",
        "objective": "Resolve reattachment length for a laminar step flow.",
        "solver": "incompressibleFluid",
        "preprocessing": ["blockMesh", "checkMesh"],
        "parameters": [],
        "files": [
            {"path": "0/U", "content": "FoamFile { class volVectorField; }"},
            {"path": "0/p", "content": "FoamFile { class volScalarField; }"},
            {"path": "constant/physicalProperties", "content": "nu 1e-5;"},
            {"path": "system/controlDict", "content": "solver incompressibleFluid;"},
            {"path": "system/fvSchemes", "content": "ddtSchemes { default steadyState; }"},
            {"path": "system/fvSolution", "content": "solvers {}"},
            {"path": "system/blockMeshDict", "content": "vertices ();"},
        ],
        "requested_outputs": ["reattachment_length", "residuals"],
        "assumptions": ["Two-dimensional incompressible flow"],
        "limitations": ["Pilot resolution is not grid independent"],
    }


def test_generated_case_contract_rejects_commands_and_extra_fields() -> None:
    payload = valid_draft_payload() | {"command": "foamRun"}
    with pytest.raises(ValidationError, match="Extra inputs"):
        GeneratedCaseDraft.model_validate(payload)


def test_generated_case_contract_bounds_file_count_and_content() -> None:
    payload = valid_draft_payload()
    payload["files"] = [{"path": "system/controlDict", "content": "x" * 1_000_001}]
    with pytest.raises(ValidationError):
        GeneratedCaseDraft.model_validate(payload)


def test_generated_case_parameter_requires_bounds_default_and_regression_values() -> None:
    parameter = GeneratedCaseParameter(
        name="inlet_velocity_m_s",
        kind="float",
        unit="m/s",
        minimum=0.1,
        maximum=2.0,
        default=0.5,
        regression_values=(0.25, 1.0),
    )
    assert parameter.minimum <= parameter.default <= parameter.maximum
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/case_generation/test_models.py -q`

Expected: import failure for the new package.

- [ ] **Step 3: Implement strict Pydantic models**

Define `GeneratedCaseFile(path, content)`, `GeneratedCaseParameter`, `GeneratedCaseDraft`, and `GeneratedCaseDraftView`. Use `extra="forbid"`, at most 64 files, at most 1 MB per file and 8 MB total, approved solver literal `incompressibleFluid`, approved preprocessing literals, non-empty assumptions/limitations, and lower-snake-case requested output and parameter names. Parameters support only bounded integer, finite float, or enum-string values; require a default and at least two bounded regression values when present. The model contains no command, remote path, binary payload, API key, or archive field.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/case_generation/test_models.py -q`

Expected: all contract tests pass.

Commit:

```bash
git add src/fluid_scientist/case_generation tests/case_generation/test_models.py
git commit -m "feat: define generated case draft contract"
```

### Task 2: Path and dictionary safety validator

**Files:**
- Create: `src/fluid_scientist/case_generation/rendering.py`
- Create: `src/fluid_scientist/case_generation/validation.py`
- Test: `tests/case_generation/test_validation.py`

- [ ] **Step 1: Write a parameterized RED test**

```python
@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("../system/controlDict", "solver incompressibleFluid;"),
        ("run.sh", "#!/bin/sh"),
        ("system/controlDict", "#codeStream { code execute; }") ,
        ("system/controlDict", 'libs ("libCustom.so");'),
        ("system/controlDict", '#include "/etc/passwd"'),
        ("system/controlDict", "systemCall touch_owned;"),
    ],
)
def test_generated_case_rejects_unsafe_members(path: str, content: str) -> None:
    draft = GeneratedCaseDraft.model_validate(
        valid_draft_payload_with_file(path=path, content=content)
    )
    with pytest.raises(GeneratedCaseRejected):
        validate_generated_case(draft)


def test_generated_case_renders_only_declared_scalar_placeholders() -> None:
    draft = parameterized_draft(content="value {{ inlet_velocity_m_s }};")
    rendered = render_generated_case(draft, {"inlet_velocity_m_s": 0.5})
    assert rendered.files_by_path["0/U"] == "value 0.5;"


def test_generated_case_rejects_expression_placeholders() -> None:
    draft = parameterized_draft(content="{{ inlet_velocity_m_s | shell }}")
    with pytest.raises(GeneratedCaseRejected):
        render_generated_case(draft, {"inlet_velocity_m_s": 0.5})
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/case_generation/test_validation.py -q`

Expected: missing validator import.

- [ ] **Step 3: Implement two-stage validation and deterministic packaging**

Normalize POSIX paths and allow only files below `0/`, `constant/`, `system/`, and `fluidScientist/`. Reject traversal, absolute paths, duplicate normalized paths, links, NUL/control bytes, non-UTF-8 content, `#codeStream`, unsafe `#include`, `dynamicCode`, `libs`, `systemCall`, and unsupported solver text. Require the same mandatory dictionaries as `validate_custom_case_archive`.

In `rendering.py`, recognize only `{{ lower_snake_case }}` placeholders declared by the draft. Reject filters, expressions, loops, unknown names, missing values, and out-of-bounds values. Render the default parameter set before the pilot; the exact rendered files and digest—not unresolved template text—become the approved archive.

Return `ValidatedGeneratedCase` with normalized members, required preprocessing, archive bytes, and `sha256:` digest. Reuse deterministic tar metadata: sorted members, uid/gid zero, empty owner names, mode 0644, mtime zero, and gzip mtime zero. Pass the produced bytes through `validate_custom_case_archive` before returning.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/case_generation/test_validation.py tests/adapters/test_custom_openfoam.py -q`

Expected: all tests pass.

Commit:

```bash
git add src/fluid_scientist/case_generation/rendering.py src/fluid_scientist/case_generation/validation.py tests/case_generation/test_validation.py
git commit -m "feat: validate and package generated cases"
```

### Task 3: Case Builder provider adapters

**Files:**
- Create: `src/fluid_scientist/case_generation/providers.py`
- Modify: `src/fluid_scientist/services/model_configuration.py`
- Test: `tests/case_generation/test_providers.py`
- Test: `tests/services/test_model_configuration.py`

- [ ] **Step 1: Write failing provider tests**

```python
def test_case_builder_requests_only_strict_file_manifest() -> None:
    client = FakeClient([json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(), client=client)
    draft = builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",))
    request = client.completions.calls[0]
    assert draft.solver == "incompressibleFluid"
    assert "shell commands" in request["messages"][0]["content"]
    assert "remote paths" in request["messages"][0]["content"]


def test_case_builder_schema_failure_gets_one_sanitized_correction() -> None:
    invalid = valid_draft_payload() | {"command": "never-print-this"}
    client = FakeClient([json.dumps(invalid), json.dumps(valid_draft_payload())])
    builder = OpenAICompatibleCaseBuilder(settings(max_retries=1), client=client)
    assert builder.generate_case(custom_plan(), capabilities=("OpenFOAM-13",)).files
    retry = json.dumps(client.completions.calls[1]["messages"])
    assert "command" in retry
    assert "never-print-this" not in retry
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/case_generation/test_providers.py -q`

Expected: missing provider implementation.

- [ ] **Step 3: Implement provider-neutral Case Builder**

Define a `CaseBuilder` protocol and OpenAI-native/OpenAI-compatible implementations following the existing planning-provider classification: authentication and model errors are terminal, timeouts are bounded, malformed JSON is rejected, and schema-only errors receive sanitized correction feedback. Accept the same optional progress callback, emitting `case_model` before provider calls and `schema_correction` before a retry. The system prompt includes the full `GeneratedCaseDraft` JSON Schema, the accepted custom plan, Foundation 13 semantics, the allow-listed file roots, constrained scalar placeholder syntax, and explicit prohibitions on scripts, dynamic code, commands, and remote paths.

Add a separate immutable `CaseBuilderConfiguration` to application state with provider, model, and builder. It must not reuse or overwrite the planning model snapshot. Credentials remain memory-only.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/case_generation/test_providers.py tests/services/test_model_configuration.py -q`

Expected: all tests pass.

Commit:

```bash
git add src/fluid_scientist/case_generation/providers.py src/fluid_scientist/services/model_configuration.py tests/case_generation tests/services/test_model_configuration.py
git commit -m "feat: add separately configured case builder"
```

### Task 4: Immutable generated-draft persistence

**Files:**
- Modify: `src/fluid_scientist/db.py`
- Modify: `src/fluid_scientist/ports.py`
- Modify: `src/fluid_scientist/adapters/sql_repository.py`
- Test: `tests/adapters/test_sql_repository.py`

- [ ] **Step 1: Write failing persistence tests**

```python
def test_generated_draft_is_immutable_and_bound_to_plan_version(tmp_path) -> None:
    repo = repository(tmp_path)
    seed_project_and_plan(repo)
    draft = StoredGeneratedCaseDraft(
        draft_id="draft-1",
        project_id="project-1",
        plan_id="plan-1",
        plan_version=1,
        version=1,
        provider="glm",
        model="glm-5.1",
        draft_json=valid_draft().model_dump_json(),
        archive_sha256="sha256:" + "a" * 64,
        archive=b"archive",
    )
    assert repo.store_generated_case_draft(draft) == draft
    with pytest.raises(ExperimentArtifactConflict):
        repo.store_generated_case_draft(replace(draft, archive=b"changed"))
```

Import `replace` from `dataclasses` in this test module.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/adapters/test_sql_repository.py -q`

Expected: missing draft persistence methods.

- [ ] **Step 3: Add draft row and repository contract**

Create `GeneratedCaseDraftRow` with immutable draft JSON and archive bytes, foreign keys to project and plan, unique `(plan_id, plan_version, version)`, provider/model audit fields, and creation time. Add store/load methods to `WorkflowRepository` and `SQLWorkflowRepository`. Reject any replacement with different bytes or metadata.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/adapters/test_sql_repository.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/db.py src/fluid_scientist/ports.py src/fluid_scientist/adapters/sql_repository.py tests/adapters/test_sql_repository.py
git commit -m "feat: persist immutable generated case drafts"
```

### Task 5: Case-generation operations and APIs

**Files:**
- Create: `src/fluid_scientist/services/case_generation.py`
- Modify: `src/fluid_scientist/api/app.py`
- Test: `tests/services/test_case_generation.py`
- Test: `tests/api/test_app.py`

- [ ] **Step 1: Write failing service and API tests**

```python
def test_custom_plan_can_start_case_generation_operation(client) -> None:
    project, plan = seed_custom_plan(client)
    response = client.post(
        f"/api/projects/{project['project_id']}/experiment-plans/{plan['plan_id']}/case-generation-operations"
    )
    assert response.status_code == 202
    assert response.json()["kind"] == "case_generation"


def test_builtin_plan_cannot_start_case_builder(client) -> None:
    project, plan = seed_pipe_plan(client)
    response = client.post(
        f"/api/projects/{project['project_id']}/experiment-plans/{plan['plan_id']}/case-generation-operations"
    )
    assert response.status_code == 409
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/services/test_case_generation.py tests/api/test_app.py -q`

Expected: endpoint 404.

- [ ] **Step 3: Implement generation lifecycle**

Add memory-only Case Builder configuration GET/POST endpoints. Add the generation endpoint above and `GET /api/generated-case-drafts/{draft_id}`. The operation stages are `case_model`, `schema_correction`, `static_validation`, `deterministic_packaging`, `ready_for_review`. Require plan ownership, plan version 1, and `custom_openfoam`; compute the idempotency digest from plan ID/version plus Case Builder provider/model. Store the validated immutable draft and set the operation result reference to its draft ID.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/services/test_case_generation.py tests/api/test_app.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/services/case_generation.py src/fluid_scientist/api/app.py tests/services/test_case_generation.py tests/api/test_app.py
git commit -m "feat: generate reviewed OpenFOAM case drafts"
```

### Task 6: Digest-bound draft approval and isolated pilot

**Files:**
- Modify: `src/fluid_scientist/services/projects.py`
- Modify: `src/fluid_scientist/api/app.py`
- Modify: `src/fluid_scientist/worker/service.py`
- Test: `tests/api/test_execution_targets.py`
- Test: `tests/worker/test_service.py`

- [ ] **Step 1: Write failing approval/submission tests**

```python
def test_generated_draft_must_match_approved_digest_before_submission(client) -> None:
    project, plan, draft = seed_valid_generated_draft(client)
    approve_gate_one_and_prepare_gate_two(client, project)
    rejected = client.post(
        f"/api/projects/{project['project_id']}/generated-case-drafts/{draft['draft_id']}/approve",
        json={"actor": "researcher", "archive_sha256": "sha256:" + "f" * 64},
    )
    assert rejected.status_code == 409


def test_worker_revalidates_generated_archive_before_launch(tmp_path) -> None:
    service = worker_service(tmp_path)
    unsafe = archive_with("system/controlDict", "#codeStream {}")
    with pytest.raises(CustomCaseRejected):
        service.submit_custom("job-1", store_incoming(tmp_path, unsafe))
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/api/test_execution_targets.py tests/worker/test_service.py -q`

Expected: generated-draft approval endpoint is missing.

- [ ] **Step 3: Bind draft bytes into the existing trusted submission path**

Approval loads the immutable draft, recomputes its digest, and stores `StoredCompiledExperiment` with `plan_id`, `plan_version`, `archive_sha256`, exact `archive` bytes, and a generated-case `preview_json`. It then invokes existing Gate 2 approval with that exact plan/version/digest. Submission uses the existing planned-experiment endpoint and deterministic case/job IDs; it must never invoke the Case Builder again. Keep worker double validation and add explicit regression cases for generated-case forbidden directives.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/api/test_execution_targets.py tests/worker/test_service.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/services/projects.py src/fluid_scientist/api/app.py src/fluid_scientist/worker/service.py tests/api/test_execution_targets.py tests/worker/test_service.py
git commit -m "feat: approve and pilot exact generated cases"
```

### Task 7: Generated-case review UI

**Files:**
- Modify: `apps/web/app.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/styles.css`
- Test: `tests/api/test_web_assets.py`

- [ ] **Step 1: Write failing asset tests**

```python
def test_custom_plan_offers_model_case_generation_before_manual_upload() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")
    assert 'id="case-builder-settings"' in html
    assert "case-generation-operations" in script
    assert "生成候选 Case" in script
    assert "审核并运行隔离 Pilot" in script
    assert "上传已有 Case" in script
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/api/test_web_assets.py -q`

Expected: missing Case Builder settings and action text.

- [ ] **Step 3: Implement the review flow**

For `custom_openfoam`, make **Generate candidate Case** the primary action and retain manual archive upload as a secondary advanced path. Render Case Builder provider/model, operation stages, file list, parameter schema/defaults/regression values, key dictionary previews, assumptions, limitations, validation results, and digest. Require an explicit checkbox acknowledging model-authored files before enabling **Review and run isolated Pilot**. Never render raw credentials, host paths, or an editable remote command field.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/api/test_web_assets.py -q`

Run: `node --check apps/web/app.js`

Expected: all pass.

Commit:

```bash
git add apps/web/app.js apps/web/index.html apps/web/styles.css tests/api/test_web_assets.py
git commit -m "feat: review model-generated cases before pilot"
```

### Task 8: End-to-end verification

**Files:**
- Modify: `docs/acceptance/2026-07-04-generated-case-pilot.md`
- Modify: `skills/fluid-research-workflow/SKILL.md`
- Modify: `skills/fluid-research-workflow/references/workflow.md`

- [ ] **Step 1: Run the full suite and skill validation**

Run: `python -m pytest -q`

Run: `python -m ruff check .`

Run: `node --check apps/web/app.js && node --check apps/web/operation-state.js && node --check apps/web/postprocess.js`

Run: `$env:PYTHONUTF8='1'; python C:\Users\baoxu\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\fluid-research-workflow`

Expected: all pass, with only documented local OpenFOAM skips.

- [ ] **Step 2: Run a safe live acceptance when the workstation is online**

Use an unsupported but bounded laminar geometry. Confirm the Case Builder operation returns immediately, static validation passes, the UI displays exact files and digest, approval binds the same digest, worker validation repeats, and the remote pilot returns a real job ID/PID, mesh result, solver completion, time directories, and `.foam` marker. Do not claim physical credibility from the short pilot.

- [ ] **Step 3: Update the workflow Skill and acceptance evidence**

Record the generated-case trust boundary, double validation, exact digest, user approval, and explicit failure behavior without recording providers' keys, host addresses, user names, or private paths.

Commit:

```bash
git add docs/acceptance/2026-07-04-generated-case-pilot.md skills/fluid-research-workflow
git commit -m "docs: accept governed generated case pilots"
```
