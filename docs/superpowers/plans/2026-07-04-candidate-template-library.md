# Candidate OpenFOAM Template Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a successful, user-selected generated Case into a versioned candidate, prove deterministic regeneration and bounded parameter behavior, require human publication approval, and make published templates reusable by future plans.

**Architecture:** A separate persisted candidate registry owns lifecycle and evidence; it never mutates the immutable built-in registry. Candidate files use a constrained placeholder language rendered by trusted code. Published candidates are exposed as capabilities, while future plans still use `custom_openfoam` plus a typed template ID and bounded parameters.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy, deterministic OpenFOAM archive validation, vanilla JavaScript, pytest.

---

## File structure

- Create `src/fluid_scientist/template_library/models.py`: candidate states, parameter schema, evidence, and public capability view.
- Create `src/fluid_scientist/template_library/extraction.py`: copy a successful draft into a candidate spec without rewriting files.
- Create `src/fluid_scientist/services/template_candidates.py`: lifecycle transitions and publication rules.
- Modify `src/fluid_scientist/db.py`, `ports.py`, and `adapters/sql_repository.py`: durable candidate/version/evidence storage.
- Modify `src/fluid_scientist/experiment_planning/models.py`: published template reference in custom plans.
- Modify `src/fluid_scientist/experiment_planning/registry.py`: merge immutable built-ins with published capabilities.
- Modify `src/fluid_scientist/api/app.py`: candidate creation, validation, approval, publication, and listing.
- Modify `apps/web/app.js`, `index.html`, and `styles.css`: post-pilot candidacy and library review.
- Add tests under `tests/template_library`, `tests/services`, `tests/api`, and web assets.

### Task 1: Candidate and parameter contracts

**Files:**
- Create: `src/fluid_scientist/template_library/__init__.py`
- Create: `src/fluid_scientist/template_library/models.py`
- Test: `tests/template_library/test_models.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_candidate_state_order_is_closed_and_publication_is_not_default() -> None:
    candidate = TemplateCandidate.new(
        candidate_id="candidate-1",
        project_id="project-1",
        plan_id="plan-1",
        draft_id="draft-1",
        source_digest="sha256:" + "a" * 64,
        name="Backward-facing step",
        parameter_schema=(),
    )
    assert candidate.state == CandidateState.DRAFT
    assert candidate.published_version is None


def test_parameter_schema_rejects_unbounded_or_duplicate_names() -> None:
    with pytest.raises(ValidationError):
        GeneratedCaseParameter(
            name="velocity",
            kind="float",
            unit="m/s",
            minimum=None,
            maximum=10.0,
            default=1.0,
            regression_values=(0.5, 2.0),
        )
    with pytest.raises(ValidationError):
        CandidateTemplateSpec(
            files=valid_template_files(),
            parameters=(valid_velocity_parameter(), valid_velocity_parameter()),
        )
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/template_library/test_models.py -q`

Expected: missing package import.

- [ ] **Step 3: Implement closed contracts**

Define states `DRAFT`, `STATIC_VALIDATED`, `PILOT_PASSED`, `CANDIDATE_APPROVED`, `REGRESSION_PASSED`, `PUBLISHED`, and `REJECTED`. Reuse `GeneratedCaseParameter` from the generated-case contract instead of creating a second parameter type. `CandidateTemplateSpec` contains safe template files, parameter definitions, requested outputs, source IDs/digest, and no commands or credentials. `TemplateEvidence` records mesh/solver/determinism/regression outcomes and artifact references.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/template_library/test_models.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/template_library tests/template_library/test_models.py
git commit -m "feat: define governed template candidates"
```

### Task 2: Candidate extraction and rendering reuse

**Files:**
- Create: `src/fluid_scientist/template_library/extraction.py`
- Test: `tests/template_library/test_extraction.py`

- [ ] **Step 1: Write failing rendering tests**

```python
def test_renderer_substitutes_only_declared_scalar_placeholders() -> None:
    draft = generated_draft(content="value {{ velocity_m_s }};")
    spec = extract_candidate_spec(draft)
    rendered = render_candidate_spec(spec, {"velocity_m_s": 0.5})
    assert rendered.files_by_path["0/U"] == "value 0.5;"


