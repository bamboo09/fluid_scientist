import {
  buildPlanRequest,
  canStartExperiment,
  restoredPlanForProject,
  shouldCreateFreshProject,
  storageKeys,
  taskView,
} from "./workbench-state.js";
import {
  elapsedLabel,
  operationAnnouncement,
  operationView,
  planningComposerView,
} from "./operation-state.js";
import {
  cancelPlanningBeforeReset,
  OperationPoller,
  createResultLoader,
} from "./operation-lifecycle.js";

const $ = (selector) => document.querySelector(selector);
const byId = (id) => document.getElementById(id);
const stream = byId("conversation-stream");
const promptInput = byId("experiment-prompt") || byId("question");
const designButton = byId("design-experiment");
const targetSelect = byId("execution-target");
const modelProvider = byId("model-provider");
const modelId = byId("model-id");
const modelApiKey = byId("model-api-key");
const welcomeMessage = byId("welcome-message");
const researchQuestionCard = byId("research-question-card");
const researchQuestionText = byId("research-question-text");
const researchForm = byId("research-form");
const startNewExperiment = byId("start-new-experiment");
const operationCard = byId("active-operation");
const operationAnnouncementNode = byId("operation-announcement");

let modelConfiguration = { configured: false, provider: null, model: null };
let executionTargets = [];
let selectedTarget = localStorage.getItem(storageKeys.targetId) || "";
let currentProject = null;
let currentPlan = null;
let currentCompilation = null;
let activeTask = null;
let latestResults = null;
let validatedCustomCase = null;
let pollTimer = null;
let pollDelay = 1500;
let confirmationActive = false;
let activeOperation = null;
let activeOperationId = localStorage.getItem(storageKeys.operationId) || "";
let operationElapsedTimer = null;
let operationRequestActive = false;
let operationPoller = null;
let lastOperationAnnouncement = "";
const renderedPlanRefs = new Set();
const planRequests = new Map();

const modelDefaults = Object.freeze({
  openai: "gpt-5.4",
  glm: "glm-4.5",
  deepseek: "deepseek-chat",
});

function text(value, fallback = "—") {
  if (value === undefined || value === null || value === "") return fallback;
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function safeDetail(payload, status) {
  const detail = payload?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || text(item)).join("；");
  }
  return `API 返回 ${status}`;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(safeDetail(payload, response.status));
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function persist(key, value) {
  if (!value) {
    localStorage.removeItem(key);
    return;
  }
  if (key === storageKeys.projectId) localStorage.setItem(storageKeys.projectId, value);
  else if (key === storageKeys.planId) localStorage.setItem(storageKeys.planId, value);
  else if (key === storageKeys.caseId) localStorage.setItem(storageKeys.caseId, value);
  else if (key === storageKeys.targetId) localStorage.setItem(storageKeys.targetId, value);
  else if (key === storageKeys.operationId) localStorage.setItem(storageKeys.operationId, value);
}

function setStatus(message) {
  const node = researchQuestionCard && !researchQuestionCard.hidden
    ? byId("session-status")
    : byId("composer-status") || byId("form-message") || byId("system-state");
  if (node) node.textContent = message;
}

function showResearchQuestion(question) {
  if (welcomeMessage) welcomeMessage.hidden = true;
  if (researchQuestionText) researchQuestionText.textContent = question;
  if (researchQuestionCard) researchQuestionCard.hidden = false;
  if (researchForm) researchForm.hidden = true;
  if (startNewExperiment) startNewExperiment.hidden = false;
  if (promptInput) promptInput.value = "";
}

function restoreResearchComposer(question) {
  if (researchQuestionCard) researchQuestionCard.hidden = true;
  if (researchQuestionText) researchQuestionText.textContent = "";
  if (researchForm) researchForm.hidden = false;
  if (startNewExperiment) startNewExperiment.hidden = true;
  if (promptInput) promptInput.value = question;
  refreshComposer();
}

function clearResearchSession() {
  stopOperationPolling();
  window.clearTimeout(pollTimer);
  pollTimer = null;
  currentProject = null;
  currentPlan = null;
  currentCompilation = null;
  activeTask = null;
  latestResults = null;
  activeOperation = null;
  activeOperationId = "";
  for (const key of [storageKeys.projectId, storageKeys.planId, storageKeys.caseId, storageKeys.operationId]) {
    localStorage.removeItem(key);
  }
  if (operationCard) {
    operationCard.hidden = true;
    operationCard.setAttribute("aria-busy", "false");
  }
  lastOperationAnnouncement = "";
  if (operationAnnouncementNode) operationAnnouncementNode.textContent = "";
  for (const id of ["active-plan-card", "active-task-card"]) byId(id)?.remove();
  if (byId("report")) byId("report").hidden = true;
  if (researchQuestionCard) researchQuestionCard.hidden = true;
  if (welcomeMessage) welcomeMessage.hidden = false;
  if (researchForm) researchForm.hidden = false;
  if (startNewExperiment) startNewExperiment.hidden = true;
  if (promptInput) promptInput.value = "";
  setStatus("");
  updateContext();
  refreshComposer();
  promptInput?.focus();
}

async function resetResearchSession() {
  if (!canStartExperiment(activeTask) || operationRequestActive) return;
  const operationId = activeOperationId;
  const operationActive = Boolean(operationId) && !operationView(activeOperation || {}).terminal;
  await cancelPlanningBeforeReset({
    operationId,
    operationActive,
    cancelOperation: async (id) => {
      stopOperationPolling();
      return requestJson(`/api/operations/${id}`, { method: "DELETE" });
    },
    resumePolling: startOperationPolling,
    clearSession: clearResearchSession,
    setRequestActive: (active) => {
      operationRequestActive = active;
      if (activeOperation) renderOperation(activeOperation);
    },
    setActionDisabled: (disabled) => {
      if (startNewExperiment) startNewExperiment.disabled = disabled;
    },
    onError: (error) => renderError("取消实验设计", error),
  });
}

function makeCard(className, title) {
  const card = document.createElement("article");
  card.className = className;
  const heading = document.createElement("h2");
  heading.textContent = title;
  card.append(heading);
  return card;
}

