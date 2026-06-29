# Fake Vertical Research Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Build a runnable, tested single-user platform slice that turns a bend-flow request into an approved research workflow, fake Slurm/OpenFOAM results, deterministic validation, Results Analyst output, and an evidence-linked report without requiring external credentials.

**Architecture:** Keep the scientific core independent from FastAPI and infrastructure. Pydantic domain models cross ports; a deterministic workflow service owns transitions and audit events; fake adapters satisfy the same protocols later used by OpenAI, SSH/Slurm, object storage, and OpenFOAM. A small FastAPI app and static web workbench expose the slice, while Docker Compose declares production-shaped dependencies.

**Tech Stack:** Python 3.11+, Pydantic 2, FastAPI, pytest, PyYAML, NumPy, SciPy, vanilla HTML/CSS/JavaScript, Docker Compose.

---

## File map

- `pyproject.toml`: package metadata, runtime/dev dependencies, pytest and Ruff configuration.
- `src/fluid_scientist/domain/models.py`: immutable research, evidence, experiment, case, validation, analysis, report and audit schemas.
- `src/fluid_scientist/physics/calculations.py`: deterministic fluid calculations.
- `src/fluid_scientist/physics/rules.py`: versioned YAML rule loading and evaluation.
- `src/fluid_scientist/orchestration/workflow.py`: allowed workflow transitions, approvals, persistence contract and audit.
- `src/fluid_scientist/ports.py`: LLM, simulator, job scheduler, artifact store and repository protocols.
- `src/fluid_scientist/adapters/fakes.py`: deterministic adapters used by tests and demo mode.
- `src/fluid_scientist/execution/hpc.py`: safe data-node and Slurm command/value builders.
- `src/fluid_scientist/validation/core.py`: convergence, mass balance and three-grid GCI.
- `src/fluid_scientist/analysis/core.py`: deterministic summary statistics and effect estimates.
- `src/fluid_scientist/services/research.py`: vertical-slice application service.
- `src/fluid_scientist/services/skill_candidates.py`: redacted candidate Skill packages and approval state.
- `src/fluid_scientist/api/app.py`: FastAPI endpoints and demo dependency wiring.
- `apps/web/index.html`, `apps/web/styles.css`, `apps/web/app.js`: project-stage workbench.
- `skills/fluid-research-workflow/`: tested repository Skill and bundled references.
- `infra/compose.yaml`, `.env.example`: local service topology and safe configuration template.
- `tests/`: unit, state-machine, adapter, API, security, end-to-end and Skill scenario tests.

### Task 1: Project scaffold and import contract

**Files:**
- Create: `pyproject.toml`
- Create: `src/fluid_scientist/__init__.py`
- Create: `tests/test_package.py`

- [x] **Step 1: Write the failing import/version test**

```python
def test_package_exposes_version():
    import fluid_scientist
    assert fluid_scientist.__version__ == "0.1.0"
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_package.py -v`  
Expected: FAIL because `fluid_scientist` is not importable.

- [x] **Step 3: Add package metadata and version**

Use a `src` layout, Python `>=3.11`, runtime dependencies `pydantic>=2.7`, `fastapi>=0.115`, `uvicorn>=0.30`, `PyYAML>=6`, `numpy>=2`, `scipy>=1.13`; dev dependencies `pytest>=8`, `httpx>=0.27`, `ruff>=0.6`. Set `__version__ = "0.1.0"`.

- [x] **Step 4: Install editable dev dependencies and verify**

Run: `python -m pip install -e ".[dev]"` then `python -m pytest tests/test_package.py -v`  
Expected: PASS.

- [x] **Step 5: Commit**

Run: `git add pyproject.toml src/fluid_scientist/__init__.py tests/test_package.py && git commit -m "build: scaffold fluid scientist package"`.

### Task 2: Strict domain schemas

**Files:**
- Create: `src/fluid_scientist/domain/__init__.py`
- Create: `src/fluid_scientist/domain/models.py`
- Create: `tests/domain/test_models.py`

- [x] **Step 1: Write failing tests for normalized ResearchSpec and immutable CaseManifest**

```python
def test_research_spec_rejects_nonpositive_diameter():
    with pytest.raises(ValidationError):
        ResearchSpec(question="bend", geometry=GeometrySpec(type="bend_90", diameter_m=0), fluid=FluidSpec())

def test_case_manifest_is_frozen():
    case = valid_case_manifest()
    with pytest.raises(ValidationError):
        case.solver = "other"
```

- [x] **Step 2: Run tests and observe missing models**

Run: `python -m pytest tests/domain/test_models.py -v`  
Expected: collection error for missing module/classes.

- [x] **Step 3: Implement focused Pydantic models**

