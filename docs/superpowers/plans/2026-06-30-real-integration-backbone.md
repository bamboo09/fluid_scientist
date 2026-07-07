# Real Integration Backbone Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the in-memory/Fake control path with persistent projects, explicit human approvals, a real OpenAI provider, safe SSH/Slurm execution, and a validated laminar-pipe OpenFOAM benchmark while preserving Fake-mode CI.

**Architecture:** Keep domain and deterministic science code independent. Add a settings boundary, SQLite/PostgreSQL-compatible SQLAlchemy repositories, provider adapters, typed SSH command execution, Slurm state parsing, and a versioned OpenFOAM template. Real integrations are opt-in through environment configuration; tests inject fake clients and never require credentials.

**Tech Stack:** Python 3.11+, Pydantic Settings, SQLAlchemy 2, OpenAI Python SDK, FastAPI, OpenSSH client, Slurm CLI, OpenFOAM, pytest.

---

### Task 1: Typed settings and secret boundary

**Files:**
- Create: `src/fluid_scientist/settings.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Test: `tests/test_settings.py`

- [ ] Write failing tests proving Fake mode needs no secrets and real modes reject missing OpenAI/HPC fields.
- [ ] Run `python -m pytest tests/test_settings.py -q`; expect missing module failure.
- [ ] Implement `AppSettings` with nested OpenAI, database, data-node, login-node, Slurm, and OpenFOAM settings. Use `SecretStr`; never expose secret values in `repr` or dumps.
- [ ] Run settings tests; expect PASS.
- [ ] Commit with `feat: add typed integration settings`.

### Task 2: Persistent workflow repository

**Files:**
- Create: `src/fluid_scientist/adapters/sql_repository.py`
- Create: `src/fluid_scientist/db.py`
- Modify: `src/fluid_scientist/ports.py`
- Test: `tests/adapters/test_sql_repository.py`

- [ ] Write failing tests that save, reload, update, and concurrently version-check workflow snapshots using temporary SQLite.
- [ ] Run the tests and confirm missing repository failure.
- [ ] Implement SQLAlchemy tables for projects, workflow snapshots, approvals, external jobs, and audit events. Use optimistic `version` checks and transaction boundaries; keep PostgreSQL URL compatibility.
- [ ] Run repository tests and all workflow tests; expect PASS.
- [ ] Commit with `feat: persist research workflows`.

### Task 3: Interactive approval and project APIs

**Files:**
- Modify: `src/fluid_scientist/api/app.py`
- Create: `src/fluid_scientist/services/projects.py`
- Modify: `apps/web/app.js`
- Modify: `apps/web/index.html`
- Test: `tests/api/test_projects.py`

- [ ] Write failing API tests for project creation, current state, Gate approve/reject, invalid transitions, and persistence across new app instances.
- [ ] Run API tests and confirm route failures.
- [ ] Implement `POST /api/projects`, `GET /api/projects/{id}`, `POST /api/projects/{id}/approvals`, and `POST /api/projects/{id}/actions`. Remove automatic approval from the real path; retain `/api/demo` for Fake mode.
- [ ] Update the workbench to show pending Gate details and approve/reject controls without exposing Skill governance.
- [ ] Run API tests and full suite; expect PASS.
- [ ] Commit with `feat: add interactive research approvals`.

### Task 4: OpenAI Responses provider

**Files:**
- Create: `src/fluid_scientist/adapters/openai_provider.py`
- Modify: `src/fluid_scientist/ports.py`
- Test: `tests/adapters/test_openai_provider.py`

- [ ] Inspect the installed official OpenAI SDK signature for structured Responses parsing and record the supported call shape in the test fixture.
- [ ] Write failing tests using a fake SDK client for ResearchSpec interpretation, Results Analyst claims, Scientific Reviewer decisions, malformed output, timeout, and redacted logging.
- [ ] Implement `OpenAIResponsesProvider` with configurable model IDs, structured Pydantic output, request IDs, bounded retries, and no raw secret/prompt logging.
- [ ] Run provider tests and Fake regression suite; expect PASS.
- [ ] Commit with `feat: add OpenAI responses provider`.

### Task 5: Safe SSH transport and data-node artifacts

**Files:**
- Create: `src/fluid_scientist/execution/ssh.py`
- Create: `src/fluid_scientist/execution/artifacts.py`
- Test: `tests/execution/test_ssh.py`
- Test: `tests/execution/test_artifacts.py`

- [ ] Write failing tests for host-key enforcement, typed argv, timeout, non-zero exit, checksum mismatch, resumable upload command construction, and remote-root confinement.
- [ ] Implement an injected process runner around local `ssh`, `sftp`/`scp`, and checksum tools. Never enable `StrictHostKeyChecking=no`; never accept free-form remote shell.
- [ ] Implement artifact manifests with source, build log, SHA-256, immutable version, and shared-storage destination.
- [ ] Run execution/security tests; expect PASS.
- [ ] Commit with `feat: add safe data-node transport`.

### Task 6: Real Slurm adapter and recovery

**Files:**
- Create: `src/fluid_scientist/adapters/slurm.py`
- Modify: `src/fluid_scientist/execution/hpc.py`
- Test: `tests/adapters/test_slurm.py`

- [ ] Write failing parser/adapter tests for `sbatch`, `squeue`, `sacct`, cancel, terminal states, transient errors, and replay of an existing job ID.
- [ ] Implement typed command invocation over SSH, exact job-ID parsing, state mapping, polling deadlines, and repository-backed idempotency.
- [ ] Run Slurm, workflow, and injection tests; expect PASS.
- [ ] Commit with `feat: add recoverable Slurm adapter`.

### Task 6A: Selectable workstation OpenFOAM target

**Files:**
- Create: `src/fluid_scientist/execution_targets/base.py`
- Create: `src/fluid_scientist/execution_targets/workstation.py`
- Modify: `src/fluid_scientist/settings.py`
- Modify: `src/fluid_scientist/api/app.py`
- Modify: `apps/web/index.html`
- Test: `tests/execution_targets/test_workstation.py`
- Test: `tests/api/test_execution_targets.py`

- [ ] Write failing tests for capability discovery, multiple candidate-host selection, unavailable targets, worker protocol mismatch, and API serialization.
- [ ] Implement `ExecutionTargetAdapter` plus `WorkstationOpenFOAMTarget` using strict SSH host verification and the fixed `fluid-worker doctor/submit/status/cancel/collect` protocol.
- [ ] Expose `GET /api/execution-targets` and add Workstation OpenFOAM/HPC Slurm selection to the experiment UI. Never persist candidate IPs in source or logs.
- [ ] Run target, API, SSH, security, and full regression tests; expect PASS.
- [ ] Commit with `feat: add selectable workstation OpenFOAM target`.

### Task 7: OpenFOAM laminar-pipe benchmark

**Files:**
- Create: `simulators/openfoam/templates/laminar_pipe/0/U`
- Create: `simulators/openfoam/templates/laminar_pipe/0/p`
- Create: `simulators/openfoam/templates/laminar_pipe/constant/physicalProperties`
- Create: `simulators/openfoam/templates/laminar_pipe/system/blockMeshDict`
- Create: `simulators/openfoam/templates/laminar_pipe/system/controlDict`
- Create: `simulators/openfoam/templates/laminar_pipe/system/fvSchemes`
- Create: `simulators/openfoam/templates/laminar_pipe/system/fvSolution`
- Create: `src/fluid_scientist/adapters/openfoam.py`
- Create: `src/fluid_scientist/adapters/openfoam_parsers.py`
- Test: `tests/adapters/test_openfoam.py`

- [ ] Write failing tests for template manifest rendering, path confinement, `checkMesh` parsing, residuals, continuity error, mass flow, pressure drop, and failure classification.
- [ ] Implement a fixed laminar-pipe template and adapter using only the OpenFOAM command enum.
- [ ] Compare numerical pressure drop against Hagen–Poiseuille and require configured tolerances.
- [ ] Run parser/template tests locally; run the marked real integration only when HPC settings exist.
- [ ] Commit with `feat: add OpenFOAM pipe benchmark`.

### Task 8: Real integration command and verification

**Files:**
- Create: `src/fluid_scientist/cli.py`
- Modify: `README.md`
- Create: `tests/test_cli.py`

- [ ] Write failing CLI tests for `doctor`, `publish-artifact`, `submit-benchmark`, `status`, and `collect` in dry-run mode.
- [ ] Implement commands that report missing external configuration without leaking secrets and resume from persisted project/job state.
- [ ] Run `python -m fluid_scientist.cli doctor`; expect a precise readiness matrix.
- [ ] Run Ruff, full pytest, Skill validation, and secret scan.
- [ ] Commit, merge to `main`, push, then run the real benchmark when the user-provided `.env` is ready.