function appendConversation(role, content, className = "conversation-message") {
  if (!stream) {
    setStatus(content);
    return null;
  }
  const card = makeCard(`${className} ${role}`, role === "user" ? "研究者" : "Fluid Scientist");
  const body = document.createElement("p");
  body.textContent = content;
  card.append(body);
  stream.append(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return card;
}

function renderError(operation, error, task = null) {
  const detail = error?.message || String(error);
  if (task) {
    renderTaskCard({ ...task, phase: "failed", error: `${operation}：${detail}` });
    return;
  }
  if (!stream) {
    setStatus(`${operation}失败：${detail}`);
    return;
  }
  const card = makeCard("error-card", `${operation}失败`);
  const body = document.createElement("p");
  body.textContent = detail;
  card.append(body);
  stream.append(card);
}

function updateContext() {
  const modelLabel = modelConfiguration.configured
    ? `${modelConfiguration.provider} / ${modelConfiguration.model}`
    : "未连接模型";
  for (const id of ["current-model", "model-context", "header-model-status"]) {
    if (byId(id)) byId(id).textContent = modelLabel;
  }
  if (byId("context-model-provider")) byId("context-model-provider").textContent = modelConfiguration.provider || "未连接";
  if (byId("context-model-id")) byId("context-model-id").textContent = modelConfiguration.model || "—";
  const target = executionTargets.find((item) => item.target_id === selectedTarget);
  const targetLabel = target?.label || target?.target_id || selectedTarget || "未选择平台";
  for (const id of ["current-target", "target-context", "header-target-status"]) {
    if (byId(id)) byId(id).textContent = targetLabel;
  }
  const capability = byId("target-capability-state");
  if (capability) capability.textContent = target ? (targetAvailable(target) ? "平台能力可用" : "平台当前不可用") : "尚未选择平台";
  for (const id of ["current-project", "project-context", "project-status"]) {
    if (byId(id)) byId(id).textContent = currentProject?.project_id || "尚未创建";
  }
  const connection = byId("system-state");
  if (connection) {
    const targetReady = !selectedTarget || Boolean(target && targetAvailable(target));
    connection.textContent = targetReady ? "服务已连接" : "服务已连接，执行平台不可用";
    connection.dataset.state = targetReady ? "connected" : "warning";
  }
  const taskNode = byId("task-context");
  if (taskNode && activeTask) {
    const view = taskView(activeTask);
    taskNode.dataset.phase = activeTask.phase;
    const label = taskNode.querySelector("[data-task-label]");
    if (label) label.textContent = view.label;
  }
}

function refreshComposer() {
  if (!designButton || !promptInput) return;
  const reason = byId("composer-prerequisite") || byId("composer-hint");
  const view = planningComposerView({
    empty: !promptInput.value.trim(),
    modelConfigured: modelConfiguration.configured,
    targetSelected: Boolean(selectedTarget),
    requestActive: operationRequestActive,
    operation: activeOperationId ? activeOperation : null,
  });
  designButton.disabled = view.disabled;
  if (reason) reason.textContent = view.hint;
}

async function loadModelConfiguration() {
  modelConfiguration = await requestJson("/api/model-configurations");
  if (modelProvider && modelConfiguration.provider) modelProvider.value = modelConfiguration.provider;
  if (modelId && modelConfiguration.model) modelId.value = modelConfiguration.model;
  updateContext();
  refreshComposer();
  return modelConfiguration;
}

function targetAvailable(target) {
  if (typeof target.available === "boolean") return target.available;
  if (typeof target.ready === "boolean") return target.ready;
  return target.status !== "unavailable" && target.reachable !== false;
}

async function loadExecutionTargets() {
  executionTargets = await requestJson("/api/execution-targets");
  if (targetSelect) {
    targetSelect.replaceChildren();
    const placeholder = new Option("选择工作站或 HPC 平台", "");
    targetSelect.add(placeholder);
    for (const target of executionTargets) {
      const label = target.label || target.target_id;
      const option = new Option(`${label}${targetAvailable(target) ? "" : "（不可用）"}`, target.target_id);
      option.disabled = !targetAvailable(target);
      targetSelect.add(option);
    }
    if (executionTargets.some((item) => item.target_id === selectedTarget)) {
      targetSelect.value = selectedTarget;
    } else {
      selectedTarget = executionTargets.find(targetAvailable)?.target_id || "";
      targetSelect.value = selectedTarget;
      persist(storageKeys.targetId, selectedTarget);
    }
  }
  updateContext();
  refreshComposer();
  return executionTargets;
}

function summaryEntries(value) {
  if (!value || typeof value !== "object") return [];
  return Object.entries(value).filter(([, item]) => item !== undefined && item !== null);
}

function addDefinitionList(parent, values) {
  const list = document.createElement("dl");
  for (const [key, value] of summaryEntries(values)) {
    const row = document.createElement("div");
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = key.replaceAll("_", " ");
    description.textContent = Array.isArray(value) ? value.join("，") : text(value);
    row.append(term, description);
    list.append(row);
  }
  parent.append(list);
}

function addList(parent, title, values) {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = title;
  const list = document.createElement("ul");
  for (const value of values || []) {
    const item = document.createElement("li");
    item.textContent = text(value);
    list.append(item);
  }
  section.append(heading, list);
  parent.append(section);
}

function renderPlanCard(response) {
  const plan = response.plan;
  const existing = byId("active-plan-card");
  if (existing) existing.remove();
  const card = makeCard("work-card plan-card", plan.experiment_name || "实验计划");
  card.id = "active-plan-card";
  renderedPlanRefs.add(response.plan_id);
  const badge = document.createElement("p");
  badge.className = "plan-type";
  badge.textContent = `${plan.experiment_type} · ${response.provider} / ${response.model}`;
  const objective = document.createElement("p");
  objective.className = "plan-objective";
  objective.textContent = plan.objective;
  card.append(badge, objective);

  const essentials = document.createElement("section");
  essentials.className = "plan-essentials";
  addDefinitionList(essentials, {
    ...plan.case,
    convergence: plan.convergence_targets,
  });
  card.append(essentials);
  addList(card, "请求输出", plan.requested_outputs);
  addList(card, "假设", plan.assumptions);
  addList(card, "风险与局限", plan.limitations);

  const details = document.createElement("details");
  const summary = document.createElement("summary");
  summary.textContent = "查看全部参数";
  const raw = document.createElement("pre");
  raw.textContent = JSON.stringify(plan, null, 2);
  details.append(summary, raw);
  card.append(details);

  const preview = document.createElement("output");
  preview.className = "compile-preview";
  preview.dataset.compilePreview = "";
  preview.textContent = "确认后将由确定性编译器生成并绑定归档摘要。";
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = "button button-primary";
  if (plan.experiment_type === "custom_openfoam") {
    confirm.textContent = "上传并审核算例归档";
    preview.textContent = "模型生成的是实验计划，不能直接作为可执行算例。自定义 OpenFOAM 实验需要一个已准备好的 OpenFOAM Case 文件夹，并将它压缩为 .tar.gz 后上传审核；模型不会生成或执行任意 OpenFOAM 字典与命令。";
    confirm.addEventListener("click", () => {
      const name = byId("custom-experiment-name");
      if (name) name.value = plan.experiment_name || "Custom OpenFOAM Study";
      const output = byId("custom-case-result");
      if (output) output.textContent = "请选择与当前实验计划对应的算例归档，并先执行安全校验。";
      openDialog("custom-case-drawer");
    });
    card.append(preview, confirm);
    const taskHost = byId("task-card-host");
    if (stream && taskHost) stream.insertBefore(card, taskHost);
    else (stream || byId("experiment-plan-review") || document.body).append(card);
    return;
  }
  confirm.textContent = "确认并提交";
  confirm.addEventListener("click", () => confirmAndSubmitPlan(confirm));
  card.append(preview, confirm);
  const taskHost = byId("task-card-host");
  if (stream && taskHost) stream.insertBefore(card, taskHost);
  else (stream || byId("experiment-plan-review") || document.body).append(card);
}

function requestPlan(planId) {
  if (!planRequests.has(planId)) {
    const request = requestJson(`/api/experiment-plans/${planId}`)
      .catch((error) => {
        planRequests.delete(planId);
        throw error;
      });
    planRequests.set(planId, request);
  }
  return planRequests.get(planId);
}

async function createProject(question) {
  const project = await requestJson("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  currentProject = project;
  persist(storageKeys.projectId, project.project_id);
  updateContext();
  return project;
}

function stopOperationPolling() {
  window.clearInterval(operationElapsedTimer);
  operationElapsedTimer = null;
  operationPoller?.stop();
}

function announceOperation(operation, message) {
  if (!operationAnnouncementNode) return;
  const announcement = operationAnnouncement(operation, message);
  if (announcement === lastOperationAnnouncement) return;
  lastOperationAnnouncement = announcement;
  operationAnnouncementNode.textContent = announcement;
}

function renderOperation(operation, options = {}) {
  if (!operationCard || !operation) return;
  const view = operationView(operation);
  activeOperation = operation;
  activeOperationId = operation.operation_id || activeOperationId;
  operationCard.hidden = false;
  operationCard.dataset.tone = view.tone;
  const pollingPaused = Boolean(operationPoller?.paused);
  operationCard.setAttribute("aria-busy", String(!view.terminal && !pollingPaused));
  byId("operation-status").textContent = view.label;
  byId("operation-stage").textContent = view.stageLabel;
  byId("operation-elapsed").textContent = elapsedLabel(operation);
  const message = options.networkMessage || operation.safe_error || "";
  byId("operation-message").textContent = message;
  announceOperation(operation, message);
  const progress = operationCard.querySelector(".operation-progress");
  progress?.classList.toggle("is-indeterminate", view.indeterminate);
  if (view.indeterminate) progress?.removeAttribute("aria-valuenow");
  else progress?.setAttribute("aria-valuenow", String(view.percent));
  if (byId("operation-progress-bar")) {
    byId("operation-progress-bar").style.width = `${view.percent}%`;
  }
  const cancel = byId("cancel-operation");
  const retry = byId("retry-operation");
  if (cancel) {
    cancel.hidden = !view.canCancel;
    cancel.disabled = operationRequestActive;
  }
  if (retry) {
    retry.hidden = !(view.canRetry || pollingPaused);
    retry.disabled = operationRequestActive;
    retry.textContent = pollingPaused ? "继续查询" : "重新规划";
  }
  window.clearInterval(operationElapsedTimer);
  operationElapsedTimer = null;
  if (!view.terminal) {
    operationElapsedTimer = window.setInterval(() => {
      const elapsed = byId("operation-elapsed");
      if (elapsed && activeOperation) elapsed.textContent = elapsedLabel(activeOperation);
    }, 1000);
  }
  refreshComposer();
}

const loadOperationResult = createResultLoader({
  fetchPlan: requestPlan,
  onPlan: async (response) => {
    if (renderedPlanRefs.has(response.plan_id)) return;
    currentPlan = response;
    currentCompilation = null;
    persist(storageKeys.planId, response.plan_id);
    renderPlanCard(response);
    setStatus("实验计划已生成。请审阅假设、参数和局限后确认。");
  },
});

async function acceptOperationStatus(operation, context = { isCurrent: () => true }) {
  if (!context.isCurrent()) return false;
  renderOperation(operation);
  if (!operationView(operation).terminal) return false;
  if (operation.state === "succeeded") {
    await loadOperationResult(operation.result_ref, { shouldApply: context.isCurrent });
  } else {
    const fallback = operation.state === "cancelled"
      ? "实验设计已取消。"
      : "实验设计未完成，可安全重试。";
    setStatus(operation.safe_error || fallback);
  }
  return true;
}

function clearMissingOperation() {
  stopOperationPolling();
  activeOperation = null;
  activeOperationId = "";
  localStorage.removeItem(storageKeys.operationId);
  if (operationCard) operationCard.hidden = true;
  refreshComposer();
}

function startOperationPolling(operationId, options = {}) {
  activeOperationId = operationId;
  if (!operationPoller) {
    operationPoller = new OperationPoller({
      fetchOperation: (id, requestOptions) => requestJson(`/api/operations/${id}`, requestOptions),
      onStatus: acceptOperationStatus,
      onMissing: () => {
        clearMissingOperation();
        setStatus("上次实验设计记录已过期，可以重新开始。");
      },
      onNetwork: ({ failures, paused }) => {
        if (activeOperation) {
          renderOperation(activeOperation, {
            networkMessage: paused
              ? "状态查询已暂停，网络恢复后可继续查询，不会重复提交模型请求。"
              : `状态查询暂时失败，正在自动重试（${failures}/5）。`,
          });
        }
      },
      setTimeout: window.setTimeout.bind(window),
      clearTimeout: window.clearTimeout.bind(window),
    });
  }
  return operationPoller.start(operationId, options);
}

async function submitPlanOperation(question) {
  if (operationRequestActive) return;
  if (!selectedTarget) {
    restoreResearchComposer(question);
    setStatus("请先选择执行平台；规划不会检查平台是否在线。");
    return;
  }
  operationRequestActive = true;
  designButton.disabled = true;
  setStatus("正在建立实验设计任务…");
  try {
    if (shouldCreateFreshProject(currentProject, activeTask)) {
      stopOperationPolling();
      currentProject = null;
      currentPlan = null;
      currentCompilation = null;
      activeTask = null;
      latestResults = null;
      activeOperation = null;
      activeOperationId = "";
      localStorage.removeItem(storageKeys.projectId);
      localStorage.removeItem(storageKeys.planId);
      localStorage.removeItem(storageKeys.caseId);
      localStorage.removeItem(storageKeys.operationId);
    }
    if (!currentProject) await createProject(question);
    const operation = await requestJson("/api/plan-operations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        buildPlanRequest(question, currentProject.project_id, selectedTarget),
      ),
    });
    activeOperation = operation;
    activeOperationId = operation.operation_id;
    persist(storageKeys.operationId, operation.operation_id);
    renderOperation(operation);
    startOperationPolling(operation.operation_id, { immediate: false });
  } catch (error) {
    renderError("实验设计", error);
    restoreResearchComposer(question);
  } finally {
    operationRequestActive = false;
    if (activeOperation) renderOperation(activeOperation);
    refreshComposer();
  }
}

