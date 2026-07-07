# Conversational workbench real acceptance

Date: 2026-07-03 (Asia/Shanghai)

## Accepted journey

- The browser root serves the UTF-8 conversation workbench with a visible natural-language composer.
- The configured in-memory planner was `glm / glm-5.1`; no credential was written to source, storage, acceptance records, or Git.
- A Chinese natural-language request asked for a very short laminar-pipe OpenFOAM 13 workstation validation.
- The planning request included the selected `workstation_openfoam` target capability.
- GLM returned plan `be6c08f8-aaa9-4d12-b0cb-4daf5e4d5f56`, version 1, owned by project `658afe24-1a19-4169-85f3-2163cfc60426`.
- Trusted compilation produced `sha256:495d95c66dd151127bbb02a8d0ee6e81cace74a9f49ee30843f665c32ec20c56` for `incompressibleFluid` with `blockMesh` and `checkMesh` preprocessing.
- Gate 2 bound the exact plan ID, version, and archive digest before submission.

## Remote identity and recovery evidence

- Plan-scoped case ID: `planned-be6c08f8-aaa9-4d12-b0cb-4daf5e-v1-1tuuf19`
- External job ID: `20260703-105621-short-laminar-pipe-validation-658afe24`
- Remote PID: `641388`
- The first response was not treated as success because no external job identity was received. An idempotent retry of the same plan/case/digest returned HTTP 201 and the existing remote identity.
- The read-only plan recovery endpoint returned the owning project ID, allowing the client to reject stale cross-project browser identifiers without recompiling or resubmitting.

## Collected OpenFOAM evidence

- Worker state: `succeeded`
- Project state after collection: `PILOT_VERIFIED`
- Mesh: passed; 30 cells; maximum aspect ratio `12.04583805`; maximum non-orthogonality `0`; maximum skewness `0.3232051687`
- Solver: completed
- Final residuals: `Ux=5.39734171e-4`, `Uy=7.85997075e-4`, `Uz=2.791429882e-4`, `p=8.456580075e-4`
- Global continuity error: `7.071071691e-6`
- Inlet/outlet mass flow: `1.7333561095e-4 / -1.7334786763e-4 kg/s`
- Pressure drop: `0.2823881934 Pa`
- Numeric times: `0`, `2000`
- ParaView marker: `20260703-105621-short-laminar-pipe-validation-658afe24.foam`

## Evidence-bound analysis

`glm-5.1` returned 12 claims. Every claim passed the exact evidence-key allow-list. The analysis explicitly identified the 30-cell mesh and approximately `1e-4` residual level as limitations and did not promote the smoke run to a publication-grade result.

## Automated verification

- `334 passed, 3 skipped`
- Ruff passed.
- JavaScript syntax and pure task-state tests passed.
- The three skips are local Windows tests gated on an unavailable local OpenFOAM 13 toolchain; the workstation run above supplies the real Foundation 13 execution evidence.

## Not accepted by this run

- Grid independence, analytical pressure-drop agreement, and publication-grade physical credibility were not established.
- Real OpenAI and DeepSeek provider calls were not exercised because no credentials were supplied for those providers.
- The HPC data/login/compute-node path remains unconfigured and unaccepted.
