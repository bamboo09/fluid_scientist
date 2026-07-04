import assert from "node:assert/strict";
import {
  elapsedLabel,
  operationView,
  planningComposerView,
} from "../../apps/web/operation-state.js";

const created = "2026-07-04T00:00:00.000Z";

assert.deepEqual(operationView({ state: "queued", stage: "queued" }), {
  label: "等待规划",
  stageLabel: "已进入队列",
  tone: "queued",
  percent: 8,
  indeterminate: true,
  terminal: false,
  canCancel: true,
  canRetry: false,
});

for (const [stage, stageLabel, percent] of [
  ["model_planning", "模型正在设计实验", 36],
  ["schema_correction", "正在校正实验结构", 58],
  ["storing_plan", "正在保存实验计划", 86],
]) {
  const view = operationView({ state: "running", stage });
  assert.equal(view.label, "规划中");
  assert.equal(view.stageLabel, stageLabel);
  assert.equal(view.percent, percent);
  assert.equal(view.canCancel, true);
}

assert.equal(operationView({ state: "succeeded", stage: "complete" }).percent, 100);
assert.equal(operationView({ state: "failed", stage: "model_planning" }).canRetry, true);
assert.equal(operationView({ state: "cancelled", stage: "queued" }).canRetry, true);

const unknown = operationView({ state: "future", stage: "future_stage" });
assert.equal(unknown.label, "状态更新中");
assert.equal(unknown.tone, "muted");
assert.equal(unknown.canCancel, false);

assert.equal(elapsedLabel({ created_at: created }, Date.parse(created)), "刚刚开始");
assert.equal(elapsedLabel({ created_at: created }, Date.parse(created) + 65_000), "已用时 1分05秒");
assert.equal(elapsedLabel({}, Date.parse(created)), "用时未知");

assert.deepEqual(planningComposerView({
  empty: false,
  modelConfigured: true,
  targetSelected: true,
  requestActive: false,
  operation: null,
}), {
  disabled: false,
  hint: "模型仅设计结构化实验；执行仍需您的确认。",
});
assert.equal(planningComposerView({
  empty: false,
  modelConfigured: true,
  targetSelected: true,
  requestActive: false,
  operation: { state: "running" },
}).disabled, true);
assert.equal(planningComposerView({
  empty: false,
  modelConfigured: true,
  targetSelected: false,
}).hint, "请选择执行平台；平台连通性不会阻塞模型规划。");