async function designExperimentFromPrompt(event) {
  event?.preventDefault();
  if (!canStartExperiment(activeTask)) {
    const warning = "已有实验正在运行，请等待当前任务结束后再开始新实验。";
    setStatus(warning);
    appendConversation("assistant", warning, "workflow-event");
    return;
  }
  const question = promptInput?.value.trim() || "";
  if (!question || !modelConfiguration.configured) return;
  showResearchQuestion(question);
  await submitPlanOperation(question);
}

function renderTaskCard(task) {
  const view = taskView(task);
  activeTask = task;
  let card = byId("active-task-card");
  if (!card) {
    card = makeCard("work-card task-card", "远程实验任务");
    card.id = "active-task-card";
    (byId("task-card-host") || stream || byId("jobs") || document.body).append(card);
  }
  card.dataset.phase = task.phase;
  card.dataset.tone = view.tone;
  let body = card.querySelector(".task-card-body");
  if (!body) {
    body = document.createElement("div");
    body.className = "task-card-body";
    card.append(body);
  }
  body.replaceChildren();
  const state = document.createElement("strong");
  state.textContent = view.label;
  const identity = document.createElement("p");
  identity.textContent = task.jobId
    ? `Job ID：${task.jobId}${task.pid !== undefined && task.pid !== null ? ` · PID：${task.pid}` : ""}`
    : "尚未分配 Job ID";
  const detail = document.createElement("p");
  detail.textContent = view.detail;
  const warning = document.createElement("p");
  warning.className = "task-warning";
  warning.textContent = task.warning || "";
  warning.hidden = !task.warning;
  const meta = document.createElement("small");
  meta.textContent = [
    task.targetLabel || task.targetId,
    task.submittedAt ? `提交时间：${task.submittedAt}` : null,
    task.lastUpdated ? `最后更新：${task.lastUpdated}` : null,
  ].filter(Boolean).join(" · ");
  body.append(state, identity, detail, warning, meta);
  if (byId("job-state")) byId("job-state").textContent = view.label;
  if (byId("job-id")) byId("job-id").textContent = task.jobId || "尚未提交";
  if (byId("job-progress")) byId("job-progress").style.width = `${view.percent}%`;
  if (byId("last-task-update")) byId("last-task-update").textContent = task.lastUpdated || "刚刚";
  updateContext();
  return card;
}

