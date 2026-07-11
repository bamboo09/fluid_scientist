# Unknown Capability Handoff

## Current Progress

- Branch: `feature/v5-study-decomposer-draft-workflow`
- Base commit before this work: `73ea41f0d8f9518937e76fc1347a1554dfb502c1`
- Completed Phase 2A foundation:
  - `RequirementGraphResolver` now defaults to `require_verified=True` and `require_healthy=True`.
  - `CapabilityRegistry.health_check(mutate=True)` degrades unhealthy `VERIFIED` entries to `UNVERIFIED`.
  - Missing test manifests and verification artifacts are blocking health errors for `VERIFIED` capabilities.
  - FastAPI startup runs registry health check with mutation.
  - Pipeline capability resolution runs registry health check before resolving requirements.
  - `CONFIG_EXTENSION` no longer counts as a completed extension.
  - Config extensions now resolve to `CONFIG_EXTENSION_PENDING`.
  - `CONFIG_EXTENSION_PENDING`, `CODE_EXTENSION_REQUIRED`, and `UNSUPPORTED` are unresolved.

- Completed Phase 2B foundation:
  - Added `UnknownCapabilityOrchestrator`.
  - Added executable `ExtensionSpec` data model.
  - Added `PipelineCheckpoint`.
  - Added per-extension lifecycle records with states including `PROPOSED`, `GENERATING`, `OPENFOAM_TESTED`, `REGISTERED`, `FAILED`, and `ROLLED_BACK`.
  - Pipeline now creates checkpoint and extension-run artifacts when mandatory requirements are unresolved.
  - Pipeline stops before case generation when mandatory capabilities are not healthy and verified.
  - The stop is recorded as `extension_pipeline_incomplete`, not as a fake compile-ready result.

- Started Phase 3A:
  - Added `ConfigExtensionExecutor`.
  - Executor generates a real minimal OpenFOAM case directory for low-risk functionObject config extensions.
  - Generated case includes `0/`, `constant/`, `system/`, `postProcessing/`, `controlDict`, `fvSchemes`, `fvSolution`, `blockMeshDict`, `U`, `p`, and `transportProperties`.
  - Executor runs existing `CompileReadinessValidator`.
  - Static validation can pass without OpenFOAM when explicitly requested by tests.
  - Runtime validation still fails when OpenFOAM is unavailable and does not create a verification artifact.

## Evidence From This Run

- `python -m pytest tests\capabilities\test_registry_health.py tests\capabilities\test_requirement_graph_resolver.py tests\capabilities\test_unknown_capability_orchestrator.py`
  - Result: `8 passed`
- `python -m pytest tests\capabilities\test_config_extension_executor.py`
  - Result: `2 passed`
- `python -m pytest tests\capabilities\test_config_extension_executor.py tests\capabilities\test_registry_health.py tests\capabilities\test_requirement_graph_resolver.py tests\capabilities\test_unknown_capability_orchestrator.py tests\e2e\test_v5_pipeline_multicase.py tests\api\test_v5_dialogue_draft_mainline.py`
  - Result: `27 passed, 1 skipped`
- `python -m pytest tests\e2e\test_v5_pipeline_multicase.py tests\api\test_v5_dialogue_draft_mainline.py`
  - Result: `17 passed, 1 skipped`
- Registry health check still reports the existing native VERIFIED entries as unhealthy because their entrypoints do not import and they lack test/verification evidence. This is intentionally no longer hidden.

## Next Step

Implement Phase 3A without bypassing the new gates:

1. Wire `ConfigExtensionExecutor` into `UnknownCapabilityOrchestrator`.
2. Add functionObject capability registration after real OpenFOAM validation.
3. Save verification artifact and test manifest.
4. Register the resulting capability only after it is `VERIFIED + REGISTERED + HEALTHY`.
5. Resume the original checkpoint and rerun capability resolution.
6. Continue with Postprocessor and Boundary Writer loops.

## Unfinished Items

- A ConfigExtension executor can generate and statically validate a minimal case, but it has not been wired into pipeline resume.
- No extension has been registered as `VERIFIED + REGISTERED + HEALTHY`.
- No original pipeline has resumed after a successful extension registration.
- Case compiler is not yet pluginized by capability stages.
- CompileReady still cannot be claimed under the new production health gate while native capabilities remain unhealthy.
- OpenFOAM validation runner, repair loop, generic worker artifact submission, UI progress wiring, and Playwright E2E remain unfinished.

## Important Guardrail

Do not mark the overall Fluid Scientist objective complete until there is real evidence for:

- Real model call records.
- ScientificIntent and OpenFOAMSimulationPlan schemas.
- Healthy verified capability resolution.
- Real extension generation.
- OpenFOAM validation artifacts.
- Registry registration records.
- Original pipeline resume.
- Real case generation and validation.
- Generic workstation artifact staging and submission.
