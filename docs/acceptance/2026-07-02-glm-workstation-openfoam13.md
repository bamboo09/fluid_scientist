# GLM → Gate 2 → workstation OpenFOAM 13 acceptance

Date: 2026-07-02 (Asia/Shanghai)

## Verified control-plane path

- Configured `glm / glm-5.1` through `/api/model-configurations`; the API key remained in server-process memory.
- Generated strict provider-neutral plans with GLM for `cylinder_flow`, `laminar_pipe`, and `lid_driven_cavity`.
- Compiled each plan into a deterministic archive, approved its exact plan ID/version/digest at Gate 2, and submitted the approved bytes through `fluid-worker` on `10.129.177.241`.
- Verified remote PID creation, `checkMesh`, solver completion, time directories, and `.foam` creation.

## Root-cause and retry evidence

The first generated cylinder job failed on the workstation:

- Job: `20260702-145728-cylinder-flow-re100-vortex-shedding-30c69da4`
- Error: Foundation 13 required `UFinal` in `fvSolution.solvers`.
- Fix: add `pFinal` and `UFinal` to both transient compilers (`804031f`).

A cavity submission then completed remotely while the API response was lost. The original timestamp-based retry could create another job. The planned-job timestamp now comes from the immutable Gate 2 approval, so retries reuse one job ID (`db8ff04`).

## Successful real jobs

### Cylinder flow Re=100

- Plan: `b5b5354d-af33-4f5a-98e2-a51272f239aa`
- Archive: `sha256:0f8b5c40f5edb8f785796f2c378bcadda62a980c092d22c208a21604d10b79f2`
- Job: `20260702-150243-cylinder-flow-at-re-100-9e589694`
- Remote PID: `207117`
- Mesh: passed; 2,688 cells; max aspect ratio 14.2843; max non-orthogonality 67.4460
- Solver: completed
- Final residuals: `Ux=5.1214e-6`, `Uy=4.6045e-6`, `p=4.6614e-4`
- Time directories: `0` through `0.02` at `0.001` intervals
- ParaView: `20260702-150243-cylinder-flow-at-re-100-9e589694.foam`

### Laminar pipe

- Plan: `8f86011c-9f10-4707-bd4a-31dd1a9acb04`
- Archive: `sha256:1d186f8e1c2e51479dea544417246b35a4d2be625a6728c6853641a1d415ed86`
- Job: `20260702-150436-laminar-pipe-flow-analysis-c4b2c0e3`
- Remote PID: `208874`
- Mesh: passed; 30 cells
- Solver: completed
- Final residuals: `Ux=5.3973e-4`, `Uy=7.8600e-4`, `Uz=2.7914e-4`, `p=8.4566e-4`
- Time directories: `0`, `2000`
- ParaView: `20260702-150436-laminar-pipe-flow-analysis-c4b2c0e3.foam`

### Lid-driven cavity

- Plan: `19668a9d-b5e7-446b-b78b-8978003fad1a`
- Archive: `sha256:211098629714431ad6f3c5d1e06e69a2254243450f062519254b328ea6553abf`
- Job: `20260702-150611-lid-driven-cavity-flow-fa9b741a`
- Remote PID: `213956`
- Mesh: passed; 64 cells; max aspect ratio 1.0; max non-orthogonality 0.0
- Solver: completed
- Final residuals: `Ux=1.3110e-12`, `Uy=1.7222e-12`, `p=9.0138e-4`
- Time directories: `0` through `0.01`
- ParaView: `20260702-150611-lid-driven-cavity-flow-fa9b741a.foam`

## Explicitly not yet accepted

- These short cases prove mesh generation, solver startup/completion, collection, and post-processing availability; they do not establish grid independence, long-time cylinder shedding statistics, or publication-grade physical credibility.
- The worker update in `8c3324d` adds structured pipe metrics, final `Cd/Cl`, and cavity probe extraction. Local tests pass, but remote deployment was blocked by the desktop approval quota and remains pending.
- The evidence-bound Results Analyst supports OpenAI, GLM, and DeepSeek locally. Real GLM analysis against the updated remote observable payload remains pending worker deployment and API restart.
- No real OpenAI or DeepSeek credential was supplied; those providers remain contract-tested but not real-provider accepted.
- The HPC data/login/compute-node path remains unconfigured and unaccepted.
