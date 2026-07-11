# V5 Runtime And Feature Audit

Audit time: 2026-07-11, Asia/Shanghai

## Executive Finding

The current environment has multiple Fluid Scientist source trees and multiple runnable services. The UI regression is not a browser-only cache issue. It is caused by running different Python interpreters against different source roots.

The most complete three-panel Chatbot workbench found on this machine is:

- Source root: `D:\desktop\local deep research\fluid_scientist`
- Branch: `feature/v5-study-decomposer-draft-workflow`
- HEAD: `af0258bb75f9bb782a0cb3c72a6af626312eba46`
- Three-panel UI commit: `d87dc51 feat(v5-ui): three-panel conversational workbench layout`
- Latest local commit on that line: `af0258b test(e2e): add real Playwright browser E2E tests for V5 workflow`
- Remote status: no remote branch contains `af0258b` or `d87dc51` according to `git branch -r --contains`; this code appears local-only in that clone.

The remote latest `origin/feature/v5-study-decomposer-draft-workflow` in the Codex clone is:

- `183cb64c6b1ec1d3cc9e821e8d9e43659bba77a5 docs: add V5 refactor spec`

That remote branch does not include the fuller local Trae/Codex clone commits `d87dc51..af0258b`.

## Required Git Baseline Commands

Run from `D:\desktop\AI FOR SCIENCE`:

```text
git status
HEAD detached at e248e0a
nothing to commit, working tree clean

git branch --show-current
<empty because detached HEAD>

git rev-parse HEAD
e248e0ae3a35b6a6626d1785fc95284dc7d1baac

git log --oneline -20
e248e0a fix(v5): route selected research tasks through compile pipeline
a0bab98 fix(v5): gate research drafts on compile-ready validation
bcaedb5 feat(v5): implement compile-readiness pipeline with static validation, incremental modification, and multi-case support
0e6e771 feat(api): expose compile-ready pipeline endpoints
0abdc1b refactor(workflow): gate draft publication on compile readiness
f7c7961 feat(case): generate and validate real OpenFOAM cases
7674cf5 feat(capability): add unified capability registry with 60+ native capabilities
2ec8f19 feat(metrics): compile scientific goals into executable measurements
5a43fc0 feat(closure): add dependency-based design closure engine
c13776f feat(intent): add generic scientific intent schemas
900bfcd fix(v5): close legacy research drafts with metrics
90b4360 feat(v5): synthesize complete draft designs and metrics
f1f9a70 feat(workstation): automated configuration dialog with SSH auto-detection
95bd6e9 fix(frontend): show workstation error messages to user
b0a6038 fix(api): mount v5_router into FastAPI app
068b8cd refactor(v5): unify duplicate models, fix circular imports, add readiness check, LLM hints
22a77fa fix(v5): wire LLM, persistence, clarification planner, and session transitions
742c91b feat(v5): complete all 20 audit gaps - models, persistence, LLM, API, extractors
7f1f0df feat(api): v5 workflow API router and E2E integration test
3cca068 feat(case-plan): CasePlan, NativeCaseCompiler, CodeExtension closed loop

git remote -v
origin https://github.com/bamboo09/fluid_scientist.git (fetch)
origin https://github.com/bamboo09/fluid_scientist.git (push)
```

`git worktree list --porcelain` from `D:\desktop\AI FOR SCIENCE`:

```text
worktree D:/desktop/AI FOR SCIENCE
HEAD e248e0ae3a35b6a6626d1785fc95284dc7d1baac
detached

worktree D:/desktop/AI FOR SCIENCE/.worktrees/v5-dialogue-draft-mainline
HEAD e248e0ae3a35b6a6626d1785fc95284dc7d1baac
detached

worktree D:/desktop/AI FOR SCIENCE/.worktrees/v5-three-panel
HEAD 69c4eca15a9e6ab7638de29baf7caa122da81341
branch refs/heads/feature/v5-three-panel-layout
```

## Discovered Source Trees

