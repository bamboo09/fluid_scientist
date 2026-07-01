# Multi-model Experiment Compiler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect OpenAI, GLM, and DeepSeek experiment planning to deterministic pipe, cylinder, cavity, and custom OpenFOAM case execution.

**Architecture:** Provider adapters return one strict discriminated `ExperimentPlan`; they never generate case files or commands. A capability registry selects a deterministic compiler, produces an immutable archive, binds its digest to Gate 2, and submits it through the existing fixed workstation worker protocol.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic v2, OpenAI Python SDK, OpenFOAM Foundation 13, vanilla HTML/CSS/JavaScript, pytest, Ruff.

---

## File structure

- Create `src/fluid_scientist/experiment_planning/models.py`: provider-neutral plan contracts.
- Create `src/fluid_scientist/experiment_planning/providers.py`: provider protocol, GLM/DeepSeek JSON adapter, provider factory.
- Create `src/fluid_scientist/experiment_planning/registry.py`: capability records and compiler dispatch.
- Create `src/fluid_scientist/experiment_planning/compilers.py`: deterministic archive compilers.
- Modify `src/fluid_scientist/adapters/openai_provider.py`: return provider-neutral plans through Responses structured parsing.
- Modify `src/fluid_scientist/api/app.py`: provider-neutral configuration, plan, compile, and submission endpoints.
- Modify `src/fluid_scientist/services/projects.py`: bind plan version and artifact digest to Gate 2.
- Modify `apps/web/index.html`, `apps/web/app.js`, `apps/web/styles.css`: provider selector, plan review, compile preview, and generic submission.
- Modify `skills/fluid-research-workflow/`: record only verified reusable provider/compiler rules.

---

### Task 1: Provider-neutral experiment plan contracts

**Files:**
- Create: `src/fluid_scientist/experiment_planning/__init__.py`
- Create: `src/fluid_scientist/experiment_planning/models.py`
- Test: `tests/experiment_planning/test_models.py`

- [ ] **Step 1: Write failing discriminated-plan tests**

```python
def test_cylinder_plan_rejects_pipe_payload() -> None:
    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate({
            "experiment_name": "Cylinder Re100",
            "experiment_type": "cylinder_flow",
            "objective": "Measure vortex shedding behind a circular cylinder.",
            "assumptions": ["Two-dimensional incompressible flow"],
            "limitations": ["Laminar Reynolds-number range only"],
            "requested_outputs": ["drag_coefficient", "lift_coefficient"],
            "case": {"diameter_m": 0.02, "length_m": 2.0},
        })

def test_plan_rejects_unknown_output_and_extra_fields() -> None:
    payload = valid_pipe_plan()
    payload["unknown"] = "forbidden"
    with pytest.raises(ValidationError):
        ExperimentPlan.model_validate(payload)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/experiment_planning/test_models.py -q`  
Expected: import failure because `experiment_planning.models` does not exist.

- [ ] **Step 3: Implement strict plan models**

```python
class PlanBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_name: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=10)
    assumptions: tuple[str, ...] = Field(min_length=1)
    limitations: tuple[str, ...] = Field(min_length=1)
    requested_outputs: tuple[str, ...] = Field(min_length=1)

class CylinderFlowCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    diameter_m: float = Field(gt=0)
    domain_upstream_diameters: float = Field(default=10, ge=5, le=30)
    domain_downstream_diameters: float = Field(default=20, ge=10, le=60)
    reynolds_number: float = Field(gt=0, le=300)
    cells_radial: int = Field(default=40, ge=16, le=400)
    end_time_s: float = Field(gt=0)

class CylinderExperimentPlan(PlanBase):
    experiment_type: Literal["cylinder_flow"]
    case: CylinderFlowCase

ExperimentPlan = RootModel[
    Annotated[
        PipeExperimentPlan | CylinderExperimentPlan | CavityExperimentPlan |
        CustomExperimentPlan,
        Field(discriminator="experiment_type"),
    ]
]
```

