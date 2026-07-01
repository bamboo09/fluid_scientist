---
name: fluid-research-workflow
description: Use when planning, executing, validating, or reporting single-phase incompressible built-in or custom OpenFOAM studies on a workstation or Slurm/HPC platform, especially with model-designed experiments, safe case uploads, post-processing, evidence, credibility checks, or human approval gates.
---

# Fluid Research Workflow

## Core principle

Treat solver completion, numerical convergence, physical credibility, and generalizable conclusions as separate claims. Make every important decision and conclusion traceable.

## Required workflow

1. Convert the request into a strict `ResearchSpec`. Surface assumptions and stop for materially missing inputs.
2. Obtain Gate 1 human approval before evidence-driven planning.
3. Build an `EvidencePackage` with source locators. Use reviewed evidence for high-risk choices.
4. Compute units, dimensionless numbers, parameter bounds, and rules with deterministic code. Never ask an LLM to supply numerical truth.
5. Design a mandatory `Pilot` before a batch. Choose a verified built-in template only when its geometry and physics match; otherwise route to a custom OpenFOAM case. Include coarse, medium, and fine meshes and relevant model sensitivity.
6. Obtain Gate 2 human approval for the solver, mesh, budget, and case count.
7. Select the approved execution target. Use typed Slurm commands on HPC or the fixed `fluid-worker` protocol on a direct workstation; never emit or run arbitrary shell.
8. Perform deterministic validation: residuals plus monitored quantities, conservation, mesh independence/GCI, benchmark agreement, and model sensitivity.
9. Run deterministic statistics before Results Analyst interpretation. Require evidence-linked claims and label observation, inference, literature support, extrapolation, or hypothesis.
10. Have an independent Scientific Reviewer check failures, uncertainty, and scope. Obtain Gate 3 human approval before the final report.

## Hard stops

- Block batch submission when the Pilot has not passed.
- Block any HARD physics-rule violation.
- Block first contact with a workstation until its SSH host fingerprint is verified out of band and recorded in `known_hosts`.
- Limit controlled numerical repair to two recorded revisions per case.
- Never hide failed cases, invent missing evidence, repair corrupt formulas, or generalize beyond the tested range.
- Never execute a custom case before local and worker-side validation, and never let the model or user supply a remote destination or shell command.
- Never call a case “ready for post-processing” without exposing its mesh, residuals, time directories, and result view to the researcher.
- Never publish a candidate Skill without a failing baseline, passing forward test, redaction, and human approval.

Read [references/workflow.md](references/workflow.md) when defining schemas, workflow states, HPC boundaries, validation outputs, or candidate Skill governance.

