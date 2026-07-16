# Model-Driven Spec Editing — Failure Baseline Report

> Captured: 2026-07-16
> Baseline commit: 57392b8 (v6-open-world)
> Refactor plan: TRAE_MODEL_DRIVEN_SPEC_EDITING_REFACTOR_PLAN_V2.md

## Problem Summary

The current system uses keyword/regex-based field extraction and per-field if/else logic to interpret user modifications. This causes seven categories of failure:

## Failure 1: "仿真时间设为15秒" — Regex-Only Time Edit

**Root cause**: `_extract_simulation_time()` in the pipeline uses regex patterns to match "仿真时间" and "秒". The regex fails on phrasings like "算15秒" or "跑到15秒为止".

**Fix**: `SimulationSpecPatch` with `PatchOperation(op="replace", path="/numerics/time/end_time", value=15, unit="s")`. The model interprets the intent, not a regex pattern.

**Test**: `tests/e2e/model_editing/test_set_end_time_15s.py` — 5 tests, all pass.

## Failure 2: Consecutive Modifications Lose State

**Root cause**: Each `/modify` call may create a new session or reset parameters. The system lacks a versioned spec store with patch history.

**Fix**: `VersionedSpecStore` maintains version chain. `PatchHistory` records all applied patches. `SessionManager` preserves session state across turns. Three consecutive patches (end_time, delta_t, add_probe) all persist in the final spec.

**Test**: `tests/e2e/model_editing/test_two_consecutive_edits.py` — 4 tests, all pass.

## Failure 3: Triangle Geometry Misidentified as Cosine Bell

**Root cause**: Hardcoded geometry mapping logic can misroute "三角" to `cosine_bell_2d` when keyword detection fails.

**Fix**: `GeometryEntity` separates `semantic_type` (user's semantic intent) from `primitive` (solver representation). The model sets `semantic_type="triangle_2d"` based on user input, and the patch engine preserves it through all operations. No keyword-based routing.

**Test**: `tests/e2e/model_editing/test_triangle_not_cosine_bell.py` — 5 tests, all pass.

## Failure 4: Material Change Doesn't Recompute Dependencies

**Root cause**: Changing material from "air" to "water" doesn't trigger Reynolds number recompute because there's no dependency graph.

**Fix**: `DependencyGraph` tracks that `/physics/reynolds_number` depends on `/physics/kinematic_viscosity`, which depends on `/physics/material`. `DerivedValueComputer` recomputes Re = U*D/nu with the new nu. `InvalidationEngine` marks case as NEEDS_RECOMPILE and results as NEEDS_RERUN.

**Test**: `tests/e2e/model_editing/test_material_change_recomputes_dependencies.py` — 6 tests, all pass.

## Failure 5: Probes Don't Reach Measurement Plan

**Root cause**: User-added probes are stored in the spec but not propagated to the MeasurementPlan used by postprocessing.

**Fix**: `PatchOperation(op="append_unique", path="/observations/probes/-")` adds probes directly to the spec's observation definition. The `PathRegistry` validates the path, and the `PatchExecutor` applies it atomically.

**Test**: `tests/e2e/model_editing/test_add_probe_reaches_measurement_plan.py` — 5 tests, all pass.

## Failure 6: Unknown Geometry Silently Uses Template

**Root cause**: When the system encounters an unknown geometry type, it falls back to an existing template (cylinder, cosine_bell) without user confirmation.

**Fix**: `PatchOperation(op="declare_unknown_capability")` explicitly marks the geometry as unknown. The system does NOT map it to any existing template. The `original_user_semantics` field preserves the user's description for later capability extension.

**Test**: `tests/e2e/model_editing/test_unknown_geometry_does_not_use_template.py` — 5 tests, all pass.

## Failure 7: Model Failure Silently Falls Back

**Root cause**: `LLMClient` has a mock backend that returns default values when the real model fails. The system continues as if the model succeeded.

**Fix**: `ModelInvocationError` with explicit codes (MODEL_UNAVAILABLE, MODEL_TIMEOUT, MODEL_OUTPUT_INVALID, etc.). `fallback_used` is forced to `False` in real mode. `ModelClient` returns `ModelInvocationResult` with explicit success/failure. `StructuredOutputValidator` rejects invalid JSON without silent defaults.

**Test**: `tests/e2e/model_editing/test_no_silent_fallback.py` — 16 tests, all pass.

## Failure 8: Ambiguity Not Clarified

**Root cause**: When user says "仿真时间设为15秒" with start_time=5s, the system doesn't know if 15s means end_time or duration. It guesses one silently.

**Fix**: `ClarificationRequest` with `ClarificationAlternative` provides structured options:
- "结束时间为15秒" (end_time=15s)
- "持续计算15秒" (duration=15s, end_time=20s)
The patch is not applied until the user selects an alternative.

**Test**: `tests/e2e/model_editing/test_ambiguity_clarification.py` — 9 tests, all pass.

## Test Summary

| Test File | Tests | Status |
|-----------|-------|--------|
| test_set_end_time_15s.py | 5 | ✅ All pass |
| test_two_consecutive_edits.py | 4 | ✅ All pass |
| test_triangle_not_cosine_bell.py | 5 | ✅ All pass |
| test_material_change_recomputes_dependencies.py | 6 | ✅ All pass |
| test_add_probe_reaches_measurement_plan.py | 5 | ✅ All pass |
| test_unknown_geometry_does_not_use_template.py | 5 | ✅ All pass |
| test_no_silent_fallback.py | 16 | ✅ All pass |
| test_ambiguity_clarification.py | 9 | ✅ All pass |
| **Total** | **55** | **✅ All pass** |

## Architecture Components Built

| Module | Files | Tests | Purpose |
|--------|-------|-------|---------|
| `model_runtime/` | 8 | 75 | Model tracing, capability eval, explicit failure |
| `study_spec/` | 11 | 73 | Canonical versioned SimulationStudySpec |
| `spec_editing/` | 13 | 56 | Schema-driven patch engine |
| `dependencies/` | 6 | 56 | Derived values and invalidation graph |
| `session_state/` | 5 | 88 | Multi-turn session management |
| `prompts/` | 4 | 32 | Spec Editor + Critic prompt design |
| `tests/e2e/` | 9 | 55 | Failure baseline tests |
| **Total** | **56** | **435** | **All pass** |
