# TRAE Running Baseline — Model-Driven Spec Editing Refactor

> Captured: 2026-07-16
> Purpose: Lock the actual running version before starting the model-driven spec editing refactor.

## Repository

```yaml
repository_root: D:/desktop/AI FOR SCIENCE
remote_origin: https://github.com/bamboo09/fluid_scientist.git
remote_trae_local: D:\desktop\local deep research\fluid_scientist
```

## Current Running Version

```yaml
branch: v6-open-world
commit: 57392b8f210fdbcf5848fa6b1ae91a00e9cb8c19
commit_message: "fix: 仿真时间设为15秒正确提取 + LLM提取仿真参数"
protection_tag: backup/pre-model-driven-spec-editing-20260716
```

## Running Process

```yaml
backend:
  cwd: D:\desktop\AI FOR SCIENCE
  start_command: python -m uvicorn fluid_scientist.api.app:create_app --factory --host 0.0.0.0 --port 8000
  python: C:\Users\baoxu\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe
  pythonpath: D:\desktop\AI FOR SCIENCE\src
  pid: 45424
  api_base: http://localhost:8000
frontend:
  served_by: same uvicorn server (FastAPI static files)
  url: http://localhost:8000
database:
  type: SQLite
  path: fluid_scientist.db
worker:
  target: 10.129.177.241 (campus VPN required)
  openfoam_version: v13 (Foundation)
  mpi: Open MPI 4.1.2
  status: offline (not on campus network)
```

## Git Branches

| Branch | Commit | Worktree |
|--------|--------|----------|
| v6-open-world (HEAD) | 57392b8 | main worktree |
| codex/v5-chat-openfoam-closed-loop | 5e88d76 | .worktrees/v5-chat-openfoam-closed-loop |
| feature/v5-three-panel-layout | 69c4eca | .worktrees/v5-three-panel |
| integration/trae-codex | c797622 | .worktrees/trae-codex-integration |
| integration/v5-chatbot-workbench | d3bbec4 | .worktrees/v5-chatbot-runtime |
| trae/workstation-auto-connect | fec5f6c | .worktrees/workstation-auto-connect |
| feature/v5-study-decomposer-draft-workflow | adb2313 | - |
| integration/v5-runtime | 38a8621 | - |
| main | c06b9bb | - |

## Working Directory State

- No modified tracked files (clean)
- Untracked files: `.env`, skill JSON files, `__pycache__`, egg-info, new result directories

## Existing Module Inventory

Already present (will be extended, not replaced):
- `src/fluid_scientist/cylinder_flow_2d/` — current experiment spec + pipeline
- `src/fluid_scientist/case_ir/` — CaseIR models, validators, dependency graph
- `src/fluid_scientist/capabilities/` — capability resolver, registry, gap analyzer
- `src/fluid_scientist/intent/` — conflict resolver, semantic fidelity guard
- `src/fluid_scientist/llm/` — LLM client (currently mock-backed)
- `src/fluid_scientist/llm_pipeline/` — multi-pass LLM pipeline
- `src/fluid_scientist/draft/` — draft generator, change agent, derivation
- `src/fluid_scientist/draft_session/` — session state, input router, persistence
- `src/fluid_scientist/measurement/` — measurement compiler, planner
- `src/fluid_scientist/experiment_spec/` — experiment spec models, native compiler

To be created (new modules per refactor plan):
- `src/fluid_scientist/model_runtime/` — model tracing, capability eval, explicit failure
- `src/fluid_scientist/study_spec/` — canonical versioned SimulationStudySpec
- `src/fluid_scientist/spec_editing/` — generic SimulationSpecPatch engine
- `src/fluid_scientist/dependencies/` — dependency graph, derived values, invalidation

## Known Issues to Fix

1. "仿真时间设为15秒" — only works via regex, not via model-driven patch
2. Consecutive modifications lose state (new session created instead of updating)
3. Triangle geometry can be misidentified as cosine_bell
4. Material changes don't recompute dependencies (Re, rho, nu)
5. Probes don't reach MeasurementPlan
6. Model failures silently fall back to regex/default templates
7. LLM client uses mock backend in production