Define equivalent bounded payloads for `laminar_pipe`, `lid_driven_cavity`, and `custom_openfoam`. Include SI units, mesh controls, convergence targets, and sweep definitions.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/experiment_planning/test_models.py -q`  
Expected: all plan schema tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fluid_scientist/experiment_planning tests/experiment_planning/test_models.py
git commit -m "feat: add provider-neutral experiment plans"
```

---

### Task 2: GLM and DeepSeek JSON providers

**Files:**
- Create: `src/fluid_scientist/experiment_planning/providers.py`
- Modify: `src/fluid_scientist/settings.py`
- Test: `tests/experiment_planning/test_providers.py`

- [ ] **Step 1: Write failing provider contract tests**

```python
@pytest.mark.parametrize(
    ("provider", "base_url"),
    [
        ("glm", "https://open.bigmodel.cn/api/paas/v4/"),
        ("deepseek", "https://api.deepseek.com"),
    ],
)
def test_chat_provider_validates_json_plan(provider, base_url) -> None:
    client = fake_chat_client(json.dumps(valid_cylinder_plan()))
    adapter = OpenAICompatiblePlanProvider(
        ProviderSettings(provider=provider, api_key=SecretStr("secret"), model="model"),
        client=client,
    )
    result = adapter.design_experiment("Study cylinder shedding", capabilities())
    assert result.root.experiment_type == "cylinder_flow"
    assert client.base_url == base_url

def test_deepseek_empty_output_retries_then_fails_without_leaking_key() -> None:
    adapter = provider_with_outputs("deepseek", ["", ""])
    with pytest.raises(ProviderOutputError, match="empty"):
        adapter.design_experiment("Study cylinder shedding", capabilities())
    assert "secret" not in repr(adapter)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/experiment_planning/test_providers.py -q`  
Expected: import failure for `OpenAICompatiblePlanProvider`.

- [ ] **Step 3: Implement provider settings and adapter**

```python
class ProviderSettings(ConfigModel):
    provider: Literal["openai", "glm", "deepseek"]
    api_key: SecretStr
    model: str = Field(min_length=1, max_length=128)
    max_retries: int = Field(default=2, ge=0, le=5)
    timeout_seconds: float = Field(default=120, gt=0, le=600)

PROVIDER_BASE_URLS = {
    "glm": "https://open.bigmodel.cn/api/paas/v4/",
    "deepseek": "https://api.deepseek.com",
}

class OpenAICompatiblePlanProvider:
    def design_experiment(self, question: str, *, capabilities: tuple[str, ...]):
        response = self._client.chat.completions.create(
            model=self._settings.model,
            messages=self._messages(question, capabilities),
            response_format={"type": "json_object"},
            stream=False,
        )
        content = response.choices[0].message.content
        if not content:
            raise ProviderOutputError("provider returned empty JSON content")
        return ExperimentPlan.model_validate_json(content)
```

Translate authentication, model-not-found, timeout, connection, empty-output, JSON decode, and Pydantic validation errors into typed errors. Retry only timeout/connection/empty-output errors up to the configured bound.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/experiment_planning/test_providers.py -q`  
Expected: GLM and DeepSeek provider tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fluid_scientist/settings.py src/fluid_scientist/experiment_planning/providers.py tests/experiment_planning/test_providers.py
git commit -m "feat: add GLM and DeepSeek plan providers"
```

---

### Task 3: OpenAI provider migration and factory

**Files:**
- Modify: `src/fluid_scientist/adapters/openai_provider.py`
- Modify: `src/fluid_scientist/experiment_planning/providers.py`
- Test: `tests/adapters/test_openai_provider.py`
- Test: `tests/experiment_planning/test_providers.py`

- [ ] **Step 1: Write failing OpenAI/factory tests**

