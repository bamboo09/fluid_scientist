# Conversational experiment workbench design

Date: 2026-07-03

## Problem

The current workbench exposes many backend capabilities at once but does not present one obvious path from a natural-language research question to a remotely executed experiment. The natural-language field is visually buried, model planning and deterministic execution look like unrelated actions, and users cannot tell whether a case reached the workstation. Several Chinese strings are also mojibake rather than valid user-facing copy.

The redesign must make the real workflow legible without weakening its safety boundaries: models design schema-valid experiments, trusted code compiles cases, the researcher confirms the exact plan, and the worker returns verifiable remote state.

## Product decision

Use a conversation-first workbench. Preserve the existing API and OpenFOAM integration, but replace the dashboard information architecture with a guided conversation and stateful task cards. Do not introduce a frontend framework in this iteration; improve the existing HTML, CSS, and JavaScript so the change remains small enough to verify against the live service.

## Primary journey

1. The researcher describes an experiment in natural language.
2. The application creates or resumes a project and sends the prompt to the configured OpenAI, GLM, or DeepSeek planner.
3. The model returns a provider-neutral experiment plan.
4. The conversation renders a review card containing the experiment type, objective, decisive parameters, mesh, runtime controls, requested outputs, assumptions, and limitations.
5. The researcher chooses one primary action: **Confirm and submit**.
6. The client deterministically compiles the plan, displays and binds its digest, records the required approval, and submits the exact approved bytes to the selected execution target.
7. A task card changes state using API and worker responses, not elapsed-time animation.
8. On completion, the card exposes deterministic results, browser post-processing, and evidence-bound model analysis.

Advanced model configuration and custom OpenFOAM archive upload remain available from secondary drawers. They do not compete with the primary journey.

## Page structure

### Header

Show the product name, current model, selected execution platform, and a concise connection indicator. Model and platform controls open drawers. Do not place credentials or detailed infrastructure configuration in the main flow.

### Conversation stream

Use the central column for ordered messages and cards:

- assistant welcome and examples;
- researcher prompt;
- planning activity;
- experiment-plan review card;
- approval/submission activity;
- live remote-task card;
- result summary and analysis card.

The stream is the source of truth for what happened. It must remain understandable after a page refresh.

### Composer and active question

Show the large multiline composer only before a research question is submitted or when planning fails and the text needs revision. Once submitted, replace the welcome panel with one prominent **Current research question** heading at the top of the stream, clear and hide the composer, and expose only a compact **Research another question** action. Restore the same heading from the server-side project question after refresh; never duplicate the prompt as a second chat bubble.

### Context rail

Use a narrow right rail for current context only:

- model provider and model ID;
- selected workstation or HPC target and capability state;
- active project name;
- active task state and last update;
- links to model settings, platform selection, custom case upload, and past experiments.

Do not reproduce the full dashboard or expose Skill governance in this rail.

## Experiment-plan review card

The default view shows:

- readable experiment title and experiment type;
- objective;
- geometry and key physical inputs;
- mesh size or resolution controls;
- end time, time step, and convergence target;
- requested outputs;
- explicit assumptions, risks, and limitations;
- target platform;
- deterministic compiler preview and archive digest after compilation.

Detailed boundary conditions, sweeps, and raw schema fields are collapsed under **View all parameters**. The only primary action is **Confirm and submit**. Editing the natural-language request creates a new plan version rather than silently mutating an approved plan.

When the user confirms, the client may perform compilation and Gate binding in sequence, but the interface must narrate each completed action. It must never say **Submitted** before the submit response contains an external job ID.

## Remote-task state model

Render one persistent task card with these states:

| UI state | Required evidence | Visible information |
| --- | --- | --- |
| Preparing | Plan and project IDs | Compiler/approval action in progress |
| Submitting | Approved digest and target ID | Selected target; no success language |
| Submitted | External job ID | Job ID, target, submission time, remote PID when available |
| Mesh check | Worker running/status payload | Current stage and latest update |
| Solving | Worker running/status payload | Solver stage and polling status |
| Collecting | Remote success plus collection request | Mesh and solver completion pending collection |
| Completed | Structured collection | Job ID, mesh, residuals, numeric times, observables, `.foam` marker |
| Failed | Typed API/worker error | Failing stage, exact safe error, job ID if assigned, retry/revise action |
| Cancelled | Confirmed remote cancellation | Job ID and cancellation time |

The task card distinguishes execution completion from scientific credibility. A successful solver can still show coarse-mesh, residual, conservation, or missing-validation warnings.

## Persistence and recovery

Persist only non-secret navigation identifiers in browser storage: active project ID, plan ID, case ID, and selected target ID. API keys remain only in server-process memory.

On load:

1. retrieve the active or recent project, including its original research question;
2. reconstruct plan and approval state;
3. restore an external job binding when present;
4. resume status or result collection for running jobs;
5. render completed data without resubmitting anything.

Retries must reuse deterministic job IDs. A lost HTTP response must result in status recovery, not duplicate submission.

## Error handling

Show errors at the operation that failed:

- model unavailable or invalid model output;
- deterministic compilation rejection;
- approval/digest mismatch;
- target unavailable;
- SSH/worker submission failure;
- remote mesh or solver failure;
- collection or analysis failure.

Messages use clear Chinese and retain safe technical detail such as stage, target type, external job ID, and worker error. Never expose API keys, local identity paths, private host details, or arbitrary remote paths.

Provide an action appropriate to the failure: reconnect model, revise request, compile again as a new version, retry the idempotent submission, continue polling, or inspect results. Do not use a generic **Something went wrong** state.

## Visual direction and accessibility

Use a restrained scientific-notebook aesthetic: warm off-white workspace, graphite text, deep teal state accents, and amber warnings. Prefer readable Chinese typography, generous line height, clear card hierarchy, and subtle grid/paper texture. Avoid a generic chat clone and avoid the current dense terminal/dashboard look.

Use semantic forms, headings, status regions, keyboard-accessible drawers, visible focus states, reduced-motion support, and responsive single-column behavior. All source files and user-facing Chinese strings must be valid UTF-8 without mojibake.

## API boundaries

Reuse the existing endpoints for model configuration, execution-target capability, plan creation, compilation, Gate approval, plan submission, job status/results, custom case validation/submission, and result analysis. Add backend fields or endpoints only when the current response cannot provide a required factual status, such as a remote PID or a recoverable current plan binding.

The client must not infer remote success from button clicks, timers, or local state. Every status transition after submission is driven by a server response.

## Testing and acceptance

Add frontend asset and API-flow tests before implementation. Acceptance requires:

1. A visible natural-language composer calls the model-planning endpoint.
2. One confirmation action performs deterministic compile, exact-artifact approval, and submission in order.
3. **Submitted** is rendered only after an external job ID is returned.
4. The task card displays target, external job ID, remote PID when available, current stage, last update, and safe failures.
5. Refreshing the page restores a running or completed task without duplicate submission.
6. Cylinder, laminar-pipe, lid-driven-cavity, and custom OpenFOAM paths remain reachable.
7. Result collection exposes mesh, residuals, numeric times, observables, and browser post-processing.
8. Evidence-bound analysis remains separate from deterministic results.
9. Chinese copy is valid UTF-8 and contains no mojibake markers.
10. Existing backend tests, linting, JavaScript syntax validation, and a real workstation smoke submission pass.

## Out of scope

- Replacing the frontend with React, Vue, or another framework.
- Removing human approval or digest binding.
- Letting a model emit or execute OpenFOAM dictionaries or shell commands.
- Claiming publication-grade credibility from smoke cases.
- Building a general chat history, collaboration, billing, or multi-tenant system.
