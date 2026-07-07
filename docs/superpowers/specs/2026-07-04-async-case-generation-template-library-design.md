# Async planning, generated cases, and candidate template library

Date: 2026-07-04

## Problem

The workbench currently treats long model and SSH calls as synchronous button actions. A GLM planning call can take more than 30 seconds, target capability checks can take several SSH timeouts, and the UI does not expose a durable operation identity, stage, elapsed time, or cancellation state. The static browser-postprocessing button is not bound to an action, while the dynamic result card renders its postprocessing content before the user clicks, making the button appear inert.

The built-in deterministic compilers cover only laminar pipe flow, cylinder flow, and lid-driven cavity flow. An unsupported geometry becomes `custom_openfoam` and requires a manually prepared archive. The requested extension is a second model that can author a runnable OpenFOAM case, followed by human review, isolated execution, and an explicit decision about whether the successful case should enter a governed candidate template library.

## Product decisions

1. Long model work becomes a persisted asynchronous operation. The first API response acknowledges the click immediately and returns an operation ID.
2. Planning is independent of current target reachability. The planner uses declared platform capabilities; target `doctor` runs when the user selects or submits to a platform, not before a research plan may exist.
3. Built-in experiment plans continue through trusted deterministic compilers.
4. When no built-in compiler matches, a separately identified Case Builder model may produce a restricted `GeneratedCaseDraft`. It may author OpenFOAM dictionary text but may not author scripts, binaries, dynamic code, shell commands, remote paths, or unrestricted files.
5. Generated files never execute immediately. They pass schema validation, static safety checks, deterministic packaging, human review, digest-bound approval, and an isolated short pilot through the fixed worker protocol.
6. A successful generated case is not automatically a template. The researcher explicitly chooses whether to create a candidate; publication into the active template registry requires repeatable validation and a second human approval.

## User journey

### Built-in experiment

1. The researcher submits a natural-language question.
2. The client receives an operation ID and immediately renders a planning card.
3. The card shows `queued`, `model planning`, `schema correction`, and `ready for review` stages with elapsed time.
4. The reviewed plan compiles deterministically and follows the existing digest-bound approval and submission path.

### Experiment without a built-in template

1. The planning model returns a schema-valid `custom_openfoam` plan and explains why no built-in template matches.
2. The UI offers **Generate candidate case**. It identifies the configured Case Builder provider/model before any request is sent.
3. The Case Builder returns a `GeneratedCaseDraft` containing an allow-listed file manifest, case metadata, expected preprocessing, requested outputs, assumptions, and limitations.
4. Trusted code rejects unsafe paths and content, creates a deterministic archive, and displays the file list, important dictionary sections, validation findings, and archive digest.
5. The researcher approves the exact draft for an isolated pilot or rejects it for regeneration. Approval is bound to draft ID, version, and digest.
6. The worker repeats archive validation, runs only the fixed preprocessing and solver chain under resource/time limits, and returns mesh, solver, time-directory, observable, and postprocessing evidence.
7. After a successful pilot, the UI offers **Add to candidate template library**. Declining leaves the experiment result intact and creates no template.
8. An accepted candidate is tested at bounded parameter points. Only a passing, reproducible candidate can receive final human publication approval and become selectable by future planning requests.

## Operation model and responsiveness

Use a persisted `OperationRecord` with:

- `operation_id`, `kind`, `project_id`, and optional plan/draft IDs;
- `state`: `queued`, `running`, `succeeded`, `failed`, or `cancelled`;
- a typed `stage`, progress message, creation/update timestamps, and safe error;
- result references rather than credentials or raw provider payloads.

Planning and case generation endpoints return HTTP 202 with the operation record. Status polling uses capped backoff; a later SSE transport may reuse the same record without changing semantics. A service restart marks an interrupted in-process operation as retryable rather than successful. Retrying reuses the same idempotency key and does not duplicate a stored plan or draft.

UI requirements:

- acknowledge every primary click within 100 ms;
- disable only the active action and show its spinner, stage, and elapsed time;
- distinguish model wait, schema correction, compiler work, target check, upload, remote execution, collection, and analysis;
- allow cancellation of queued work and discard late provider results from a cancelled operation;
- never require an online execution target merely to design a plan;
- cache successful target capability checks briefly and expose their age;
- preserve operation state across refresh.

## Generated case contract

