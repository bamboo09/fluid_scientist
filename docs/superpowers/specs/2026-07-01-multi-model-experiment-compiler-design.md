# Multi-model experiment compiler design

**Date:** 2026-07-01  
**Status:** Approved design, pending implementation plan  
**Scope:** OpenAI, GLM, DeepSeek; model-designed OpenFOAM experiments; workstation execution

## Goal

Turn the current pipe parameter helper and manual custom-case uploader into one traceable workflow:

`research question -> model plan -> deterministic case compilation -> human approval -> workstation execution -> post-processing -> analysis`

The model chooses and explains an experiment, but deterministic code owns files, commands, validation, and execution.

## Non-goals

- Do not let a model emit or execute shell commands.
- Do not treat OpenAI-compatible APIs as behaviorally identical.
- Do not support arbitrary solver names, remote paths, or preprocessing commands.
- Do not infer scientific credibility from solver completion alone.
- Do not persist API keys in browser storage, project files, logs, reports, or Skills.

## Provider architecture

Define one `ExperimentDesigner` protocol returning the same strict `ExperimentPlan`.

### OpenAI adapter

- Use the Responses API and native structured parsing into Pydantic models.
- Accept any user-entered model ID that supports the required endpoint and structured output.
- Keep provider-specific request IDs for audit without storing credentials.

### GLM adapter

- Use the official OpenAI-compatible Chat Completions endpoint.
- Request JSON output, parse the response locally, then validate it against `ExperimentPlan`.
- Reject malformed or schema-incompatible output; retry only bounded transient or empty-output failures.

### DeepSeek adapter

- Use the official OpenAI-compatible Chat Completions endpoint and JSON Output.
- Include explicit JSON instructions and schema in the prompt.
- Treat empty content, truncated JSON, and schema violations as typed provider errors with bounded retry.

### Runtime configuration

The workbench exposes provider, model ID, and API key. Base URLs use fixed official defaults and are not caller-controlled in the first release. Credentials live only in server-process memory and disappear on restart. The response returns provider and model metadata, never the key.

## Unified experiment plan

`ExperimentPlan` contains:

- experiment name and experiment type;
- objective, scientific rationale, assumptions, and limitations;
- fluid properties and operating conditions in SI units;
- geometry parameters;
- boundary conditions;
- mesh strategy and resolution parameters;
- time/iteration controls and convergence targets;
- parameter sweep definitions;
- requested physical outputs and post-processing products;
- expected verification or benchmark method;
- selected execution target capability, without host details.

Use a discriminated union for experiment-specific payloads:

- `laminar_pipe`;
- `cylinder_flow`;
- `lid_driven_cavity`;
- `custom_openfoam`.

Pydantic rejects extra fields, invalid units, impossible ranges, incompatible geometry, and missing required outputs. Model output remains a proposal until Gate 2 approval.

## Capability registry and deterministic compilers

Create an experiment registry. Each entry declares:

- experiment type and human label;
- plan schema;
- supported OpenFOAM version and solver;
- deterministic case compiler;
- allowed preprocessing chain;
- required outputs;
- credibility checks;
- workbench form renderer metadata.

First-release compilers:

1. **Laminar pipe:** retain Hagen-Poiseuille pressure-drop validation.
2. **Cylinder flow:** compile a Foundation 13 case with fixed `blockMesh -> mirrorMesh -> checkMesh -> foamRun` processing, force coefficients, residuals, and time directories.
3. **Lid-driven cavity:** compile a fixed block-mesh incompressible case with velocity probes and residual checks.
4. **Custom OpenFOAM:** preserve safe tar.gz upload and double validation; no generated shell or caller-selected command chain.

Compilers produce an immutable archive, manifest, readable job name, and preview. A plan unsupported by the registry is not silently coerced to pipe parameters; it is routed to custom upload with an explicit explanation.

## End-to-end workflow

