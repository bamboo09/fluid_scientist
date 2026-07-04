import assert from "node:assert/strict";
import test from "node:test";

import {
  analysisAvailability,
  normalizeResultPayload,
  plannedResultUrl,
} from "../../apps/web/result-state.js";

test("raw custom WorkerCollection normalizes real mesh and solver summary", () => {
  const raw = {
    job_id: "job-1",
    mesh: { passed: true, cells: 8192 },
    solver: { completed: true },
    observables: { pressure_probes: [1, 2] },
  };
  const normalized = normalizeResultPayload(raw, { source: "custom" });
  assert.equal(normalized.source, "legacy_custom");
  assert.equal(normalized.collection, raw);
  assert.deepEqual(normalized.summary, {
    mesh_passed: true,
    solver_completed: true,
    cells: 8192,
  });
  assert.equal(normalized.postprocessPayload, raw);
});

test("planned envelope keeps its summary and bound plan identity", () => {
  const payload = {
    summary: { mesh_passed: false, solver_completed: true, cells: 1024 },
    collection: { mesh: { passed: true, cells: 99 }, solver: { completed: false } },
  };
  const normalized = normalizeResultPayload(payload, { source: "planned", planId: "plan-1" });
  assert.equal(normalized.summary.cells, 1024);
  assert.equal(normalized.boundPlanId, "plan-1");
  assert.equal(normalized.postprocessPayload, payload);
});

test("legacy custom results cannot use a stale planned analysis route", () => {
  const custom = normalizeResultPayload({ mesh: {}, solver: {} }, { source: "custom" });
  const unavailable = analysisAvailability({
    resultContext: custom,
    projectId: "project-old",
    planId: "plan-old",
    caseId: "case-custom",
    targetId: "workstation",
  });
  assert.equal(unavailable.allowed, false);
  assert.match(unavailable.message, /自定义算例.*未绑定实验计划/);
});

test("planned analysis requires the exact bound plan and all identifiers", () => {
  const planned = normalizeResultPayload(
    { collection: { mesh: {}, solver: {} } },
    { source: "planned", planId: "plan-bound" },
  );
  assert.equal(analysisAvailability({
    resultContext: planned,
    projectId: "project-1",
    planId: "plan-stale",
    caseId: "case-1",
    targetId: "target-1",
  }).allowed, false);
  assert.equal(analysisAvailability({
    resultContext: planned,
    projectId: "project-1",
    planId: "plan-bound",
    caseId: "case-1",
    targetId: "target-1",
  }).allowed, true);
});

test("result and analysis URLs encode every untrusted identifier", () => {
  const url = plannedResultUrl({
    projectId: "project/../?admin=true",
    planId: "plan/#secret",
    caseId: "case&target_id=evil",
    targetId: "target?x=1",
    action: "analysis",
  });
  assert.doesNotMatch(url, /\.\.|#secret|admin=true|case&target_id=evil|target\?x/);
  assert.match(url, /project%2F%2E%2E%2F%3Fadmin%3Dtrue/);
  assert.match(url, /plan%2F%23secret/);
  assert.match(url, /case_id=case%26target_id%3Devil/);
  assert.match(url, /target_id=target%3Fx%3D1/);
});