@pytest.mark.parametrize(
    "content",
    [
        "{{ __class__ }}",
        "{{ velocity_m_s | shell }}",
        "{% for x in values %}{{ x }}{% endfor %}",
        "{{ missing_parameter }}",
    ],
)
def test_renderer_rejects_expressions_filters_control_flow_and_unknowns(content: str) -> None:
    with pytest.raises(TemplateRenderRejected):
        render_candidate_spec(
            extract_candidate_spec(generated_draft(content=content)),
            {"velocity_m_s": 0.5},
        )


def test_candidate_extraction_preserves_source_files_and_parameter_schema() -> None:
    draft = generated_draft(content="value {{ velocity_m_s }};")
    spec = extract_candidate_spec(draft)
    assert spec.files == draft.files
    assert spec.parameters == draft.parameters
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/template_library/test_extraction.py -q`

Expected: missing renderer import.

- [ ] **Step 3: Extract without model rewriting and reuse the trusted renderer**

`extract_candidate_spec()` copies the immutable draft files, parameters, outputs, assumptions, limitations, source IDs, and digest exactly; it does not call a model or rewrite template content. `render_candidate_spec()` delegates to `case_generation.rendering.render_generated_case`, then calls `validate_generated_case`. Map generated-case render rejections to `TemplateRenderRejected`. Do not add Jinja, Python evaluation, filters, loops, includes, or a second placeholder implementation.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/template_library/test_extraction.py tests/case_generation/test_validation.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/template_library/extraction.py tests/template_library/test_extraction.py
git commit -m "feat: extract candidate specs from verified drafts"
```

### Task 3: Durable candidate versions and evidence

**Files:**
- Modify: `src/fluid_scientist/db.py`
- Modify: `src/fluid_scientist/ports.py`
- Modify: `src/fluid_scientist/adapters/sql_repository.py`
- Test: `tests/adapters/test_sql_repository.py`

- [ ] **Step 1: Write failing repository tests**

```python
def test_candidate_versions_are_immutable_and_publication_is_atomic(tmp_path) -> None:
    repo = repository(tmp_path)
    seed_project_plan_and_draft(repo)
    candidate = stored_candidate(version=1, state="DRAFT")
    assert repo.store_template_candidate(candidate) == candidate
    with pytest.raises(ExperimentArtifactConflict):
        repo.store_template_candidate(replace(candidate, spec_json="{}"))
    published = repo.publish_template_candidate(
        candidate.candidate_id,
        expected_version=1,
        approved_by="researcher",
    )
    assert published.state == "PUBLISHED"
    assert published.version == 2
```

Import `replace` from `dataclasses` in this test module.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/adapters/test_sql_repository.py -q`

Expected: missing candidate repository methods.

- [ ] **Step 3: Add candidate and evidence rows**

Persist immutable candidate versions keyed by `(candidate_id, version)`, a current-state row using optimistic versioning, and append-only evidence rows. Foreign-key project, plan, and draft identities. Store spec/evidence JSON and artifact digests; do not store API keys, host details, private paths, or raw model responses. Publication updates current state and version in one transaction.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/adapters/test_sql_repository.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/db.py src/fluid_scientist/ports.py src/fluid_scientist/adapters/sql_repository.py tests/adapters/test_sql_repository.py
git commit -m "feat: persist candidate templates and evidence"
```

### Task 4: Candidate lifecycle service

**Files:**
- Create: `src/fluid_scientist/services/template_candidates.py`
- Test: `tests/services/test_template_candidates.py`

- [ ] **Step 1: Write failing transition tests**

```python
def test_candidate_requires_verified_pilot_before_creation(service, project) -> None:
    project = seed_project(state="PILOT_RUNNING")
    with pytest.raises(CandidateTransitionError, match="PILOT_VERIFIED"):
        service.create_from_draft(project.project_id, "draft-1", actor="researcher")


def test_publication_requires_determinism_regression_and_human_approval(service) -> None:
    candidate = service.create_from_draft(
        "project-1",
        "draft-1",
        actor="researcher",
    )
    service.record_static_validation(candidate.candidate_id, passed=True, evidence_ref="e1")
    service.record_pilot(candidate.candidate_id, passed=True, evidence_ref="e2")
    with pytest.raises(PublishBlocked):
        service.publish(candidate.candidate_id, actor="researcher")
    service.approve(candidate.candidate_id, actor="researcher")
    service.record_regression(candidate.candidate_id, passed=True, deterministic=True, evidence_ref="e3")
    assert service.publish(candidate.candidate_id, actor="researcher").state == "PUBLISHED"
```

