const form = document.querySelector("#research-form");
const button = document.querySelector("#run-button");
const message = document.querySelector("#form-message");
const createProjectButton = document.querySelector("#create-project");
const designExperimentButton = document.querySelector("#design-experiment");
const approveButton = document.querySelector("#gate-approve");
const rejectButton = document.querySelector("#gate-reject");
const advanceButton = document.querySelector("#gate-advance");
const benchmarkForm = document.querySelector("#benchmark-form");
const submitBenchmarkButton = document.querySelector("#submit-benchmark");
const executionTarget = document.querySelector("#execution-target");
const customCaseFile = document.querySelector("#custom-case-file");
const validateCustomCaseButton = document.querySelector("#validate-custom-case");
const submitCustomCaseButton = document.querySelector("#submit-custom-case");
const configureModelButton = document.querySelector("#configure-model");
const viewPostprocessButton = document.querySelector("#view-postprocess");
let currentProject = null;
let selectedTarget = "";
let pollTimer = null;
let latestBenchmarkResults = null;
let latestCustomCollection = null;
const benchmarkCaseId = "pilot-pipe";
const projectStorageKey = "fluid-scientist-project-id";
const targetStorageKey = "fluid-scientist-target-id";
const customSubmitEndpoint = "/api/custom-cases/submit";

const number = (value, digits = 2) => Number(value).toFixed(digits);

async function loadExecutionTargets() {
  try {
    const targets = await requestJson("/api/execution-targets");
    executionTarget.replaceChildren(...targets.map((target) => {
      const option = document.createElement("option");
      option.value = target.target_id;
      option.disabled = !target.available;
      const platform = target.kind === "workstation_openfoam" ? "工作站 OpenFOAM" : "HPC Slurm";
      option.textContent = target.available
        ? `${platform} · ${target.foam_version || "版本未知"} · ${target.cpu_count || "?"} CPU`
        : `${platform} · 不可用：${target.reason}`;
      return option;
    }));
    const savedTarget = localStorage.getItem(targetStorageKey);
    const available = targets.find(
      (target) => target.available && target.target_id === savedTarget,
    ) || targets.find((target) => target.available);
    if (available) {
      executionTarget.value = available.target_id;
      selectedTarget = available.target_id;
      document.querySelector("#system-state").innerHTML =
        `<span></span> REAL TARGET · ${available.foam_version || "OPENFOAM"}`;
    } else {
      executionTarget.innerHTML = '<option value="">尚未配置真实执行平台</option>';
      document.querySelector("#system-state").innerHTML =
        "<span></span> FAKE MODE · 未配置真实平台";
    }
    refreshBenchmarkControls();
  } catch (error) {
    executionTarget.innerHTML = `<option value="">能力检查失败：${error.message}</option>`;
    document.querySelector("#system-state").innerHTML = "<span></span> TARGET CHECK FAILED";
  }
}

function renderResult(result) {
  document.querySelector("#postprocess-card").hidden = true;
  document.querySelector("#secondary-metric-label").textContent = "标准差";
  document.querySelector("#third-metric-label").textContent = "细网格 GCI";
  document.querySelector("#project-status").textContent = result.workflow_state;
  document.querySelector("#job-state").textContent = "COMPLETED 3 / 3";
  document.querySelector("#job-progress").style.width = "100%";
  document.querySelector("#credibility-state").textContent = "PASSED";
  document.querySelector("#mass-balance").textContent =
    `${number(result.validation.mass_imbalance_percent, 3)}%`;
  document.querySelector("#mesh-score").textContent =
    number(result.validation.mesh_independence * 100, 2) + "%";
  document.querySelector("#benchmark-score").textContent =
    number(result.validation.benchmark_agreement * 100, 0) + "%";

  const ids = new Set(result.report.claims.flatMap((claim) => claim.evidence_ids));
  document.querySelector("#evidence-count").textContent = ids.size;
  document.querySelector("#audit-count").textContent =
    `${result.audit_event_count} AUDIT EVENTS`;
  document.querySelector("#mean-pressure").textContent =
    number(result.analysis.metrics.pressure_drop_pa_mean, 1);
  document.querySelector("#std-pressure").textContent =
    number(result.analysis.metrics.pressure_drop_pa_std, 2);
  document.querySelector("#gci").textContent =
    number(result.analysis.metrics.fine_grid_gci_percent, 3);
  document.querySelector("#scope-note").textContent =
    `${result.report.scope} 限制：${result.report.limitations.join("；")}`;

  const list = document.querySelector("#claim-list");
  list.replaceChildren(...result.report.claims.map((claim) => {
    const item = document.createElement("li");
    item.append(document.createTextNode(claim.text));
    const evidence = document.createElement("small");
    evidence.textContent = `${claim.level} · ${claim.evidence_ids.join(" · ")}`;
    item.append(evidence);
    return item;
  }));
  document.querySelector("#report").hidden = false;
  document.querySelector("#report").scrollIntoView({ behavior: "smooth", block: "start" });
}