Define strict models and enums for `GeometrySpec`, `FluidSpec`, `VariableRange`, `SimulationBudget`, `ResearchSpec`, `EvidenceItem`, `EvidencePackage`, `ExperimentPlan`, `CaseManifest`, `ValidationResult`, `AnalysisResult`, `EvidenceLinkedClaim`, `ResearchReport`, `Approval`, and `AuditEvent`. Reject extra fields; freeze manifests, approvals, evidence and audit events.

- [x] **Step 4: Run model tests**

Run: `python -m pytest tests/domain/test_models.py -v`  
Expected: PASS.

- [x] **Step 5: Commit**

Run: `git add src/fluid_scientist/domain tests/domain && git commit -m "feat: add strict research domain schemas"`.

### Task 3: Deterministic physics and validation

**Files:**
- Create: `src/fluid_scientist/physics/__init__.py`
- Create: `src/fluid_scientist/physics/calculations.py`
- Create: `src/fluid_scientist/physics/rules.py`
- Create: `src/fluid_scientist/physics/default_rules.yaml`
- Create: `src/fluid_scientist/validation/__init__.py`
- Create: `src/fluid_scientist/validation/core.py`
- Create: `tests/physics/test_calculations.py`
- Create: `tests/physics/test_rules.py`
- Create: `tests/validation/test_core.py`

- [x] **Step 1: Write failing numerical tests**

```python
def test_reynolds_number_is_deterministic():
    assert reynolds_number(rho=998.2, velocity=2.0, diameter=0.2, mu=1.002e-3) == pytest.approx(398483.03, rel=1e-6)

def test_mass_imbalance_uses_absolute_reference():
    assert mass_imbalance_percent(10.0, -9.99) == pytest.approx(0.1)

def test_gci_rejects_non_monotonic_grid_sizes():
    with pytest.raises(ValueError, match="strictly decrease"):
        grid_convergence_index([1000, 900, 1200], [1.0, 0.9, 0.8])
```

- [x] **Step 2: Run tests and observe missing functions**

Run: `python -m pytest tests/physics tests/validation -v`  
Expected: collection errors.

- [x] **Step 3: Implement calculations, rules and validation**

Implement SI-only `reynolds_number`, `dean_number`, `area`, `velocity_from_reynolds`, `mass_imbalance_percent`, residual/monitor convergence checks and three-grid GCI. Load YAML rules with `id`, `severity`, conditions and deterministic checks; return structured violations without executing code from YAML.

- [x] **Step 4: Run tests**

Run: `python -m pytest tests/physics tests/validation -v`  
Expected: PASS.

- [x] **Step 5: Commit**

Run: `git add src/fluid_scientist/physics src/fluid_scientist/validation tests/physics tests/validation && git commit -m "feat: add deterministic physics validation"`.

### Task 4: Audited workflow and three approval gates

**Files:**
- Create: `src/fluid_scientist/orchestration/__init__.py`
- Create: `src/fluid_scientist/orchestration/workflow.py`
- Create: `tests/orchestration/test_workflow.py`

- [x] **Step 1: Write failing transition tests**

```python
def test_pilot_cannot_submit_without_gate_two():
    workflow = workflow_at("PILOT_READY")
    with pytest.raises(TransitionError, match="GATE_2"):
        workflow.transition("SUBMIT_PILOT")

def test_replayed_transition_is_idempotent():
    workflow = workflow_at("PILOT_RUNNING")
    first = workflow.record_external_job("case-1", "123")
    second = workflow.record_external_job("case-1", "123")
    assert first == second
    assert len(workflow.state.external_jobs) == 1
```

- [x] **Step 2: Run tests and observe failure**

Run: `python -m pytest tests/orchestration/test_workflow.py -v`  
Expected: missing workflow module.

- [x] **Step 3: Implement explicit state/action table**

Define states from `CREATED` through `REPORTED`, actions, gate requirements, terminal failures, maximum revision counters, immutable audit events and JSON snapshot serialization. Reject undeclared transitions and mismatched external job replay.

- [x] **Step 4: Run tests**

Run: `python -m pytest tests/orchestration/test_workflow.py -v`  
Expected: PASS.

- [x] **Step 5: Commit**

Run: `git add src/fluid_scientist/orchestration tests/orchestration && git commit -m "feat: add audited research workflow"`.

### Task 5: Ports, fake adapters and vertical service

**Files:**
- Create: `src/fluid_scientist/ports.py`
- Create: `src/fluid_scientist/adapters/__init__.py`
- Create: `src/fluid_scientist/adapters/fakes.py`
- Create: `src/fluid_scientist/analysis/__init__.py`
- Create: `src/fluid_scientist/analysis/core.py`
- Create: `src/fluid_scientist/services/__init__.py`
- Create: `src/fluid_scientist/services/research.py`
- Create: `tests/services/test_research.py`