async function approveGate(project, gate, binding = {}) {
  return requestJson(`/api/projects/${project.project_id}/approvals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      gate,
      decision: "approve",
      actor: "researcher",
      subject_version: project.version,
      ...binding,
    }),
  });
}

async function applyWorkflowAction(project, action) {
  return requestJson(`/api/projects/${project.project_id}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, actor: "researcher" }),
  });
}

async function prepareProjectForGateTwo() {
  if (currentProject.workflow_state === "SPEC_READY") {
    if (!currentProject.approvals?.some((item) => item.gate === "GATE_1")) {
      currentProject = await approveGate(currentProject, "GATE_1");
      appendConversation("assistant", "Gate 1 已批准研究规格。", "workflow-event");
    }
    currentProject = await applyWorkflowAction(currentProject, "RETRIEVE_EVIDENCE");
    appendConversation("assistant", "已完成证据检索阶段。", "workflow-event");
  }
  if (currentProject.workflow_state === "EVIDENCE_READY") {
    currentProject = await applyWorkflowAction(currentProject, "DESIGN_PILOT");
    appendConversation("assistant", "Pilot 设计阶段已就绪。", "workflow-event");
  }
  if (!["PILOT_READY", "PILOT_RUNNING"].includes(currentProject.workflow_state)) {
    throw new Error(`项目状态 ${currentProject.workflow_state} 无法提交新的 Pilot`);
  }
}