`GeneratedCaseDraft` contains:

- stable draft ID and version;
- experiment name, objective, geometry summary, and supported parameter schema;
- solver fixed to an approved OpenFOAM Foundation 13 solver;
- allow-listed preprocessing requirements;
- a tuple of UTF-8 text files with normalized relative POSIX paths and bounded sizes;
- requested outputs, expected observables, assumptions, and limitations;
- provider/model identity and source-plan identity for audit, but no credential.

Allowed files are limited to known OpenFOAM case locations such as `0/`, `constant/`, `system/`, and `fluidScientist/`. Reject absolute paths, traversal, links, archives inside archives, executables, binaries, function-code injection, `#codeStream`, unsafe includes, dynamic libraries, system calls, and unknown solvers. The server—not the model—owns tar metadata, file ordering, digest calculation, remote names, and execution commands.

## Candidate template lifecycle

Use a separate candidate registry; do not edit the immutable built-in registry directly.

States:

`DRAFT -> STATIC_VALIDATED -> PILOT_PASSED -> CANDIDATE_APPROVED -> REGRESSION_PASSED -> PUBLISHED`

Each transition records actor, timestamp, source plan/draft, archive digest, worker protocol, OpenFOAM version, validation evidence, and test results. Rejection records a reason and is reversible through a new version. Publication requires:

- static validation of every generated member;
- a successful isolated pilot;
- deterministic regeneration with the same inputs and digest;
- bounded tests at at least two parameter points when the template is parameterized;
- successful mesh and solver collection;
- explicit human approval.

Published templates expose only their reviewed parameter schema to the planning model. Model-authored raw files remain immutable artifacts and are never silently rewritten in place.

## Browser postprocessing

Bind both static and dynamically created postprocessing controls to one controller. On click:

1. show a visible loading state;
2. fetch structured results when they are not already cached;
3. unhide, focus, and scroll to the postprocessing panel;
4. render mesh metrics, residuals, numeric times, observables, and the `.foam` marker;
5. render experiment-specific plots when evidence exists, including cavity centerline velocity profiles and cylinder force histories;
6. show a typed inline error and retry action on collection failure.

Do not label a static text dump as complete browser postprocessing. ParaView remains the advanced workstation path.

## Error handling

- Provider authentication and model-not-found errors are terminal and actionable.
- Schema-only plan or draft failures receive bounded correction retries using sanitized field paths and messages; rejected values are never echoed.
- Target unavailability blocks submission, not planning or review.
- A lost HTTP response recovers through the operation ID or deterministic job ID.
- Generated-case safety rejection cannot be bypassed by approval.
- Remote failure preserves the draft, digest, job ID, logs, and validation evidence for review.
- User-facing messages are Chinese and include the failing stage; raw secrets, private paths, hosts, and provider bodies remain hidden.

## API and storage boundaries

Add typed APIs for operation creation/status/cancellation, generated-case drafts, draft approval and pilot submission, candidate creation, candidate validation, and candidate publication. Persist operation, draft, artifact, approval, candidate, and audit records in the existing repository boundary. Store API keys only in process memory. Do not store generated archives or provider bodies in browser storage.

## Testing and acceptance

Automated acceptance requires:

1. The browser postprocessing button fetches or reveals results and moves focus to the panel.
2. A long planner call returns an operation ID immediately and exposes stage/elapsed state.
3. Refresh restores an active operation without issuing a duplicate provider call.
4. Planning succeeds while the selected workstation is offline; submission remains blocked with a clear reason.
5. GLM cavity grid-independence planning accepts an integer `cells_per_side` sweep.
6. Unsupported experiments route to Case Builder instead of manual archive upload as the only path.
7. Unsafe generated members and directives are rejected before packaging and again by the worker.
8. The user sees and approves the exact draft digest before isolated execution.
9. Template candidacy is offered only after a successful pilot and never auto-publishes.
10. A published template is reproducible, regression-tested, versioned, auditable, and reversible.
11. Existing built-in compilation, approval, workstation recovery, custom upload, and evidence-bound analysis tests remain green.

## Out of scope for the first implementation

- Executing model-generated shell, Python, C++, shared libraries, or arbitrary function objects.
- Automatically publishing a generated case without human approval and regression evidence.
- Replacing ParaView with a full browser-native 3D renderer.
- Distributed multi-worker orchestration; the persisted operation contract should permit it later.