const gateForState = (state) => ({
  SPEC_READY: "GATE_1",
  PILOT_READY: "GATE_2",
  REVIEW_READY: "GATE_3",
})[state] || null;

const actionForState = (state) => ({
  SPEC_READY: "RETRIEVE_EVIDENCE",
  EVIDENCE_READY: "DESIGN_PILOT",
  PILOT_VERIFIED: "DESIGN_FULL",
  FULL_READY: "SUBMIT_FULL",
  FULL_RUNNING: "ANALYZE",
  ANALYZED: "REVIEW",
  REVIEW_READY: "PUBLISH_REPORT",
})[state] || null;

function renderProject(project) {
  currentProject = project;
  localStorage.setItem(projectStorageKey, project.project_id);
  document.querySelector("#project-status").textContent = project.workflow_state;
  document.querySelector("#audit-count").textContent = `${project.audit_event_count} AUDIT EVENTS`;
  const gate = gateForState(project.workflow_state);
  const approved = gate && project.approvals.some((item) => item.gate === gate);
  approveButton.disabled = !gate || approved;
  rejectButton.disabled = !gate;
  advanceButton.disabled = !actionForState(project.workflow_state) || (gate && !approved);
  if (project.workflow_state === "PILOT_READY" && approved) {
    message.textContent = "Gate 2 已批准：请检查圆管参数并提交真实工作站算例。";
  } else if (project.workflow_state === "PILOT_RUNNING") {
    message.textContent = "真实算例已提交，正在轮询工作站状态。";
  } else {
    message.textContent = gate
      ? `${project.workflow_state}：等待 ${gate} 人工审批。`
      : `${project.workflow_state}：可执行下一工作流动作。`;
  }
  refreshBenchmarkControls();
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `API returned ${response.status}`);
  }
  return response.json();
}

function gateTwoApproved() {
  return Boolean(currentProject?.approvals.some((item) => item.gate === "GATE_2"));
}

function refreshBenchmarkControls() {
  const ready = currentProject?.workflow_state === "PILOT_READY" && gateTwoApproved();
  benchmarkForm.hidden = !ready;
  submitBenchmarkButton.disabled = !ready || !selectedTarget;
}

function pipeCasePayload() {
  return {
    diameter_m: Number(document.querySelector("#pipe-diameter").value),
    length_m: Number(document.querySelector("#pipe-length").value),
    mean_velocity_m_s: Number(document.querySelector("#pipe-velocity").value),
    kinematic_viscosity_m2_s: Number(document.querySelector("#pipe-nu").value),
    density_kg_m3: Number(document.querySelector("#pipe-density").value),
    axial_cells: Number(document.querySelector("#axial-cells").value),
    radial_cells: Number(document.querySelector("#radial-cells").value),
  };
}