| Path | Kind | Branch / HEAD | Status | Notes |
|---|---|---|---|---|
| `D:\desktop\AI FOR SCIENCE` | Main Codex clone | detached `e248e0a` | clean | Currently restored to the 13:00-near commit. |
| `D:\desktop\AI FOR SCIENCE\.worktrees\v5-dialogue-draft-mainline` | Git worktree | detached `e248e0a` | clean at last check | Temporary worktree created during version lookup. |
| `D:\desktop\AI FOR SCIENCE\.worktrees\v5-three-panel` | Git worktree | `feature/v5-three-panel-layout`, `69c4eca` | clean | Contains a three-panel UI commit pushed to `origin/feature/v5-three-panel-layout`; not the fuller Trae clone UI. |
| `D:\desktop\local deep research\fluid_scientist` | Separate clone, likely Trae source | `feature/v5-study-decomposer-draft-workflow`, `af0258b` | clean | Contains the richer three-panel UI and V5 backend commits; not pushed to any visible remote branch. |

Other directories seen:

- `D:\desktop\algorithm_research\.trae`
- `D:\desktop\local deep research\.trae-html-share-packages`

No `docker-compose*` files were found under `D:\desktop\AI FOR SCIENCE` during the audit. Docker image/container audit is therefore not yet applicable from this workspace, but should be re-run if a Docker deployment path is introduced.

## Three-Panel UI Evidence

### Rich local-only UI

Source:

- `D:\desktop\local deep research\fluid_scientist\apps\web\index.html`
- `D:\desktop\local deep research\fluid_scientist\apps\web\v5-app.js`
- `D:\desktop\local deep research\fluid_scientist\apps\web\styles.css`

Evidence:

- `index.html` contains `Fluid Scientist · V5 对话式科研工作台`
- `index.html` contains `研究任务`
- `index.html` contains `当前会话的研究任务`
- `index.html` contains `研究方案`
- `v5-app.js` contains `Three-panel layout: Left (Session/Studies), Center (Chat), Right (Draft)`
- `v5-app.js` contains `对草案提出修改（自然语言）`
- `v5-app.js` contains `确认草案`

Commit lineage in that clone:

```text
af0258b test(e2e): add real Playwright browser E2E tests for V5 workflow
c4ef9d3 feat(workstation): connect v5 cases to remote execution
2bb21c8 feat(case-compiler): generate valid OpenFOAM dictionary files
4d4a027 feat(v5-storage): SQLite unified persistence for all V5 entities
1a7ed58 feat(v5-draft): improve proposal diff display and state management
d87dc51 feat(v5-ui): three-panel conversational workbench layout
f1f9a70 feat(workstation): automated configuration dialog with SSH auto-detection
...
```

Remote status:

```text
git branch -r --contains af0258b
<empty>

git branch -r --contains d87dc51
<empty>
```

Conclusion: this richer UI exists as committed local code, but it is not present on the visible GitHub remote branches.

### Pushed but smaller three-panel UI

Source:

- `D:\desktop\AI FOR SCIENCE\.worktrees\v5-three-panel\apps\web\index.html`
- `D:\desktop\AI FOR SCIENCE\.worktrees\v5-three-panel\apps\web\app.js`
- `D:\desktop\AI FOR SCIENCE\.worktrees\v5-three-panel\apps\web\styles.css`

Branch and commit:

- `feature/v5-three-panel-layout`
- `69c4eca15a9e6ab7638de29baf7caa122da81341 feat: three-panel layout (task sidebar + dialogue + scheme panel)`
- Remote branch exists: `origin/feature/v5-three-panel-layout`

Evidence:

- `index.html` contains `task-sidebar`
- `index.html` contains `研究任务`
- `index.html` contains `scheme-panel`
- `index.html` contains `研究方案`
- `app.js` contains `三面板：研究任务列表`
- `app.js` contains `三面板：研究方案渲染`

Conclusion: this branch is pushed, but it is not the richer `v5-app.js` implementation from the other clone.

## Current Running Processes

Listening ports:

```text
0.0.0.0:8000     LISTENING PID 4152
127.0.0.1:8009   LISTENING PID 15488
```

### Port 8000

Process:

```text
PID: 4152
Executable: C:\Users\baoxu\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe
CommandLine: "...\python.exe" -m uvicorn fluid_scientist.api.app:app --host 0.0.0.0 --port 8000
```

Import path proof, queried with the same Trae Python executable:

```text
fluid_scientist:
D:\desktop\local deep research\fluid_scientist\src\fluid_scientist\__init__.py

fluid_scientist.api.app:
D:\desktop\local deep research\fluid_scientist\src\fluid_scientist\api\app.py
```

HTTP evidence:

```json
{
  "workflow": "v5",
  "git_commit": "e248e0a",
  "api_version": "5.0",
  "schema_version": "5.0"
}
```

UI evidence from `GET /`:

