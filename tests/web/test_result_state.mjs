import assert from "node:assert/strict";
import test from "node:test";

import {
  AnalysisRequestController,
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
  const identity = {
    projectId: "project-1",
    planId: "plan-1",
    caseId: "case-1",
    targetId: "target-1",
  };
  const normalized = normalizeResultPayload(payload, { source: "planned", identity });
  assert.equal(normalized.summary.cells, 1024);
  assert.deepEqual(normalized.boundIdentity, identity);
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
    {
      source: "planned",
      identity: {
        projectId: "project-1",
        planId: "plan-bound",
        caseId: "case-1",
        targetId: "target-1",
      },
    },
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

test("planned analysis rejects project case and target drift under the same plan", () => {
  const identity = {
    projectId: "project-1",
    planId: "plan-1",
    caseId: "case-1",
    targetId: "target-1",
  };
  const planned = normalizeResultPayload(
    { collection: { mesh: {}, solver: {} } },
    { source: "planned", identity },
  );
  for (const changed of [
    { ...identity, projectId: "project-2" },
    { ...identity, caseId: "case-2" },
    { ...identity, targetId: "target-2" },
  ]) {
    assert.equal(analysisAvailability({ resultContext: planned, ...changed }).allowed, false);
  }
});

test("analysis controller deduplicates clicks and discards a stale response", async () => {
  const controller = new AnalysisRequestController();
  let currentSession = "session-1";
  let resolveOld;
  let requests = 0;
  const rendered = [];
  const request = () => {
    requests += 1;
    return new Promise((resolve) => { resolveOld = resolve; });
  };
  const options = {
    sessionKey: "session-1",
    request,
    isCurrent: (sessionKey) => currentSession === sessionKey,
    onResult: (result) => rendered.push(result),
  };
  const first = controller.run(options);
  const second = controller.run(options);
  assert.equal(first, second);
  assert.equal(requests, 1);

  currentSession = "session-2";
  resolveOld({ analysis: "old" });
  const outcome = await first;
  assert.equal(outcome.stale, true);
  assert.deepEqual(rendered, []);

  await controller.run({
    sessionKey: "session-2",
    request: async () => {
      requests += 1;
      return { analysis: "new" };
    },
    isCurrent: (sessionKey) => currentSession === sessionKey,
    onResult: (result) => rendered.push(result),
  });
  assert.equal(requests, 2);
  assert.deepEqual(rendered, [{ analysis: "new" }]);
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