function applyExperimentDesign(design) {
  if (design.experiment_type === "custom_openfoam") {
    document.querySelector("#custom-experiment-name").value = design.experiment_name;
    const rationale = document.querySelector("#design-rationale");
    rationale.textContent =
      `${design.objective} 设计理由：${design.rationale} `
      + `几何：${design.custom_case.geometry}；边界：${design.custom_case.boundary_conditions.join("、")}；`
      + `网格：${design.custom_case.mesh_strategy}；运行：${design.custom_case.run_strategy}。`
      + "请据此准备 tar.gz case，安全校验后提交工作站。";
    rationale.hidden = false;
    document.querySelector("#experiment").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  document.querySelector("#experiment-name").value = design.experiment_name;
  document.querySelector("#pipe-diameter").value = design.case.diameter_m;
  document.querySelector("#pipe-length").value = design.case.length_m;
  document.querySelector("#pipe-velocity").value = design.case.mean_velocity_m_s;
  document.querySelector("#pipe-nu").value = design.case.kinematic_viscosity_m2_s;
  document.querySelector("#pipe-density").value = design.case.density_kg_m3;
  document.querySelector("#axial-cells").value = design.case.axial_cells;
  document.querySelector("#radial-cells").value = design.case.radial_cells;
  const rationale = document.querySelector("#design-rationale");
  rationale.textContent =
    `${design.objective} 设计理由：${design.rationale} `
    + `假设：${design.assumptions.join("；")}。`;
  rationale.hidden = false;
}

function resultMetric(label, value, unit = "") {
  const item = document.createElement("div");
  const name = document.createElement("span");
  const reading = document.createElement("strong");
  name.textContent = label;
  reading.textContent = `${value}${unit}`;
  item.append(name, reading);
  return item;
}

function renderPostprocessResults(results) {
  if (!results) return;
  const { collection, validation } = results;
  const output = document.querySelector("#postprocess-results");
  const heading = document.createElement("header");
  const title = document.createElement("strong");
  const subtitle = document.createElement("small");
  title.textContent = validation.passed ? "可信性验收通过" : "结果未通过可信性验收";
  subtitle.textContent = `${collection.mesh.cells} cells · ${collection.post_processing.time_directories.length} time directories`;
  heading.append(title, subtitle);

  const metrics = document.createElement("div");
  metrics.className = "postprocess-metrics";
  metrics.append(
    resultMetric("数值压降", number(validation.numerical_pressure_drop_pa, 3), " Pa"),
    resultMetric("解析压降", number(validation.analytical_pressure_drop_pa, 3), " Pa"),
    resultMetric("压降误差", number(validation.pressure_drop_error_percent, 3), "%"),
    resultMetric("质量不平衡", number(validation.mass_imbalance_percent, 4), "%"),
  );

  const residualTitle = document.createElement("p");
  residualTitle.textContent = "FINAL RESIDUALS";
  const residuals = document.createElement("div");
  residuals.className = "residual-list";
  Object.entries(collection.solver.final_residuals).forEach(([field, value]) => {
    const row = document.createElement("div");
    const label = document.createElement("span");
    const bar = document.createElement("i");
    const reading = document.createElement("code");
    label.textContent = field;
    const magnitude = Math.max(0, Math.min(100, (-Math.log10(Math.max(Number(value), 1e-12)) / 12) * 100));
    bar.style.setProperty("--residual", `${magnitude}%`);
    reading.textContent = Number(value).toExponential(2);
    row.append(label, bar, reading);
    residuals.append(row);
  });
  output.replaceChildren(heading, metrics, residualTitle, residuals);
  output.hidden = false;
  viewPostprocessButton.textContent = "结果已展开";
}

function renderCustomPostprocessResults(collection) {
  if (!collection) return;
  const output = document.querySelector("#postprocess-results");
  const heading = document.createElement("header");
  const title = document.createElement("strong");
  const subtitle = document.createElement("small");
  title.textContent = collection.mesh.passed ? "网格与求解完成" : "网格检查未通过";
  subtitle.textContent = `${collection.mesh.cells} cells · ${collection.post_processing.time_directories.length} time directories`;
  heading.append(title, subtitle);
  const metrics = document.createElement("div");
  metrics.className = "postprocess-metrics";
  metrics.append(
    resultMetric("网格单元", collection.mesh.cells),
    resultMetric("最大长宽比", number(collection.mesh.max_aspect_ratio, 2)),
    resultMetric("最大非正交度", number(collection.mesh.max_non_orthogonality, 2), "°"),
    resultMetric("最大偏斜度", number(collection.mesh.max_skewness, 3)),
  );
  const residualTitle = document.createElement("p");
  residualTitle.textContent = "FINAL RESIDUALS";
  const residuals = document.createElement("div");
  residuals.className = "residual-list";
  Object.entries(collection.solver.final_residuals).forEach(([field, value]) => {
    const row = document.createElement("div");
    const label = document.createElement("span");
    const bar = document.createElement("i");
    const reading = document.createElement("code");
    label.textContent = field;
    const magnitude = Math.max(0, Math.min(100, (-Math.log10(Math.max(Number(value), 1e-12)) / 12) * 100));
    bar.style.setProperty("--residual", `${magnitude}%`);
    reading.textContent = Number(value).toExponential(2);
    row.append(label, bar, reading);
    residuals.append(row);
  });
  output.replaceChildren(heading, metrics, residualTitle, residuals);
  output.hidden = false;
  viewPostprocessButton.textContent = "结果已展开";
}

function renderCustomCaseResults(collection) {
  latestBenchmarkResults = null;
  latestCustomCollection = collection;
  document.querySelector("#job-state").textContent = collection.state.toUpperCase();
  document.querySelector("#job-id").textContent = collection.job_id;
  document.querySelector("#job-progress").style.width = "100%";
  document.querySelector("#credibility-state").textContent = collection.mesh.passed ? "MESH PASSED" : "FAILED";
  document.querySelector("#mesh-score").textContent = `${collection.mesh.cells} CELLS`;
  document.querySelector("#benchmark-score").textContent = "CUSTOM CASE";
  document.querySelector("#mass-balance").textContent = "—";
  document.querySelector("#mean-pressure").textContent = collection.mesh.cells;
  document.querySelector("#secondary-metric-label").textContent = "最终残差字段";
  document.querySelector("#std-pressure").textContent = Object.keys(collection.solver.final_residuals).length;
  document.querySelector("#third-metric-label").textContent = "时间目录";
  document.querySelector("#gci").textContent = collection.post_processing.time_directories.length;
  const list = document.querySelector("#claim-list");
  list.replaceChildren(...[
    `网格检查通过，共 ${collection.mesh.cells} 个单元。`,
    `求解器正常结束：${collection.solver.completed ? "是" : "否"}。`,
    `已生成 ${collection.post_processing.time_directories.length} 个可后处理时间目录。`,
  ].map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
  const postProcessing = collection.post_processing;
  document.querySelector("#postprocess-command").textContent =
    `cd ~/.local/share/fluid-scientist/${postProcessing.case_path} && paraFoam`;
  document.querySelector("#postprocess-times").textContent =
    `可用时间目录：${postProcessing.time_directories.join(", ")} · ParaView 文件：${postProcessing.paraview_file}`;
  document.querySelector("#postprocess-card").hidden = false;
  document.querySelector("#postprocess-results").hidden = true;
  viewPostprocessButton.textContent = "在页面中查看结果";
  document.querySelector("#report").hidden = false;
  document.querySelector("#scope-note").textContent = "自定义算例只报告网格、残差和求解产物；不会套用圆管解析基准。";
}

function renderBenchmarkResults(results) {
  latestBenchmarkResults = results;
  latestCustomCollection = null;
  const { collection, validation, project } = results;
  currentProject = project;
  document.querySelector("#project-status").textContent = project.workflow_state;
  document.querySelector("#job-state").textContent = collection.state.toUpperCase();
  document.querySelector("#job-id").textContent = collection.job_id;
  document.querySelector("#job-progress").style.width = "100%";
  document.querySelector("#credibility-state").textContent = validation.passed ? "PASSED" : "FAILED";
  document.querySelector("#mass-balance").textContent =
    `${number(validation.mass_imbalance_percent, 4)}%`;
  document.querySelector("#mesh-score").textContent = collection.mesh.passed ? "MESH OK" : "FAILED";
  document.querySelector("#benchmark-score").textContent =
    `${number(validation.pressure_drop_error_percent, 2)}% ERR`;
  document.querySelector("#mean-pressure").textContent =
    number(validation.numerical_pressure_drop_pa, 3);
  document.querySelector("#secondary-metric-label").textContent = "解析压降";
  document.querySelector("#std-pressure").textContent =
    number(validation.analytical_pressure_drop_pa, 3);
  document.querySelector("#third-metric-label").textContent = "解析误差";
  document.querySelector("#gci").textContent =
    number(validation.pressure_drop_error_percent, 3);
  document.querySelector("#audit-count").textContent =
    `${project.audit_event_count} AUDIT EVENTS`;
  const claims = [
    `网格检查通过，共 ${collection.mesh.cells} 个单元。`,
    `入口与出口质量不平衡为 ${number(validation.mass_imbalance_percent, 4)}%。`,
    `数值压降 ${number(validation.numerical_pressure_drop_pa, 3)} Pa，`
      + `相对解析解误差 ${number(validation.pressure_drop_error_percent, 2)}%。`,
  ];
  const list = document.querySelector("#claim-list");
  list.replaceChildren(...claims.map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
  document.querySelector("#scope-note").textContent = validation.passed
    ? "该结果已通过网格、残差、质量守恒和 Hagen–Poiseuille 解析基准门禁。"
    : "该算例已完成，但未通过全部可信性门禁，不能用于科研结论。";
  const postProcessing = collection.post_processing;
  if (postProcessing) {
    const postprocessCommand =
      `cd ~/.local/share/fluid-scientist/${postProcessing.case_path} && paraFoam`;
    document.querySelector("#postprocess-command").textContent = postprocessCommand;
    document.querySelector("#postprocess-times").textContent =
      `可用时间目录：${postProcessing.time_directories.join(", ")} · `
      + `ParaView 文件：${postProcessing.paraview_file}`;
    document.querySelector("#postprocess-card").hidden = false;
  } else {
    document.querySelector("#postprocess-card").hidden = true;
  }
  document.querySelector("#report").hidden = false;
  message.textContent = validation.passed
    ? "真实工作站仿真完成，确定性可信性验收通过。"
    : "真实工作站仿真完成，但可信性验收未通过。";
}

async function pollBenchmark(projectId, targetId) {
  const query = `target_id=${encodeURIComponent(targetId)}`;
  try {
    const job = await requestJson(
      `/api/projects/${projectId}/benchmarks/${benchmarkCaseId}?${query}`,
    );
    document.querySelector("#job-id").textContent = job.job_id;
    document.querySelector("#job-state").textContent = job.state.toUpperCase();
    document.querySelector("#job-progress").style.width =
      job.state === "running" ? "58%" : "18%";
    if (job.state === "succeeded") {
      const results = await requestJson(
        `/api/projects/${projectId}/benchmarks/${benchmarkCaseId}/results?${query}`,
      );
      renderBenchmarkResults(results);
      return;
    }
    if (["failed", "cancelled"].includes(job.state)) {
      message.textContent = `工作站算例终止：${job.error || job.state}`;
      return;
    }
    pollTimer = window.setTimeout(() => pollBenchmark(projectId, targetId), 1500);
  } catch (error) {
    message.textContent = `状态查询失败：${error.message}，稍后自动重试。`;
    pollTimer = window.setTimeout(() => pollBenchmark(projectId, targetId), 3000);
  }
}

async function pollCustomCase(jobId, targetId) {
  const query = `target_id=${encodeURIComponent(targetId)}`;
  try {
    const job = await requestJson(`/api/custom-cases/${jobId}?${query}`);
    document.querySelector("#job-state").textContent = job.state.toUpperCase();
    document.querySelector("#job-progress").style.width = job.state === "running" ? "58%" : "18%";
    if (job.state === "succeeded") {
      const collection = await requestJson(`/api/custom-cases/${jobId}/results?${query}`);
      renderCustomCaseResults(collection);
      document.querySelector("#custom-case-result").textContent = "工作站仿真完成；可在下方直接查看后处理结果。";
      return;
    }
    if (["failed", "cancelled"].includes(job.state)) {
      document.querySelector("#custom-case-result").textContent = `仿真终止：${job.error || job.state}`;
      submitCustomCaseButton.disabled = false;
      return;
    }
    pollTimer = window.setTimeout(() => pollCustomCase(jobId, targetId), 1500);
  } catch (error) {
    document.querySelector("#custom-case-result").textContent = `状态查询失败：${error.message}，稍后重试。`;
    pollTimer = window.setTimeout(() => pollCustomCase(jobId, targetId), 3000);
  }
}

async function resumeProject() {
  const savedProjectId = localStorage.getItem(projectStorageKey);
  try {
    let project;
    if (savedProjectId) {
      project = await requestJson(`/api/projects/${savedProjectId}`);
    } else {
      const response = await fetch("/api/projects/recent");
      if (response.status === 404) return;
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      project = await response.json();
    }
    renderProject(project);
    const jobId = project.external_jobs?.[benchmarkCaseId];
    if (jobId) document.querySelector("#job-id").textContent = jobId;
    const targetId = localStorage.getItem(targetStorageKey) || selectedTarget;
    if (!jobId || !targetId) return;
    if (project.workflow_state === "PILOT_RUNNING") {
      message.textContent = "已恢复未完成项目，正在重新连接工作站作业。";
      pollBenchmark(project.project_id, targetId);
    } else if (project.workflow_state === "PILOT_VERIFIED") {
      const query = `target_id=${encodeURIComponent(targetId)}`;
      const results = await requestJson(
        `/api/projects/${project.project_id}/benchmarks/${benchmarkCaseId}/results?${query}`,
      );
      renderBenchmarkResults(results);
      message.textContent = "已恢复上次工作站实验及可信性结果。";
    }
  } catch (error) {
    localStorage.removeItem(projectStorageKey);
    message.textContent = `项目恢复失败：${error.message}`;
  }
}

async function bootstrap() {
  await loadExecutionTargets();
  await resumeProject();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  message.textContent = "正在执行结构化、Fake Slurm、可信性验证与科研审查…";
  try {
    const response = await fetch("/api/demo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: document.querySelector("#question").value }),
    });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    const result = await response.json();
    renderResult(result);
    message.textContent = "闭环完成：所有结论均已绑定分析、仿真或文献证据。";
  } catch (error) {
    message.textContent = `运行失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
});

createProjectButton.addEventListener("click", async () => {
  createProjectButton.disabled = true;
  try {
    const project = await requestJson("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: document.querySelector("#question").value }),
    });
    renderProject(project);
  } catch (error) {
    message.textContent = `创建失败：${error.message}`;
  } finally {
    createProjectButton.disabled = false;
  }
});

designExperimentButton.addEventListener("click", async () => {
  designExperimentButton.disabled = true;
  message.textContent = "模型正在根据研究问题和 OpenFOAM 能力设计实验…";
  try {
    const design = await requestJson("/api/experiment-designs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: document.querySelector("#question").value }),
    });
    applyExperimentDesign(design);
    message.textContent = "模型实验设计已写入参数表；仍需人工检查并完成 Gate 2 审批。";
  } catch (error) {
    message.textContent = `模型设计不可用：${error.message}`;
  } finally {
    designExperimentButton.disabled = false;
  }
});

validateCustomCaseButton.addEventListener("click", async () => {
  const file = customCaseFile.files[0];
  const output = document.querySelector("#custom-case-result");
  if (!file) {
    output.textContent = "请先选择 tar.gz Case 包；尚未提交。";
    return;
  }
  validateCustomCaseButton.disabled = true;
  submitCustomCaseButton.disabled = true;
  output.textContent = "正在检查路径、链接、动态代码、求解器和 Case 结构…";
  try {
    const response = await fetch("/api/custom-cases/validate", {
      method: "POST",
      headers: { "Content-Type": "application/gzip" },
      body: file,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `API returned ${response.status}`);
    output.textContent =
      `校验通过但尚未提交 · solver=${payload.solver} · `
      + `needsBlockMesh=${payload.needs_block_mesh} · `
      + `needsMirrorMesh=${payload.needs_mirror_mesh} · ${payload.archive_sha256}`;
    submitCustomCaseButton.disabled = !selectedTarget;
  } catch (error) {
    output.textContent = `校验拒绝：${error.message}；尚未提交。`;
  } finally {
    validateCustomCaseButton.disabled = false;
  }
});

submitCustomCaseButton.addEventListener("click", async () => {
  const file = customCaseFile.files[0];
  const output = document.querySelector("#custom-case-result");
  if (!file || !selectedTarget) return;
  submitCustomCaseButton.disabled = true;
  output.textContent = "正在安全上传并提交到工作站…";
  try {
    const params = new URLSearchParams({
      target_id: selectedTarget,
      experiment_name: document.querySelector("#custom-experiment-name").value,
    });
    const job = await requestJson(`${customSubmitEndpoint}?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/gzip" },
      body: file,
    });
    document.querySelector("#job-id").textContent = job.job_id;
    document.querySelector("#job-state").textContent = job.state.toUpperCase();
    document.querySelector("#job-progress").style.width = "18%";
    output.textContent = `作业 ${job.job_id} 已提交，正在工作站运行。`;
    pollCustomCase(job.job_id, selectedTarget);
  } catch (error) {
    output.textContent = `提交失败：${error.message}`;
    submitCustomCaseButton.disabled = false;
  }
});

