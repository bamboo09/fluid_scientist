# Runtime integration contract

This document records business-code gaps only; no business code was modified.

## Skill/runtime boundary

Skills return typed proposals. They do not execute, compile, write arbitrary OpenFOAM files, approve studies, or assert run success. The product runtime must explicitly select/load Skills; repository presence alone does not prove production use.

## Proposed spec-edit input/output

Input: current immutable full spec, version, accepted patch ledger/inverses, confirmed facts/conflicts, capability inventory, user turn and schema map.

Output: `SimulationSpecPatch` with base version, minimal semantic operations, source quote/confidence, clarifications, conflicts, dependency impacts, unknown capabilities and confirmation summary.

Runtime responsibilities: authenticate ownership, validate version/schema/pointers/units, present confirmation, apply accepted operations deterministically, create a new immutable version when required, persist inverse/provenance, run derivation/validation, and reject unrelated changes.

## Observed current-model gaps

Read-only inspection found:

- canonical runtime class is `ExperimentSpec`, not a declared `SimulationStudySpec`;
- geometry/boundaries are largely parameter-centric rather than first-class stable-ID trees;
- `SpecEditOperation` supports coarse operation names but not JSON Pointer `add/replace/remove/merge/append_unique/set_relation/declare_unknown_capability`;
- proposals have experiment version, but no explicit per-operation precondition or inverse-patch contract;
- current executor updates parameters by ID but does not implement general stable-entity patching, undo, relation solving or unknown-capability operations;
- output/plot entities and analysis-window semantics are not a single canonical editing tree;
- existing MissingCapability types exist in multiple compatibility layers; an adapter must choose one canonical contract;
- some inspected source text is mojibake in this Windows checkout, increasing documentation/schema review risk.

These are integration requirements, not authorization to modify runtime.

## Controlled OpenFOAM handoff

Case planning returns `OpenFOAMCaseBlueprint`; trusted Foundation-13-aware compilation owns final dictionaries. Execution must remain a small allowlist/worker protocol, bind immutable digests, separate doctor/execute/verify/save, expose external job ID, preserve failures, and never accept model-generated shell.

## Result contract

Validation consumes artifact IDs/hashes for logs, mesh, fields, function-object data, postprocessing configs, images and sensitivity studies. Status is one of PROCESS_COMPLETED, NUMERICALLY_ACCEPTABLE, PHYSICALLY_CREDIBLE, RESEARCH_READY; each higher state needs independent evidence.

