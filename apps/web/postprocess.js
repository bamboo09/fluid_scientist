const SVG_NS = "http://www.w3.org/2000/svg";
const inflightByRoot = new WeakMap();
const renderedByRoot = new WeakMap();
const buttonLabels = new WeakMap();
const MAX_EVIDENCE_ROWS = 24;
const MAX_CHART_POINTS = 240;
const MAX_CHART_TABLE_ROWS = 24;
const MAX_RESIDUAL_FIELDS = 24;
const SAFE_STATUS_VALUES = new Set([
  "complete", "completed", "converged", "diverged", "failed", "invalid", "ok",
  "passed", "valid", "warning", "不可信", "可信", "完成", "收敛", "未完成", "未收敛",
  "未通过", "通过",
]);
const OBSERVABLE_LABELS = Object.freeze({
  pressure_probes: "压力探针（pressure_probes）",
  velocity_probes: "速度探针（velocity_probes）",
});
const CHART_OBSERVABLES = new Set([
  "centerline_velocity",
  "cavity_centerline_velocity",
  "centerline_profile",
  "force_history",
  "cylinder_force_history",
  "forces",
]);
let chartSequence = 0;

function node(tagName, text, className) {
  const element = document.createElement(tagName);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function svgNode(tagName, attributes = {}) {
  const element = document.createElementNS(SVG_NS, tagName);
  for (const [name, value] of Object.entries(attributes)) {
    element.setAttribute(name, String(value));
  }
  return element;
}

function finiteNumber(value) {
  if (value === null || value === undefined || typeof value === "boolean") return null;
  if (typeof value === "string" && !value.trim()) return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

function displayValue(value) {
  if (value === true) return "是";
  if (value === false) return "否";
  const number = finiteNumber(value);
  if (number !== null) return Number.isInteger(number) ? String(number) : number.toPrecision(5);
  if (typeof value === "string" && value.trim()) {
    const normalized = value.trim().toLowerCase();
    return SAFE_STATUS_VALUES.has(normalized) ? value.trim() : "已省略非数值文本";
  }
  return "未提供";
}

function displayLabel(value, fallback = "未命名字段") {
  if (typeof value !== "string" || !/^[A-Za-z_\u3400-\u9fff][A-Za-z0-9_\-\u3400-\u9fff]{0,63}$/.test(value)) {
    return fallback;
  }
  return OBSERVABLE_LABELS[value] || value;
}

function appendTable(root, captionText, rows, className = "evidence-table") {
  const section = node("section", undefined, "postprocess-section");
  const heading = node("h4", captionText);
  const table = node("table", undefined, className);
  const body = node("tbody");
  const visibleRows = rows.slice(0, MAX_EVIDENCE_ROWS);
  for (const [label, value] of visibleRows) {
    const row = node("tr");
    const header = node("th", label);
    header.setAttribute("scope", "row");
    row.append(header, node("td", value));
    body.append(row);
  }
  table.append(body);
  section.append(heading, table);
  if (rows.length > visibleRows.length) {
    section.append(node(
      "p",
      `共 ${rows.length} 项；显示前 ${visibleRows.length} 项，已省略 ${rows.length - visibleRows.length} 项。`,
      "evidence-summary",
    ));
  }
  root.append(section);
  return section;
}

function numericPoints(series, xKeys, yKeys) {
  if (!Array.isArray(series)) return [];
  return envelopePoints(series.length, MAX_CHART_POINTS, (index) => {
    const item = series[index];
    if (!item || typeof item !== "object") return null;
    const x = xKeys.map((key) => finiteNumber(item[key])).find((value) => value !== null);
    const y = yKeys.map((key) => finiteNumber(item[key])).find((value) => value !== null);
    return x !== undefined && y !== undefined ? { x, y } : null;
  });
}

function envelopePoints(length, limit, pointAt) {
  if (length <= 0) return [];
  if (length <= limit) {
    return Array.from({ length }, (_, index) => pointAt(index)).filter(Boolean);
  }
  const selected = new Map();
  const add = (index, point) => {
    if (point) selected.set(index, point);
  };
  add(0, pointAt(0));
  const bucketCount = Math.floor((limit - 2) / 2);
  const interiorLength = length - 2;
  for (let bucket = 0; bucket < bucketCount; bucket += 1) {
    const start = 1 + Math.floor((bucket * interiorLength) / bucketCount);
    const end = 1 + Math.floor(((bucket + 1) * interiorLength) / bucketCount);
    let minimum = null;
    let maximum = null;
    for (let index = start; index < end; index += 1) {
      const point = pointAt(index);
      if (!point) continue;
      if (!minimum || point.y < minimum.point.y) minimum = { index, point };
      if (!maximum || point.y > maximum.point.y) maximum = { index, point };
    }
    if (minimum) add(minimum.index, minimum.point);
    if (maximum) add(maximum.index, maximum.point);
  }
  add(length - 1, pointAt(length - 1));
  return [...selected.entries()]
    .sort(([left], [right]) => left - right)
    .map(([, point]) => point);
}

function sampleIndexes(length, limit) {
  if (length <= 0) return [];
  if (length <= limit) return Array.from({ length }, (_, index) => index);
  return Array.from({ length: limit }, (_, index) => (
    Math.round((index * (length - 1)) / (limit - 1))
  ));
}

function parallelPoints(series, xKeys, yKeys) {
  if (!series || typeof series !== "object" || Array.isArray(series)) return [];
  const xValues = xKeys.map((key) => series[key]).find(Array.isArray);
  const yValues = yKeys.map((key) => series[key]).find(Array.isArray);
  if (!xValues || !yValues || xValues.length !== yValues.length) return [];
  return envelopePoints(xValues.length, MAX_CHART_POINTS, (index) => {
    const xValue = xValues[index];
    const x = finiteNumber(xValue);
    const y = finiteNumber(yValues[index]);
    return x === null || y === null ? null : { x, y };
  });
}

function missingChart(title) {
  const root = node("section", undefined, "evidence-chart evidence-chart-missing");
  root.append(node("h4", title), node("p", "当前结果未包含该曲线"));
  return root;
}

function renderChart({ title, xLabel, yLabel, series }) {
  const usable = series.slice(0, 4).filter((entry) => entry.points.length >= 2);
  if (!usable.length) return missingChart(title);

  let xMin = Infinity;
  let xMax = -Infinity;
  let yMin = Infinity;
  let yMax = -Infinity;
  for (const entry of usable) {
    for (const point of entry.points) {
      xMin = Math.min(xMin, point.x);
      xMax = Math.max(xMax, point.x);
      yMin = Math.min(yMin, point.y);
      yMax = Math.max(yMax, point.y);
    }
  }
  if (xMin === xMax) { xMin -= 0.5; xMax += 0.5; }
  if (yMin === yMax) { yMin -= 0.5; yMax += 0.5; }

  const width = 560;
  const height = 280;
  const margin = { top: 30, right: 22, bottom: 50, left: 60 };
  const xScale = (value) => margin.left + ((value - xMin) / (xMax - xMin)) * (width - margin.left - margin.right);
  const yScale = (value) => height - margin.bottom - ((value - yMin) / (yMax - yMin)) * (height - margin.top - margin.bottom);
  const titleId = `postprocess-chart-${++chartSequence}`;
  const root = node("section", undefined, "evidence-chart");
  root.append(node("h4", title));
  const legend = node("div", undefined, "chart-legend");
  legend.append(node("strong", "图例"));
  usable.forEach((entry, index) => {
    const item = node("span");
    item.append(node("i", undefined, `chart-key chart-key-${index + 1}`), node("span", entry.label));
    legend.append(item);
  });
  root.append(legend);
  const svg = svgNode("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-labelledby": titleId,
  });
  const svgTitle = svgNode("title", { id: titleId });
  svgTitle.textContent = `${title}；横轴 ${xLabel}；纵轴 ${yLabel}`;
  const xAxis = svgNode("line", { x1: margin.left, y1: height - margin.bottom, x2: width - margin.right, y2: height - margin.bottom, class: "chart-axis" });
  const yAxis = svgNode("line", { x1: margin.left, y1: margin.top, x2: margin.left, y2: height - margin.bottom, class: "chart-axis" });
  const xText = svgNode("text", { x: width / 2, y: height - 13, class: "chart-label", "text-anchor": "middle" });
  xText.textContent = xLabel;
  const yText = svgNode("text", { x: 16, y: height / 2, class: "chart-label", transform: `rotate(-90 16 ${height / 2})`, "text-anchor": "middle" });
  yText.textContent = yLabel;
  svg.append(svgTitle, xAxis, yAxis, xText, yText);
  usable.forEach((entry, index) => {
    const path = svgNode("polyline", {
      points: entry.points.map((point) => `${xScale(point.x).toFixed(2)},${yScale(point.y).toFixed(2)}`).join(" "),
      class: `chart-series chart-series-${index + 1}`,
      fill: "none",
      role: "img",
      "aria-label": `${entry.label}曲线`,
      "stroke-dasharray": index % 2 === 1 ? "7 4" : "none",
    });
    const seriesTitle = svgNode("title");
    seriesTitle.textContent = entry.label;
    path.append(seriesTitle);
    svg.append(path);
  });
  root.append(svg);

  const fallback = node("table", undefined, "chart-fallback");
  fallback.setAttribute("aria-label", `${title}数值表`);
  const head = node("thead");
  const headingRow = node("tr");
  for (const label of ["曲线", xLabel, yLabel]) headingRow.append(node("th", label));
  head.append(headingRow);
  const body = node("tbody");
  let fallbackRows = 0;
  for (const entry of usable) {
    for (const point of entry.points) {
      if (fallbackRows >= MAX_CHART_TABLE_ROWS) break;
      const row = node("tr");
      row.append(node("th", entry.label), node("td", displayValue(point.x)), node("td", displayValue(point.y)));
      body.append(row);
      fallbackRows += 1;
    }
    if (fallbackRows >= MAX_CHART_TABLE_ROWS) break;
  }
  fallback.append(head, body);
  root.append(fallback);
  for (const entry of usable) {
    const totalCount = entry.totalCount || entry.points.length;
    if (totalCount > entry.points.length || totalCount > MAX_CHART_TABLE_ROWS) {
      root.append(node(
        "p",
        `${entry.label}共 ${totalCount} 个原始点；采用首尾与分桶极值包络采样，绘制 ${entry.points.length} 个代表点，已省略 ${Math.max(0, totalCount - entry.points.length)} 个绘图点；表格显示前 ${Math.min(MAX_CHART_TABLE_ROWS, entry.points.length)} 条。`,
        "evidence-summary",
      ));
    }
  }
  for (const entry of series.filter((candidate) => candidate.points.length < 2)) {
    root.append(node("p", `${entry.label}：当前结果未包含该曲线`, "curve-note"));
  }
  return root;
}

export function renderCavityCenterlineProfile(series) {
  const source = series && !Array.isArray(series) && typeof series === "object"
    ? series.points || series.vertical || []
    : series;
  const points = numericPoints(source, ["position", "coordinate", "x", "y"], ["velocity", "u", "value"]);
  return renderChart({
    title: "方腔中心线速度",
    xLabel: "位置",
    yLabel: "速度",
    series: [{
      label: "中心线速度",
      totalCount: Array.isArray(source)
        ? source.length
        : Array.isArray(series?.position)
          ? series.position.length
          : points.length,
      points: points.length ? points : parallelPoints(
        series,
        ["position", "coordinate", "x", "y"],
        ["velocity", "u", "value"],
      ),
    }],
  });
}

export function renderCylinderForceHistory(series) {
  const drag = numericPoints(series, ["time", "t"], ["drag", "cd", "drag_coefficient"]);
  const lift = numericPoints(series, ["time", "t"], ["lift", "cl", "lift_coefficient"]);
  return renderChart({
    title: "圆柱受力历史",
    xLabel: "时间",
    yLabel: "力系数",
    series: [
      {
        label: "阻力",
        totalCount: Array.isArray(series) ? series.length : series?.time?.length,
        points: drag.length ? drag : parallelPoints(series, ["time", "t"], ["drag", "cd", "drag_coefficient"]),
      },
      {
        label: "升力",
        totalCount: Array.isArray(series) ? series.length : series?.time?.length,
        points: lift.length ? lift : parallelPoints(series, ["time", "t"], ["lift", "cl", "lift_coefficient"]),
      },
    ],
  });
}

function residualRows(solver) {
  const rows = [];
  const finalEntries = Object.entries(solver?.final_residuals || {});
  const historyEntries = Object.entries(solver?.residual_history || {});
  const totalFields = finalEntries.length + historyEntries.length;
  const dataLimit = totalFields > MAX_RESIDUAL_FIELDS
    ? MAX_RESIDUAL_FIELDS - 1
    : MAX_RESIDUAL_FIELDS;
  for (const [field, value] of finalEntries) {
    if (rows.length >= dataLimit) break;
    rows.push([`${displayLabel(field)} 最终值`, displayValue(value)]);
  }
  for (const [field, history] of historyEntries) {
    if (rows.length >= dataLimit) break;
    if (!Array.isArray(history)) continue;
    let first = null;
    let last = null;
    let count = 0;
    for (const value of history) {
      const number = finiteNumber(value);
      if (number === null) continue;
      if (first === null) first = number;
      last = number;
      count += 1;
    }
    if (!count) continue;
    rows.push([`${displayLabel(field)} 历史`, `${count} 点；初值 ${displayValue(first)}；末值 ${displayValue(last)}`]);
  }
  if (totalFields > MAX_RESIDUAL_FIELDS) {
    rows.push([
      "显示范围",
      `共 ${totalFields} 个残差字段；显示前 ${dataLimit} 个，已省略 ${totalFields - dataLimit} 个`,
    ]);
  }
  return rows.length ? rows : [["残差", "未提供"]];
}

function solverRows(solver) {
  const source = scalarObject(solver);
  const inlet = finiteNumber(source.inlet_mass_flow);
  const outlet = finiteNumber(source.outlet_mass_flow);
  const reference = inlet === null || outlet === null
    ? 0
    : Math.max(Math.abs(inlet), Math.abs(outlet));
  const derivedImbalance = reference > 0
    ? Math.abs(inlet + outlet) / reference
    : null;
  return [
    ["求解器状态", source.completed === true ? "已完成" : source.completed === false ? "未完成" : "未提供"],
    ["全局连续性误差", displayValue(source.global_continuity_error)],
    ["累计连续性误差", displayValue(source.cumulative_continuity_error)],
    ["入口质量流量", displayValue(source.inlet_mass_flow)],
    ["出口质量流量", displayValue(source.outlet_mass_flow)],
    ["质量不平衡", displayValue(
      source.mass_imbalance ?? source.mass_imbalance_fraction ?? derivedImbalance,
    )],
    ["压降 / Pa", displayValue(source.pressure_drop_pa)],
    ["迭代次数", displayValue(source.iterations)],
  ];
}

function scalarRows(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  const rows = [];
  for (const [key, item] of Object.entries(value)) {
    if (item === null || ["string", "number", "boolean"].includes(typeof item)) {
      rows.push([displayLabel(key), displayValue(item)]);
    }
  }
  return rows;
}

function finiteVector(value) {
  if (!Array.isArray(value) || !value.length || value.length > 12) return null;
  const numbers = value.map(finiteNumber);
  return numbers.every((item) => item !== null) ? numbers : null;
}

function probePoint(item) {
  const scalar = finiteNumber(item);
  if (scalar !== null) return displayValue(scalar);
  const vector = finiteVector(item);
  if (vector) return `(${vector.map(displayValue).join("，")})`;
  if (!item || typeof item !== "object" || Array.isArray(item)) return null;

  const explicitCoordinates = finiteVector(
    item.coordinates || item.coordinate || item.position,
  );
  const xyz = [item.x, item.y, item.z].some((value) => value !== undefined)
    ? [item.x, item.y, item.z].map(finiteNumber)
    : null;
  const coordinates = explicitCoordinates
    || (xyz?.every((value) => value !== null) ? xyz : null);
  const rawValue = item.values ?? item.value ?? item.velocity ?? item.pressure;
  const scalarValue = finiteNumber(rawValue);
  const vectorValue = finiteVector(rawValue);
  const value = scalarValue !== null
    ? displayValue(scalarValue)
    : vectorValue
      ? `(${vectorValue.map(displayValue).join("，")})`
      : null;
  if (!coordinates || !value) return null;
  return `坐标 (${coordinates.map(displayValue).join("，")})；值 ${value}`;
}

function normalizeProbeCollection(value) {
  if (Array.isArray(value)) return value;
  if (!value || typeof value !== "object") return [];
  const coordinates = value.coordinates || value.positions;
  const values = value.values;
  if (!Array.isArray(coordinates) || !Array.isArray(values) || coordinates.length !== values.length) {
    return [];
  }
  return coordinates.map((coordinate, index) => ({ coordinate, value: values[index] }));
}

function appendObservableEvidence(root, key, value) {
  const items = normalizeProbeCollection(value);
  const rows = [];
  let invalidCount = 0;
  for (let index = 0; index < items.length; index += 1) {
    const rendered = probePoint(items[index]);
    if (rendered === null) {
      invalidCount += 1;
      continue;
    }
    if (rows.length < MAX_EVIDENCE_ROWS) rows.push([`探针 ${index + 1}`, rendered]);
  }
  const section = appendTable(
    root,
    displayLabel(key, "探针观测量"),
    rows.length ? rows : [["观测证据", "当前结果未包含有效数值"]],
  );
  if (items.length > MAX_EVIDENCE_ROWS) {
    section.append(node("p", `共 ${items.length} 条；显示前 ${MAX_EVIDENCE_ROWS} 条。`, "evidence-summary"));
  }
  if (invalidCount) {
    section.append(node("p", `当前结果未包含 ${invalidCount} 条有效数值，已跳过。`, "curve-note"));
  }
}

function mergedValidation(results, collection) {
  return {
    ...scalarObject(collection.credibility),
    ...scalarObject(collection.validation),
    ...scalarObject(results?.credibility),
    ...scalarObject(results?.validation),
  };
}

function scalarObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function markerFilename(postProcessing) {
  const candidates = [
    postProcessing?.paraview_file,
    postProcessing?.foam_marker,
    postProcessing?.command,
  ];
  for (const candidate of candidates) {
    if (typeof candidate !== "string") continue;
    const match = candidate.match(/(?:^|[\\/\s'"`])([^\\/\s'"`]+\.foam)(?=$|\s|['"`])/i);
    if (match) return match[1].replace(/[^a-zA-Z0-9._-]/g, "");
  }
  return "未提供 .foam 标记";
}

export function renderPostprocessResults(root, results) {
  const collection = results?.collection || results || {};
  root.replaceChildren();
  root.removeAttribute("role");
  root.setAttribute("aria-live", "polite");
  root.append(node("h3", "浏览器后处理结果"), node("p", "以下内容来自已采集的数值证据，不由模型补写。", "postprocess-summary"));

  const mesh = collection.mesh || {};
  appendTable(root, "网格指标", [
    ["检查状态", mesh.passed === true ? "通过" : mesh.passed === false ? "未通过" : "未提供"],
    ["单元数", displayValue(mesh.cells)],
    ["最大长宽比", displayValue(mesh.max_aspect_ratio)],
    ["最大非正交度", displayValue(mesh.max_non_orthogonality)],
    ["平均非正交度", displayValue(mesh.average_non_orthogonality)],
    ["最大偏斜度", displayValue(mesh.max_skewness)],
  ]);
  appendTable(root, "求解与守恒", solverRows(collection.solver));
  appendTable(root, "残差", residualRows(collection.solver));

  const rawTimes = Array.isArray(collection.numeric_times)
    ? collection.numeric_times
    : Array.isArray(collection.post_processing?.time_directories)
      ? collection.post_processing.time_directories
      : [];
  const numericTimes = rawTimes
    .map(finiteNumber)
    .filter((value) => value !== null)
    .sort((a, b) => a - b);
  const visibleTimes = numericTimes.length <= MAX_EVIDENCE_ROWS
    ? numericTimes
    : sampleIndexes(numericTimes.length, MAX_EVIDENCE_ROWS).map((index) => numericTimes[index]);
  const timeSummary = visibleTimes.length ? visibleTimes.join("、") : "未提供";
  appendTable(root, "数值时间", [[
    "时间目录",
    numericTimes.length > visibleTimes.length
      ? `${timeSummary}（共 ${numericTimes.length} 个，已省略 ${numericTimes.length - visibleTimes.length} 个）`
      : timeSummary,
  ]]);

  const observables = scalarObject(collection.observables);
  const observableScalars = scalarRows(observables);
  appendTable(root, "请求观测量", observableScalars.length ? observableScalars : [["标量观测量", "未提供"]]);
  for (const [key, value] of Object.entries(observables)) {
    if (!CHART_OBSERVABLES.has(key) && (Array.isArray(value) || scalarObject(value).values)) {
      appendObservableEvidence(root, key, value);
    }
  }
  const validation = mergedValidation(results, collection);
  const validationRows = scalarRows(validation);
  appendTable(root, "验证与可信度", validationRows.length ? validationRows : [["验证字段", "未提供"]]);
  appendTable(root, "ParaView 标记", [["文件名", markerFilename(collection.post_processing)]]);

  const cavitySeries = observables.centerline_velocity || observables.cavity_centerline_velocity || observables.centerline_profile;
  const forceSeries = observables.force_history || observables.cylinder_force_history || observables.forces;
  if (cavitySeries !== undefined) root.append(renderCavityCenterlineProfile(cavitySeries));
  if (forceSeries !== undefined) root.append(renderCylinderForceHistory(forceSeries));
  if (cavitySeries === undefined && forceSeries === undefined) {
    root.append(node("p", "当前结果未包含可绘制的中心线速度或圆柱受力曲线。", "curve-note"));
  }
  renderedByRoot.set(root, results);
  return root;
}

function revealPanel(root) {
  const reducedMotion = typeof window !== "undefined"
    && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  root.hidden = false;
  root.setAttribute("tabindex", "-1");
  root.focus({ preventScroll: true });
  root.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "start" });
}

export function revealPostprocess({ root, button, results, fetchResults, sessionKey = results }) {
  if (!root || !button) return Promise.resolve({ ok: false });
  const active = inflightByRoot.get(root);
  if (active && active.sessionKey === sessionKey) return active.promise;

  if (results && renderedByRoot.get(root) === results) {
    revealPanel(root);
    return Promise.resolve({ ok: true, results });
  }

  const previousLabel = buttonLabels.get(button) || button.textContent || "查看浏览器后处理";
  buttonLabels.set(button, previousLabel);
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "正在读取后处理结果…";
  root.setAttribute("aria-busy", "true");
  root.setAttribute("role", "status");
  root.setAttribute("aria-live", "polite");
  root.replaceChildren(node("p", "正在整理网格、残差与观测量…", "postprocess-loading"));
  root.hidden = false;
  const token = Symbol("postprocess-request");

  let loaded;
  try {
    loaded = results || fetchResults?.();
  } catch (error) {
    loaded = Promise.reject(error);
  }
  const promise = Promise.resolve(loaded)
    .then((payload) => {
      if (!payload) throw new Error("missing-results");
      const current = inflightByRoot.get(root);
      if (current?.token !== token) return { ok: false, stale: true };
      renderPostprocessResults(root, payload);
      revealPanel(root);
      return { ok: true, results: payload };
    })
    .catch(() => {
      const current = inflightByRoot.get(root);
      if (current?.token !== token) return { ok: false, stale: true };
      root.replaceChildren(
        node("h3", "浏览器后处理暂不可用"),
        node("p", "后处理结果暂时无法读取，请稍后重试。"),
      );
      root.setAttribute("role", "alert");
      root.setAttribute("aria-live", "assertive");
      revealPanel(root);
      return { ok: false };
    })
    .finally(() => {
      const current = inflightByRoot.get(root);
      if (current?.token !== token) return;
      inflightByRoot.delete(root);
      root.setAttribute("aria-busy", "false");
      button.setAttribute("aria-busy", "false");
      button.disabled = false;
      button.textContent = previousLabel;
    });
  inflightByRoot.set(root, { promise, sessionKey, token });
  return promise;
}

export function bindPostprocessButton({ button, root, getRequest }) {
  if (!button || !root || typeof getRequest !== "function") return () => {};
  const listener = () => {
    void revealPostprocess({ root, button, ...getRequest() });
  };
  button.addEventListener("click", listener);
  return listener;
}
