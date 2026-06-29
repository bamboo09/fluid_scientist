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

## Failure routing

- Retry transient infrastructure faults idempotently.
- Regenerate invalid meshes instead of replaying them.
- Apply at most two versioned numerical repairs.
- Return model applicability and HARD rule failures to planning or human review.
- Preserve external job IDs so recovery polls existing jobs instead of submitting duplicates.

## Candidate Skill lifecycle

Require `DRAFT → RED_RECORDED → GREEN_PASSED → APPROVED → PUBLISHED`. Redact secrets, users, hosts, absolute paths, and sensitive research values. Keep source audit IDs and test evidence. Publication must create a versioned Git change and remain reversible.
