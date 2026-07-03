# Conversational Experiment Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dense dashboard with a Chinese conversation-first workbench that turns a natural-language request into a model-designed, researcher-confirmed, remotely traceable OpenFOAM experiment.

**Architecture:** Keep the existing FastAPI endpoints and framework-free frontend. Rebuild `index.html` as a conversation shell, split presentation-independent workflow state into `workbench-state.js`, and make `app.js` an API/controller layer that renders plan, submission, task, result, and error cards from factual server responses. Preserve model secrets in server memory and restore only non-secret project/job identifiers after refresh.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, vanilla HTML/CSS/JavaScript, pytest, Ruff, Node syntax checks, OpenFOAM `fluid-worker` protocol.

---

## File structure

- Modify `apps/web/index.html`: semantic conversation shell, composer, context rail, settings/custom-case drawers, reusable card templates.
- Modify `apps/web/styles.css`: scientific-notebook visual system, responsive conversation/cards, accessibility and reduced motion.
- Create `apps/web/workbench-state.js`: pure state labels, transition guards, task view-model construction, persistence key definitions.
- Rewrite `apps/web/app.js`: API calls, project/plan lifecycle, one-click confirm-and-submit pipeline, polling, recovery, result analysis, drawers.
- Modify `src/fluid_scientist/api/app.py`: serve the new state module and expose remote PID/status timestamps only if absent from existing response models.
- Modify `tests/api/test_web_assets.py`: UTF-8 copy, conversation structure, controller endpoint order, no false submitted state, recovery and advanced-path coverage.
- Modify `tests/api/test_app.py`: add response-contract coverage only if API fields are added.
- Create `tests/web/test_workbench_state.mjs`: run pure JavaScript state tests with Node without introducing a frontend framework.

### Task 1: Lock the conversation and truthful-status contract

**Files:**
- Modify: `tests/api/test_web_assets.py`
- Create: `tests/web/test_workbench_state.mjs`

- [ ] **Step 1: Replace mojibake assertions with failing UTF-8 and conversation-shell assertions**

Assert that `index.html`, `app.js`, and `workbench-state.js` decode as UTF-8; reject representative mojibake fragments; require `experiment-prompt`, `design-experiment`, `conversation-stream`, `task-context`, `model-settings`, and `custom-case-drawer`.

```python
def test_workbench_is_a_utf8_conversation_first_interface() -> None:
    assets = {
        path: (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "apps/web/index.html",
            "apps/web/app.js",
            "apps/web/workbench-state.js",
        )
    }
    combined = "".join(assets.values())
    assert 'id="conversation-stream"' in assets["apps/web/index.html"]
    assert 'id="experiment-prompt"' in assets["apps/web/index.html"]
    assert "设计实验" in combined
    for broken in ("瀹為獙", "绉戠爺", "宸ヤ綔绔?"):
        assert broken not in combined
```

- [ ] **Step 2: Add failing controller-order assertions**

Require `designExperimentFromPrompt`, `confirmAndSubmitPlan`, `renderTaskCard`, and `restoreActiveExperiment`. Verify the controller source contains plan creation before compile, compile before Gate approval, and approval before submit. Verify the submitted label is assigned only inside a branch that has `external_job_id` or `job_id`.

- [ ] **Step 3: Add a failing Node test for task-state view models**

```javascript
import assert from "node:assert/strict";
import { taskView } from "../../apps/web/workbench-state.js";

assert.equal(taskView({ phase: "submitting" }).label, "正在提交");
assert.equal(
  taskView({ phase: "submitted", jobId: "job-42", pid: 321 }).label,
  "已到达工作站",
);
assert.throws(() => taskView({ phase: "submitted" }), /Job ID/);
```

- [ ] **Step 4: Run RED checks**

Run:

```powershell
python -m pytest tests/api/test_web_assets.py -q
node tests/web/test_workbench_state.mjs
```

Expected: failures for the missing state module, missing conversation IDs, mojibake copy, and missing state guards.

- [ ] **Step 5: Commit the contract tests**

```powershell
git add tests/api/test_web_assets.py tests/web/test_workbench_state.mjs
git commit -m "test: define conversational workbench contract"
```

### Task 2: Implement pure truthful task state

**Files:**
- Create: `apps/web/workbench-state.js`
- Test: `tests/web/test_workbench_state.mjs`

- [ ] **Step 1: Implement the smallest state module that passes the Node test**

Export immutable persistence keys and `taskView(task)`. Reject `submitted`, `mesh_check`, `solving`, `collecting`, `completed`, `failed`, and `cancelled` states without a job ID. Map phases to explicit Chinese labels and include a tone, percent, and safe detail string.

```javascript
export const storageKeys = Object.freeze({
  projectId: "fluid-scientist-project-id",
  planId: "fluid-scientist-plan-id",
  caseId: "fluid-scientist-case-id",
  targetId: "fluid-scientist-target-id",
});

export function taskView(task) {
  const remote = new Set(["submitted", "mesh_check", "solving", "collecting", "completed", "failed", "cancelled"]);
  if (remote.has(task.phase) && !task.jobId) throw new Error("Remote state requires Job ID");
  // Return the phase-specific label/tone/progress/detail mapping.
}
```