Seed `project-1` as `PILOT_VERIFIED` and bind `draft-1` to its successful generated-case pilot before calling `create_from_draft`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/services/test_template_candidates.py -q`

Expected: missing service import.

- [ ] **Step 3: Implement explicit transitions**

Every method loads the current candidate, verifies the exact allowed source state, appends typed evidence/audit records, and saves a new version. `record_regression` requires at least two bounded parameter points for parameterized templates, successful archive validation at every point, matching digests for repeated identical inputs, mesh pass, solver completion, and result collection. `publish` requires state `REGRESSION_PASSED` plus a recorded human candidate approval.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/services/test_template_candidates.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/services/template_candidates.py tests/services/test_template_candidates.py
git commit -m "feat: govern candidate template lifecycle"
```

### Task 5: Candidate APIs and post-pilot user decision

**Files:**
- Modify: `src/fluid_scientist/api/app.py`
- Test: `tests/api/test_app.py`
- Test: `tests/api/test_execution_targets.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_candidate_creation_is_offered_only_after_verified_generated_pilot(client) -> None:
    project, draft = seed_generated_pilot(client, state="PILOT_VERIFIED")
    created = client.post(
        f"/api/projects/{project['project_id']}/generated-case-drafts/{draft['draft_id']}/template-candidates",
        json={"actor": "researcher"},
    )
    assert created.status_code == 201
    assert created.json()["state"] == "DRAFT"


def test_candidate_cannot_publish_before_regression_evidence(client) -> None:
    candidate = seed_draft_candidate(client)
    response = client.post(
        f"/api/template-candidates/{candidate['candidate_id']}/publish",
        json={"actor": "researcher", "expected_version": candidate["version"]},
    )
    assert response.status_code == 409
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/api/test_app.py tests/api/test_execution_targets.py -q`

Expected: candidate endpoints return 404.

- [ ] **Step 3: Add lifecycle APIs**

Add create, get, list, approve, reject, run-regression, and publish endpoints. Return 409 for invalid transitions, 422 for invalid specs, and 404 for unknown identities. Regression execution uses the operation infrastructure and fixed worker protocol; it never accepts caller-supplied commands or remote paths. Candidate listing separates unpublished and published records.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/api/test_app.py tests/api/test_execution_targets.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/api/app.py tests/api/test_app.py tests/api/test_execution_targets.py
git commit -m "feat: expose candidate template governance APIs"
```

### Task 6: Published template planning and compilation

**Files:**
- Modify: `src/fluid_scientist/experiment_planning/models.py`
- Modify: `src/fluid_scientist/experiment_planning/registry.py`
- Create: `src/fluid_scientist/template_library/compiler.py`
- Modify: `src/fluid_scientist/api/app.py`
- Test: `tests/experiment_planning/test_models.py`
- Test: `tests/experiment_planning/test_registry.py`
- Test: `tests/template_library/test_compiler.py`

- [ ] **Step 1: Write failing reuse tests**

```python
def test_custom_plan_may_reference_published_template_with_typed_parameters() -> None:
    payload = custom_plan()
    payload["case"] = {
        **payload["case"],
        "template_id": "tpl-backward-step-v1",
        "parameters": {"velocity_m_s": 0.5},
    }
    plan = ExperimentPlan.model_validate(payload).root
    assert plan.case.template_id == "tpl-backward-step-v1"


