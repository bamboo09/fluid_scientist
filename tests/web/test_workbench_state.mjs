import assert from "node:assert/strict";
import {
  buildPlanRequest,
  canStartExperiment,
  restoredPlanForProject,
  shouldCreateFreshProject,
  storageKeys,
  taskView,
} from "../../apps/web/workbench-state.js";

assert.deepEqual(storageKeys, {
  projectId: "fluid-scientist-project-id",
  planId: "fluid-scientist-plan-id",
  caseId: "fluid-scientist-case-id",
  targetId: "fluid-scientist-target-id",
});
assert.equal(Object.isFrozen(storageKeys), true);

assert.deepEqual(taskView({ phase: "preparing" }), {
  label: "准备中",
  tone: "active",
  percent: 10,
  detail: "正在准备编译与审批",
});
assert.deepEqual(taskView({ phase: "submitting", targetId: "lab-pc" }), {
  label: "正在提交",
  tone: "active",
  percent: 30,
  detail: "目标：lab-pc",
});
assert.deepEqual(taskView({ phase: "submitted", jobId: "job-42", pid: 321 }), {
  label: "已到达工作站",
  tone: "active",
  percent: 40,
  detail: "任务 job-42 · 远程 PID 321",
});
assert.deepEqual(taskView({ phase: "mesh_check", jobId: "job-42" }), {
  label: "网格检查",
  tone: "active",
  percent: 55,
  detail: "任务 job-42",
});
assert.deepEqual(taskView({ phase: "solving", jobId: "job-42" }), {
  label: "正在求解",
  tone: "active",
  percent: 70,
  detail: "任务 job-42",
});
assert.deepEqual(taskView({ phase: "collecting", jobId: "job-42" }), {
  label: "正在收集结果",
  tone: "active",
  percent: 85,
  detail: "任务 job-42",
});
assert.deepEqual(taskView({ phase: "completed", jobId: "job-42" }), {
  label: "已完成",
  tone: "success",
  percent: 100,
  detail: "任务 job-42",
});
assert.deepEqual(
  taskView({ phase: "failed", error: "编译失败" }),
  {
    label: "失败",
    tone: "danger",
    percent: 100,
    detail: "编译失败",
  },
);
assert.deepEqual(
  taskView({ phase: "failed", jobId: "job-42", error: "网格质量不合格" }),
  {
    label: "失败",
    tone: "danger",
    percent: 100,
    detail: "任务 job-42 · 网格质量不合格",
  },
);
assert.deepEqual(taskView({ phase: "cancelled", jobId: "job-42" }), {
  label: "已取消",
  tone: "muted",
  percent: 100,
  detail: "任务 job-42",
});

for (const phase of [
  "submitted",
  "mesh_check",
  "solving",
  "collecting",
  "completed",
  "cancelled",
]) {
  assert.throws(() => taskView({ phase }), /Job ID/);
}

assert.throws(() => taskView({ phase: "unknown" }), /Unknown task phase/);

// A completed/failed remote run is historical evidence, never a container for
// a new natural-language request.  Starting again must allocate a fresh
// project, while a still-running task must be rejected instead of silently
// stealing the single global poll timer.
assert.equal(
  shouldCreateFreshProject({ workflow_state: "PILOT_VERIFIED" }, null),
  true,
);
assert.equal(
  shouldCreateFreshProject(
    { workflow_state: "PILOT_READY" },
    { phase: "completed", jobId: "job-old" },
  ),
  true,
);
assert.equal(
  shouldCreateFreshProject(
    { workflow_state: "PILOT_READY" },
    { phase: "failed", jobId: "job-old" },
  ),
  true,
);
assert.equal(
  shouldCreateFreshProject(
    { workflow_state: "PILOT_READY" },
    { phase: "cancelled", jobId: "job-old" },
  ),
  true,
);
assert.equal(
  shouldCreateFreshProject({ workflow_state: "PILOT_READY" }, null),
  false,
);
assert.equal(canStartExperiment(null), true);
assert.equal(canStartExperiment({ phase: "completed", jobId: "job-old" }), true);
assert.equal(canStartExperiment({ phase: "failed", jobId: "job-old" }), true);
assert.equal(canStartExperiment({ phase: "cancelled", jobId: "job-old" }), true);
for (const phase of ["preparing", "submitting", "submitted", "mesh_check", "solving", "collecting"]) {
  assert.equal(
    canStartExperiment({ phase, jobId: phase === "preparing" ? undefined : "job-live" }),
    false,
    `phase ${phase} must block a competing experiment`,
  );
}

// Optional target selection must be represented by absence, not an empty
// string that violates the API's min_length constraint.
assert.deepEqual(
  buildPlanRequest("研究 Re=100 圆柱绕流", "project-1", ""),
  { question: "研究 Re=100 圆柱绕流", project_id: "project-1" },
);
assert.deepEqual(
  buildPlanRequest("研究 Re=100 圆柱绕流", "project-1", "workstation"),
  {
    question: "研究 Re=100 圆柱绕流",
    project_id: "project-1",
    target_id: "workstation",
  },
);

// Recovery is valid only when the persisted plan belongs to the recovered
// project. A stale plan identifier from another project must be discarded.
const recoveredPlan = { plan_id: "plan-1", project_id: "project-1" };
assert.equal(
  restoredPlanForProject(recoveredPlan, { project_id: "project-1" }),
  recoveredPlan,
);
assert.equal(
  restoredPlanForProject(recoveredPlan, { project_id: "project-2" }),
  null,
);