- [ ] **Step 2: Run the Node state test**

Run: `node tests/web/test_workbench_state.mjs`

Expected: no output and exit code 0.

- [ ] **Step 3: Commit pure state**

```powershell
git add apps/web/workbench-state.js tests/web/test_workbench_state.mjs
git commit -m "feat: add truthful experiment task state"
```

### Task 3: Build the semantic conversation shell

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/styles.css`
- Modify: `tests/api/test_web_assets.py`

- [ ] **Step 1: Replace the dashboard DOM with the approved shell**

Create:

- header with model and platform status buttons;
- `main.workbench` containing `#conversation-stream` and a sticky composer;
- `#experiment-prompt` with a real natural-language placeholder;
- `#task-context` right rail;
- native `<dialog>` elements for model settings and custom-case upload;
- `<template>` elements for assistant messages, plan cards, task cards, error cards, and result cards.

Keep no Skill UI. Use correct Chinese source text throughout.

- [ ] **Step 2: Implement the scientific-notebook style system**

Use CSS variables for paper, ink, teal, amber, red, borders, and shadows. Use a readable Chinese-first font stack, 680–760 px conversation measure, cards with strong state headings, visible focus, semantic disabled styles with adjacent explanations, mobile single-column layout, and `prefers-reduced-motion` support.

- [ ] **Step 3: Run focused asset tests**

Run: `python -m pytest tests/api/test_web_assets.py -q`

Expected: structural/UTF-8 tests pass; controller tests may remain RED until Task 4.

- [ ] **Step 4: Commit the shell**

```powershell
git add apps/web/index.html apps/web/styles.css tests/api/test_web_assets.py
git commit -m "feat: build conversational research shell"
```

### Task 4: Connect natural language to model planning

**Files:**
- Rewrite: `apps/web/app.js`
- Modify: `apps/web/index.html`
- Test: `tests/api/test_web_assets.py`

- [ ] **Step 1: Implement one API utility and initialization path**

Implement `requestJson`, `loadModelConfiguration`, `loadExecutionTargets`, `restoreActiveExperiment`, and `init`. Render factual model/target availability and explain why the composer is blocked when unavailable.

- [ ] **Step 2: Implement `designExperimentFromPrompt`**

On submit:

1. append the researcher message;
2. create a project when no active project exists;
3. POST the exact natural-language text and selected target to `/api/experiment-plans`;
4. persist returned non-secret IDs;
5. render a plan review card from `response.plan`;
6. render typed model/API errors in the stream.

Never use the fake closed-loop endpoint for the primary composer.

- [ ] **Step 3: Render the concise plan and expandable details**

Show title, objective, experiment type, core geometry/physics, mesh, end time/time step, convergence, outputs, assumptions, and limitations. Place full boundaries/sweeps/raw details inside `<details>`. Add exactly one primary button, **确认并提交**.

- [ ] **Step 4: Run focused tests and syntax check**

Run:

```powershell
python -m pytest tests/api/test_web_assets.py -q
node --check apps/web/app.js
```

Expected: natural-language planning and UTF-8 tests pass.

- [ ] **Step 5: Commit planning interaction**

```powershell
git add apps/web/app.js apps/web/index.html tests/api/test_web_assets.py
git commit -m "feat: design experiments from conversation"
```

### Task 5: Implement one-click compile, approve, and submit

**Files:**
- Modify: `apps/web/app.js`
- Modify: `apps/web/workbench-state.js`
- Modify: `tests/api/test_web_assets.py`
- Modify: `tests/web/test_workbench_state.mjs`

- [ ] **Step 1: Expand RED tests for ordered transitions**

Require `confirmAndSubmitPlan` to set `preparing`, call the plan compile endpoint, render its digest, approve the exact `plan_id`, `plan_version`, and `archive_sha256`, set `submitting`, and only set `submitted` after reading a returned job ID.

- [ ] **Step 2: Implement `confirmAndSubmitPlan`**

Disable duplicate confirmation while active. Narrate each server-confirmed step in the card. Use the existing Gate endpoint sequence required by current workflow state, then POST `/api/projects/${projectId}/experiment-plans/${planId}/submit` with `target_id` and deterministic `case_id`.

- [ ] **Step 3: Render external identity immediately**

After submit returns, store and display target label, external Job ID, remote PID if present, and submission timestamp. If the response is lost, call project recovery/status rather than generating a new case ID.

- [ ] **Step 4: Run controller/state tests**

Run:

```powershell
python -m pytest tests/api/test_web_assets.py -q
node tests/web/test_workbench_state.mjs
node --check apps/web/app.js
```

Expected: all pass.

- [ ] **Step 5: Commit submission flow**

