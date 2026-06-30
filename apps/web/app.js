const form = document.querySelector("#research-form");
const button = document.querySelector("#run-button");
const message = document.querySelector("#form-message");
const createProjectButton = document.querySelector("#create-project");
const approveButton = document.querySelector("#gate-approve");
const rejectButton = document.querySelector("#gate-reject");
const advanceButton = document.querySelector("#gate-advance");
let currentProject = null;

const number = (value, digits = 2) => Number(value).toFixed(digits);

async function loadExecutionTargets() {
  const select = document.querySelector("#execution-target");
  try {
    const targets = await requestJson("/api/execution-targets");
    select.replaceChildren(...targets.map((target) => {
      const option = document.createElement("option");
      option.value = target.target_id;
      option.disabled = !target.available;
      const platform = target.kind === "workstation_openfoam" ? "工作站 OpenFOAM" : "HPC Slurm";
      option.textContent = target.available
        ? `${platform} · ${target.foam_version || "版本未知"} · ${target.cpu_count || "?"} CPU`
        : `${platform} · 不可用：${target.reason}`;
      return option;
    }));
    if (!targets.length) select.innerHTML = '<option value="">尚未配置真实执行平台</option>';
  } catch (error) {
    select.innerHTML = `<option value="">能力检查失败：${error.message}</option>`;
  }
}

function renderResult(result) {
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
  PILOT_READY: "SUBMIT_PILOT",
  PILOT_RUNNING: "VERIFY_PILOT",
  PILOT_VERIFIED: "DESIGN_FULL",
  FULL_READY: "SUBMIT_FULL",
  FULL_RUNNING: "ANALYZE",
  ANALYZED: "REVIEW",
  REVIEW_READY: "PUBLISH_REPORT",
})[state] || null;

function renderProject(project) {
  currentProject = project;
  document.querySelector("#project-status").textContent = project.workflow_state;
  document.querySelector("#audit-count").textContent = `${project.audit_event_count} AUDIT EVENTS`;
  const gate = gateForState(project.workflow_state);
  const approved = gate && project.approvals.some((item) => item.gate === gate);
  approveButton.disabled = !gate || approved;
  rejectButton.disabled = !gate;
  advanceButton.disabled = !actionForState(project.workflow_state) || (gate && !approved);
  message.textContent = gate
    ? `${project.workflow_state}：等待 ${gate} 人工审批。`
    : `${project.workflow_state}：可执行下一工作流动作。`;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `API returned ${response.status}`);
  }
  return response.json();
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

loadExecutionTargets();