function deterministicCaseId() {
  const sanitize = (value, fallback) => (
    String(value ?? fallback)
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || fallback
  );
  const planId = sanitize(currentPlan.plan_id, "plan");
  const planVersion = sanitize(currentPlan.plan_version, "0");
  const identity = `${currentPlan.plan_id}\u0000${currentPlan.plan_version}`;
  let hash = 2166136261;
  for (let index = 0; index < identity.length; index += 1) {
    hash ^= identity.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const suffix = (hash >>> 0).toString(36).padStart(7, "0");
  return `planned-${planId.slice(0, 30)}-v${planVersion.slice(0, 12)}-${suffix}`.slice(0, 64);
}

function submissionJob(response) {
  return response?.job || response || {};
}

async function confirmAndSubmitPlan(button) {
  if (!canStartExperiment(activeTask)) {
    const warning = "已有实验正在运行，请等待当前任务结束后再确认提交。";
    setStatus(warning);
    appendConversation("assistant", warning, "workflow-event");
    return;
  }
  if (confirmationActive || !currentProject || !currentPlan || !selectedTarget) {
    if (!selectedTarget) renderError("提交", new Error("请先选择可用的执行平台"));
    return;
  }
  if (currentPlan.plan.experiment_type === "custom_openfoam") {
    setStatus("自定义 OpenFOAM 计划必须上传并审核算例归档，不能使用内置编译器直接提交。");
    openDialog("custom-case-drawer");
    return;
  }
  confirmationActive = true;
  if (button) button.disabled = true;
  renderTaskCard({ phase: "preparing", targetId: selectedTarget, lastUpdated: new Date().toLocaleString() });
  try {
    await prepareProjectForGateTwo();
    currentCompilation = await requestJson(`/api/experiment-plans/${currentPlan.plan_id}/compile`, {
      method: "POST",
    });
    const preview = $("[data-compile-preview]");
    if (preview) {
      preview.textContent = `SHA-256：${currentCompilation.archive_sha256}\n求解器：${currentCompilation.manifest.solver}\n预处理：${currentCompilation.preprocessing.join(" → ")}`;
    }
    appendConversation("assistant", `确定性编译与安全校验完成：${currentCompilation.archive_sha256}`, "workflow-event");

    const approvedArtifact = currentProject.approved_artifacts?.[currentPlan.plan_id];
    if (approvedArtifact) {
      if (approvedArtifact.archive_sha256 !== currentCompilation.archive_sha256) {
        throw new Error("重新编译的归档摘要与 Gate 2 已批准摘要不一致，已停止提交。 ");
      }
      if (approvedArtifact.plan_version !== undefined
        && approvedArtifact.plan_version !== currentPlan.plan_version) {
        throw new Error("当前计划版本与 Gate 2 已批准版本不一致，已停止提交。 ");
      }
      appendConversation("assistant", "已核对现有 Gate 2 绑定，摘要一致，无需重复审批。", "workflow-event");
    } else {
      currentProject = await requestJson(`/api/projects/${currentProject.project_id}/approvals`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gate: "GATE_2",
          decision: "approve",
          actor: "researcher",
          subject_version: currentProject.version,
          plan_id: currentPlan.plan_id,
          plan_version: currentPlan.plan_version,
          archive_sha256: currentCompilation.archive_sha256,
        }),
      });
      appendConversation("assistant", "Gate 2 已绑定当前计划版本与归档摘要。", "workflow-event");
    }

    const caseId = deterministicCaseId();
    persist(storageKeys.caseId, caseId);
    persist(storageKeys.targetId, selectedTarget);
    renderTaskCard({
      phase: "submitting",
      targetId: selectedTarget,
      lastUpdated: new Date().toLocaleString(),
    });
    const response = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-plans/${currentPlan.plan_id}/submit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_id: selectedTarget,
          case_id: caseId,
          actor: "researcher",
          archive_sha256: currentCompilation.archive_sha256,
        }),
      },
    );
    currentProject = response.project || currentProject;
    const job = submissionJob(response);
    const externalJobId = response.external_job_id ?? response.job_id ?? job.external_job_id ?? job.job_id;
    if (!externalJobId) {
      throw new Error("提交响应缺少外部 Job ID；尚不能确认任务已提交。请通过项目恢复检查远程状态。");
    }
    renderTaskCard({
      phase: "submitted",
      jobId: externalJobId,
      pid: job.pid,
      targetId: selectedTarget,
      submittedAt: job.submitted_at,
      lastUpdated: new Date().toLocaleString(),
    });
    startPolling(() => pollPlannedExperiment(
      externalJobId,
      selectedTarget,
      currentProject.project_id,
      currentPlan.plan_id,
      caseId,
    ));
  } catch (error) {
    const assignedJobId = activeTask?.jobId;
    renderError("确认与提交", error, {
      phase: "failed",
      jobId: assignedJobId,
      targetId: selectedTarget,
      lastUpdated: new Date().toLocaleString(),
    });
  } finally {
    confirmationActive = false;
    if (button && !activeTask?.jobId) button.disabled = false;
  }
}

function phaseFromJob(job) {
  if (job.state === "failed") return "failed";
  if (job.state === "cancelled") return "cancelled";
  if (job.state === "succeeded") return "collecting";
  const stage = String(job.stage || job.status || "").toLowerCase();
  if (stage.includes("mesh")) return "mesh_check";
  if (job.state === "running" || stage.includes("solv")) return "solving";
  return "submitted";
}

function schedulePoll(callback) {
  window.clearTimeout(pollTimer);
  pollTimer = window.setTimeout(callback, pollDelay);
  pollDelay = Math.min(Math.round(pollDelay * 1.35), 10000);
}

function startPolling(callback) {
  window.clearTimeout(pollTimer);
  pollDelay = 1500;
  return callback();
}

function formatObject(value) {
  return Object.entries(value || {}).map(([key, item]) => `${key}: ${text(item)}`).join("；") || "无";
}

