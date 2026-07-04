import assert from "node:assert/strict";
import test from "node:test";

import {
  OperationPoller,
  createResultLoader,
} from "../../apps/web/operation-lifecycle.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

function fakeClock() {
  let nextId = 1;
  const jobs = new Map();
  return {
    setTimeout(callback) {
      const id = nextId++;
      jobs.set(id, callback);
      return id;
    },
    clearTimeout(id) {
      jobs.delete(id);
    },
    runNext() {
      const entry = jobs.entries().next().value;
      assert.ok(entry, "expected a scheduled poll");
      const [id, callback] = entry;
      jobs.delete(id);
      callback();
    },
    get size() {
      return jobs.size;
    },
  };
}

test("a stale response cannot replace the newer operation", async () => {
  const first = deferred();
  const second = deferred();
  const seen = [];
  const poller = new OperationPoller({
    fetchOperation: (id) => id === "old" ? first.promise : second.promise,
    onStatus: (operation) => seen.push(operation.operation_id),
  });

  const oldPoll = poller.start("old");
  const newPoll = poller.start("new");
  second.resolve({ operation_id: "new", state: "succeeded" });
  await newPoll;
  first.resolve({ operation_id: "old", state: "succeeded" });
  await oldPoll;

  assert.deepEqual(seen, ["new"]);
});

test("stop aborts the request and clears its timer", async () => {
  const clock = fakeClock();
  let signal;
  const poller = new OperationPoller({
    fetchOperation: (_id, options) => {
      signal = options.signal;
      return new Promise(() => {});
    },
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
  });

  poller.start("op", { immediate: false });
  assert.equal(clock.size, 1);
  poller.stop();
  assert.equal(clock.size, 0);

  poller.start("op");
  poller.stop();
  assert.equal(signal.aborted, true);
});

test("an immediate retry cancels the delayed poll and starts now", async () => {
  const clock = fakeClock();
  const requested = [];
  const poller = new OperationPoller({
    fetchOperation: async (id) => {
      requested.push(id);
      return { operation_id: id, state: "succeeded" };
    },
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
  });

  poller.start("old", { immediate: false });
  assert.equal(clock.size, 1);
  await poller.start("retry");
  assert.equal(clock.size, 0);
  assert.deepEqual(requested, ["retry"]);
});

test("404 clears persisted operation state without retrying", async () => {
  const clock = fakeClock();
  const missing = [];
  const error = new Error("missing");
  error.status = 404;
  const poller = new OperationPoller({
    fetchOperation: async () => { throw error; },
    onMissing: (id) => missing.push(id),
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
  });

  await poller.start("gone");
  assert.deepEqual(missing, ["gone"]);
  assert.equal(clock.size, 0);
});

test("network polling pauses after five failures and resumes without resubmission", async () => {
  const clock = fakeClock();
  let attempts = 0;
  const pauses = [];
  const poller = new OperationPoller({
    fetchOperation: async () => {
      attempts += 1;
      if (attempts <= 5) throw new TypeError("offline");
      return { operation_id: "op", state: "succeeded" };
    },
    onNetwork: (detail) => pauses.push(detail),
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
  });

  await poller.start("op");
  for (let index = 0; index < 4; index += 1) {
    clock.runNext();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  assert.equal(attempts, 5);
  assert.equal(poller.paused, true);
  assert.equal(clock.size, 0);
  assert.equal(pauses.at(-1).paused, true);

  await poller.resume();
  assert.equal(attempts, 6);
  assert.equal(poller.paused, false);
});

test("a successful result reference is fetched and rendered exactly once", async () => {
  let fetches = 0;
  const rendered = [];
  const loadResult = createResultLoader({
    fetchPlan: async (id) => {
      fetches += 1;
      return { plan_id: id };
    },
    onPlan: (plan) => rendered.push(plan.plan_id),
  });

  await Promise.all([loadResult("plan-1"), loadResult("plan-1")]);
  await loadResult("plan-1");
  assert.equal(fetches, 1);
  assert.deepEqual(rendered, ["plan-1"]);
});

test("a result that becomes stale while loading is never rendered", async () => {
  const result = deferred();
  let current = true;
  const rendered = [];
  const loadResult = createResultLoader({
    fetchPlan: () => result.promise,
    onPlan: (plan) => rendered.push(plan.plan_id),
  });

  const loading = loadResult("stale-plan", { shouldApply: () => current });
  current = false;
  result.resolve({ plan_id: "stale-plan" });
  await loading;
  assert.deepEqual(rendered, []);

  await loadResult("stale-plan");
  assert.deepEqual(rendered, ["stale-plan"]);
});
