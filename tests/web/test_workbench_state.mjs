import assert from "node:assert/strict";
import {
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