function renderPostprocessResults(results) {
  const root = byId("postprocess-results") || document.createElement("section");
  root.id = "postprocess-results";
  root.hidden = false;
  root.replaceChildren();
  const collection = results.collection || results;
  const mesh = document.createElement("p");
  mesh.textContent = [
    `网格：${collection.mesh?.passed ? "通过" : "未通过"}`,
    `单元数 ${text(collection.mesh?.cells)}`,
    `最大长宽比 ${text(collection.mesh?.max_aspect_ratio)}`,
    `最大非正交度 ${text(collection.mesh?.max_non_orthogonality)}`,
    `平均非正交度 ${text(collection.mesh?.average_non_orthogonality)}`,
    `最大偏斜度 ${text(collection.mesh?.max_skewness)}`,
  ].join("；");
  const solver = document.createElement("p");
  solver.textContent = `求解器完成标记：${collection.solver?.completed ? "已完成" : "未完成"}`;
  const conservation = document.createElement("p");
  conservation.textContent = [
    `全局连续性误差 ${text(collection.solver?.global_continuity_error)}`,
    `累计连续性误差 ${text(collection.solver?.cumulative_continuity_error)}`,
    `入口质量流量 ${text(collection.solver?.inlet_mass_flow)}`,
    `出口质量流量 ${text(collection.solver?.outlet_mass_flow)}`,
    `压降 ${text(collection.solver?.pressure_drop_pa)} Pa`,
  ].join("；");
  const residuals = document.createElement("p");
  residuals.textContent = `残差：${formatObject(collection.solver?.final_residuals)}`;
  const numeric_times = collection.numeric_times || collection.post_processing?.time_directories || [];
  const times = document.createElement("p");
  times.textContent = `数值时间：${numeric_times.join("，") || "未提供"}`;
  const observables = document.createElement("p");
  observables.textContent = `观测量：${formatObject(collection.observables)}`;
  const paraviewFile = document.createElement("p");
  paraviewFile.textContent = `ParaView 标记：${collection.post_processing?.paraview_file || "未提供 .foam 文件"}`;
  const advanced = document.createElement("details");
  const advancedTitle = document.createElement("summary");
  advancedTitle.textContent = "工作站 ParaView 指引";
  const advancedBody = document.createElement("p");
  advancedBody.textContent = collection.post_processing?.paraview_file
    ? "请在可信工作站环境中打开该 .foam 标记；浏览器显示的是结构化采集结果。"
    : "本次采集未返回 ParaView 标记。";
  advanced.append(advancedTitle, advancedBody);
  root.append(mesh, solver, conservation, residuals, times, observables, paraviewFile, advanced);
  if (!root.isConnected) (stream || document.body).append(root);
}

function renderResultsCard(results) {
  latestResults = results;
  renderTaskCard({
    ...activeTask,
    phase: "completed",
    lastUpdated: new Date().toLocaleString(),
  });
  let card = byId("deterministic-results-card");
  if (!card) {
    card = makeCard("work-card result-card", "确定性结果");
    card.id = "deterministic-results-card";
    (stream || document.body).append(card);
  }
  card.querySelectorAll(":scope > :not(h2)").forEach((node) => node.remove());
  const summary = results.summary || {};
  const body = document.createElement("p");
  body.textContent = `网格 ${summary.mesh_passed ? "通过" : "未通过"} · 求解 ${summary.solver_completed ? "完成" : "未完成"} · ${text(summary.cells)} 个单元`;
  const postButton = document.createElement("button");
  postButton.type = "button";
  postButton.className = "button button-secondary";
  postButton.textContent = "查看浏览器后处理";
  postButton.addEventListener("click", () => renderPostprocessResults(results));
  const analyzeButton = document.createElement("button");
  analyzeButton.type = "button";
  analyzeButton.className = "button button-primary";
  analyzeButton.textContent = "模型分析结果";
  analyzeButton.addEventListener("click", analyzeExperimentResults);
  card.append(body, postButton, analyzeButton);
  renderPostprocessResults(results);
}

async function pollPlannedExperiment(jobId, targetId, projectId, planId, caseId) {
  const statusUrl = `/api/projects/${projectId}/benchmarks/${caseId}?target_id=${encodeURIComponent(targetId)}`;
  try {
    const job = await requestJson(statusUrl);
    const phase = phaseFromJob(job);
    renderTaskCard({
      phase,
      jobId,
      pid: job.pid,
      error: job.error,
      targetId,
      submittedAt: job.submitted_at,
      lastUpdated: new Date().toLocaleString(),
    });
    if (phase === "failed" || phase === "cancelled") return;
    if (job.state === "succeeded") {
      const query = new URLSearchParams({ target_id: targetId, case_id: caseId });
      const results = await requestJson(
        `/api/projects/${projectId}/experiment-plans/${planId}/results?${query}`,
      );
      currentProject = results.project || currentProject;
      renderResultsCard(results);
      return;
    }
    schedulePoll(() => pollPlannedExperiment(jobId, targetId, projectId, planId, caseId));
  } catch (error) {
    renderTaskCard({
      phase: activeTask?.phase || "submitted",
      jobId,
      targetId,
      pid: activeTask?.pid,
      warning: `状态查询暂时失败：${error.message}。将自动重试。`,
      lastUpdated: new Date().toLocaleString(),
    });
    schedulePoll(() => pollPlannedExperiment(jobId, targetId, projectId, planId, caseId));
  }
}

function renderExperimentAnalysis(result) {
  let card = byId("experiment-analysis-card");
  if (!card) {
    card = makeCard("work-card analysis-card", "模型分析结果（证据绑定）");
    card.id = "experiment-analysis-card";
    (stream || document.body).append(card);
  }
  card.querySelectorAll(":scope > :not(h2)").forEach((node) => node.remove());
  const analysis = result.analysis || {};
  const summary = document.createElement("p");
  summary.textContent = analysis.executive_summary || "未提供摘要";
  const claims = document.createElement("ol");
  for (const claim of analysis.claims || []) {
    const item = document.createElement("li");
    item.textContent = `${claim.text}（证据：${(claim.evidence_keys || []).join("，")}）`;
    claims.append(item);
  }
  card.append(summary, claims);
  addList(card, "可信度判断", analysis.credibility_assessment);
  addList(card, "局限", analysis.limitations);
  addList(card, "建议下一步", analysis.recommended_next_steps);
}