```text
8000_UI=v2-context
contains: conversation-stream, task-context, 实验记录
does not expose the expected three-panel V5 entry
```

Classification:

- Current service type: B/C/E mixture.
- It is a local clone loaded via Trae's Python environment and editable/import path.
- Frontend/backend source root: `D:\desktop\local deep research\fluid_scientist`
- Runtime command does not expose cwd through `Win32_Process`; package import path is confirmed.

### Port 8009

Process:

```text
PID: 15488
Executable: D:\python\python.exe
CommandLine: python -m uvicorn fluid_scientist.api.app:create_app --factory --host 127.0.0.1 --port 8009
Launch cwd: D:\desktop\AI FOR SCIENCE
Launch env used by Codex: PYTHONPATH=D:\desktop\AI FOR SCIENCE\src
```

Import path proof:

```text
fluid_scientist:
D:\desktop\AI FOR SCIENCE\src\fluid_scientist\__init__.py

fluid_scientist.api.app:
D:\desktop\AI FOR SCIENCE\src\fluid_scientist\api\app.py
```

HTTP evidence:

```json
{
  "workflow": "v2",
  "git_commit": "e248e0a",
  "api_version": "2.0",
  "schema_version": "2.0"
}
```

UI evidence from `GET /`:

```text
8009_UI=v2-context
contains: conversation-stream, task-context, 当前上下文, 实验记录
```

Classification:

- Current service type: B.
- It is a local worktree/clone started manually by Codex from `D:\desktop\AI FOR SCIENCE`.

## Why Restart Regresses The UI

1. The machine has at least two source roots with different histories:
   - `D:\desktop\AI FOR SCIENCE`
   - `D:\desktop\local deep research\fluid_scientist`
2. Port `8000` is controlled by Trae's Python executable and imports from `D:\desktop\local deep research\fluid_scientist`.
3. Manual Codex runs have used other worktrees and ports (`8007`, `8008`, `8009`).
4. The richer three-panel UI is committed only in the local clone at `D:\desktop\local deep research\fluid_scientist`, not in the remote feature branch visible from `D:\desktop\AI FOR SCIENCE`.
5. The same short SHA `e248e0a` can still serve different build metadata (`workflow v5` on 8000, `workflow v2` on 8009), proving there are local edits or divergent code around runtime identity.

## Existing Strategy Completion Matrix

This matrix is based on static source inspection plus the runtime checks above. It does not claim completion unless an endpoint/runtime/test was identified.

