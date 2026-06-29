---
name: fluid-research-workflow
description: Use when planning, executing, validating, or reporting single-phase incompressible OpenFOAM pipe or 90-degree bend studies, especially with Slurm/HPC, evidence retrieval, mesh independence, mass conservation, result analysis, or human approval gates.
---

# Fluid Research Workflow

## Core principle

Treat solver completion, numerical convergence, physical credibility, and generalizable conclusions as separate claims. Make every important decision and conclusion traceable.

## Required workflow

1. Convert the request into a strict `ResearchSpec`. Surface assumptions and stop for materially missing inputs.
2. Obtain Gate 1 human approval before evidence-driven planning.
3. Build an `EvidencePackage` with source locators. Use reviewed evidence for high-risk choices.
4. Compute units, dimensionless numbers, parameter bounds, and rules with deterministic code. Never ask an LLM to supply numerical truth.
5. Design a mandatory `Pilot` before a batch. Include coarse, medium, and fine meshes and relevant model sensitivity.
6. Obtain Gate 2 human approval for the solver, mesh, budget, and case count.
7. Render an immutable Case Manifest from versioned templates. Submit only through allowlisted HPC tools; never emit or run arbitrary shell.
8. Perform deterministic validation: residuals plus monitored quantities, conservation, mesh independence/GCI, benchmark agreement, and model sensitivity.
9. Run deterministic statistics before Results Analyst interpretation. Require evidence-linked claims and label observation, inference, literature support, extrapolation, or hypothesis.
10. Have an independent Scientific Reviewer check failures, uncertainty, and scope. Obtain Gate 3 human approval before the final report.

## Hard stops

- Block batch submission when the Pilot has not passed.
- Block any HARD physics-rule violation.
- Limit controlled numerical repair to two recorded revisions per case.
- Never hide failed cases, invent missing evidence, repair corrupt formulas, or generalize beyond the tested range.
- Never publish a candidate Skill without a failing baseline, passing forward test, redaction, and human approval.

Read [references/workflow.md](references/workflow.md) when defining schemas, workflow states, HPC boundaries, validation outputs, or candidate Skill governance.