async function analyzeExperimentResults() {
  const projectId = currentProject?.project_id || localStorage.getItem(storageKeys.projectId);
  const planId = currentPlan?.plan_id || localStorage.getItem(storageKeys.planId);
  const caseId = localStorage.getItem(storageKeys.caseId);
  const targetId = selectedTarget || localStorage.getItem(storageKeys.targetId);
  if (!projectId || !planId || !caseId || !targetId || !latestResults) return;
  try {
    const query = new URLSearchParams({ target_id: targetId, case_id: caseId });
    const analysis = await requestJson(
      `/api/projects/${projectId}/experiment-plans/${planId}/analysis?${query}`,
      { method: "POST" },
    );
    renderExperimentAnalysis(analysis);
  } catch (error) {
    renderError("模型结果分析", error);
  }
}

async function cancelActiveOperation() {
  if (!activeOperationId || operationRequestActive) return;
  const operationId = activeOperationId;
  operationRequestActive = true;
  stopOperationPolling();
  if (activeOperation) renderOperation(activeOperation);
  try {
    const operation = await requestJson(`/api/operations/${operationId}`, {
      method: "DELETE",
    });
    persist(storageKeys.operationId, operation.operation_id);
    await acceptOperationStatus(operation);
  } catch (error) {
    renderError("取消实验设计", error);
    startOperationPolling(operationId);
  } finally {
    operationRequestActive = false;
    if (activeOperation) renderOperation(activeOperation);
    refreshComposer();
  }
}

async function retryActiveOperation() {
  if (operationRequestActive) return;
  if (operationPoller?.paused && activeOperationId) {
    operationRequestActive = true;
    if (activeOperation) renderOperation(activeOperation);
    try {
      await operationPoller.resume();
    } finally {
      operationRequestActive = false;
      if (activeOperation) renderOperation(activeOperation);
    }
    return;
  }
  const question = researchQuestionText?.textContent?.trim() || promptInput?.value.trim() || "";
  if (!question) {
    setStatus("无法恢复研究问题，请重新输入后再规划。");
    restoreResearchComposer("");
    return;
  }
  showResearchQuestion(question);
  await submitPlanOperation(question);
}

async function restoreActiveOperation() {
  const operationId = localStorage.getItem(storageKeys.operationId);
  if (!operationId) return;
  activeOperationId = operationId;
  activeOperation = {
    operation_id: operationId,
    state: "queued",
    stage: "queued",
    created_at: new Date().toISOString(),
  };
  renderOperation(activeOperation, {
    networkMessage: "正在恢复上次实验设计状态…",
  });
  refreshComposer();
  void startOperationPolling(operationId);
}

async function restoreActiveExperiment() {
  const projectId = localStorage.getItem(storageKeys.projectId);
  const planId = localStorage.getItem(storageKeys.planId);
  const caseId = localStorage.getItem(storageKeys.caseId);
  const targetId = localStorage.getItem(storageKeys.targetId);
  try {
    if (projectId) {
      currentProject = await requestJson(`/api/projects/${projectId}`);
    } else {
      const response = await fetch("/api/projects/recent");
      if (response.status === 404) return;
      if (!response.ok) throw new Error(`API 返回 ${response.status}`);
      currentProject = await response.json();
      persist(storageKeys.projectId, currentProject.project_id);
    }
    if (currentProject?.question) showResearchQuestion(currentProject.question);
    if (planId) {
      if (currentPlan?.plan_id !== planId) {
        currentPlan = await requestPlan(planId);
      }
      const planOwnerMatches = currentPlan.project_id === currentProject.project_id;
      const restoredPlan = planOwnerMatches
        ? restoredPlanForProject(currentPlan, currentProject)
        : null;
      if (!restoredPlan) {
        currentPlan = null;
        currentCompilation = null;
        localStorage.removeItem(storageKeys.planId);
        localStorage.removeItem(storageKeys.caseId);
        updateContext();
        appendConversation(
          "assistant",
          "已检测到上次实验的过期草稿，已自动清理，不会影响当前实验。",
          "workflow-event",
        );
        return;
      }
      currentPlan = restoredPlan;
      if (!renderedPlanRefs.has(planId)) renderPlanCard(currentPlan);
      const approvedArtifact = currentProject.approved_artifacts?.[planId];
      if (approvedArtifact) {
        currentCompilation = {
          plan_id: planId,
          plan_version: approvedArtifact.plan_version,
          archive_sha256: approvedArtifact.archive_sha256,
        };
        const preview = $("[data-compile-preview]");
        if (preview) {
          preview.textContent = `已恢复 Gate 2 批准绑定：${approvedArtifact.archive_sha256}`;
        }
      }
    }
    if (targetId) {
      selectedTarget = targetId;
      if (targetSelect) targetSelect.value = targetId;
    }
    updateContext();
    const jobId = caseId ? currentProject.external_jobs?.[caseId] : null;
    if (!jobId || !planId || !caseId || !targetId) return;
    appendConversation("assistant", `已恢复远程任务 ${jobId}，不会重复提交。`, "workflow-event");
    await startPolling(() => pollPlannedExperiment(
      jobId,
      targetId,
      currentProject.project_id,
      planId,
      caseId,
    ));
  } catch (error) {
    renderError("恢复实验", error);
  }
}

async function configureModel() {
  const state = byId("model-config-state");
  const apiKey = modelApiKey?.value.trim() || "";
  if (!apiKey) {
    if (state) state.textContent = "请输入 API Key；密钥只发送到本机服务进程内存。";
    return;
  }
  try {
    await requestJson("/api/model-configurations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider: modelProvider.value,
        model: modelId.value.trim(),
        api_key: apiKey,
      }),
    });
    if (state) state.textContent = "模型连接成功。";
    await loadModelConfiguration();
  } catch (error) {
    if (state) state.textContent = `模型配置失败：${error.message}`;
  } finally {
    modelApiKey.value = "";
  }
}