```python
def test_provider_factory_selects_each_adapter_without_exposing_key() -> None:
    for name in ("openai", "glm", "deepseek"):
        provider = create_plan_provider(settings(name), client=fake_client(name))
        assert provider.provider_name == name
        assert "secret" not in repr(provider)

def test_openai_returns_provider_neutral_plan() -> None:
    provider = OpenAIResponsesProvider(settings(), client=responses_client(valid_pipe_plan()))
    assert provider.design_experiment("Validate pipe loss", capabilities()).root.experiment_type == "laminar_pipe"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/adapters/test_openai_provider.py tests/experiment_planning/test_providers.py -q`  
Expected: factory missing or return type mismatch.

- [ ] **Step 3: Migrate OpenAI and implement factory**

Use a Pydantic wrapper model as the Responses `text_format`; unwrap it to `ExperimentPlan`. Keep the current Results Analyst and reviewer methods unchanged. Implement:

```python
def create_plan_provider(settings: ProviderSettings, *, client=None) -> ExperimentDesigner:
    if settings.provider == "openai":
        return OpenAIResponsesProvider(settings.to_openai(), client=client)
    return OpenAICompatiblePlanProvider(settings, client=client)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/adapters/test_openai_provider.py tests/experiment_planning/test_providers.py -q`  
Expected: all three providers satisfy the same plan contract.

- [ ] **Step 5: Commit**

```bash
git add src/fluid_scientist/adapters/openai_provider.py src/fluid_scientist/experiment_planning/providers.py tests
git commit -m "refactor: unify experiment plan providers"
```

---

### Task 4: Provider-neutral model configuration and plan API

**Files:**
- Modify: `src/fluid_scientist/api/app.py`
- Test: `tests/api/test_app.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_configures_glm_without_echoing_key() -> None:
    response = client.post("/api/model-configurations", json={
        "provider": "glm", "model": "glm-5.1", "api_key": "secret"
    })
    assert response.json() == {"configured": True, "provider": "glm", "model": "glm-5.1"}
    assert "secret" not in response.text

def test_plan_endpoint_returns_provider_metadata_and_typed_plan() -> None:
    response = client.post("/api/experiment-plans", json={"question": "Study Re=100 cylinder shedding"})
    assert response.json()["provider"] == "glm"
    assert response.json()["plan"]["experiment_type"] == "cylinder_flow"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/api/test_app.py -q`  
Expected: new endpoints return 404.

- [ ] **Step 3: Implement endpoints and compatibility aliases**

Store `provider`, `model`, and provider instance in `application.state.model_configuration`. Implement `POST/GET /api/model-configurations`, `GET /api/experiment-capabilities`, and `POST /api/experiment-plans`. Preserve `/api/settings/openai` and `/api/experiment-designs` as adapters to the new service until the workbench migration is complete.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/api/test_app.py tests/api/test_execution_targets.py -q`  
Expected: provider-neutral and compatibility route tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fluid_scientist/api/app.py tests/api
git commit -m "feat: add provider-neutral planning API"
```

---

### Task 5: Capability registry and deterministic archive contract

**Files:**
- Create: `src/fluid_scientist/experiment_planning/registry.py`
- Create: `src/fluid_scientist/experiment_planning/compilers.py`
- Test: `tests/experiment_planning/test_registry.py`
- Test: `tests/experiment_planning/test_compilers.py`

- [ ] **Step 1: Write failing registry/compiler tests**

```python
def test_cylinder_plan_selects_cylinder_compiler() -> None:
    capability = registry.get("cylinder_flow")
    assert capability.solver == "incompressibleFluid"
    assert capability.preprocessing == ("blockMesh", "mirrorMesh", "checkMesh")

def test_identical_plan_compiles_to_identical_archive() -> None:
    first = compile_plan(valid_pipe_plan())
    second = compile_plan(valid_pipe_plan())
    assert first.archive_sha256 == second.archive_sha256
    assert first.archive == second.archive
    assert validate_custom_case_archive(first.archive).solver == "incompressibleFluid"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/experiment_planning/test_registry.py tests/experiment_planning/test_compilers.py -q`  