def test_published_template_compiler_rejects_unknown_parameter(repo) -> None:
    compiler = PublishedTemplateCompiler(repo)
    with pytest.raises(TemplateRenderRejected):
        compiler.compile("tpl-backward-step-v1", {"unknown": 1.0})
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/experiment_planning/test_models.py tests/experiment_planning/test_registry.py tests/template_library/test_compiler.py -q`

Expected: custom plan rejects `template_id` and compiler import is missing.

- [ ] **Step 3: Integrate published candidates without mutating built-ins**

Add optional `template_id` and strict scalar `parameters` to `CustomOpenFOAMCase`; require both together or neither. `PublishedTemplateCompiler` loads only a `PUBLISHED` candidate/version, validates exact parameters, renders files, validates/package bytes, and returns the same `CompiledExperiment` contract as built-in compilers. API capability listing merges immutable built-ins with published candidate capability views. Planning prompts receive published IDs and parameter schemas, but no raw template files.

At compile time, route a custom plan with `template_id` to `PublishedTemplateCompiler`; a custom plan without it still routes to generated-case/manual-upload review.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/experiment_planning tests/template_library/test_compiler.py -q`

Expected: all pass.

Commit:

```bash
git add src/fluid_scientist/experiment_planning src/fluid_scientist/template_library/compiler.py src/fluid_scientist/api/app.py tests/experiment_planning tests/template_library/test_compiler.py
git commit -m "feat: reuse published generated templates"
```

### Task 7: Candidate library UI

**Files:**
- Modify: `apps/web/app.js`
- Modify: `apps/web/index.html`
- Modify: `apps/web/styles.css`
- Test: `tests/api/test_web_assets.py`

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_successful_generated_pilot_offers_candidate_not_automatic_publication() -> None:
    html = read_asset("apps/web/index.html")
    script = read_asset("apps/web/app.js")
    assert 'id="template-library"' in html
    assert "加入候选模板库" in script
    assert "发布为可复用模板" in script
    assert "自动发布" not in script
    assert "/template-candidates" in script
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/api/test_web_assets.py -q`

Expected: template library UI is absent.

- [ ] **Step 3: Implement governed candidate UI**

After a verified generated pilot, show **Add to candidate template library** and **Do not save**. Candidate detail displays source digest, OpenFOAM version, validation evidence, parameter schema, regression points, lifecycle state, and audit history. Enable final publish only in `REGRESSION_PASSED`; require an explicit confirmation dialog naming the candidate version. Published templates appear in a separate reusable section and never expose model credentials or private infrastructure data.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/api/test_web_assets.py -q`

Run: `node --check apps/web/app.js`

Expected: all pass.

Commit:

```bash
git add apps/web/app.js apps/web/index.html apps/web/styles.css tests/api/test_web_assets.py
git commit -m "feat: review and publish candidate templates"
```

### Task 8: Full regression and live publication acceptance

**Files:**
- Modify: `docs/acceptance/2026-07-04-candidate-template-library.md`
- Modify: `skills/fluid-research-workflow/SKILL.md`
- Modify: `skills/fluid-research-workflow/references/workflow.md`

- [ ] **Step 1: Run complete verification**

Run: `python -m pytest -q`

Run: `python -m ruff check .`

Run: `node --check apps/web/app.js && node --check apps/web/operation-state.js && node --check apps/web/postprocess.js`

Run: `$env:PYTHONUTF8='1'; python C:\Users\baoxu\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\fluid-research-workflow`

Expected: all checks pass with only documented local OpenFOAM skips.

- [ ] **Step 2: Live end-to-end publication and reuse**

When the workstation is online, select a previously successful generated pilot, create a candidate, define bounded parameters and two regression points, confirm repeated identical inputs produce the same archive digest, execute both points through the fixed worker, approve publication, then create a new natural-language plan that selects the published template. Verify the new plan compiles and submits exact approved bytes without calling the Case Builder again.

- [ ] **Step 3: Record evidence, update Skill, and commit**

Document candidate/version IDs, digests, regression evidence, human approvals, reuse plan/job IDs, limitations, test counts, and rollback procedure. Redact credentials, hosts, user names, and private paths. Update the workflow Skill with the candidate lifecycle and deterministic reuse boundary.

Commit:

```bash
git add docs/acceptance/2026-07-04-candidate-template-library.md skills/fluid-research-workflow
git commit -m "docs: accept candidate template publication and reuse"
```