async function validateCustomCase() {
  const file = byId("custom-case-file")?.files?.[0];
  const output = byId("custom-case-result");
  validatedCustomCase = null;
  const submit = byId("submit-custom-case");
  if (submit) submit.disabled = true;
  if (!file) {
    if (output) output.textContent = "请先选择 tar.gz 算例归档；尚未提交。";
    return;
  }
  try {
    validatedCustomCase = await requestJson("/api/custom-cases/validate", {
      method: "POST",
      headers: { "Content-Type": "application/gzip" },
      body: file,
    });
    if (output) output.textContent = `安全校验通过，归档摘要 ${validatedCustomCase.archive_sha256}；尚未提交。`;
    if (submit) submit.disabled = !selectedTarget;
  } catch (error) {
    if (output) output.textContent = `安全校验拒绝：${error.message}；尚未提交。`;
  }
}

async function submitCustomCase() {
  const file = byId("custom-case-file")?.files?.[0];
  const output = byId("custom-case-result");
  if (!canStartExperiment(activeTask)) {
    const warning = "已有实验正在运行，请等待当前任务结束后再提交自定义算例。";
    if (output) output.textContent = warning;
    setStatus(warning);
    return;
  }
  if (!validatedCustomCase) {
    if (output) output.textContent = "必须先通过当前归档的安全校验。";
    return;
  }
  if (!file || !selectedTarget) return;
  try {
    const customSubmitEndpoint = "/api/custom-cases/submit";
    const params = new URLSearchParams({
      target_id: selectedTarget,
      experiment_name: byId("custom-experiment-name")?.value.trim() || "custom-openfoam",
    });
    const job = await requestJson(`${customSubmitEndpoint}?${params}`, {
      method: "POST",
      headers: { "Content-Type": "application/gzip" },
      body: file,
    });
    if (!job.job_id) throw new Error("提交响应缺少 Job ID；尚不能确认已提交。 ");
    renderTaskCard({
      phase: phaseFromJob(job),
      jobId: job.job_id,
      pid: job.pid,
      targetId: selectedTarget,
      lastUpdated: new Date().toLocaleString(),
    });
    if (output) output.textContent = `自定义算例已获得 Job ID：${job.job_id}`;
    startPolling(() => pollCustomCase(job.job_id, selectedTarget));
  } catch (error) {
    renderError("自定义算例提交", error, {
      phase: "failed",
      targetId: selectedTarget,
      lastUpdated: new Date().toLocaleString(),
    });
  }
}

async function pollCustomCase(jobId, targetId) {
  const query = `target_id=${encodeURIComponent(targetId)}`;
  try {
    const job = await requestJson(`/api/custom-cases/${jobId}?${query}`);
    const phase = phaseFromJob(job);
    renderTaskCard({
      phase,
      jobId,
      pid: job.pid,
      error: job.error,
      targetId,
      lastUpdated: new Date().toLocaleString(),
    });
    if (phase === "failed" || phase === "cancelled") return;
    if (job.state === "succeeded") {
      const collection = await requestJson(`/api/custom-cases/${jobId}/results?${query}`);
      renderPostprocessResults(collection);
      renderTaskCard({ ...activeTask, phase: "completed", lastUpdated: new Date().toLocaleString() });
      return;
    }
    schedulePoll(() => pollCustomCase(jobId, targetId));
  } catch (error) {
    renderTaskCard({
      phase: activeTask?.phase || "submitted",
      jobId,
      targetId,
      pid: activeTask?.pid,
      warning: `状态查询暂时失败：${error.message}。将自动重试。`,
      lastUpdated: new Date().toLocaleString(),
    });
    schedulePoll(() => pollCustomCase(jobId, targetId));
  }
}

function openDialog(id) {
  const dialog = byId(id);
  if (dialog?.showModal) dialog.showModal();
}

function bindEvents() {
  const composer = byId("experiment-composer") || byId("research-form");
  composer?.addEventListener("submit", designExperimentFromPrompt);
  if (designButton && (designButton.type !== "submit" || designButton.form !== composer)) {
    designButton.addEventListener("click", designExperimentFromPrompt);
  }
  promptInput?.addEventListener("input", refreshComposer);
  targetSelect?.addEventListener("change", () => {
    selectedTarget = targetSelect.value;
    persist(storageKeys.targetId, selectedTarget);
    updateContext();
    refreshComposer();
  });
  modelProvider?.addEventListener("change", () => {
    if (modelId) modelId.value = modelDefaults[modelProvider.value] || "";
  });
  byId("configure-model")?.addEventListener("click", configureModel);
  byId("custom-case-file")?.addEventListener("change", () => {
    validatedCustomCase = null;
    const submit = byId("submit-custom-case");
    if (submit) submit.disabled = true;
    const output = byId("custom-case-result");
    if (output) output.textContent = "文件已更换，请重新执行安全校验。";
  });
  byId("validate-custom-case")?.addEventListener("click", validateCustomCase);
  byId("submit-custom-case")?.addEventListener("click", submitCustomCase);
  byId("open-model-settings")?.addEventListener("click", () => openDialog("model-settings"));
  byId("open-target-settings")?.addEventListener("click", () => openDialog("target-settings"));
  byId("open-custom-case")?.addEventListener("click", () => openDialog("custom-case-drawer"));
  byId("cancel-operation")?.addEventListener("click", cancelActiveOperation);
  byId("retry-operation")?.addEventListener("click", retryActiveOperation);
  startNewExperiment?.addEventListener("click", resetResearchSession);
  document.querySelectorAll("[data-open-dialog]").forEach((button) => {
    button.addEventListener("click", () => openDialog(button.dataset.openDialog));
  });
  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      if (promptInput) promptInput.value = button.dataset.prompt || "";
      refreshComposer();
      promptInput?.focus();
    });
  });
  document.querySelectorAll("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  });
  window.addEventListener("beforeunload", () => {
    window.clearTimeout(pollTimer);
    stopOperationPolling();
  });
}

async function init() {
  bindEvents();
  const modelLoad = loadModelConfiguration().catch((error) => {
    renderError("模型配置", error);
  });
  const operationRecovery = restoreActiveOperation().catch((error) => {
    renderError("恢复实验设计", error);
  });
  const experimentRecovery = restoreActiveExperiment().catch((error) => {
    renderError("恢复实验", error);
  });
  loadExecutionTargets().catch((error) => {
    renderError("执行平台", error);
  });
  await Promise.all([operationRecovery, modelLoad, experimentRecovery]);
  refreshComposer();
}

init();