configureModelButton.addEventListener("click", async () => {
  const keyInput = document.querySelector("#openai-api-key");
  const state = document.querySelector("#model-config-state");
  if (!keyInput.value.trim()) {
    state.textContent = "请输入 API Key；密钥只会发送到本机服务的内存。";
    return;
  }
  configureModelButton.disabled = true;
  state.textContent = "正在建立模型连接…";
  try {
    const configured = await requestJson("/api/settings/openai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: keyInput.value,
        planner_model: document.querySelector("#openai-planner-model").value,
        extractor_model: "gpt-5.4-mini",
      }),
    });
    keyInput.value = "";
    state.textContent = `已连接 ${configured.planner_model}；现在可点击“AI 设计实验”。`;
  } catch (error) {
    state.textContent = `模型配置失败：${error.message}`;
  } finally {
    configureModelButton.disabled = false;
  }
});

viewPostprocessButton.addEventListener("click", () => {
  if (latestBenchmarkResults) renderPostprocessResults(latestBenchmarkResults);
  else renderCustomPostprocessResults(latestCustomCollection);
});

approveButton.addEventListener("click", async () => {
  if (!currentProject) return;
  const gate = gateForState(currentProject.workflow_state);
  try {
    const project = await requestJson(`/api/projects/${currentProject.project_id}/approvals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gate,
        decision: "approve",
        actor: "researcher",
        subject_version: currentProject.version,
      }),
    });
    renderProject(project);
  } catch (error) {
    message.textContent = `审批失败：${error.message}`;
  }
});

rejectButton.addEventListener("click", async () => {
  if (!currentProject) return;
  const gate = gateForState(currentProject.workflow_state);
  try {
    const project = await requestJson(`/api/projects/${currentProject.project_id}/approvals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gate,
        decision: "reject",
        actor: "researcher",
        subject_version: currentProject.version,
        reason: "Researcher requested revision from the workbench.",
      }),
    });
    renderProject(project);
  } catch (error) {
    message.textContent = `驳回失败：${error.message}`;
  }
});

