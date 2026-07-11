/**
 * V5 Compile-Ready Pipeline UI
 *
 * Provides a one-click "fast generate" path that calls /api/v5/pipeline/run
 * and renders the resulting CompileReadyDraftView, plus an incremental
 * modification flow that calls /api/v5/pipeline/modify.
 */

const $ = (sel) => document.querySelector(sel);

const STAGE_LABELS = {
  understanding: "理解意图",
  designing: "设计参数",
  closing: "闭合参数依赖",
  resolving_capabilities: "检查能力",
  generating_case: "生成算例文件",
  validating_case: "验证算例",
  compile_ready: "编译就绪",
  failed: "失败",
};

const SEVERITY_LABELS = {
  error: "错误",
  warning: "警告",
  info: "信息",
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let v5SessionId = null;
let v5CompileReadyView = null;
let v5Polling = false;

// ---------------------------------------------------------------------------
// DOM references (lazily resolved)
// ---------------------------------------------------------------------------

function els() {
  return {
    card: $("#v5-pipeline-card"),
    runBtn: $("#v5-run-pipeline"),
    modifyBtn: $("#v5-apply-modify"),
    modifyInput: $("#v5-modify-input"),
    status: $("#v5-pipeline-status"),
    stageList: $("#v5-stage-list"),
    result: $("#v5-compile-ready-view"),
    errorBox: $("#v5-error-box"),
    spinner: $("#v5-spinner"),
    genFiles: $("#v5-generated-files"),
    checksList: $("#v5-checks-list"),
    objective: $("#v5-objective"),
    solverBox: $("#v5-solver"),
    geomBox: $("#v5-geometry"),
    meshBox: $("#v5-mesh"),
    bcBox: $("#v5-bc"),
    metricsBox: $("#v5-metrics"),
    modifiableHint: $("#v5-modifiable-hint"),
    fileCount: $("#v5-file-count"),
  };
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function runPipeline(userDescription) {
  const resp = await fetch("/api/v5/pipeline/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_description: userDescription }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Pipeline failed (${resp.status}): ${text}`);
  }
  return resp.json();
}

async function modifyPipeline(sessionId, modificationText) {
  const resp = await fetch("/api/v5/pipeline/modify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, modification_text: modificationText }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Modify failed (${resp.status}): ${text}`);
  }
  return resp.json();
}

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

function renderStageHistory(stageHistory) {
  const e = els();
  if (!e.stageList) return;
  e.stageList.innerHTML = "";
  for (const stage of stageHistory) {
    const li = document.createElement("li");
    const name = STAGE_LABELS[stage.stage] || stage.stage;
    const detail = stage.detail ? ` — ${stage.detail}` : "";
    li.textContent = `${name}${detail}`;
    if (stage.stage === "compile_ready") li.classList.add("stage-done");
    e.stageList.appendChild(li);
  }
}

function renderValidationChecks(validationResults) {
  const e = els();
  if (!e.checksList) return;
  e.checksList.innerHTML = "";
  const checks = (validationResults && validationResults.checks) || [];
  for (const c of checks) {
    const li = document.createElement("li");
    const icon = c.passed ? "✓" : "✗";
    const sev = SEVERITY_LABELS[c.severity] || c.severity;
    li.className = `check check-${c.passed ? "pass" : "fail"} check-sev-${c.severity}`;
    li.innerHTML = `<span class="check-icon">${icon}</span><span class="check-sev">${sev}</span><span class="check-name">${c.check_name}</span><span class="check-msg">${c.message || ""}</span>`;
    e.checksList.appendChild(li);
  }
}

function renderGeneratedFiles(files) {
  const e = els();
  if (!e.genFiles) return;
  e.genFiles.innerHTML = "";
  for (const f of files || []) {
    const li = document.createElement("li");
    li.textContent = f;
    e.genFiles.appendChild(li);
  }
  if (e.fileCount) e.fileCount.textContent = (files || []).length;
}

function fmtValue(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") {
    if ("value" in v) {
      const unit = v.unit ? ` ${v.unit}` : "";
      return `${v.value}${unit}`;
    }
    return JSON.stringify(v);
  }
  return String(v);
}

function kvTable(parent, obj, keys) {
  if (!parent) return;
  parent.innerHTML = "";
  const items = keys || Object.keys(obj || {});
  for (const k of items) {
    const v = obj ? obj[k] : undefined;
    if (v === undefined || v === null) continue;
    const row = document.createElement("div");
    row.className = "kv-row";
    row.innerHTML = `<span class="kv-key">${k}</span><span class="kv-val">${fmtValue(v)}</span>`;
    parent.appendChild(row);
  }
}

function renderMetricsList(parent, metrics, label) {
  if (!parent) return;
  parent.innerHTML = "";
  if (!metrics || !metrics.length) {
    parent.innerHTML = `<p class="empty-hint">无${label}</p>`;
    return;
  }
  for (const m of metrics) {
    const div = document.createElement("div");
    div.className = "metric-item";
    const name = m.name || m.metric_name || m.goal || "(unnamed)";
    const desc = m.description || m.type || "";
    div.innerHTML = `<strong>${name}</strong>${desc ? `<small>${desc}</small>` : ""}`;
    parent.appendChild(div);
  }
}

function renderCompileReadyView(view) {
  const e = els();
  if (!e.result) return;
  v5CompileReadyView = view;

  // Objective
  if (e.objective) e.objective.textContent = view.research_objective || "(未提供)";

  // Solver
  if (e.solverBox) {
    const s = view.solver || {};
    e.solverBox.innerHTML = `
      <div class="kv-row"><span class="kv-key">求解器</span><span class="kv-val highlight">${s.name || s.solver_name || "—"}</span></div>
      <div class="kv-row"><span class="kv-key">湍流模型</span><span class="kv-val">${s.turbulence_model || view.physical_models?.turbulence_model || "—"}</span></div>
      <div class="kv-row"><span class="kv-key">时间类型</span><span class="kv-val">${s.temporal_type || view.numerics?.time_control?.temporal_type || "—"}</span></div>
    `;
  }

  // Geometry
  kvTable(e.geomBox, view.geometry);

  // Mesh
  if (e.meshBox) {
    const m = view.mesh || {};
    e.meshBox.innerHTML = `
      <div class="kv-row"><span class="kv-key">网格族</span><span class="kv-val">${m.geometry_family || m.family || "—"}</span></div>
      <div class="kv-row"><span class="kv-key">分辨率</span><span class="kv-val">${m.resolution || m.mesh_resolution || view.design?.mesh_resolution?.value || "—"}</span></div>
      ${m.n_cells ? `<div class="kv-row"><span class="kv-key">单元数</span><span class="kv-val">${m.n_cells}</span></div>` : ""}
    `;
  }

  // Boundary conditions
  if (e.bcBox) {
    const bcs = view.boundary_conditions || {};
    e.bcBox.innerHTML = "";
    for (const [patch, cfg] of Object.entries(bcs)) {
      const div = document.createElement("div");
      div.className = "bc-patch";
      const type = typeof cfg === "object" ? (cfg.type || JSON.stringify(cfg)) : cfg;
      div.innerHTML = `<strong>${patch}</strong><span>${type}</span>`;
      e.bcBox.appendChild(div);
    }
    if (!Object.keys(bcs).length) {
      e.bcBox.innerHTML = `<p class="empty-hint">无边界条件信息</p>`;
    }
  }

  // Metrics
  renderMetricsList(e.metricsBox?.querySelector("#v5-scientific-metrics"), view.scientific_metrics, "科学指标");
  renderMetricsList(e.metricsBox?.querySelector("#v5-credibility-metrics"), view.credibility_metrics, "可信度指标");

  // Modifiable hint
  if (e.modifiableHint && view.modifiable_fields) {
    e.modifiableHint.textContent = `可修改：${view.modifiable_fields.join("、")}`;
  }

  // Validation & files
  renderValidationChecks(view.validation_results);
  renderGeneratedFiles(view.case_manifest?.generated_files || []);

  // Show result card, enable modify
  e.result.hidden = false;
  if (e.modifyBtn) e.modifyBtn.disabled = false;
  if (e.modifyInput) e.modifyInput.disabled = false;
}

function showError(message) {
  const e = els();
  if (e.errorBox) {
    e.errorBox.hidden = false;
    e.errorBox.textContent = message;
  }
  if (e.spinner) e.spinner.hidden = true;
}

function clearError() {
  const e = els();
  if (e.errorBox) e.errorBox.hidden = true;
}

function setRunning(isRunning) {
  const e = els();
  if (e.runBtn) e.runBtn.disabled = isRunning;
  if (e.spinner) e.spinner.hidden = !isRunning;
}

// ---------------------------------------------------------------------------
// Event handlers
// ---------------------------------------------------------------------------

async function handleRunPipeline() {
  const e = els();
  const promptEl = document.getElementById("experiment-prompt");
  const userText = (promptEl?.value || "").trim();
  if (userText.length < 10) {
    showError("请输入至少10个字符的研究问题描述。");
    return;
  }
  clearError();
  setRunning(true);
  if (e.status) e.status.textContent = "正在运行编译就绪流水线...";
  if (e.result) e.result.hidden = true;
  if (e.stageList) e.stageList.innerHTML = "";
  v5SessionId = null;
  v5CompileReadyView = null;

  try {
    const resp = await runPipeline(userText);
    v5SessionId = resp.session_id;
    renderStageHistory(resp.stage_history || []);
    if (resp.status === "compile_ready" && resp.compile_ready_view) {
      if (e.status) e.status.textContent = "编译就绪 ✓";
      renderCompileReadyView(resp.compile_ready_view);
    } else if (resp.failure) {
      const msg = resp.failure.message || resp.failure.failure_category || "未知错误";
      showError(`流水线在 "${resp.current_stage}" 阶段失败：${msg}`);
      if (e.status) e.status.textContent = "失败";
    } else {
      showError(`意外状态：${resp.status}`);
    }
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    setRunning(false);
  }
}

async function handleModifyPipeline() {
  const e = els();
  if (!v5SessionId) {
    showError("没有活动会话，请先生成一个算例。");
    return;
  }
  const modText = (e.modifyInput?.value || "").trim();
  if (modText.length < 3) {
    showError("请输入修改描述（至少3个字符）。");
    return;
  }
  clearError();
  if (e.modifyBtn) e.modifyBtn.disabled = true;
  if (e.spinner) e.spinner.hidden = false;
  if (e.status) e.status.textContent = "正在应用增量修改...";

  try {
    const resp = await modifyPipeline(v5SessionId, modText);
    renderStageHistory(resp.stage_history || []);
    if (resp.status === "compile_ready" && resp.compile_ready_view) {
      if (e.status) e.status.textContent = "修改成功，编译就绪 ✓";
      renderCompileReadyView(resp.compile_ready_view);
      if (e.modifyInput) e.modifyInput.value = "";
    } else if (resp.failure) {
      showError(`修改失败：${resp.failure.message || resp.failure.failure_category}`);
      if (e.modifyBtn) e.modifyBtn.disabled = false;
    }
  } catch (err) {
    showError(err.message || String(err));
    if (e.modifyBtn) e.modifyBtn.disabled = false;
  } finally {
    if (e.spinner) e.spinner.hidden = true;
  }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

export function initV5Pipeline() {
  const e = els();
  if (!e.card) return; // card not present in DOM yet
  e.card.hidden = false;
  if (e.runBtn) e.runBtn.addEventListener("click", handleRunPipeline);
  if (e.modifyBtn) {
    e.modifyBtn.disabled = true;
    e.modifyBtn.addEventListener("click", handleModifyPipeline);
  }
  if (e.modifyInput) {
    e.modifyInput.disabled = true;
    e.modifyInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        handleModifyPipeline();
      }
    });
  }
}

// Auto-init when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initV5Pipeline);
} else {
  initV5Pipeline();
}