1. Researcher enters a research question and selects/configures a provider.
2. Provider returns a strict `ExperimentPlan` based on the registry capabilities.
3. The API validates the plan and returns provider/model metadata plus validation diagnostics.
4. The workbench renders the complete plan, not only pipe parameters.
5. The deterministic compiler builds and validates a preview archive.
6. The researcher reviews the plan, case manifest, expected outputs, and resource estimate.
7. Gate 2 approval binds the approved plan version and archive digest.
8. The target uploads the immutable archive and invokes only the fixed worker protocol.
9. The worker revalidates, executes the allow-listed chain, and collects results even when credibility checks fail.
10. The workbench displays mesh, solver, residual, physical-output, time-directory, and ParaView results.
11. The Results Analyst receives deterministic results and plan scope; its claims remain evidence-linked.

## API surface

- `POST /api/model-configurations`: configure an in-memory OpenAI, GLM, or DeepSeek adapter.
- `GET /api/model-configurations`: return only configured provider/model metadata.
- `GET /api/experiment-capabilities`: list registry experiment types and required plan fields.
- `POST /api/experiment-plans`: ask the configured provider for a strict plan.
- `POST /api/experiment-plans/{plan_id}/compile`: compile and validate an immutable case preview.
- Existing project approval endpoints bind the plan version and archive digest.
- Existing job status and collection contracts remain the execution boundary.

Compatibility aliases may preserve the current `/api/settings/openai` and `/api/experiment-designs` routes during migration, but the workbench uses the provider-neutral routes.

## Workbench design

- Replace the OpenAI-only disclosure with a model-provider card.
- Provider selector: OpenAI, GLM, DeepSeek.
- Model ID remains editable and is initialized to a provider-specific default.
- Show connection state, request failure category, and active provider/model without exposing credentials.
- Replace the pipe-only AI result with a plan review panel containing geometry, physics, mesh, numerics, requested outputs, assumptions, and limitations.
- Select the matching compiler form automatically. Keep custom upload visible for unsupported plans.
- Enable compile only after schema validation; enable submit only after Gate 2 approval.
- After collection, show in-browser results directly and retain the ParaView command as an advanced path.

## Error handling

- Authentication and model-not-found errors are returned without retry.
- Timeouts, connection failures, empty JSON, and truncated JSON receive bounded retry with provider-specific classification.
- Schema-invalid plans return field-level diagnostics and are never compiled.
- Unsupported experiment types route to custom upload; they never fall back to pipe.
- Compilation failures leave no partial approved artifact.
- Remote submission remains idempotent after an external job ID is bound.
- Failed mesh or credibility checks still return inspectable results with `passed=false`.

## Security and privacy

- Redact API keys from representation, responses, logs, exceptions, reports, and Skills.
- Fix official provider base URLs in code/configuration for the first release.
- Validate model output before it crosses the compiler boundary.
- Keep generated archives under size/member limits and validate locally and on the worker.
- Preserve strict SSH host-key verification and fixed remote destinations.
- Keep every executed OpenFOAM command in an allow-list derived from registry capabilities.

## Testing and acceptance

### Provider contract tests

- Each adapter returns the same valid `ExperimentPlan` from representative provider payloads.
- Empty, malformed, truncated, extra-field, and wrong-type payloads fail predictably.
- Credentials never appear in responses, logs, representations, or snapshots.

### Compiler tests

- Pipe, cylinder, and cavity plans render deterministic Foundation 13 archives.
- Archive digests are stable for identical plans.
- Every archive passes the custom-case safety validator.
- Each compiler emits only its declared preprocessing chain and outputs.

### Workflow tests

- Model plan flows into the correct compiler and Gate 2 subject version.
- A cylinder request never populates the pipe form.
- Unsupported geometry routes to custom upload.
- Submission uses the compiled archive digest approved at Gate 2.

### Real acceptance

- Configure each provider with a real key and produce one valid plan.
- Compile and execute at least pipe, cylinder, and cavity cases on the OpenFOAM 13 workstation.
- Collect mesh, solver, residual, time-directory, and browser post-processing results.
- Confirm the cylinder and cavity workflows require no manual tar.gz preparation.

## Delivery order

1. Provider-neutral plan contracts and provider adapters.
2. Capability registry and provider-neutral API/UI.
3. Plan-to-compiler-to-Gate 2 workflow binding.
4. Cylinder compiler.
5. Cavity compiler.
6. Real-provider and real-workstation acceptance.
7. Update the internal fluid-research Skill with verified reusable rules.
