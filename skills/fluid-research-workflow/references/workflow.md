# Fluid research workflow reference

## Contracts

- `ResearchSpec`: question, geometry, fluid, independent variables, responses, constraints, and simulation budget.
- Provider-neutral `ExperimentPlan`: experiment type, bounded case inputs, convergence targets, parameter sweeps, requested outputs, assumptions, and limitations.
- `EvidencePackage`: query, source-located evidence, conflicts, coverage gaps, review state, and confidence.
- `CaseManifest`: immutable template commit, software/artifact digest, geometry, physics, numerics, resources, and outputs.
- `ValidationResult`: iterative convergence, mass imbalance, grid/time-step independence, benchmark agreement, sensitivity, and warnings.
- `AnalysisResult`: deterministic statistics, effect sizes, uncertainty, artifact IDs, and observations.
- `ResearchReport`: scope, evidence-linked claims, limitations, failed cases, and approval records.

## HPC boundaries

Use the data node for transfer, download, compilation, checksums, and artifact publication. Use the Login node only for typed `sbatch`, `squeue`, `sacct`, and `scancel` operations. Use compute nodes only for approved OpenFOAM commands against immutable artifacts. Exchange manifests and results through configured shared storage or explicit safe synchronization.

## Workstation OpenFOAM boundary

- Keep workstation hosts, usernames, identity paths, and `known_hosts` paths in runtime configuration. Never place them in source, reports, logs, or published Skills.
- Read the SSH host fingerprint without accepting it. Require the researcher to compare it with the workstation's local host-key fingerprint before adding `known_hosts`.
- Run direct workstation jobs through `fluid-worker doctor/submit/status/cancel/collect`. Do not route them through Slurm and do not pass free-form remote shell.
- Require protocol-version and command capability checks before submission. Preserve deterministic job IDs so retries query the same job.

### Custom OpenFOAM execution

- Apply double validation: validate the tar.gz before transfer and repeat the same validation inside `fluid-worker` before extraction.
- Reject absolute paths, traversal, links, oversized expansion, dynamic code, system calls, missing dictionaries, and non-allow-listed solvers.
- Upload only to the fixed home-relative incoming directory. Accept an archive name, never a caller-selected remote path.
- Execute only `submit-custom` with the fixed chain: optional `blockMesh`, optional `mirrorMesh` when `system/mirrorMeshDict` is present, mandatory `checkMesh -allGeometry -allTopology`, then `foamRun -solver incompressibleFluid`.
- Collect the mesh report, solver completion marker, final residuals, numeric time directories, case manifest, and `.foam` marker. Present these results in the browser; retain ParaView as an advanced workstation view.
- Do not apply pipe pressure-drop or mass-flow acceptance thresholds to cylinder, bend, or other custom geometries unless their case defines and validates equivalent observables.

### Model configuration

Support only the first-batch providers OpenAI, GLM, and DeepSeek. Accept arbitrary model IDs, but keep each interactively supplied API key only in server-process memory. Never echo a key, place it in browser storage, write it to project files, or include it in logs and Skills. Require re-entry after service restart.

Use native structured parsing for OpenAI. Request JSON from GLM and DeepSeek, then perform the same strict local schema validation. Treat authentication, model-not-found, transport, empty-output, JSON, and schema failures as different typed errors. Do not retry authentication, model-not-found, or invalid-plan failures.

### Provider-neutral planning and deterministic compilation

- Allow the model to select only `laminar_pipe`, `cylinder_flow`, `lid_driven_cavity`, or `custom_openfoam` and fill their bounded plan fields. Reject unknown capabilities and extra fields.
- Keep model planning separate from execution. Reject model-generated commands, shell, remote paths, and OpenFOAM dictionaries.
- Route built-in plans through a deterministic compiler. Sort archive members, normalize tar metadata, set gzip time to zero, and validate the resulting archive before storing it.
- Route `custom_openfoam` to the reviewed upload path instead of pretending it can use a built-in compiler.
- Persist the immutable plan and compiled bytes. Preview the solver, preprocessing chain, required outputs, and archive digest without exposing server paths.
- At Gate 2, bind the plan ID, plan version, and archive digest. On submission, retrieve the stored bytes, recompute their digest, compare it with the binding, and never recompile after approval.

## OpenFOAM Foundation 13 benchmark

Use the Foundation distribution semantics, not similarly named OpenCFD releases. OpenFOAM Foundation 13 uses `foamRun -solver incompressibleFluid`, with `solver incompressibleFluid` in `controlDict`, viscosity in `constant/physicalProperties`, and laminar selection in `constant/momentumTransport`.

Treat OpenFOAM incompressible `p` as kinematic pressure and `phi` as volumetric flow. Convert pressure drop to Pa and flow to kg/s using the case density before comparing against Hagen-Poiseuille or checking mass conservation. Require mesh quality, final residual, mass imbalance, and analytical benchmark thresholds before marking the Pilot verified.

For a blockMesh axisymmetric pipe with a collapsed centreline, use a one-cell wedge in the circumferential direction. Subdividing that direction creates extremely thin centreline cells, high aspect ratio, and small determinant failures even when the solver exits normally.

Do not classify the normal `SIGFPE` trapping banner as a floating-point crash. Require an actual exception or fatal-error record; preserve a regression case containing the trapping banner followed by a normal `End` marker.

## Failure routing

- Retry transient infrastructure faults idempotently.
- Regenerate invalid meshes instead of replaying them.
- Apply at most two versioned numerical repairs.
- Return model applicability and HARD rule failures to planning or human review.
- Preserve external job IDs so recovery polls existing jobs instead of submitting duplicates.

## Candidate Skill lifecycle

Require `DRAFT → RED_RECORDED → GREEN_PASSED → APPROVED → PUBLISHED`. Redact secrets, users, hosts, absolute paths, and sensitive research values. Keep source audit IDs and test evidence. Publication must create a versioned Git change and remain reversible.
