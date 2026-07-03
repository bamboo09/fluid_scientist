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
5. Ask OpenAI, GLM, or DeepSeek for one strict provider-neutral `ExperimentPlan`. Apply local schema validation before accepting any provider output.
6. Design a mandatory `Pilot` before a batch. Route `laminar_pipe`, `cylinder_flow`, and `lid_driven_cavity` through deterministic compilers; route `custom_openfoam` to reviewed archive upload.
7. Compile and preview the case before Gate 2. Bind the plan ID, plan version, and archive digest to the approval; submit the exact approved bytes without recompiling.
8. Select the approved execution target. Use typed Slurm commands on HPC or the fixed `fluid-worker` protocol on a direct workstation; never emit or run arbitrary shell. After a worker upgrade, replay collection against an existing job from every supported experiment type before accepting the deployment.
9. Perform deterministic validation: residuals plus monitored quantities, conservation, mesh independence/GCI, benchmark agreement, and model sensitivity.
10. Collect deterministic metrics, force coefficients, and velocity and pressure probes before Results Analyst interpretation. Require evidence-linked claims with exact evidence keys; the model must never alter deterministic values. Label observation, inference, extrapolation, or hypothesis.
11. Have an independent Scientific Reviewer check failures, uncertainty, and scope. Obtain Gate 3 human approval before the final report.
12. In user-facing task views, never show `submitted` until the execution target returns an external job ID. Persist only non-secret identifiers, validate recovered plan ownership, and resume polling the same job without resubmission.

## Hard stops

- Block batch submission when the Pilot has not passed.
- Block any HARD physics-rule violation.
- Block first contact with a workstation until its SSH host fingerprint is verified out of band and recorded in `known_hosts`.
- Limit controlled numerical repair to two recorded revisions per case.
- Never hide failed cases, invent missing evidence, repair corrupt formulas, or generalize beyond the tested range.
- Never execute a custom case before local and worker-side validation, and never let the model or user supply a remote destination or shell command.
- Never execute model-generated commands or model-generated OpenFOAM dictionaries. Models may propose only a schema-valid plan; trusted code owns compilation and execution.
- Never approve or submit an archive whose recomputed digest differs from the Gate 2 archive digest.
- Never call a case “ready for post-processing” without exposing its mesh, residuals, time directories, and result view to the researcher.
- Never publish a candidate Skill without a failing baseline, passing forward test, redaction, and human approval.

Read [references/workflow.md](references/workflow.md) when defining schemas, workflow states, HPC boundaries, validation outputs, or candidate Skill governance.

