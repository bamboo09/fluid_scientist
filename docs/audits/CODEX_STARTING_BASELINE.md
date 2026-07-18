# Codex Starting Baseline

> Recorded: 2026-07-18 (Asia/Shanghai)
>
> Scope: incremental model-native refactor starting from the Trae handoff.

## Version identity

```yaml
required_trae_merge_sha: 98cfed86139a4ef5fd7a52509991d83aa7edb433
starting_head: d3b9e717e7f0eaf3917d88e776b820132bbc703e
starting_branch: main
codex_branch: codex/v6-model-native-fluid-scientist
contains_required_trae_merge: true
remote_fetch_performed: false
```

`d3b9e71` is the Trae handoff-document commit directly on top of the required
merge baseline.  No old Codex branch, Trae worktree, UI, or compiler was used
as a source.

The runtime identity endpoint now reports:

```json
{
  "current_sha": "d3b9e717e7f0eaf3917d88e776b820132bbc703e",
  "branch": "codex/v6-model-native-fluid-scientist",
  "required_baseline_sha": "98cfed86139a4ef5fd7a52509991d83aa7edb433",
  "contains_required_baseline": true,
  "source": "git"
}
```

Endpoint: `GET /api/system/build-info`.

## Runtime commands

```text
Backend: python -u -m uvicorn fluid_scientist.api.app:app --host 127.0.0.1 --port 8000
Frontend: static files under apps/web, served by FastAPI
Primary UI: http://127.0.0.1:8000/
```

## Workstation Doctor

Read-only status response captured from the current application:

```yaml
connected: true
host: 10.129.177.241
username: ls
port: 22
foam_version: OpenFOAM-13
cpu_count: 64
memory_gb: 125.13
disk_free_gb: 765.57
error: null
```

## Test baseline

### Passing focused suites

```text
102 passed in 4.57s
```

Covered suites:

- `tests/cylinder_flow_2d`
- `tests/v6_open_world/test_v6_e2e.py`
- `tests/api/test_build_info.py`

The API project/execution subset, rerun with a workspace-local pytest temp
directory, produced:

```text
21 passed, 1 failed
```

The remaining failure is the pre-existing UI contract assertion
`test_workbench_exposes_conversation_driven_project_and_gate_workflow`: current
`apps/web/index.html` no longer contains `id="experiment-prompt"` expected by
the older test.

### Collection blocker

`tests/api/test_app.py` is truncated in the committed baseline at its final
test (`bundle.addfile(info, io.BytesIO(pay`), causing a Python syntax error at
line 725.  The file is identically truncated in `HEAD`; this is not a Codex
working-tree edit.  Full-suite collection cannot be considered authoritative
until the test is reconstructed from current API behavior or replaced.

### Browser evidence

The root HTML was served successfully through FastAPI's TestClient.  A real
browser screenshot was not captured in this baseline run because no browser
driver is configured in the current test environment.  The existing UI
contract mismatch above remains open and is not reported as passing.

## P0 findings at start

1. Unspecified material received water-derived density (`998 kg/m3`) despite
   `material.name` being unresolved.
2. A rectangular computational domain could create a rectangle obstacle.
3. Regex and LLM obstacle candidates could both remain enabled in Canonical
   Spec after arbitration.
4. `/modify` mutated the in-memory canonical object before persistence,
   swallowed persistence errors, skipped read-back verification, and returned
   no structural diff.
5. Runtime build identity did not enforce/report the Trae merge ancestry in
   the active app implementation.

## P0 corrections in the first Codex increment

- Material defaults now require an explicitly named water/air material.
  `U/D/Re` may derive equivalent kinematic viscosity while material identity
  and density remain null.
- Rectangle extraction distinguishes `DOMAIN` wording from explicit
  `SOLID_OBSTACLE` wording.
- The resolved `obstacle.type` semantic slot is enforced as the only selected
  bottom-obstacle candidate in Canonical Spec; rejected candidates remain in
  the audit payload.
- `/modify` works on a detached copy, performs durable write plus read-back
  validation, restores the previous database payload on verification failure,
  publishes to memory only after success, increments the version, and returns
  `change_summary`.
- `GET /api/system/build-info` exposes runtime SHA, branch, required Trae SHA,
  and ancestry result.

## Remaining high-priority baseline issues

1. Repair or replace the truncated `tests/api/test_app.py` using current API
   behavior, without importing a historical implementation.
2. Reconcile the current UI with the project API contract test; determine
   whether the test or UI is the intended authority.
3. Complete the other P0 state/blocker/provenance browser assertions on the
   real trapezoid conversation.
4. Diagnose RUN-003 job identity semantics and RUN-002 force coefficient
   credibility.
5. Continue the model-native migration: structured semantic result, regex as
   evidence-only, runtime Skill trace/ablation, general CaseIR, and structured
   model-assisted compile planning.