```powershell
git add apps/web/app.js apps/web/workbench-state.js tests/api/test_web_assets.py tests/web/test_workbench_state.mjs
git commit -m "feat: confirm and submit approved experiments"
```

### Task 6: Poll, recover, and display real results

**Files:**
- Modify: `apps/web/app.js`
- Modify: `apps/web/workbench-state.js`
- Modify: `tests/api/test_web_assets.py`
- Modify: `tests/web/test_workbench_state.mjs`

- [ ] **Step 1: Add failing recovery and completion tests**

Require restoration from project/plan/case/target keys, polling only when the project has an external job binding, rendering failure details with the job ID, and rendering completion only from structured results.

- [ ] **Step 2: Implement `pollPlannedExperiment` and `restoreActiveExperiment`**

Use project workflow and plan result endpoints. Update `lastUpdated` on every response. Use bounded backoff and stop timers on completed, failed, cancelled, or navigation teardown. Never resubmit during restore.

- [ ] **Step 3: Render deterministic result and analysis cards**

Show mesh pass/cells/quality, solver completion/residuals/continuity, numeric times, pipe metrics or cylinder coefficients or cavity probes, and the `.foam` marker. Keep **模型分析结果** separate and POST the existing analysis endpoint only on researcher action.

- [ ] **Step 4: Expose browser post-processing and advanced ParaView guidance**

Render structured results directly in the page. Put workstation ParaView instructions under an advanced disclosure; do not label a bare case path as post-processing output.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests/api/test_web_assets.py -q
node tests/web/test_workbench_state.mjs
node --check apps/web/app.js
```

Expected: all pass.

- [ ] **Step 6: Commit polling and results**

```powershell
git add apps/web/app.js apps/web/workbench-state.js tests/api/test_web_assets.py tests/web/test_workbench_state.mjs
git commit -m "feat: restore and trace remote experiment tasks"
```

### Task 7: Restore advanced model and custom-case paths

**Files:**
- Modify: `apps/web/app.js`
- Modify: `apps/web/index.html`
- Modify: `tests/api/test_web_assets.py`

- [ ] **Step 1: Add failing drawer-path tests**

Require OpenAI/GLM/DeepSeek provider selection, editable model ID, password API key with no browser persistence, execution target selection, custom archive validation before submit, and custom job polling.

- [ ] **Step 2: Implement model and target drawers**

Configure `/api/model-configurations`, clear the password field after every request, reload model status, and keep only target ID in local storage. Use native dialog focus and close behavior.

- [ ] **Step 3: Implement the custom-case drawer**

Preserve local validation, fixed upload endpoint, target selection, explicit submit, remote Job ID rendering, polling, and structured collection. Insert its task card into the same conversation stream.

- [ ] **Step 4: Run focused tests and commit**

Run:

```powershell
python -m pytest tests/api/test_web_assets.py -q
node --check apps/web/app.js
```

Expected: all pass.

```powershell
git add apps/web/app.js apps/web/index.html tests/api/test_web_assets.py
git commit -m "feat: preserve advanced experiment paths"
```

### Task 8: Full verification and live workstation acceptance

**Files:**
- Modify: `docs/acceptance/2026-07-03-conversational-workbench.md`
- Modify: `skills/fluid-research-workflow/SKILL.md` only if the live UX test reveals reusable process knowledge, after skill RED/GREEN validation.

- [ ] **Step 1: Run the complete local quality gate**

Run:

```powershell
python -m pytest -q
python -m ruff check .
node --check apps/web/workbench-state.js
node --check apps/web/app.js
node tests/web/test_workbench_state.mjs
```

Expected: all automated tests pass; only environment-gated OpenFOAM smoke tests may skip on Windows.

- [ ] **Step 2: Restart the local API with workstation runtime configuration**

Start the latest source with the already verified SSH identity and known-hosts configuration. Reconfigure the selected model only in server memory. Confirm `/api/model-configurations` and `/api/execution-targets` return configured/available states without exposing secrets.

- [ ] **Step 3: Execute a browser/API acceptance journey**

Use a short natural-language smoke request. Verify the model-generated plan appears, confirmation compiles/approves/submits the exact digest, the page shows the real remote Job ID/PID, polling follows the same job, and completion exposes structured results and analysis. Verify refresh recovery before completion by reloading once after submission.

- [ ] **Step 4: Record truthful acceptance evidence**

Create `docs/acceptance/2026-07-03-conversational-workbench.md` with the prompt category, provider/model (no key), plan ID, digest, job ID, timestamps, state transitions, structured result fields, refresh-recovery evidence, limitations, and any unaccepted path.

- [ ] **Step 5: Run final clean-tree verification and commit**

```powershell
git diff --check
git status --short
git add apps/web src/fluid_scientist/api tests docs/acceptance skills/fluid-research-workflow
git commit -m "feat: deliver conversational experiment workbench"
```

- [ ] **Step 6: Push the verified branch**

Run: `git push origin feature/real-integration-backbone`

Expected: local HEAD equals `origin/feature/real-integration-backbone` and the working tree is clean.
