# Responsive planning and browser postprocessing acceptance

Date: 2026-07-04 (Asia/Shanghai)

## Automated verification

- `python -m pytest -q`: **480 passed, 3 skipped**.
- `python -m ruff check .`: passed.
- JavaScript syntax checks passed for `app.js`, `workbench-state.js`,
  `operation-state.js`, `operation-lifecycle.js`, `postprocess.js`, and
  `result-state.js`.
- Web behavior tests cover stale polling responses, cancellation, refresh
  recovery, result identity, real static/dynamic postprocess clicks, retry,
  focus/scroll, bounded scientific chart sampling, and source-aware analysis.
- The three skips are the documented local-Windows OpenFOAM checks; the real
  workstation evidence below is independent of those local skips.

## Live asynchronous GLM planning

- Service process: PID `60744`, serving the current worktree at
  `http://127.0.0.1:8000`.
- Runtime planner: `glm / glm-5.1`; its credential was configured only in
  process memory and was not written to this record, browser storage, Git, or
  the database.
- Workstation target `workstation-openfoam` reported available. A subsequent
  capability read used the 30-second cache and returned in 50 ms.
- Research project: `0b8ac110-b9a6-4ec5-8c63-a9a6881dbfed`.
- Planning operation: `f1d1c784-f918-42e5-8a38-9cde785cb984`.
- `POST /api/plan-operations` returned `queued` / `queued` in **19 ms**, before
  the model completed.
- Polling observed `running` / `model_planning`; the operation reached
  `succeeded` / `complete` about 28 seconds after creation.
- Accepted plan: `ea764c3b-2db3-4997-a1b0-29ead89342f3`.
- The strict plan type is `lid_driven_cavity`, requests velocity probes and
  residuals, and contains the required grid-independence sweep
  `cells_per_side = [16, 32, 64, 128]`.

This verifies that target reachability does not block model planning and that
the previously failing cavity grid-independence request now returns a valid,
persisted plan through the asynchronous operation API.

## Retained real-workstation postprocessing evidence

The current service collected an already completed OpenFOAM Foundation 13 job
through the real workstation adapter:

- Project: `658afe24-1a19-4169-85f3-2163cfc60426`.
- Plan: `be6c08f8-aaa9-4d12-b0cb-4daf5e4d5f56`.
- Case: `planned-be6c08f8-aaa9-4d12-b0cb-4daf5e-v1-1tuuf19`.
- Mesh passed with 30 cells; solver completed.
- Pressure drop: `0.28238819337432197 Pa`.
- Inlet/outlet mass flow:
  `1.73335610950194e-4 / -1.7334786762839002e-4 kg/s`.
- Numeric time directories: `0`, `2000`.
- ParaView marker:
  `20260703-105621-short-laminar-pipe-validation-658afe24.foam`.

The served page contains the persistent operation card and static
postprocessing button. The served postprocessing module contains the shared
static/dynamic click binder. Runtime Node tests dispatch both button types and
verify fetch-on-demand, focus, scroll, retry, stale-session rejection, and
exactly-once rendering.

## Explicit limitations

- This acceptance reused a retained completed workstation job for result
  collection; it did **not** submit a new OpenFOAM job.
- The newly generated cavity plan was not compiled, approved at Gate 2, or run
  during this acceptance.
- No claim of grid independence or publication-grade physical credibility is
  made from the retained 30-cell pipe smoke case.
- HPC data/login/compute-node execution remains outside this acceptance.
