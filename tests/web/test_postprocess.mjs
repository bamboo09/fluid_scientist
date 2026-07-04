import assert from "node:assert/strict";
import test from "node:test";

import {
  renderCavityCenterlineProfile,
  renderCylinderForceHistory,
  renderPostprocessResults,
  revealPostprocess,
} from "../../apps/web/postprocess.js";

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.attributes = new Map();
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this._text = "";
    this.focusCalls = [];
    this.scrollCalls = [];
  }

  set textContent(value) {
    this._text = String(value ?? "");
    this.children = [];
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join("");
  }

  append(...nodes) {
    for (const node of nodes) {
      if (typeof node === "string") {
        const text = new FakeElement("#text");
        text.textContent = node;
        this.children.push(text);
      } else if (node) {
        this.children.push(node);
      }
    }
  }

  appendChild(node) {
    this.append(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.children = [];
    this._text = "";
    this.append(...nodes);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  focus(options) {
    this.focusCalls.push(options);
  }

  scrollIntoView(options) {
    this.scrollCalls.push(options);
  }
}

globalThis.document = {
  createElement: (tagName) => new FakeElement(tagName),
  createElementNS: (_namespace, tagName) => new FakeElement(tagName),
};

function element(tagName = "section") {
  return new FakeElement(tagName);
}

function validResults() {
  return {
    collection: {
      mesh: {
        passed: true,
        cells: 4096,
        max_aspect_ratio: 1.2,
        max_non_orthogonality: 7.5,
      },
      solver: {
        completed: true,
        final_residuals: { Ux: 1e-8, p: 2e-7 },
        residual_history: { Ux: [0.1, 0.001, 1e-8] },
      },
      numeric_times: [10, "2", 0.5, "not-a-time"],
      observables: {
        centerline_velocity: [
          { position: 0, velocity: 0 },
          { position: 0.5, velocity: 0.42 },
          { position: 1, velocity: 1 },
        ],
      },
      validation: { credible: true, mass_balance_error: 0.0002 },
      post_processing: { paraview_file: "/srv/jobs/private/run-7/cavity.foam" },
    },
  };
}

test("existing results reveal without fetching and move focus to the panel", async () => {
  const root = element();
  root.hidden = true;
  const button = element("button");
  let fetches = 0;

  const rendered = await revealPostprocess({
    root,
    button,
    results: validResults(),
    fetchResults: async () => {
      fetches += 1;
      return validResults();
    },
  });

  assert.equal(fetches, 0);
  assert.equal(rendered.ok, true);
  assert.equal(root.hidden, false);
  assert.equal(root.getAttribute("tabindex"), "-1");
  assert.deepEqual(root.focusCalls, [{ preventScroll: true }]);
  assert.deepEqual(root.scrollCalls, [{ behavior: "smooth", block: "start" }]);
  assert.equal(button.disabled, false);
});

test("absent results make exactly one fetch", async () => {
  const root = element();
  const button = element("button");
  let fetches = 0;
  await revealPostprocess({
    root,
    button,
    fetchResults: async () => {
      fetches += 1;
      return validResults();
    },
  });
  assert.equal(fetches, 1);
});

test("busy state is immediate and concurrent calls share one fetch", async () => {
  const root = element();
  root.hidden = true;
  const button = element("button");
  let release;
  let fetches = 0;
  const fetchResults = () => {
    fetches += 1;
    return new Promise((resolve) => { release = resolve; });
  };

  const first = revealPostprocess({ root, button, fetchResults });
  const second = revealPostprocess({ root, button, fetchResults });
  assert.equal(button.disabled, true);
  assert.equal(button.getAttribute("aria-busy"), "true");
  assert.match(button.textContent, /正在读取/);
  assert.equal(root.hidden, false);
  assert.match(root.textContent, /正在整理/);
  assert.equal(fetches, 1);
  release(validResults());
  await Promise.all([first, second]);
  assert.equal(root.children.length > 0, true);
  assert.equal(fetches, 1);
});

test("failure is safe, visible, and retryable", async () => {
  const root = element();
  const button = element("button");
  const first = await revealPostprocess({
    root,
    button,
    fetchResults: async () => {
      throw new Error("ssh host 10.0.0.7 /home/private/case system(command)");
    },
  });

  assert.equal(first.ok, false);
  assert.equal(button.disabled, false);
  assert.equal(root.hidden, false);
  assert.equal(root.getAttribute("role"), "alert");
  assert.match(root.textContent, /后处理结果暂时无法读取/);
  assert.doesNotMatch(root.textContent, /10\.0\.0\.7|\/home\/private|ssh|command/i);

  const second = await revealPostprocess({
    root,
    button,
    fetchResults: async () => validResults(),
  });
  assert.equal(second.ok, true);
  assert.notEqual(root.getAttribute("role"), "alert");
});

test("repeated reveal reuses the rendered panel without duplicate content", async () => {
  const root = element();
  const button = element("button");
  const results = validResults();
  await revealPostprocess({ root, button, results, fetchResults: async () => results });
  const childCount = root.children.length;
  await revealPostprocess({ root, button, results, fetchResults: async () => results });
  assert.equal(root.children.length, childCount);
  assert.equal(root.focusCalls.length, 2);
});

test("structured results sort numeric times and sanitize the marker filename", () => {
  const root = element();
  renderPostprocessResults(root, validResults());
  assert.match(root.textContent, /0\.5、2、10/);
  assert.match(root.textContent, /cavity\.foam/);
  assert.doesNotMatch(root.textContent, /\/srv\/jobs|private|run-7/);
  assert.match(root.textContent, /网格指标|残差|请求观测量|可信度/);
});

test("cavity chart renders only finite evidence with title, axes, and fallback table", () => {
  const chart = renderCavityCenterlineProfile(
    validResults().collection.observables.centerline_velocity,
  );
  assert.match(chart.textContent, /方腔中心线速度/);
  assert.match(chart.textContent, /位置|速度/);
  assert.equal(chart.children.some((child) => child.tagName === "SVG"), true);
  assert.doesNotMatch(chart.textContent, /NaN|Infinity/);

  for (const malformed of [null, [], [{ position: "x", velocity: Infinity }]]) {
    const missing = renderCavityCenterlineProfile(malformed);
    assert.match(missing.textContent, /当前结果未包含该曲线/);
    assert.equal(missing.children.some((child) => child.tagName === "SVG"), false);
  }
});

test("cylinder chart renders drag/lift history and rejects malformed evidence", () => {
  const valid = renderCylinderForceHistory([
    { time: 0, drag: 1.1, lift: 0 },
    { time: 1, drag: 1.3, lift: -0.2 },
    { time: 2, drag: 1.2, lift: 0.2 },
  ]);
  assert.match(valid.textContent, /圆柱受力历史|阻力|升力|时间/);
  assert.equal(valid.children.some((child) => child.tagName === "SVG"), true);
  assert.doesNotMatch(valid.textContent, /NaN|Infinity/);

  const missing = renderCylinderForceHistory([{ time: 0, drag: "bad" }]);
  assert.match(missing.textContent, /当前结果未包含该曲线/);
  assert.equal(missing.children.some((child) => child.tagName === "SVG"), false);
});

test("partial cylinder evidence names the missing curve without inventing it", () => {
  const partial = renderCylinderForceHistory([
    { time: 0, drag: 1.1 },
    { time: 1, drag: 1.2 },
  ]);
  assert.equal(partial.children.some((child) => child.tagName === "SVG"), true);
  assert.match(partial.textContent, /升力：当前结果未包含该曲线/);
});

test("a result from an older session cannot replace newer evidence", async () => {
  const root = element();
  const button = element("button");
  let resolveOld;
  let resolveNew;
  const oldRequest = revealPostprocess({
    root,
    button,
    sessionKey: "old-session",
    fetchResults: () => new Promise((resolve) => { resolveOld = resolve; }),
  });
  const newRequest = revealPostprocess({
    root,
    button,
    sessionKey: "new-session",
    fetchResults: () => new Promise((resolve) => { resolveNew = resolve; }),
  });
  const newer = validResults();
  newer.collection.mesh.cells = 8192;
  resolveNew(newer);
  await newRequest;
  resolveOld(validResults());
  await oldRequest;
  assert.match(root.textContent, /8192/);
  assert.doesNotMatch(root.textContent, /4096/);
});

test("parallel numeric arrays are accepted only as aligned finite evidence", () => {
  const cavity = renderCavityCenterlineProfile({
    position: [0, 0.5, 1],
    velocity: [0, 0.42, 1],
  });
  assert.equal(cavity.children.some((child) => child.tagName === "SVG"), true);

  const forces = renderCylinderForceHistory({
    time: [0, 1, 2],
    drag: [1.1, 1.3, 1.2],
    lift: [0, -0.2, 0.2],
  });
  assert.equal(forces.children.some((child) => child.tagName === "SVG"), true);

  const partial = renderCavityCenterlineProfile({ position: [0, 1], velocity: [0] });
  assert.match(partial.textContent, /当前结果未包含该曲线/);

  const nullPoint = renderCavityCenterlineProfile({
    position: [0, null],
    velocity: [0, 1],
  });
  assert.match(nullPoint.textContent, /当前结果未包含该曲线/);
  assert.equal(nullPoint.children.some((child) => child.tagName === "SVG"), false);
});

test("malformed numeric time collections degrade to an explicit empty value", () => {
  const root = element();
  const results = validResults();
  results.collection.numeric_times = { latest: 10 };
  assert.doesNotThrow(() => renderPostprocessResults(root, results));
  assert.match(root.textContent, /时间目录未提供/);
});

test("reveal avoids smooth scrolling when reduced motion is requested", async () => {
  globalThis.window = { matchMedia: () => ({ matches: true }) };
  const root = element();
  const button = element("button");
  await revealPostprocess({ root, button, results: validResults() });
  assert.deepEqual(root.scrollCalls, [{ behavior: "auto", block: "start" }]);
  delete globalThis.window;
});