| Module | Code exists | Enters current main chain | Real call | Tests | Runtime verified | Status |
|---|---|---|---|---|---|---|
| Session management | `draft_session`, `research`, `api/v5_router.py` | `/api/research-sessions` and `/api/v5/session` both exist | Partial | `tests/research`, `tests/api/test_v5_*` | Not end-to-end verified in browser | PARTIALLY_WORKING |
| Study decomposition | `study_decomposition` | Used by V5 router/session flow | Partial | `tests/study_decomposition` | Not runtime traced from browser | PARTIALLY_WORKING |
| active Study selection | `api/v5_router.py` select endpoint around study selection | Present in V5 router | Partial | API tests present | Not browser verified | PARTIALLY_WORKING |
| Real LLM provider | `llm`, `services/model_configuration.py`, provider endpoints | Model config endpoints exist | Needs runtime log proof | Provider tests exist | Not verified against GLM in this audit | PARTIALLY_WORKING |
| Intent recognition | `draft_session/input_router.py`, `research` | Present | Partial | V5 clarification/research tests | Not runtime traced | PARTIALLY_WORKING |
| Draft context recognition | `draft_session`, `draft/change_agent.py` | Present | Partial | Draft tests present | Not runtime traced | PARTIALLY_WORKING |
| Modify vs new experiment | `InputRouter`, `DraftChangeAgent` | Present | Partial | Some tests present | Not browser verified | PARTIALLY_WORKING |
| Scientific Intent | `study_decomposition`, `physics`, `research` | Present | Partial | Unit tests | Not runtime traced | PARTIALLY_WORKING |
| Complete experiment design | `draft/draft_generator.py`, `workbench` | Present | Partial | Draft/API tests | UI still shows missing in prior runs | PARTIALLY_WORKING |
| Auto-fill parameters | `workbench/physics_spec_builder.py`, closure modules | Present | Partial | Closure tests | Prior UI showed missing fields | PARTIALLY_WORKING |
| Dependency closure | `workbench/requirement_graph.py`, closure engine | Present | Partial | Tests present | Not runtime traced | PARTIALLY_WORKING |
| Goal to metric planner | `metric_spec`, `measurement`, `workbench` | Present | Partial | Metric tests likely present | Not runtime traced | PARTIALLY_WORKING |
| Boundary verification metrics | `measurement`, `metric_spec` | Present | Unknown | Unknown | Not runtime traced | EXISTS_BUT_NOT_CONNECTED |
| Capability Registry | `capabilities/registry.py` | Used by validation/preview | Partial | Capability tests | Not runtime traced | PARTIALLY_WORKING |
| Capability Resolver | `capabilities`, `study_decomposition` | Present | Partial | Tests present | Not runtime traced | PARTIALLY_WORKING |
| Code Extension | `code_extension` | Present | Unknown | Unknown | Not runtime traced | EXISTS_BUT_NOT_CONNECTED |
| CasePlan | `case_plan`, `api/v5_router.py` case-plan endpoints | Present | Partial | CasePlan tests likely present | Prior UI generated CasePlan with issues | PARTIALLY_WORKING |
| OpenFOAM Case Compiler | `case_generation`, `case-compiler` commits in local clone | Present | Partial | Case compiler tests | Not workstation verified | PARTIALLY_WORKING |
| compile-readiness | `validation`, `draft` | Present | Partial | Readiness tests | Runtime showed blocking issues | PARTIALLY_WORKING |
| Workstation upload/submit | `execution_targets`, `api/v5_router.py` submit endpoints | Present | Partial | Execution target tests | Not live verified | PARTIALLY_WORKING |
| Job status write-back | `worker`, `operations`, V5 submit endpoints | Present | Unknown | Some API tests | Not runtime verified | PARTIALLY_WORKING |
| Session persistence | SQLite repos in local clone | Present | Partial | SQL repo tests | Runtime persistence not audited | PARTIALLY_WORKING |
| Frontend Chatbot workbench | `apps/web/v5-app.js` in local clone, `apps/web/app.js` in pushed three-panel branch | Present | Fragmented | E2E exists in local clone | 8000/8009 do not show final three-panel | BROKEN |
| Proposal confirmation | `DraftChangeAgent`, V5 router proposal endpoints, `v5-app.js` | Present | Partial | Draft tests | Not browser verified | PARTIALLY_WORKING |
| Browser E2E | `af0258b` local clone adds Playwright E2E | Present only local clone | Not in remote feature branch | Yes local | Not run in this audit | EXISTS_BUT_NOT_CONNECTED |

## Immediate Integration Recommendation

Do not continue development from detached `e248e0a` or from ad hoc ports.

Recommended next step:

1. Create `integration/v5-chatbot-workbench` from `origin/feature/v5-study-decomposer-draft-workflow`.
2. Cherry-pick or merge the local-only useful commits from `D:\desktop\local deep research\fluid_scientist`, at minimum:
   - `d87dc51 feat(v5-ui): three-panel conversational workbench layout`
   - `1a7ed58 feat(v5-draft): improve proposal diff display and state management`
   - `4d4a027 feat(v5-storage): SQLite unified persistence for all V5 entities`
   - `2bb21c8 feat(case-compiler): generate valid OpenFOAM dictionary files`
   - `c4ef9d3 feat(workstation): connect v5 cases to remote execution`
   - `af0258b test(e2e): add real Playwright browser E2E tests for V5 workflow`
3. Create one clean runtime worktree, for example `.worktrees/v5-chatbot-runtime`.
4. Add `/api/system/build-info` before further UI work so every service can identify source root, branch, SHA, package path, frontend root, and asset hash.
5. Add one startup script that refuses to run from dirty or wrong branches.
6. Stop Trae PID `4152` or re-point it to the unified runtime only after the integration branch is created.

## Known Gaps From Phase A

- `Win32_Process` does not expose process cwd; cwd for PID `4152` remains unknown, but its Python import path is confirmed.
- Docker was not found under the Codex clone; Docker-specific runtime identity still needs a separate check if containers exist outside this repo.
- `/api/system/build-info` does not exist yet.
- No browser E2E was run in this audit phase.
- Workstation auto-discovery is not yet proven; current endpoint can still inspect configured paths and has shown permission errors against `C:\Users\baoxu\.ssh\fluid_scientist_ed25519` in a prior run.