Expected: registry and compiler modules missing.

- [ ] **Step 3: Implement registry and deterministic tar creation**

```python
@dataclass(frozen=True)
class ExperimentCapability:
    experiment_type: str
    label: str
    solver: str
    preprocessing: tuple[str, ...]
    required_outputs: tuple[str, ...]
    compiler: Callable[[PlanBase], CompiledCase]

@dataclass(frozen=True)
class CompiledCase:
    archive: bytes
    archive_sha256: str
    manifest: CustomCaseManifest
```

Sort members, set tar metadata (`mtime=0`, uid/gid=0, empty owner names), use gzip `mtime=0`, and validate every compiled archive before returning it.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/experiment_planning/test_registry.py tests/experiment_planning/test_compilers.py -q`  
Expected: deterministic archive and registry tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fluid_scientist/experiment_planning tests/experiment_planning
git commit -m "feat: add experiment compiler registry"
```

---

### Task 6: Pipe, cylinder, and cavity compilers

**Files:**
- Modify: `src/fluid_scientist/experiment_planning/compilers.py`
- Test: `tests/experiment_planning/test_compilers.py`

- [ ] **Step 1: Add failing case-content tests**

```python
def test_cylinder_case_contains_force_outputs_and_mirror_mesh() -> None:
    archive = members(compile_plan(valid_cylinder_plan()).archive)
    assert "system/mirrorMeshDict" in archive
    assert "system/forceCoeffsIncompressible" in archive
    assert b"solver incompressibleFluid;" in archive["system/controlDict"]

def test_cavity_case_has_moving_lid_and_no_mirror_step() -> None:
    compiled = compile_plan(valid_cavity_plan())
    archive = members(compiled.archive)
    assert b"movingWall" in archive["0/U"]
    assert compiled.manifest.needs_mirror_mesh is False
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/experiment_planning/test_compilers.py -q`  
Expected: cylinder/cavity compiler not implemented.

- [ ] **Step 3: Implement Foundation 13 templates**

Render Foundation 13 dictionaries only. Cylinder includes `blockMeshDict`, `mirrorMeshDict`, forces, force coefficients, residuals, and write intervals. Cavity includes `blockMeshDict`, moving-lid `U`, pressure, probes, residuals, and write intervals. Reuse the existing pipe renderer through a compiler adapter.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/experiment_planning/test_compilers.py tests/adapters/test_custom_openfoam.py -q`  
Expected: all three built-in archives pass safety validation.

- [ ] **Step 5: Commit**

```bash
git add src/fluid_scientist/experiment_planning/compilers.py tests/experiment_planning/test_compilers.py
git commit -m "feat: compile built-in OpenFOAM experiments"
```

---

### Task 7: Compile preview, Gate 2 digest binding, and submission

**Files:**
- Modify: `src/fluid_scientist/api/app.py`
- Modify: `src/fluid_scientist/services/projects.py`
- Test: `tests/api/test_execution_targets.py`
- Test: `tests/services/test_projects.py`

- [ ] **Step 1: Write failing workflow tests**

```python
def test_gate_two_binds_plan_version_and_archive_digest() -> None:
    plan = create_cylinder_plan(client)
    preview = client.post(f"/api/experiment-plans/{plan['plan_id']}/compile").json()
    approved = approve_gate_two(client, plan_id=plan["plan_id"], digest=preview["archive_sha256"])
    assert approved["approved_artifacts"][plan["plan_id"]] == preview["archive_sha256"]

