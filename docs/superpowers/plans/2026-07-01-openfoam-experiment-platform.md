# OpenFOAM Experiment Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the workstation parameter demo into a traceable experiment platform with usable ParaView cases, readable names, model-designed experiments, and safely validated custom OpenFOAM case bundles.

**Architecture:** Keep the fixed worker protocol and strict host verification. Expose only home-relative case locations, create ParaView marker files after mesh verification, generate idempotent ASCII job IDs from UTC time and experiment content, and route model output through strict Pydantic schemas. Custom bundles must be validated before transfer and again on the worker; only allow-listed OpenFOAM solvers and fixed command sequences may run.

**Tech Stack:** FastAPI, Pydantic, OpenAI Responses structured outputs, OpenSSH/SCP, OpenFOAM Foundation 13, pytest, vanilla HTML/CSS/JavaScript.

---

### Task 1: Post-processing contract

**Files:**
- Modify: `src/fluid_scientist/worker/service.py`
- Modify: `src/fluid_scientist/execution_targets/workstation.py`
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Test: `tests/worker/test_service.py`
- Test: `tests/api/test_web_assets.py`

- [ ] Add a failing worker test requiring `constant/polyMesh`, numeric time directories, a `<job>.foam` marker, and a home-relative `jobs/<job>/case` location in collected output.
- [ ] Add a failing Web asset test requiring visible post-processing instructions.
- [ ] Implement marker creation and deterministic post-processing metadata without exposing usernames or absolute paths.
- [ ] Render the exact workstation `cd` and `paraFoam` instructions after collection.
- [ ] Run worker, target, API, and Web tests.

### Task 2: Human-readable experiment names

**Files:**
- Modify: `src/fluid_scientist/api/app.py`
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Test: `tests/api/test_execution_targets.py`

- [ ] Add a failing API test requiring a job ID shaped like `YYYYMMDD-HHMMSS-<experiment-slug>-<short-project-id>`.
- [ ] Accept a bounded experiment name and deterministically sanitize it to an ASCII slug; fall back to the experiment type when the name contains no ASCII words.
- [ ] Add an experiment-name field and display the generated job ID throughout polling and recovery.
- [ ] Verify idempotent retries retain the already-bound external job ID.

### Task 3: Model-designed experiments

**Files:**
- Modify: `src/fluid_scientist/adapters/openai_provider.py`
- Modify: `src/fluid_scientist/api/app.py`
- Modify: `apps/web/index.html`
- Modify: `apps/web/app.js`
- Test: `tests/adapters/test_openai_provider.py`
- Test: `tests/api/test_app.py`

- [ ] Define a strict `ExperimentDesign` schema containing objective, experiment type, assumptions, rationale, requested outputs, and a validated `LaminarPipeCase`.
- [ ] Add failing provider tests proving structured model output is used and malformed plans are rejected.
- [ ] Add a failing API test proving missing model configuration returns 503 rather than silently using hard-coded parameters.
- [ ] Implement `POST /api/experiment-designs` with an injected provider for tests and a configured OpenAI provider at runtime.
- [ ] Add an “AI 设计实验” control that fills the typed form while keeping Gate 2 approval mandatory.

### Task 4: Safe custom OpenFOAM bundles

**Files:**
- Create: `src/fluid_scientist/adapters/custom_openfoam.py`
- Modify: `src/fluid_scientist/worker/service.py`
- Modify: `src/fluid_scientist/worker/cli.py`
- Modify: `src/fluid_scientist/execution/ssh.py`
- Modify: `src/fluid_scientist/execution_targets/workstation.py`
- Test: `tests/adapters/test_custom_openfoam.py`
- Test: `tests/security/test_custom_case_safety.py`

- [ ] Add failing tests for path traversal, links, oversized archives, missing case dictionaries, dynamic code, system calls, and non-allow-listed solvers.
- [ ] Implement a deterministic tar archive validator and immutable manifest.
- [ ] Add typed upload to a fixed worker incoming directory; do not accept a remote destination from the model or user.
- [ ] Revalidate on the worker, extract into a confined job directory, and run only `blockMesh` when needed, `checkMesh`, allow-listed `foamRun`, and fixed post-processing.
- [ ] Return logs, mesh results, time directories, and ParaView metadata even when pipe-specific analytical validation does not apply.
- [ ] Run the complete security and regression suite before exposing the custom-case UI.
