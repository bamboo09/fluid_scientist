# Unknown Capability Current Audit

Date: 2026-07-11

Branch: `feature/v5-study-decomposer-draft-workflow`

HEAD: `183cb64c6b1ec1d3cc9e821e8d9e43659bba77a5`

Remote: `origin https://github.com/bamboo09/fluid_scientist.git`

Worktree status before edits: clean and up to date with `origin/feature/v5-study-decomposer-draft-workflow`.

## Required Git Audit

- `git status`: clean, on `feature/v5-study-decomposer-draft-workflow`.
- `git branch --show-current`: `feature/v5-study-decomposer-draft-workflow`.
- `git rev-parse HEAD`: `183cb64c6b1ec1d3cc9e821e8d9e43659bba77a5`.
- `git diff --stat`: empty before edits.
- `git remote -v`: origin points to `https://github.com/bamboo09/fluid_scientist.git`.
- `git worktree list --porcelain`: main worktree is the target feature branch; additional worktrees exist for `integration/v5-chatbot-workbench`, detached `v5-dialogue-draft-mainline`, and `feature/v5-three-panel-layout`.

## Current Main Runtime Chain

Browser UI (`apps/web/app.js`, `apps/web/v5-pipeline.js`)
-> FastAPI (`src/fluid_scientist/api/app.py`, `src/fluid_scientist/api/v5_router.py`)
-> session and message routing (`draft_session/*`)
-> study decomposition / intent helpers (`study_decomposition/*`)
-> V5 compile-ready pipeline (`workflow_pipeline/pipeline.py`)
-> deterministic design closure (`closure/__init__.py`)
-> metric compilation (`metrics/__init__.py`)
-> capability requirement matching (`capabilities/registry.py`)
-> direct case generation inside `V5WorkflowPipeline._stage_generate_case`
-> case writing (`case_generation/writer.py`)
-> compile readiness validation (`case_generation/validator.py`)
-> optional workstation targets / worker (`execution_targets/workstation.py`, `worker/*`).

The intended unique chain exists as a partial implementation, but the case compiler and capability extension loop are not yet true plugin consumers.

## Module Truth Table

| Module | Code exists | Enters main chain | Uses real model | Uses real OpenFOAM | Test coverage | Conclusion |
|---|---|---|---|---|---|---|
| Scientific Problem Interpreter | Yes: `_stage_understanding`, `LLMClient`, `scientific_intent/models.py` | Partially through `/api/v5/pipeline/run` | Yes when configured; explicit mock mode only | No | `tests/e2e/test_v5_pipeline_multicase.py`, API LLM tests | PARTIALLY_WORKING |
| OpenFOAM Solution Planner | Partial: design stage returns raw design, not full `OpenFOAMSimulationPlan` schema | Partially | Partially, mostly deterministic design synthesis | No | Pipeline static tests | PARTIALLY_WORKING |
| ExperimentDesignSynthesizer | Yes | Yes in legacy/session route, not the final pipeline design source | No direct model call | No | `tests/workbench/test_experiment_design_synthesizer.py` | PARTIALLY_WORKING |
| DesignClosureEngine | Yes: robust graph in `closure/__init__.py`; older workbench closure also exists | Yes in final pipeline | No | No | workbench and pipeline tests | PARTIALLY_WORKING |
| GoalMetricCompiler | Yes: simple workbench compiler and richer `GoalToMetricCompiler` | Yes via `GoalToMetricCompiler` in final pipeline | No, assumes upstream mapping | Indirect via functionObject config | measurement / metric tests | PARTIALLY_WORKING |
| BoundaryVerificationCompiler | Yes | Used in draft-generation route, not clearly in final pipeline | No | Indirect configs only | `tests/measurement/test_boundary_verification_compiler.py` | EXISTS_BUT_NOT_CONNECTED |
| CapabilityRegistry | Yes | Yes | No | No direct OpenFOAM validation | New health check plus existing capability tests | BROKEN |
| CapabilityResolver | Yes, but legacy metric-only resolver | Not in final `V5WorkflowPipeline`; pipeline does inline matching | No | No | capability tests cover old metric flow | EXISTS_BUT_NOT_CONNECTED |
| CodeExtensionSpec | Yes | API endpoints exist | No automatic model generation in final pipeline | No | `tests/code_extension/*`, API extension tests | PARTIALLY_WORKING |
| CodeExtension Generator | Partial API code generation exists | Manual endpoint, not automatic unknown-capability loop | Yes if LLM configured | No | API test coverage | EXISTS_BUT_NOT_CONNECTED |
| Extension Sandbox | Yes, pure Python sandbox | Manual extension tests only | No | No | `tests/code_extension/*` | PARTIALLY_WORKING |
| Extension Test Runner | Yes, but mostly syntax/basic sandbox tests | Manual extension endpoint only | No | No minimal OpenFOAM case | code extension tests | MOCK_ONLY |
| Case Compiler | Yes: `NativeCaseCompiler` and direct pipeline generation | Final pipeline bypasses plugin compiler stages | No | Writes OpenFOAM dictionaries | case plan and pipeline tests | PARTIALLY_WORKING |
| CompileReadinessValidator | Yes | Yes in final pipeline | No | Yes when OpenFOAM is installed; fails closed otherwise | `tests/case_generation/test_compile_readiness_validator.py` | PARTIALLY_WORKING |
| Workstation Adapter | Yes | Existing non-V5 execution flow; not compile-ready artifact protocol | No | Remote execution target can run OpenFOAM | execution target tests | PARTIALLY_WORKING |
| Remote Worker | Yes | Not generic artifact protocol; still has pipe-specific `submit` and custom archive | No | Intended remote OpenFOAM | worker tests | PARTIALLY_WORKING |
| Result Collector | Yes | Existing result ingestion/worker collect | No | Parses outputs when present | results / worker tests | PARTIALLY_WORKING |
| V5 Chatbot UI | Yes | Yes for V5 endpoints and progress | Shows pipeline state but not full unknown loop | No | web and E2E tests | PARTIALLY_WORKING |