def test_submission_rejects_recompiled_digest_after_approval() -> None:
    response = submit_with_changed_plan_after_gate_two(client)
    assert response.status_code == 409
    assert "digest" in response.json()["detail"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/services/test_projects.py tests/api/test_execution_targets.py -q`  
Expected: plan compilation and digest binding APIs missing.

- [ ] **Step 3: Implement plan store and immutable binding**

Persist plans and compiled archives in the project repository. `POST /api/experiment-plans/{plan_id}/compile` returns manifest/preview metadata without exposing arbitrary server paths. Gate 2 records plan ID, plan version, and archive digest. Submission retrieves the bound archive and calls `submit_custom`; never recompiles after approval.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/services/test_projects.py tests/api/test_execution_targets.py -q`  
Expected: compile, approval, idempotency, and digest mismatch tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/fluid_scientist/api/app.py src/fluid_scientist/services/projects.py tests
git commit -m "feat: bind compiled experiments to Gate 2"
```

---

### Task 8: Provider and plan review workbench

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Modify: `apps/web/styles.css`
- Test: `tests/api/test_web_assets.py`

- [ ] **Step 1: Write failing UI contract tests**

```python
def test_workbench_exposes_provider_neutral_plan_flow() -> None:
    html = read("apps/web/index.html")
    script = read("apps/web/app.js")
    assert 'id="model-provider"' in html
    assert 'value="openai"' in html
    assert 'value="glm"' in html
    assert 'value="deepseek"' in html
    assert 'id="experiment-plan-review"' in html
    assert 'id="compile-experiment"' in html
    assert '"/api/model-configurations"' in script
    assert '"/api/experiment-plans"' in script
    assert "localStorage.setItem(\"api" not in script
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/api/test_web_assets.py -q`  
Expected: provider-neutral controls missing.

- [ ] **Step 3: Implement the provider card and complete plan review**

Provider selection updates an editable default model ID. Submit provider/model/key to `/api/model-configurations`; clear the password input after response. Render geometry, physics, boundaries, mesh, numerics, sweep, outputs, assumptions, and limitations using DOM `textContent`. Compile the selected plan, display archive digest/manifest/command chain, then enable Gate 2 and submission.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/api/test_web_assets.py tests/api/test_app.py -q && node --check apps/web/app.js`  
Expected: Web/API tests and JavaScript syntax pass.

- [ ] **Step 5: Commit**

```bash
git add apps/web tests/api
git commit -m "feat: add multi-model experiment planning workbench"
```

---

### Task 9: Full regression, Skill update, and real acceptance

**Files:**
- Modify: `skills/fluid-research-workflow/SKILL.md`
- Modify: `skills/fluid-research-workflow/references/workflow.md`
- Modify: `README.md`
- Test: `tests/skills/test_workflow_skill.py`

- [ ] **Step 1: Write failing Skill/documentation assertions**

Require the Skill to mention provider-neutral plans, local schema validation for GLM/DeepSeek, deterministic compilers, Gate 2 digest binding, and prohibition of model-generated commands.

- [ ] **Step 2: Run the Skill test and verify RED**

Run: `python -m pytest tests/skills/test_workflow_skill.py -q`  
Expected: new provider/compiler governance assertions fail.

- [ ] **Step 3: Update Skill and README from verified behavior only**

Document exact provider configuration, plan/compile/approve/submit flow, built-in experiment types, failure routing, and security boundaries. Do not document unverified model IDs as guaranteed.

- [ ] **Step 4: Run full local verification**

```bash
python -m pytest -q
python -m ruff check .
node --check apps/web/app.js
python C:/Users/baoxu/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/fluid-research-workflow
```

Expected: zero failures, zero Ruff findings, valid JavaScript, valid Skill.

- [ ] **Step 5: Run real acceptance**

For each provider, configure a real key without persisting it and generate one typed plan. Compile and run pipe, cylinder, and cavity through the workstation API. Record job IDs, mesh state, solver completion, final residuals, time directories, and `.foam` paths. A provider without a supplied real key remains explicitly unverified rather than mocked.

- [ ] **Step 6: Commit and push**

```bash
git add README.md skills tests
git commit -m "docs: record multi-model experiment workflow"
git push origin feature/real-integration-backbone
```
