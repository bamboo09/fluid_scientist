# Fluid research workflow reference

## Contracts

- `ResearchSpec`: question, geometry, fluid, independent variables, responses, constraints, and simulation budget.
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

## OpenFOAM Foundation 13 benchmark

Use the Foundation distribution semantics, not similarly named OpenCFD releases. OpenFOAM Foundation 13 uses `foamRun -solver incompressibleFluid`, with `solver incompressibleFluid` in `controlDict`, viscosity in `constant/physicalProperties`, and laminar selection in `constant/momentumTransport`.

Treat OpenFOAM incompressible `p` as kinematic pressure and `phi` as volumetric flow. Convert pressure drop to Pa and flow to kg/s using the case density before comparing against Hagen鈥揚oiseuille or checking mass conservation. Require mesh quality, final residual, mass imbalance, and analytical benchmark thresholds before marking the Pilot verified.

## Failure routing

- Retry transient infrastructure faults idempotently.
- Regenerate invalid meshes instead of replaying them.
- Apply at most two versioned numerical repairs.
- Return model applicability and HARD rule failures to planning or human review.
- Preserve external job IDs so recovery polls existing jobs instead of submitting duplicates.

## Candidate Skill lifecycle

Require `DRAFT → RED_RECORDED → GREEN_PASSED → APPROVED → PUBLISHED`. Redact secrets, users, hosts, absolute paths, and sensitive research values. Keep source audit IDs and test evidence. Publication must create a versioned Git change and remain reversible.