## False-Completion Findings

1. Registry contains 60 native capabilities; 41 are marked `verified`.
2. A concrete health check found 41 error-level health failures among those verified capabilities.
3. Typical failure: `implementation_entrypoint` points to modules that do not exist, such as `fluid_scientist.case_generation.geometry`, `mesh`, `physics`, `boundaries`, `function_objects`, and `postprocessors`.
4. Registry entries do not record per-capability unit/minimal-case test manifests or verification artifact hashes.
5. `CapabilityResolver` is still a legacy metric-only resolver and does not implement exact, parameterized, compositional, config-extension, code-extension, unsupported ordering.
6. `V5WorkflowPipeline._stage_resolve_capabilities` performs inline keyword matching and sends missing mandatory capabilities directly to `FAILED`; it does not enter an `UnknownCapabilityOrchestrator`.
7. `CodeExtensionWorkflow.run_tests` can pass with only Python syntax checks when no acceptance tests exist.
8. There is no minimal OpenFOAM case test attached to generated extensions.
9. Case generation is still inside one large pipeline function with hard-coded geometry families (`internal_flow`, `external_flow`, `jet_impingement`) instead of consuming verified capability plugins by stage.
10. Worker CLI still exposes pipe-specific `submit --diameter --length --velocity --nu`; generic archive support exists only as `submit-custom`, not the requested immutable CaseArtifact protocol with `stage`, `validate`, `submit`, `status`, `collect`.

## Registry Health Snapshot

New health-check API added in this phase:

- Python API: `get_capability_registry().health_check(mutate=False)`
- HTTP API: `GET /api/v5/capabilities/health`

Observed snapshot on 2026-07-11:

- total capabilities: 60
- raw status `verified`: 41
- health-unverified records: 41
- degraded during report-only audit: 0

The check can run with `mutate=True` to downgrade erroring verified entries to `unverified`; this is intentionally not wired as a hard production gate yet because the current final pipeline would otherwise fail at capability resolution before the extension loop exists.

## Phase 0 Implementation Completed

- Added concrete `CapabilityHealthIssue`, `CapabilityHealthRecord`, and `CapabilityHealthReport`.
- Added importable entrypoint verification for `module:function` declarations.
- Added implementation source hashing.
- Added warnings for missing test manifests and verification artifacts.
- Added optional mutation mode that downgrades invalid `verified` capabilities to `unverified`.
- Added `/api/v5/capabilities/health` for UI/API inspection.
- Added tests proving false `VERIFIED` entrypoints are detected and can be downgraded.

## Immediate Next Phase

Phase 1 should replace inline `_stage_resolve_capabilities` with a real requirement graph and resolver that can:

1. Match only healthy verified capabilities in strict mode.
2. Compose healthy verified capabilities.
3. Return structured `EXTENSION_REQUIRED` instead of failing directly.
4. Persist the original pipeline breakpoint for resume after extension registration.