advanceButton.addEventListener("click", async () => {
  if (!currentProject) return;
  const action = actionForState(currentProject.workflow_state);
  try {
    const project = await requestJson(`/api/projects/${currentProject.project_id}/actions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, actor: "researcher" }),
    });
    renderProject(project);
  } catch (error) {
    message.textContent = `工作流操作失败：${error.message}`;
  }
});

executionTarget.addEventListener("change", () => {
  selectedTarget = executionTarget.value;
  if (selectedTarget) localStorage.setItem(targetStorageKey, selectedTarget);
  refreshBenchmarkControls();
});

benchmarkForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentProject || !selectedTarget || !gateTwoApproved()) return;
  submitBenchmarkButton.disabled = true;
  message.textContent = "正在提交类型化圆管算例到工作站…";
  if (pollTimer) window.clearTimeout(pollTimer);
  try {
    const submission = await requestJson(
      `/api/projects/${currentProject.project_id}/benchmarks`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_id: selectedTarget,
          case_id: benchmarkCaseId,
          experiment_name: document.querySelector("#experiment-name").value,
          case: pipeCasePayload(),
          actor: "researcher",
        }),
      },
    );
    currentProject = submission.project;
    localStorage.setItem(projectStorageKey, currentProject.project_id);
    localStorage.setItem(targetStorageKey, selectedTarget);
    document.querySelector("#project-status").textContent = currentProject.workflow_state;
    document.querySelector("#job-state").textContent = submission.job.state.toUpperCase();
    document.querySelector("#job-id").textContent = submission.job.job_id;
    document.querySelector("#job-progress").style.width = "18%";
    benchmarkForm.hidden = true;
    message.textContent = `作业 ${submission.job.job_id} 已提交，正在等待 OpenFOAM。`;
    pollBenchmark(currentProject.project_id, selectedTarget);
  } catch (error) {
    message.textContent = `提交失败：${error.message}`;
    submitBenchmarkButton.disabled = false;
  }
});

bootstrap();