- [x] **Step 1: Write failing end-to-end service test**

```python
def test_demo_research_produces_evidence_linked_report():
    result = build_demo_service().run_approved_demo("curvature and Reynolds effects")
    assert result.workflow_state == "REPORTED"
    assert result.validation.mass_conservation_passed is True
    assert result.report.claims
    assert all(claim.evidence_ids for claim in result.report.claims)
```

- [x] **Step 2: Run and observe missing service**

Run: `python -m pytest tests/services/test_research.py -v`  
Expected: collection error.

- [x] **Step 3: Implement ports and deterministic fakes**

Define Protocols for `LLMProvider`, `EvidenceRetriever`, `SimulatorAdapter`, `JobScheduler`, `ArtifactStore`, and `WorkflowRepository`. Fake adapters return fixed but physically plausible three-grid bend results and structured analyst/reviewer claims. `ResearchService` advances the workflow only through declared transitions and uses deterministic analysis before model explanation.

- [x] **Step 4: Run service test**

Run: `python -m pytest tests/services/test_research.py -v`  
Expected: PASS.

- [x] **Step 5: Commit**

Run: `git add src/fluid_scientist/ports.py src/fluid_scientist/adapters src/fluid_scientist/analysis src/fluid_scientist/services tests/services && git commit -m "feat: run fake research vertical slice"`.

### Task 6: Safe HPC value builders

**Files:**
- Create: `src/fluid_scientist/execution/__init__.py`
- Create: `src/fluid_scientist/execution/hpc.py`
- Create: `tests/execution/test_hpc.py`
- Create: `tests/security/test_hpc_injection.py`

- [x] **Step 1: Write failing security tests**

```python
@pytest.mark.parametrize("value", ["job; rm -rf /", "../outside", "$(curl bad)", "name\n#SBATCH --uid=0"])
def test_slurm_values_reject_control_and_shell_syntax(value):
    with pytest.raises(UnsafeValueError):
        SafeSlurmValue(value)
```

- [x] **Step 2: Run and observe failure**

Run: `python -m pytest tests/execution tests/security -v`  
Expected: missing execution module.

- [x] **Step 3: Implement allowlisted builders**

Create validated value objects for remote relative paths, job names, partitions, module names and resource counts. Render `sbatch` scripts only from typed values and a fixed command enum (`blockMesh`, `snappyHexMesh`, `checkMesh`, `simpleFoam`, `postProcess`). Expose argv arrays for `sbatch`, `squeue`, `sacct`, and `scancel`; never concatenate user shell text.

- [x] **Step 4: Run tests**

Run: `python -m pytest tests/execution tests/security -v`  
Expected: PASS.

- [x] **Step 5: Commit**

Run: `git add src/fluid_scientist/execution tests/execution tests/security && git commit -m "feat: add safe Slurm command builders"`.

### Task 7: FastAPI and web workbench

**Files:**
- Create: `src/fluid_scientist/api/__init__.py`
- Create: `src/fluid_scientist/api/app.py`
- Create: `apps/web/index.html`
- Create: `apps/web/styles.css`
- Create: `apps/web/app.js`
- Create: `tests/api/test_app.py`

- [x] **Step 1: Write failing API tests**

```python
def test_demo_endpoint_returns_reported_project(client):
    response = client.post("/api/demo", json={"question": "bend pressure drop"})
    assert response.status_code == 201
    body = response.json()
    assert body["workflow_state"] == "REPORTED"
    assert body["report"]["claims"]

def test_static_workbench_does_not_expose_skill_navigation(client):
    html = client.get("/").text
    assert "实验结果分析与报告" in html
    assert "Skill 候选" not in html
```

- [x] **Step 2: Run and observe missing API**

Run: `python -m pytest tests/api/test_app.py -v`  
Expected: collection error.

- [x] **Step 3: Implement API and workbench**

Expose `/health`, `POST /api/demo`, `GET /api/projects/{id}`, and static files. The workbench shows stage progression, Gate actions, HPC state, credibility snapshot, evidence coverage and a combined results-analysis/report view. Do not display Skill operations.

- [x] **Step 4: Run API tests and a local smoke request**

Run: `python -m pytest tests/api/test_app.py -v`  
Expected: PASS. Then run `python -m uvicorn fluid_scientist.api.app:app --host 127.0.0.1 --port 8000` and verify `GET /health` returns `{"status":"ok"}`.

- [x] **Step 5: Commit**

Run: `git add src/fluid_scientist/api apps/web tests/api && git commit -m "feat: add research API and workbench"`.

### Task 8: Background Skill candidate pipeline

**Files:**
- Create: `src/fluid_scientist/services/skill_candidates.py`
- Create: `skills/fluid-research-workflow/SKILL.md`
- Create: `skills/fluid-research-workflow/agents/openai.yaml`
- Create: `skills/fluid-research-workflow/references/workflow.md`
- Create: `tests/skills/test_candidate_pipeline.py`
- Create: `tests/skills/scenarios/no-skill-baseline.md`

- [x] **Step 1: Record a failing baseline and write pipeline tests**

The baseline fixture records an unskilled response that submits all cases before Pilot and treats solver completion as scientific validity.

```python
def test_candidate_redacts_environment_details():
    candidate = extractor.extract(event_with(host="login.internal", path="/home/alice/case", secret="sk-live"))
    serialized = candidate.model_dump_json()
    assert "login.internal" not in serialized
    assert "/home/alice" not in serialized
    assert "sk-live" not in serialized

def test_candidate_cannot_publish_without_red_green_evidence():
    with pytest.raises(PublishBlocked):
        publisher.publish(candidate_without_tests())
```

- [x] **Step 2: Run and observe failure**

Run: `python -m pytest tests/skills -v`  
Expected: missing service and fixtures.

- [x] **Step 3: Implement minimal tested Skill and candidate states**

Create `DRAFT → RED_RECORDED → GREEN_PASSED → APPROVED → PUBLISHED`. Redact secrets, hostnames, usernames and absolute paths. The repository Skill requires ResearchSpec, Pilot, deterministic validation, evidence-linked claims and human gates; keep detailed schemas in `references/workflow.md`.

- [x] **Step 4: Validate Skill and tests**

Run the skill creator `quick_validate.py` against `skills/fluid-research-workflow`, then `python -m pytest tests/skills -v`.  
Expected: validator success and all tests PASS.

- [x] **Step 5: Commit**

Run: `git add src/fluid_scientist/services/skill_candidates.py skills tests/skills && git commit -m "feat: add governed skill candidate pipeline"`.

### Task 9: Production-shaped configuration and CI

**Files:**
- Create: `.env.example`
- Create: `infra/compose.yaml`
- Create: `.github/workflows/ci.yml`
- Create: `README.md`
- Create: `tests/test_config_safety.py`

- [x] **Step 1: Write failing safety test**

```python
def test_example_environment_contains_no_real_secrets():
    text = Path(".env.example").read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "BEGIN OPENSSH PRIVATE KEY" not in text
    assert "login.internal" not in text
```

- [x] **Step 2: Run and observe missing configuration**

Run: `python -m pytest tests/test_config_safety.py -v`  
Expected: FAIL because `.env.example` is absent.

- [x] **Step 3: Add local infrastructure, CI and operator documentation**

Compose defines PostgreSQL, Redis, Qdrant and MinIO with health checks and named volumes. `.env.example` uses explicit empty/default-safe values. CI installs `.[dev]`, runs Ruff and pytest, and never invokes real OpenAI or HPC. README documents fake demo, service layout, three-node HPC contract, real-integration prerequisites and security boundaries.

- [x] **Step 4: Run full local verification**

Run: `python -m ruff check .` and `python -m pytest -q`  
Expected: no lint errors and all tests PASS.

- [x] **Step 5: Commit**

Run: `git add .env.example infra .github README.md tests/test_config_safety.py && git commit -m "chore: add infrastructure and CI"`.

### Task 10: Final end-to-end verification and push

**Files:**
- Modify: `docs/superpowers/plans/2026-06-29-fake-vertical-slice.md`

- [x] **Step 1: Run the complete quality gate**

Run: `python -m ruff check .`  
Expected: PASS.

Run: `python -m pytest -q`  
Expected: PASS with no skipped Fake-mode tests.

- [x] **Step 2: Verify repository safety**

Run: `git grep -n -E "(sk-[A-Za-z0-9_-]{16,}|BEGIN OPENSSH PRIVATE KEY|login\.internal|/home/alice)" -- ':!docs/superpowers/plans/*' ':!tests/skills/scenarios/*'`  
Expected: no output.

- [x] **Step 3: Verify demo API**

Start Uvicorn, call `POST /api/demo`, and confirm the response contains `REPORTED`, a passing mass-conservation result, deterministic analysis, evidence-linked claims and reviewer approval.

- [x] **Step 4: Record plan completion and commit**

Check completed steps in this plan and commit with `git commit -am "docs: record fake vertical slice completion"`.

- [x] **Step 5: Push**

Run: `git push origin main`  
Expected: local `main` and `origin/main` point to the same commit.
