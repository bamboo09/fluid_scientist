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
import {
  bindPostprocessButton as bindPostprocessReveal,
} from "./postprocess.js";
import {
  AnalysisRequestController,
  analysisAvailability,
  normalizeResultPayload,
  plannedResultUrl,
} from "./result-state.js";

// Workflow mode: "v5" (conversation workbench) or "legacy" (old ExperimentPlan flow)
const workflowMode = "v5";

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

// Fetch and display system version
async function loadSystemVersion() {
  try {
    const response = await fetch('/api/system/version');
    if (response.ok) {
      const info = await response.json();
      const badge = document.getElementById('system-version');
      if (badge) {
        const sha = info.git_commit || 'unknown';
        const wf = info.workflow || 'v5';
        badge.textContent = `Workflow ${wf} · ${sha.substring(0, 7)}`;
      }
      // Populate footer
      const wfMode = document.getElementById('wf-mode');
      if (wfMode) wfMode.textContent = (info.workflow || 'v5').toUpperCase() + ' Beta';
      const wfGit = document.getElementById('wf-git');
      if (wfGit) wfGit.textContent = info.git_commit ? info.git_commit.substring(0, 12) : '—';
      const wfSchema = document.getElementById('wf-schema');
      if (wfSchema) wfSchema.textContent = info.schema_version || '—';
      const wfApi = document.getElementById('wf-api');
      if (wfApi) wfApi.textContent = info.api_version || '—';
    }
  } catch (e) {
    // Version display is non-critical
  }
}
let currentPlan = null;
let currentCompilation = null;
let currentSpec = null;
let currentEditProposal = null;
let specCompiling = false;
let activeTask = null;
let latestResults = null;
let postprocessSessionVersion = 0;
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
const analysisRequests = new AnalysisRequestController();

// ===== Research Session (multi-turn clarification) =====
let currentResearchSession = null;
let currentClarificationQuestions = [];

// Bridge aliases that map the research-session UI onto existing elements.
const researchQuestionInput = promptInput;
const designExperimentBtn = designButton;
const planProgressCard = operationCard;

// Dedicated container for research-session result cards (clarification / draft / unsupported).
let planResults = document.getElementById("research-session-results");
if (!planResults) {
  planResults = document.createElement("section");
  planResults.id = "research-session-results";
  planResults.className = "research-session-results";
  const taskHost = byId("task-card-host");
  if (stream && taskHost) stream.insertBefore(planResults, taskHost);
  else (stream || document.body).append(planResults);
}

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

function escapeHtml(str) {
  if (str === null || str === undefined) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
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

const specStatusLabels = {
  draft: "草稿",
  ready: "就绪",
  confirmed: "已确认",
  compiling: "编译中",
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  rejected: "已拒绝",
  awaiting_code_approval: "等待代码审批",
};

const sourceLabels = {
  user: "用户输入",
  derived: "系统确定",
  system_recommended: "系统选择",
  template_default: "模板默认",
  literature: "文献参考",
  generated_by_code: "代码生成",
  unknown: "未知",
};

const criticalityLabels = {
  critical: "关键",
  high: "高",
  medium: "中",
  low: "低",
};

const paramStatusLabels = {
  pending: "待确认",
  accepted: "已接受",
  modified: "已修改",
  rejected: "已拒绝",
};

const valueStatusLabels = {
  USER_CONFIRMED: "用户确认",
  USER_EXTRACTED: "用户提供",
  MODEL_INFERRED: "模型推断",
  SYSTEM_DERIVED: "系统推导",
  MISSING_REQUIRED: "待补充",
  CONFLICT: "存在冲突",
  NOT_APPLICABLE: "不适用",
};

const capabilityStatusLabels = {
  SUPPORTED_NATIVE: "原生支持",
  SUPPORTED_EXTENSION: "扩展支持",
  UNSUPPORTED: "能力缺失",
  UNKNOWN: "待确认",
  NOT_CHECKED: "未检查",
  NOT_APPLICABLE: "不适用",
};

const categoryLabels = {
  geometry: "几何",
  boundary_condition: "边界条件",
  physics: "物理属性",
  material: "材料属性",
  numerics: "数值参数",
  mesh: "网格",
  other: "其他",
};

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
  else if (key === storageKeys.specId) localStorage.setItem(storageKeys.specId, value);
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
  currentSpec = null;
  pendingParameterChanges.clear();
  activeTask = null;
  latestResults = null;
  postprocessSessionVersion += 1;
  activeOperation = null;
  activeOperationId = "";
  currentResearchSession = null;
  currentClarificationQuestions = [];
  for (const key of [storageKeys.projectId, storageKeys.planId, storageKeys.caseId, storageKeys.operationId, storageKeys.specId, storageKeys.researchSessionId]) {
    localStorage.removeItem(key);
  }
  if (operationCard) {
    operationCard.hidden = true;
    operationCard.setAttribute("aria-busy", "false");
  }
  lastOperationAnnouncement = "";
  if (operationAnnouncementNode) operationAnnouncementNode.textContent = "";
  for (const id of ["active-plan-card", "active-task-card", "active-spec-card"]) byId(id)?.remove();
  if (planResults) planResults.innerHTML = "";
  if (byId("report")) byId("report").hidden = true;
  if (researchQuestionCard) researchQuestionCard.hidden = true;
  if (welcomeMessage) welcomeMessage.hidden = false;
  if (researchForm) researchForm.hidden = false;
  if (startNewExperiment) startNewExperiment.hidden = true;
  // Hide vertical workflow stepper
  const stepper = byId("workflow-stepper");
  if (stepper) stepper.hidden = true;
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
    if (workflowMode === "legacy") {
      renderPlanCard(response);
      setStatus("实验计划已生成。请审阅假设、参数和局限后确认。");
    } else {
      // V2 mode: plan is intermediate, spec workbench is the main UI
      // Plan creation triggers spec migration if not already done
      setStatus("实验计划已生成，正在创建结构化实验规格...");
    }
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

// ===== Research Session helpers =====
function showElement(element) { if (element) element.hidden = false; }
function hideElement(element) { if (element) element.hidden = true; }
function showError(message) { renderError("研究会话", new Error(message)); }

async function loadAndRenderSpec(sessionId, specId) {
  try {
    // 通过研究会话 API 获取 spec
    const response = await fetch(`/api/research-sessions/${sessionId}/experiment-spec`);
    if (!response.ok) {
      // 如果通过 session API 获取失败，尝试直接通过 project API
      if (currentProject) {
        const altResponse = await fetch(
          `/api/projects/${currentProject.project_id}/experiment-specs/${specId}`,
        );
        if (altResponse.ok) {
          const spec = await altResponse.json();
          currentSpec = spec;
          persist(storageKeys.specId, spec.experiment_id);
          renderSpecWorkbench(spec);
          return;
        }
      }
      throw new Error(`HTTP ${response.status}`);
    }
    const spec = await response.json();
    currentSpec = spec;
    persist(storageKeys.specId, spec.experiment_id);
    renderSpecWorkbench(spec);
  } catch (error) {
    showError(`加载实验规格失败: ${error.message}`);
  }
}

// 创建研究会话
async function createResearchSession(message) {
  if (!currentProject) {
    currentProject = await createProject(message);
  }
  const response = await fetch("/api/research-sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project_id: currentProject.project_id,
      message: message,
    }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const result = await response.json();
  currentResearchSession = { session_id: result.session_id, ...result };
  if (result.session_id) localStorage.setItem(storageKeys.researchSessionId, result.session_id);
  handleResearchTurnResult(result);
}

// 继续研究会话
async function continueResearchSession(sessionId, message) {
  const response = await fetch(`/api/research-sessions/${sessionId}/turns`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: message }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const result = await response.json();
  currentResearchSession = { ...currentResearchSession, ...result };
  if (result.session_id) localStorage.setItem(storageKeys.researchSessionId, result.session_id);
  handleResearchTurnResult(result);
}

// 处理研究会话结果
function handleResearchTurnResult(result) {
  hideElement(planProgressCard);
  setStatus("");
  if (result.type === "clarification_required") {
    currentClarificationQuestions = result.questions || [];
    renderClarificationCard(result);
  } else if (result.type === "draft_ready") {
    renderDraftReadyCard(result);
  } else if (result.type === "pipeline_failed") {
    renderPipelineFailedCard(result);
  } else if (result.type === "unsupported") {
    renderUnsupportedCard(result);
  }
}

function renderPipelineFailedCard(result) {
  const container = planResults || stream;
  if (!container) return;
  container.innerHTML = "";

  const card = document.createElement("div");
  card.className = "card unsupported-card";

  const title = document.createElement("h3");
  title.textContent = "求解方案尚未通过验证";
  card.appendChild(title);

  const reason = document.createElement("p");
  reason.textContent =
    result.failure?.message ||
    "系统未能完成真实 Case 生成与验证，因此不会发布实验草案。";
  card.appendChild(reason);

  if (Array.isArray(result.stage_history) && result.stage_history.length) {
    const list = document.createElement("ul");
    for (const stage of result.stage_history) {
      const item = document.createElement("li");
      item.textContent = stage.error
        ? `${stage.stage}: ${stage.error}`
        : stage.stage;
      list.appendChild(item);
    }
    card.appendChild(list);
  }

  container.appendChild(card);
}

// 渲染澄清卡片
function renderClarificationCard(result) {
  const container = planResults || stream;
  if (!container) return;
  container.innerHTML = "";

  const card = document.createElement("div");
  card.className = "card clarification-card";

  const title = document.createElement("h3");
  title.textContent = "需要补充信息";
  card.appendChild(title);

  const summary = document.createElement("p");
  summary.className = "clarification-summary";
  summary.textContent = result.summary;
  card.appendChild(summary);

  if (result.current_understanding && Object.keys(result.current_understanding).length > 0) {
    const understanding = document.createElement("div");
    understanding.className = "clarification-understanding";
    understanding.innerHTML = "<strong>当前理解：</strong>";
    for (const [key, value] of Object.entries(result.current_understanding)) {
      const item = document.createElement("span");
      item.className = "understanding-chip";
      item.textContent = `${key}: ${value}`;
      understanding.appendChild(item);
    }
    card.appendChild(understanding);
  }

  for (const q of result.questions) {
    const qDiv = document.createElement("div");
    qDiv.className = "clarification-question";

    const qLabel = document.createElement("label");
    qLabel.textContent = q.text;
    qDiv.appendChild(qLabel);

    if (q.options && q.options.length > 0) {
      const optionsDiv = document.createElement("div");
      optionsDiv.className = "clarification-options";
      for (const opt of q.options) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "button button-secondary clarification-option-btn";
        btn.textContent = opt;
        btn.addEventListener("click", () => {
          optionsDiv.querySelectorAll(".clarification-option-btn").forEach((b) => b.classList.remove("selected"));
          btn.classList.add("selected");
        });
        optionsDiv.appendChild(btn);
      }
      qDiv.appendChild(optionsDiv);
    }

    card.appendChild(qDiv);
  }

  const inputDiv = document.createElement("div");
  inputDiv.className = "clarification-input";
  const input = document.createElement("textarea");
  input.className = "input";
  input.placeholder = "输入您的回答（可选）...";
  inputDiv.appendChild(input);
  card.appendChild(inputDiv);

  const buttonDiv = document.createElement("div");
  buttonDiv.className = "clarification-actions";

  const submitBtn = document.createElement("button");
  submitBtn.className = "button button-primary";
  submitBtn.textContent = "提交回答";
  submitBtn.addEventListener("click", async () => {
    let answer = input.value.trim();
    if (!answer) {
      const selected = card.querySelectorAll(".clarification-option-btn.selected");
      const selectedTexts = Array.from(selected).map((b) => b.textContent);
      answer = selectedTexts.join("，");
    }
    if (!answer) {
      showError("请选择选项或输入回答");
      return;
    }
    submitBtn.disabled = true;
    submitBtn.textContent = "处理中...";
    try {
      await continueResearchSession(currentResearchSession.session_id, answer);
    } catch (e) {
      showError(`继续会话失败: ${e.message}`);
      submitBtn.disabled = false;
      submitBtn.textContent = "提交回答";
    }
  });
  buttonDiv.appendChild(submitBtn);

  const skipBtn = document.createElement("button");
  skipBtn.className = "button button-secondary";
  skipBtn.textContent = "按推荐继续";
  skipBtn.addEventListener("click", async () => {
    skipBtn.disabled = true;
    try {
      await continueResearchSession(currentResearchSession.session_id, "按推荐默认值继续");
    } catch (e) {
      showError(`继续会话失败: ${e.message}`);
      skipBtn.disabled = false;
    }
  });
  buttonDiv.appendChild(skipBtn);

  card.appendChild(buttonDiv);
  container.appendChild(card);
}

// 渲染草案就绪卡片
function renderDraftReadyCard(result) {
  const container = planResults;
  container.innerHTML = "";

  if (result.experiment_spec_id) {
    // 有 spec_id，获取并渲染工作台
    loadAndRenderSpec(result.session_id, result.experiment_spec_id);
  } else {
    // 没有 spec_id，显示提示
    const card = document.createElement("div");
    card.className = "card draft-ready-card";

    const title = document.createElement("h3");
    title.textContent = "实验草案已就绪";
    card.appendChild(title);

    if (result.warnings && result.warnings.length > 0) {
      for (const w of result.warnings) {
        const warn = document.createElement("div");
        warn.className = "warning";
        warn.textContent = w;
        card.appendChild(warn);
      }
    }

    renderCapabilityPreview(card, result.capability_preview || result.draft?.capability_preview);

    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "实验规格正在生成中，请稍后刷新页面。";
    card.appendChild(note);

    container.appendChild(card);
  }
}

function renderCapabilityPreview(card, preview) {
  const fields = preview?.fields;
  if (!fields || typeof fields !== "object") return;
  const list = document.createElement("ul");
  list.className = "capability-preview-list";
  for (const [name, info] of Object.entries(fields)) {
    const item = document.createElement("li");
    const value = valueStatusLabels[info.value_status] || info.value_status || "待补充";
    const capability = capabilityStatusLabels[info.capability_status] || info.capability_status || "未检查";
    const display = info.display_value ? `：${info.display_value}` : "";
    item.textContent = `${name}${display} / ${value} / ${capability}`;
    list.appendChild(item);
  }
  card.appendChild(list);
}

// 渲染不支持卡片
function renderUnsupportedCard(result) {
  const container = planResults || stream;
  if (!container) return;
  container.innerHTML = "";

  const card = document.createElement("div");
  card.className = "card unsupported-card";

  const title = document.createElement("h3");
  title.textContent = "暂不支持该请求";
  card.appendChild(title);

  const reason = document.createElement("p");
  reason.textContent = result.reason;
  card.appendChild(reason);

  if (result.missing_capabilities && result.missing_capabilities.length > 0) {
    const capList = document.createElement("ul");
    for (const cap of result.missing_capabilities) {
      const li = document.createElement("li");
      li.textContent = `${cap.description}（${cap.severity}）`;
      capList.appendChild(li);
    }
    card.appendChild(capList);
  }

  container.appendChild(card);
}

/** @deprecated Use createResearchSession() instead. Kept for backward compatibility. */
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
      postprocessSessionVersion += 1;
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
  const question = researchQuestionInput?.value.trim() || promptInput?.value.trim() || "";
  if (!question) return;

  // V5 mode: use compile-ready pipeline directly
  if (workflowMode === "v5") {
    await runV5Pipeline(question);
    return;
  }

  if (!modelConfiguration.configured) return;
  showResearchQuestion(question);
  if (designExperimentBtn) designExperimentBtn.disabled = true;
  showElement(planProgressCard);
  setStatus("正在分析研究需求…");
  try {
    await createResearchSession(question);
  } catch (error) {
    showError(`研究需求提交失败: ${error.message}`);
  } finally {
    if (designExperimentBtn) designExperimentBtn.disabled = false;
    hideElement(planProgressCard);
    setStatus("");
  }
}

// ---- V5 Compile-Ready Pipeline ----

let v5SessionId = null;

async function runV5Pipeline(question) {
  showResearchQuestion(question);
  if (designExperimentBtn) designExperimentBtn.disabled = true;
  showElement(planProgressCard);
  setStatus("正在运行编译就绪流水线…");
  try {
    const resp = await fetch("/api/v5/pipeline/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_description: question }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${text}`);
    }
    const result = await resp.json();
    v5SessionId = result.session_id;
    renderV5Result(result);
  } catch (error) {
    showError(`V5 流水线失败: ${error.message}`);
  } finally {
    if (designExperimentBtn) designExperimentBtn.disabled = false;
    hideElement(planProgressCard);
    setStatus("");
  }
}

function renderV5Result(result) {
  const container = planResults || stream;
  if (!container) return;
  container.innerHTML = "";

  const card = document.createElement("div");
  card.className = "work-card v5-result-card";

  // Header
  const header = document.createElement("header");
  header.className = "card-header";
  header.innerHTML = `
    <div><p class="card-kicker">编译就绪</p><h2>OpenFOAM 算例已生成</h2></div>
    <span class="type-chip type-chip-teal">${result.status === "compile_ready" ? "✓ 就绪" : "✗ " + result.status}</span>
  `;
  card.appendChild(header);

  if (result.failure) {
    const errP = document.createElement("p");
    errP.className = "v5-error-msg";
    errP.textContent = result.failure.message || "未知错误";
    card.appendChild(errP);
    if (result.stage_history) {
      const list = document.createElement("ul");
      list.className = "v5-stages";
      for (const s of result.stage_history) {
        const li = document.createElement("li");
        li.textContent = s.detail ? `${s.stage} — ${s.detail}` : s.stage;
        if (s.stage === "failed") li.style.color = "var(--red)";
        list.appendChild(li);
      }
      card.appendChild(list);
    }
    container.appendChild(card);
    return;
  }

  const view = result.compile_ready_view;
  if (!view) {
    const p = document.createElement("p");
    p.textContent = "未收到编译就绪视图数据。";
    card.appendChild(p);
    container.appendChild(card);
    return;
  }

  // Objective
  const objSec = document.createElement("section");
  objSec.className = "v5-section";
  objSec.innerHTML = `<h3>研究目标</h3><p>${escapeHtml(view.research_objective || "—")}</p>`;
  card.appendChild(objSec);

  // Solver + mesh grid
  const grid = document.createElement("div");
  grid.className = "v5-grid";
  const solver = view.solver || {};
  const mesh = view.mesh || {};
  const geom = view.geometry || {};
  const bcs = view.boundary_conditions || {};
  grid.innerHTML = `
    <section class="v5-section">
      <h3>求解器配置</h3>
      <div class="v5-kv">
        <div class="kv-row"><span class="kv-key">求解器</span><span class="kv-val highlight">${escapeHtml(solver.name || solver.solver_name || "—")}</span></div>
        <div class="kv-row"><span class="kv-key">湍流模型</span><span class="kv-val">${escapeHtml(solver.turbulence_model || view.physical_models?.turbulence_model || "—")}</span></div>
        <div class="kv-row"><span class="kv-key">时间推进</span><span class="kv-val">${escapeHtml(solver.temporal_type || view.numerics?.time_control?.temporal_type || "—")}</span></div>
      </div>
    </section>
    <section class="v5-section">
      <h3>几何</h3>
      <div class="v5-kv">${fmtV5Kv(geom)}</div>
    </section>
    <section class="v5-section">
      <h3>网格</h3>
      <div class="v5-kv">
        <div class="kv-row"><span class="kv-key">网格族</span><span class="kv-val">${escapeHtml(mesh.geometry_family || mesh.family || "—")}</span></div>
        <div class="kv-row"><span class="kv-key">分辨率</span><span class="kv-val">${escapeHtml(String(mesh.resolution || mesh.mesh_resolution || "—"))}</span></div>
      </div>
    </section>
    <section class="v5-section">
      <h3>边界条件</h3>
      <div class="v5-bc-list">${fmtV5BCs(bcs)}</div>
    </section>
  `;
  card.appendChild(grid);

  // Validation checks
  const checks = (view.validation_results || {}).checks || [];
  if (checks.length) {
    const checkSec = document.createElement("section");
    checkSec.className = "v5-section";
    checkSec.innerHTML = "<h3>算例验证</h3>";
    const ul = document.createElement("ul");
    ul.className = "v5-checks";
    for (const c of checks) {
      const li = document.createElement("li");
      li.className = `check check-${c.passed ? "pass" : "fail"} check-sev-${c.severity}`;
      li.innerHTML = `<span class="check-icon">${c.passed ? "✓" : "✗"}</span><span class="check-sev check-sev-${c.severity}">${c.severity}</span><span class="check-name">${escapeHtml(c.check_name)}</span><span class="check-msg">${escapeHtml(c.message || "")}</span>`;
      ul.appendChild(li);
    }
    checkSec.appendChild(ul);
    card.appendChild(checkSec);
  }

  // Generated files
  const files = (view.case_manifest || {}).generated_files || result.generated_files || [];
  if (files.length) {
    const fileSec = document.createElement("section");
    fileSec.className = "v5-section";
    fileSec.innerHTML = `<h3>已生成文件 <small>${files.length}</small> 个</h3>`;
    const ul = document.createElement("ul");
    ul.className = "v5-files";
    for (const f of files) {
      const li = document.createElement("li");
      li.textContent = f;
      ul.appendChild(li);
    }
    fileSec.appendChild(ul);
    card.appendChild(fileSec);
  }

  // Incremental modification
  const modSec = document.createElement("section");
  modSec.className = "v5-section v5-modify-section";
  modSec.innerHTML = `
    <h3>增量修改</h3>
    <p class="v5-hint">${view.modifiable_fields ? "可修改：" + view.modifiable_fields.map(escapeHtml).join("、") : "输入修改描述"}</p>
    <div class="v5-modify-controls">
      <input type="text" id="v5-modify-input" placeholder="例如：把雷诺数改为200，终止时间设为50">
      <button type="button" id="v5-apply-modify" class="button button-secondary">应用修改</button>
    </div>
  `;
  card.appendChild(modSec);

  container.appendChild(card);

  // Bind modify button
  const modifyBtn = card.querySelector("#v5-apply-modify");
  const modifyInput = card.querySelector("#v5-modify-input");
  if (modifyBtn && modifyInput) {
    modifyBtn.addEventListener("click", async () => {
      const modText = modifyInput.value.trim();
      if (modText.length < 3) return;
      modifyBtn.disabled = true;
      modifyBtn.textContent = "修改中...";
      try {
        const resp = await fetch("/api/v5/pipeline/modify", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: v5SessionId, modification_text: modText }),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const result2 = await resp.json();
        renderV5Result(result2);
      } catch (err) {
        showError(`修改失败: ${err.message}`);
      } finally {
        modifyBtn.disabled = false;
        modifyBtn.textContent = "应用修改";
      }
    });
    modifyInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        modifyBtn.click();
      }
    });
  }
}

function fmtV5Kv(obj) {
  if (!obj || typeof obj !== "object") return "";
  const keys = Object.keys(obj).filter((k) => obj[k] !== undefined && obj[k] !== null);
  return keys.map((k) => {
    const v = obj[k];
    const val = typeof v === "object" && "value" in v ? `${v.value}${v.unit ? " " + v.unit : ""}` : String(v);
    return `<div class="kv-row"><span class="kv-key">${escapeHtml(k)}</span><span class="kv-val">${escapeHtml(val)}</span></div>`;
  }).join("");
}

function fmtV5BCs(bcs) {
  if (!bcs || typeof bcs !== "object") return '<p class="empty-hint">无边界条件信息</p>';
  const entries = Object.entries(bcs);
  if (!entries.length) return '<p class="empty-hint">无边界条件信息</p>';
  return entries.map(([patch, cfg]) => {
    const type = typeof cfg === "object" ? (cfg.type || JSON.stringify(cfg)) : cfg;
    return `<div class="bc-patch"><strong>${escapeHtml(patch)}</strong><span>${escapeHtml(String(type))}</span></div>`;
  }).join("");
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

// ------------------------------------------------------------------
// Experiment Spec workbench (P0-P3 structured parameter system)
// ------------------------------------------------------------------

async function createExperimentSpec(planId) {
  const spec = await requestJson(`/api/projects/${currentProject.project_id}/experiment-specs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plan_id: planId }),
  });
  currentSpec = spec;
  persist(storageKeys.specId, spec.experiment_id);
  return spec;
}

function isSpecEditable(spec) {
  return spec && ["draft", "ready"].includes(spec.status);
}

function groupParameters(parameters) {
  const groups = {};
  for (const param of parameters || []) {
    const category = param.category || "other";
    if (!groups[category]) groups[category] = [];
    groups[category].push(param);
  }
  return groups;
}

function renderParameterRow(param, spec) {
  const row = document.createElement("div");
  row.className = "spec-param-row";
  row.dataset.paramId = param.parameter_id;
  const editable = param.editable && isSpecEditable(spec);
  row.dataset.editable = String(editable);
  if (param.source?.type === "unknown") {
    row.classList.add("spec-param-unknown");
  }

  const label = document.createElement("div");
  label.className = "spec-param-label";
  const name = document.createElement("strong");
  name.textContent = param.display_name;
  const id = document.createElement("small");
  id.textContent = param.parameter_id;
  label.append(name, id);

  const valueContainer = document.createElement("div");
  valueContainer.className = "spec-param-value";
  if (editable) {
    const input = document.createElement("input");
    input.type = param.data_type === "integer" ? "number" : "text";
    input.value = param.value ?? "";
    input.placeholder = "未设置";
    input.addEventListener("input", () => markParameterDirty(param.parameter_id, input.value, param.unit));
    valueContainer.append(input);
  } else {
    const valueSpan = document.createElement("span");
    valueSpan.textContent = text(param.value);
    valueContainer.append(valueSpan);
  }
  if (param.unit) {
    const unit = document.createElement("small");
    unit.className = "spec-param-unit";
    unit.textContent = param.unit;
    valueContainer.append(unit);
  }

  const meta = document.createElement("div");
  meta.className = "spec-param-meta";
  const sourceChip = document.createElement("span");
  sourceChip.className = "spec-chip spec-chip-source";
  sourceChip.textContent = sourceLabels[param.source?.type] || param.source?.type || "—";
  const critChip = document.createElement("span");
  critChip.className = "spec-chip spec-chip-criticality";
  critChip.dataset.criticality = param.criticality;
  critChip.textContent = criticalityLabels[param.criticality] || param.criticality || "—";
  const statusChip = document.createElement("span");
  statusChip.className = "spec-chip spec-chip-status";
  statusChip.dataset.status = param.status;
  statusChip.textContent = paramStatusLabels[param.status] || param.status || "—";
  meta.append(sourceChip, critChip, statusChip);

  // Display recommendation reason if available
  if (param.source?.reason) {
    const reasonEl = document.createElement("div");
    reasonEl.className = "spec-param-reason";
    reasonEl.textContent = param.source.reason;
    if (param.source.confidence) {
      reasonEl.dataset.confidence = param.source.confidence;
    }
    meta.append(reasonEl);
  }

  row.append(label, valueContainer, meta);
  return row;
}

// ============================================================
// Vertical Workflow Stepper (left sidebar)
// ============================================================

// Ordered workflow steps matching the design spec (top-to-bottom = left-to-right in original design)
const WORKFLOW_STEPS = [
  { id: "spec-save-btn",        text: "保存草案",         action: () => saveSpecDraft() },
  { id: "spec-apply-btn",       text: "应用修改",         action: () => applyPendingParameterChanges() },
  { id: "spec-discard-btn",     text: "放弃修改",         action: () => discardPendingParameterChanges() },
  { id: "spec-accept-rec-btn",  text: "接受推荐值",       action: () => acceptAllRecommendations() },
  { id: "spec-ready-btn",       text: "准备就绪",         action: () => transitionSpec("ready") },
  { id: "spec-confirm-btn",     text: "确认实验版本",     action: () => transitionSpec("confirmed") },
  { id: "spec-compile-btn",     text: "生成 Case",        action: () => compileSpec() },
  { id: "spec-submit-btn",      text: "提交运行",         action: () => submitSpec() },
  { id: "spec-run-status-btn",  text: "查看运行状态",     action: () => showRunStatus() },
  { id: "spec-report-btn",      text: "查看分析报告",     action: () => showAnalysisReport() },
  { id: "spec-capability-btn",  text: "查看缺失能力",     action: () => showMissingCapabilities() },
  { id: "spec-clone-btn",       text: "修改参数（创建新版本）", action: () => cloneSpec() },
  { id: "spec-error-btn",       text: "查看错误",         action: () => showErrorDetails() },
  { id: "spec-back-draft-btn",  text: "回到草案",         action: () => backToDraft() },
  { id: "spec-revalidate-btn",  text: "重新校验",         action: () => revalidateSpec() },
];

function buildWorkflowStepper() {
  const stepper = byId("workflow-stepper");
  if (!stepper) return;
  stepper.innerHTML = "";

  for (const step of WORKFLOW_STEPS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "wf-step";
    btn.id = "wf-" + step.id;
    btn.textContent = step.text;
    btn.dataset.originalId = step.id;
    btn.addEventListener("click", () => {
      if (btn.disabled) return;
      step.action();
    });
    stepper.append(btn);
  }
}

function updateWorkflowStepper(spec) {
  const stepper = byId("workflow-stepper");
  if (!stepper) return;

  if (!spec) {
    stepper.hidden = true;
    return;
  }
  stepper.hidden = false;

  const status = spec.status;
  const editable = isSpecEditable(spec);
  const hasPendingChanges = pendingParameterChanges.size > 0;
  const actions = getWorkbenchActions(status);

  // Build a set of visible button IDs
  const visibleIds = new Set();
  if (actions.primaryId) visibleIds.add(actions.primaryId);
  actions.secondary.forEach(s => visibleIds.add(s.id));

  // Special cases always visible depending on state
  if (status === "failed") {
    visibleIds.add("spec-error-btn");
    visibleIds.add("spec-back-draft-btn");
  }
  if (status === "awaiting_code_approval") {
    visibleIds.add("spec-capability-btn");
  }

  // Compile button special handling
  if (specCompiling) visibleIds.add("spec-compile-btn");

  // Determine the "current active" step based on status
  const activeStepMap = {
    draft: "spec-apply-btn",
    ready: "spec-confirm-btn",
    confirmed: "spec-compile-btn",
    compiling: "spec-compile-btn",
    compiled: "spec-submit-btn",
    running: "spec-run-status-btn",
    completed: "spec-report-btn",
    awaiting_code_approval: "spec-capability-btn",
    failed: "spec-error-btn",
    rejected: "spec-error-btn",
  };
  const activeId = activeStepMap[status] || null;

  // Determine completed steps (steps before the active one that are in the normal flow)
  const flowOrder = [
    "spec-save-btn", "spec-apply-btn", "spec-discard-btn", "spec-accept-rec-btn",
    "spec-ready-btn", "spec-confirm-btn", "spec-compile-btn", "spec-submit-btn",
    "spec-run-status-btn", "spec-report-btn",
  ];
  const activeIdx = flowOrder.indexOf(activeId);

  for (const step of WORKFLOW_STEPS) {
    const btn = byId("wf-" + step.id);
    if (!btn) continue;

    const isVisible = visibleIds.has(step.id);
    btn.style.display = isVisible ? "" : "none";

    if (!isVisible) continue;

    // Reset classes
    btn.classList.remove("wf-active", "wf-completed", "wf-danger");
    btn.disabled = false;

    // Set active
    if (step.id === activeId) {
      btn.classList.add("wf-active");
    }

    // Set completed
    const stepIdx = flowOrder.indexOf(step.id);
    if (activeIdx >= 0 && stepIdx >= 0 && stepIdx < activeIdx) {
      btn.classList.add("wf-completed");
    }

    // Danger state for error
    if (step.id === "spec-error-btn" && (status === "failed" || status === "rejected")) {
      btn.classList.add("wf-danger");
      if (step.id === activeId) btn.classList.add("wf-active");
    }

    // Apply/discard disabled based on pending changes
    if (step.id === "spec-apply-btn" || step.id === "spec-discard-btn") {
      btn.disabled = !editable || !hasPendingChanges;
    }

    // Compile button during compilation
    if (step.id === "spec-compile-btn" && specCompiling) {
      btn.disabled = true;
      btn.textContent = "正在编译...";
    } else if (step.id === "spec-compile-btn") {
      btn.textContent = "生成 Case";
    }

    // Back to draft only visible when failed or rejected
    if (step.id === "spec-back-draft-btn") {
      btn.style.display = (status === "failed" || status === "rejected") ? "" : "none";
    }

    // Revalidate only in ready state
    if (step.id === "spec-revalidate-btn") {
      btn.style.display = (status === "ready") ? "" : "none";
    }
  }
}

function getWorkbenchActions(status) {
  const actions = {
    primary: null,
    primaryId: null,
    secondary: [],
    hidden: [],
  };

  switch (status) {
    case "draft":
      actions.primary = "校验草案";
      actions.primaryId = "spec-ready-btn";
      actions.secondary = [
        { id: "spec-save-btn", text: "保存草案" },
        { id: "spec-apply-btn", text: "应用修改" },
        { id: "spec-discard-btn", text: "放弃修改" },
      ];
      actions.hidden = [
        "spec-compile-btn", "spec-submit-btn", "spec-clone-btn",
        "spec-run-status-btn", "spec-report-btn", "spec-capability-btn",
        "spec-accept-rec-btn",
      ];
      break;
    case "ready":
      actions.primary = "确认实验版本";
      actions.primaryId = "spec-confirm-btn";
      actions.secondary = [
        { id: "spec-save-btn", text: "保存草案" },
        { id: "spec-ready-btn", text: "继续修改" },
        { id: "spec-revalidate-btn", text: "重新校验" },
      ];
      actions.hidden = [
        "spec-compile-btn", "spec-submit-btn", "spec-clone-btn",
        "spec-apply-btn", "spec-discard-btn", "spec-accept-rec-btn",
      ];
      break;
    case "confirmed":
      actions.primary = "生成 Case";
      actions.primaryId = "spec-compile-btn";
      actions.secondary = [
        { id: "spec-clone-btn", text: "克隆并修改参数" },
      ];
      actions.hidden = [
        "spec-apply-btn", "spec-discard-btn", "spec-accept-rec-btn",
        "spec-ready-btn", "spec-confirm-btn", "spec-submit-btn",
        "spec-save-btn",
      ];
      break;
    case "compiled":
      actions.primary = "提交运行";
      actions.primaryId = "spec-submit-btn";
      actions.secondary = [
        { id: "spec-clone-btn", text: "克隆新版本" },
      ];
      actions.hidden = [
        "spec-apply-btn", "spec-confirm-btn", "spec-compile-btn",
        "spec-ready-btn", "spec-save-btn",
      ];
      break;
    case "running":
      actions.primary = "查看运行状态";
      actions.primaryId = "spec-run-status-btn";
      actions.secondary = [];
      actions.hidden = [
        "spec-apply-btn", "spec-compile-btn", "spec-confirm-btn",
        "spec-clone-btn", "spec-save-btn",
      ];
      break;
    case "completed":
      actions.primary = "查看分析报告";
      actions.primaryId = "spec-report-btn";
      actions.secondary = [
        { id: "spec-clone-btn", text: "克隆新实验" },
      ];
      actions.hidden = [
        "spec-apply-btn", "spec-compile-btn", "spec-confirm-btn",
        "spec-save-btn",
      ];
      break;
    case "awaiting_code_approval":
      actions.primary = "查看缺失能力";
      actions.primaryId = "spec-capability-btn";
      actions.secondary = [];
      actions.hidden = [
        "spec-compile-btn", "spec-submit-btn", "spec-clone-btn",
        "spec-apply-btn", "spec-save-btn",
      ];
      break;
    case "failed":
      actions.primary = "查看错误";
      actions.primaryId = "spec-error-btn";
      actions.secondary = [
        { id: "spec-back-draft-btn", text: "回到草案" },
        { id: "spec-clone-btn", text: "克隆新版本" },
      ];
      actions.hidden = [
        "spec-compile-btn", "spec-submit-btn", "spec-apply-btn",
        "spec-confirm-btn", "spec-ready-btn",
      ];
      break;
    default:
      break;
  }
  return actions;
}

function updateSpecControls(spec) {
  const status = spec?.status;
  const editable = isSpecEditable(spec);
  const hasCompilation = !!currentCompilation;
  const submitted = ["running", "completed", "failed", "rejected"].includes(status);
  // States where the spec is locked and a new version can be cloned.
  const cloneableStates = ["confirmed", "compiling", "running", "completed", "failed"];
  const actions = getWorkbenchActions(status);

  const allButtonIds = [
    "spec-save-btn", "spec-ready-btn", "spec-confirm-btn", "spec-compile-btn",
    "spec-submit-btn", "spec-apply-btn", "spec-discard-btn",
    "spec-accept-rec-btn", "spec-clone-btn", "spec-run-status-btn",
    "spec-report-btn", "spec-capability-btn",
    "spec-error-btn", "spec-back-draft-btn", "spec-revalidate-btn",
  ];

  // Hide all first
  allButtonIds.forEach((id) => {
    const btn = byId(id);
    if (btn) btn.style.display = "none";
  });

  // Show primary
  if (actions.primaryId) {
    const btn = byId(actions.primaryId);
    if (btn) {
      btn.style.display = "";
      btn.classList.add("button-primary");
      btn.classList.remove("button-secondary");
    }
  }

  // Show secondary
  actions.secondary.forEach((s) => {
    const btn = byId(s.id);
    if (btn) {
      btn.style.display = "";
      btn.classList.remove("button-primary");
      btn.classList.add("button-secondary");
    }
  });

  // Handle compile button text during compilation
  const compileBtn = byId("spec-compile-btn");
  if (compileBtn && specCompiling) {
    compileBtn.style.display = "";
    compileBtn.disabled = true;
    compileBtn.textContent = "正在编译...";
  } else if (compileBtn) {
    compileBtn.disabled = false;
    compileBtn.textContent = "生成 Case";
  }

  // Handle apply/discard disabled state based on pending changes
  const applyBtn = byId("spec-apply-btn");
  if (applyBtn && applyBtn.style.display !== "none") {
    applyBtn.disabled = !editable || pendingParameterChanges.size === 0;
  }
  const discardBtn = byId("spec-discard-btn");
  if (discardBtn && discardBtn.style.display !== "none") {
    discardBtn.disabled = !editable || pendingParameterChanges.size === 0;
  }

  // Disabled reason display
  updateDisabledReason(spec);

  // When confirmed, run an advisory pre-check to surface blocking issues.
  if (spec && status === "confirmed") {
    runPreCheck(spec);
  }

  // Sync vertical workflow stepper
  updateWorkflowStepper(spec);
}

function updateDisabledReason(spec) {
  const node = byId("spec-disabled-reason");
  if (!node || !spec) return;
  const status = spec.status;
  const editable = isSpecEditable(spec);
  const reasons = [];

  if (status === "draft") {
    reasons.push("确认参数后点击「准备就绪」进入校验");
  } else if (status === "ready") {
    reasons.push("校验通过后点击「确认实验版本」锁定参数");
  } else if (status === "confirmed") {
    const blockingIssues = spec._blocking_issues;
    if (blockingIssues && blockingIssues.length) {
      reasons.push("无法生成 Case：" + blockingIssues.map(i => i.message).join("；"));
    } else {
      reasons.push("参数已锁定，点击「生成 Case」编译算例");
    }
  } else if (status === "awaiting_code_approval") {
    reasons.push("缺少代码能力，需完成代码扩展审批后继续");
  } else if (status === "failed") {
    reasons.push("实验失败，点击「修改参数（创建新版本）」创建修复版本");
  }

  if (reasons.length) {
    node.hidden = false;
    node.textContent = reasons.join("；");
  } else {
    node.hidden = true;
    node.textContent = "";
  }
}


function showErrorDetails() {
  if (!currentSpec) return;
  const errorInfo = currentSpec.error_info || currentSpec.error || '未知错误';
  const reasonArea = document.getElementById('spec-disabled-reason');
  if (reasonArea) {
    reasonArea.innerHTML = `<div class="error-details">
      <strong>错误详情:</strong> ${escapeHtml(typeof errorInfo === 'string' ? errorInfo : JSON.stringify(errorInfo))}
    </div>`;
  }
}

async function backToDraft() {
  if (!currentSpec) return;
  if (!confirm('确定要将实验回到草案状态吗？这将允许重新编辑参数。')) return;
  await transitionSpec('draft');
}

async function revalidateSpec() {
  if (!currentSpec) return;
  try {
    const response = await requestJson(
      `/api/projects/${currentSpec.project_id}/experiment-specs/${currentSpec.experiment_id}/pre-check`,
      'GET'
    );
    updateDisabledReason(currentSpec, response);
    showWorkbenchToast(response.can_compile ? '校验通过' : '存在阻塞问题',
      response.can_compile ? 'success' : 'warning');
  } catch (err) {
    showWorkbenchToast(`校验失败: ${err.message}`, 'error');
  }
}

async function runPreCheck(spec) {
  if (!currentProject || !spec) return;
  try {
    const preCheck = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${spec.experiment_id}/pre-check`,
    );
    if (!currentSpec || spec.experiment_id !== currentSpec.experiment_id) return;
    currentSpec = { ...currentSpec, _blocking_issues: preCheck.blocking_issues };
    const node = byId("spec-disabled-reason");
    if (node && preCheck.blocking_issues && preCheck.blocking_issues.length) {
      node.hidden = false;
      node.textContent = "无法生成 Case：" + preCheck.blocking_issues.map(i => i.message).join("；");
    }
  } catch (e) {
    // Pre-check is advisory; silently ignore network errors.
  }
}

function renderSpecWorkbench(spec) {
  const existing = byId("active-spec-card");
  if (existing) existing.remove();
  const card = makeCard("work-card spec-workbench", "参数工作台");
  card.id = "active-spec-card";

  const statusBar = document.createElement("div");
  statusBar.className = "spec-status-bar";
  const statusLabel = document.createElement("span");
  statusLabel.className = "spec-status-chip";
  statusLabel.dataset.status = spec.status;
  statusLabel.textContent = specStatusLabels[spec.status] || spec.status;
  const versionLabel = document.createElement("span");
  versionLabel.className = "spec-version-label";
  versionLabel.textContent = `v${spec.experiment_version}`;
  statusBar.append(statusLabel, versionLabel);
  card.append(statusBar);

  const toastHost = document.createElement("div");
  toastHost.id = "workbench-toast-host";
  toastHost.className = "workbench-toast-host";
  card.append(toastHost);

  if (spec.research) {
    const research = document.createElement("section");
    research.className = "spec-research";
    const title = document.createElement("h3");
    title.textContent = spec.research.title || "—";
    const objective = document.createElement("p");
    objective.textContent = spec.research.objective || "—";
    research.append(title, objective);
    card.append(research);
  }

  if (spec.physics) {
    const physics = document.createElement("section");
    physics.className = "spec-physics";
    const heading = document.createElement("h3");
    heading.textContent = "物理设置";
    physics.append(heading);
    addDefinitionList(physics, spec.physics);
    card.append(physics);
  }

  const groups = groupParameters(spec.parameters);
  for (const [category, params] of Object.entries(groups)) {
    const group = document.createElement("section");
    group.className = "spec-param-group";
    const heading = document.createElement("h3");
    heading.textContent = categoryLabels[category] || category;
    group.append(heading);
    for (const param of params) {
      group.append(renderParameterRow(param, spec));
    }
    card.append(group);
  }

  // Natural language edit section
  const nlSection = document.createElement("div");
  nlSection.className = "spec-nl-edit";
  nlSection.id = "spec-nl-edit";
  const nlLabel = document.createElement("label");
  nlLabel.textContent = "对当前实验草案提出修改";
  nlLabel.htmlFor = "spec-nl-input";
  const nlInput = document.createElement("input");
  nlInput.type = "text";
  nlInput.id = "spec-nl-input";
  nlInput.placeholder = "对当前实验草案提出修改";
  nlInput.className = "spec-nl-input";
  const nlBtn = document.createElement("button");
  nlBtn.type = "button";
  nlBtn.className = "button button-secondary";
  nlBtn.id = "spec-nl-btn";
  nlBtn.textContent = "生成修改建议";
  nlBtn.addEventListener("click", () => processWorkbenchTurn());
  const nlPreview = document.createElement("div");
  nlPreview.className = "spec-nl-preview";
  nlPreview.id = "spec-nl-preview";
  nlPreview.hidden = true;
  nlSection.append(nlLabel, nlInput, nlBtn, nlPreview);
  card.append(nlSection);

  const propagation = document.createElement("div");
  propagation.className = "spec-propagation";
  propagation.id = "spec-propagation";
  propagation.hidden = true;
  card.append(propagation);

  // Hidden action buttons (kept in DOM for existing code references, but not visible — use vertical stepper instead)
  const actions = document.createElement("div");
  actions.className = "spec-actions card-actions";
  actions.style.display = "none";
  actions.id = "spec-actions-hidden";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "button button-quiet";
  saveBtn.id = "spec-save-btn";
  saveBtn.textContent = "保存草案";
  saveBtn.addEventListener("click", () => saveSpecDraft());
  const applyBtn = document.createElement("button");
  applyBtn.type = "button";
  applyBtn.className = "button button-primary";
  applyBtn.id = "spec-apply-btn";
  applyBtn.textContent = "应用修改";
  applyBtn.disabled = true;
  applyBtn.addEventListener("click", () => applyPendingParameterChanges());
  const discardBtn = document.createElement("button");
  discardBtn.type = "button";
  discardBtn.className = "button button-quiet";
  discardBtn.id = "spec-discard-btn";
  discardBtn.textContent = "放弃修改";
  discardBtn.disabled = true;
  discardBtn.addEventListener("click", () => discardPendingParameterChanges());
  const acceptRecBtn = document.createElement("button");
  acceptRecBtn.type = "button";
  acceptRecBtn.className = "button button-secondary";
  acceptRecBtn.id = "spec-accept-rec-btn";
  acceptRecBtn.textContent = "接受系统选择";
  acceptRecBtn.addEventListener("click", () => acceptAllRecommendations());
  const readyBtn = document.createElement("button");
  readyBtn.type = "button";
  readyBtn.className = "button button-secondary";
  readyBtn.id = "spec-ready-btn";
  readyBtn.textContent = "准备就绪";
  readyBtn.addEventListener("click", () => transitionSpec("ready"));
  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "button button-secondary";
  confirmBtn.id = "spec-confirm-btn";
  confirmBtn.textContent = "确认实验版本";
  confirmBtn.addEventListener("click", () => transitionSpec("confirmed"));
  const compileBtn = document.createElement("button");
  compileBtn.type = "button";
  compileBtn.className = "button button-primary";
  compileBtn.id = "spec-compile-btn";
  compileBtn.textContent = "生成 Case";
  compileBtn.addEventListener("click", () => compileSpec());
  const submitBtn = document.createElement("button");
  submitBtn.type = "button";
  submitBtn.className = "button button-primary";
  submitBtn.id = "spec-submit-btn";
  submitBtn.textContent = "提交运行";
  submitBtn.addEventListener("click", () => submitSpec());
  const runStatusBtn = document.createElement("button");
  runStatusBtn.type = "button";
  runStatusBtn.className = "button button-secondary";
  runStatusBtn.id = "spec-run-status-btn";
  runStatusBtn.textContent = "查看运行状态";
  runStatusBtn.addEventListener("click", () => showRunStatus());
  const reportBtn = document.createElement("button");
  reportBtn.type = "button";
  reportBtn.className = "button button-secondary";
  reportBtn.id = "spec-report-btn";
  reportBtn.textContent = "查看分析报告";
  reportBtn.addEventListener("click", () => showAnalysisReport());
  const capabilityBtn = document.createElement("button");
  capabilityBtn.type = "button";
  capabilityBtn.className = "button button-secondary";
  capabilityBtn.id = "spec-capability-btn";
  capabilityBtn.textContent = "查看缺失能力";
  capabilityBtn.addEventListener("click", () => showMissingCapabilities());
  const cloneBtn = document.createElement("button");
  cloneBtn.type = "button";
  cloneBtn.className = "button button-secondary";
  cloneBtn.id = "spec-clone-btn";
  cloneBtn.textContent = "修改参数（创建新版本）";
  cloneBtn.addEventListener("click", () => cloneSpec());
  const errorBtn = document.createElement("button");
  errorBtn.type = "button";
  errorBtn.className = "button button-primary";
  errorBtn.id = "spec-error-btn";
  errorBtn.textContent = "查看错误";
  errorBtn.addEventListener("click", () => showErrorDetails());
  const backDraftBtn = document.createElement("button");
  backDraftBtn.type = "button";
  backDraftBtn.className = "button button-secondary";
  backDraftBtn.id = "spec-back-draft-btn";
  backDraftBtn.textContent = "回到草案";
  backDraftBtn.addEventListener("click", () => backToDraft());
  const revalidateBtn = document.createElement("button");
  revalidateBtn.type = "button";
  revalidateBtn.className = "button button-secondary";
  revalidateBtn.id = "spec-revalidate-btn";
  revalidateBtn.textContent = "重新校验";
  revalidateBtn.addEventListener("click", () => revalidateSpec());
  actions.append(saveBtn, applyBtn, discardBtn, acceptRecBtn, readyBtn, confirmBtn, compileBtn, submitBtn, runStatusBtn, reportBtn, capabilityBtn, cloneBtn, errorBtn, backDraftBtn, revalidateBtn);
  card.append(actions);

  // Build vertical workflow stepper (once)
  if (!byId("wf-spec-save-btn")) {
    buildWorkflowStepper();
  }

  const disabledReason = document.createElement("div");
  disabledReason.className = "spec-disabled-reason";
  disabledReason.id = "spec-disabled-reason";
  disabledReason.hidden = true;
  card.append(disabledReason);

  updateSpecControls(spec);
  updateWorkflowStepper(spec);

  const taskHost = byId("task-card-host");
  if (stream && taskHost) stream.insertBefore(card, taskHost);
  else (stream || document.body).append(card);

  return card;
}

function renderPropagation(propagation) {
  const node = byId("spec-propagation");
  if (!node) return;
  node.hidden = false;
  node.replaceChildren();
  const heading = document.createElement("p");
  heading.className = "spec-propagation-title";
  heading.textContent = "依赖传播结果";
  node.append(heading);
  if (propagation.summary) {
    const summary = document.createElement("pre");
    summary.textContent = propagation.summary;
    node.append(summary);
  }
  if (propagation.auto_recomputed?.length) {
    const item = document.createElement("p");
    item.textContent = `自动重算：${propagation.auto_recomputed.join("，")}`;
    node.append(item);
  }
  if (propagation.stale_artifacts?.length) {
    const item = document.createElement("p");
    item.className = "spec-propagation-warning";
    item.textContent = `过期产物：${propagation.stale_artifacts.join("，")}`;
    node.append(item);
  }
  if (propagation.new_warnings?.length) {
    for (const warning of propagation.new_warnings) {
      const item = document.createElement("p");
      item.className = "spec-propagation-warning";
      item.textContent = warning;
      node.append(item);
    }
  }
}

function showWorkbenchToast(message, type = "success") {
  const host = document.getElementById("workbench-toast-host");
  if (!host) return;
  const toast = document.createElement("div");
  toast.className = `workbench-toast workbench-toast-${type}`;
  toast.textContent = message;
  toast.setAttribute("role", type === "error" ? "alert" : "status");
  host.append(toast);
  // Auto-remove after 3 seconds
  window.setTimeout(() => {
    toast.classList.add("workbench-toast-fading");
    window.setTimeout(() => toast.remove(), 300);
  }, 2700);
}

const pendingParameterChanges = new Map();

function markParameterDirty(parameterId, value, unit = null) {
  // If value matches original, remove from pending
  if (currentSpec) {
    const original = currentSpec.parameters.find(p => p.parameter_id === parameterId);
    if (original && String(original.value) === String(value)) {
      pendingParameterChanges.delete(parameterId);
    } else {
      pendingParameterChanges.set(parameterId, {
        parameter_id: parameterId,
        value: value === "" ? null : (isNaN(Number(value)) ? value : Number(value)),
        unit: unit || original?.unit || null,
      });
    }
  }
  renderPendingChangeSummary();
  updateDirtyRowStyles();
}

function updateDirtyRowStyles() {
  document.querySelectorAll(".spec-param-row").forEach(row => {
    const paramId = row.dataset.paramId;
    if (pendingParameterChanges.has(paramId)) {
      row.classList.add("spec-param-dirty");
    } else {
      row.classList.remove("spec-param-dirty");
    }
  });
  // Update apply/discard button states
  const applyBtn = byId("spec-apply-btn");
  const discardBtn = byId("spec-discard-btn");
  const hasPending = pendingParameterChanges.size > 0;
  if (applyBtn) applyBtn.disabled = !hasPending;
  if (discardBtn) discardBtn.disabled = !hasPending;
}

function renderPendingChangeSummary() {
  let summary = byId("spec-pending-summary");
  const count = pendingParameterChanges.size;
  if (count === 0) {
    if (summary) summary.hidden = true;
    return;
  }
  if (!summary) {
    summary = document.createElement("div");
    summary.id = "spec-pending-summary";
    summary.className = "spec-pending-summary";
    const card = byId("active-spec-card");
    if (card) {
      const actions = card.querySelector(".spec-actions");
      if (actions) {
        card.insertBefore(summary, actions);
      } else {
        card.append(summary);
      }
    }
  }
  summary.hidden = false;
  summary.textContent = `${count} 个参数待保存`;
}

async function updateSpecParameter(parameterId, newValue) {
  if (!currentSpec || !isSpecEditable(currentSpec)) return;
  const coerced = newValue === "" ? null : (isNaN(Number(newValue)) ? newValue : Number(newValue));

  // 记录滚动位置和焦点，防止页面跳动
  const savedScrollY = window.scrollY;
  const activeParamRow = document.querySelector(`.spec-param-row[data-param-id="${parameterId}"]`);
  const activeInput = activeParamRow?.querySelector("input");

  try {
    const response = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${currentSpec.experiment_id}/parameters/${parameterId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: coerced }),
      },
    );
    const propagation = response._propagation;
    currentSpec = response;

    // 局部更新参数行，不重建整个工作台
    updateParameterRowInPlace(parameterId, currentSpec);

    if (propagation) renderPropagation(propagation);

    // 恢复滚动位置，防止页面跳动
    window.scrollTo({ top: savedScrollY, behavior: "instant" });

    // 恢复输入焦点
    if (activeInput) {
      const newInput = document.querySelector(`.spec-param-row[data-param-id="${parameterId}"] input`);
      if (newInput) newInput.focus();
    }
  } catch (error) {
    showWorkbenchToast(`参数更新失败：${error.message || error}`, "error");
    updateParameterRowInPlace(parameterId, currentSpec);
    window.scrollTo({ top: savedScrollY, behavior: "instant" });
  }
}

function updateParameterRowInPlace(parameterId, spec) {
  const param = spec.parameters.find(p => p.parameter_id === parameterId);
  if (!param) return;

  const row = document.querySelector(
    `.spec-param-row[data-param-id="${parameterId}"]`
  );
  if (!row) return;

  const editable = param.editable && isSpecEditable(spec);
  row.dataset.editable = String(editable);
  if (param.source?.type === "unknown") {
    row.classList.add("spec-param-unknown");
  } else {
    row.classList.remove("spec-param-unknown");
  }

  // Update input value without recreating the element
  const input = row.querySelector("input");
  if (input && editable) {
    // Only update if the value actually changed and input doesn't have focus
    // (to avoid disrupting user typing)
    if (document.activeElement !== input) {
      input.value = param.value ?? "";
    }
  } else if (input && !editable) {
    // Replace input with span
    const valueSpan = document.createElement("span");
    valueSpan.textContent = text(param.value);
    input.replaceWith(valueSpan);
  } else if (!input && editable) {
    // Replace span with input
    const valueContainer = row.querySelector(".spec-param-value");
    if (valueContainer) {
      const span = valueContainer.querySelector("span");
      if (span) {
        const newInput = document.createElement("input");
        newInput.type = param.data_type === "integer" ? "number" : "text";
        newInput.value = param.value ?? "";
        newInput.placeholder = "未设置";
        newInput.addEventListener("input", () => markParameterDirty(param.parameter_id, newInput.value, param.unit));
        span.replaceWith(newInput);
      }
    }
  }

  // Update status chip
  const statusChip = row.querySelector(".spec-chip-status");
  if (statusChip) {
    statusChip.dataset.status = param.status;
    statusChip.textContent = paramStatusLabels[param.status] || param.status || "—";
  }

  // Update source chip
  const sourceChip = row.querySelector(".spec-chip-source");
  if (sourceChip) {
    sourceChip.textContent = sourceLabels[param.source?.type] || param.source?.type || "—";
  }

  // Update criticality chip
  const critChip = row.querySelector(".spec-chip-criticality");
  if (critChip) {
    critChip.dataset.criticality = param.criticality;
    critChip.textContent = criticalityLabels[param.criticality] || param.criticality || "—";
  }

  // Update or add reason display
  const existingReason = row.querySelector(".spec-param-reason");
  if (param.source?.reason) {
    if (existingReason) {
      existingReason.textContent = param.source.reason;
      if (param.source.confidence) {
        existingReason.dataset.confidence = param.source.confidence;
      }
    } else {
      const meta = row.querySelector(".spec-param-meta");
      if (meta) {
        const reasonEl = document.createElement("div");
        reasonEl.className = "spec-param-reason";
        reasonEl.textContent = param.source.reason;
        if (param.source.confidence) {
          reasonEl.dataset.confidence = param.source.confidence;
        }
        meta.append(reasonEl);
      }
    }
  } else if (existingReason) {
    existingReason.remove();
  }

  // Update version label
  const versionLabel = document.querySelector(".spec-version-label");
  if (versionLabel) versionLabel.textContent = `v${spec.experiment_version}`;

  // Update controls
  updateSpecControls(spec);
}

async function transitionSpec(targetStatus) {
  if (!currentSpec) return;
  const button = targetStatus === "ready" ? byId("spec-ready-btn") : byId("spec-confirm-btn");
  if (button) button.disabled = true;
  try {
    const response = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${currentSpec.experiment_id}/transition`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_status: targetStatus }),
      },
    );
    currentSpec = response;
    // Partial update: update status chip, parameter rows, and controls without full re-render
    const statusChip = document.querySelector(".spec-status-chip");
    if (statusChip) {
      statusChip.dataset.status = response.status;
      statusChip.textContent = specStatusLabels[response.status] || response.status;
    }
    for (const p of response.parameters) {
      updateParameterRowInPlace(p.parameter_id, response);
    }
    updateSpecControls(response);
    const label = specStatusLabels[targetStatus] || targetStatus;
    showWorkbenchToast(`实验规格已转换到「${label}」状态`, "success");
  } catch (error) {
    showWorkbenchToast(`状态转换失败：${error.message || error}`, "error");
    if (button) button.disabled = false;
  }
}

function deterministicSpecCaseId() {
  const sanitize = (value, fallback) => (
    String(value ?? fallback)
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, "-")
      .replace(/^-+|-+$/g, "") || fallback
  );
  const expId = sanitize(currentSpec.experiment_id, "exp");
  const expVersion = sanitize(currentSpec.experiment_version, "1");
  const identity = `${currentSpec.experiment_id}\u0000${currentSpec.experiment_version}`;
  let hash = 2166136261;
  for (let index = 0; index < identity.length; index += 1) {
    hash ^= identity.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const suffix = (hash >>> 0).toString(36).padStart(7, "0");
  return `spec-${expId.slice(0, 30)}-v${expVersion.slice(0, 12)}-${suffix}`.slice(0, 64);
}

async function saveSpecDraft() {
  if (!currentProject || !currentSpec) return;
  try {
    const spec = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${currentSpec.experiment_id}`,
    );
    currentSpec = spec;
    // Partial update: refresh parameter rows and controls without full re-render
    for (const p of spec.parameters) {
      updateParameterRowInPlace(p.parameter_id, spec);
    }
    updateSpecControls(spec);
    showWorkbenchToast("实验草案已保存", "success");
  } catch (error) {
    showWorkbenchToast(`保存失败：${error.message || error}`, "error");
  }
}

async function applyPendingParameterChanges() {
  if (!currentProject || !currentSpec) return;
  if (pendingParameterChanges.size === 0) {
    showWorkbenchToast("没有待保存的修改", "info");
    return;
  }

  const savedScrollY = window.scrollY;
  const updates = Array.from(pendingParameterChanges.values());

  try {
    const response = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${currentSpec.experiment_id}/parameters`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          experiment_version: currentSpec.experiment_version,
          updates: updates,
        }),
      },
    );

    const propagation = response._batch_propagation;
    currentSpec = response;
    pendingParameterChanges.clear();

    // Partial update: only refresh changed parameter rows
    const changedIds = new Set(updates.map(u => u.parameter_id));
    if (propagation?.auto_recomputed) {
      for (const id of propagation.auto_recomputed) changedIds.add(id);
    }
    if (propagation?.derived_updates) {
      for (const d of propagation.derived_updates) changedIds.add(d.parameter_id);
    }
    for (const id of changedIds) {
      updateParameterRowInPlace(id, response);
    }
    updateSpecControls(response);
    updateDirtyRowStyles();
    renderPendingChangeSummary();

    // Render propagation if available
    if (propagation) {
      renderBatchPropagation(propagation);
    }

    // Show toast with summary
    if (propagation?.summary) {
      showWorkbenchToast(propagation.summary, "success");
    } else {
      showWorkbenchToast(`已保存 ${updates.length} 个参数`, "success");
    }

    // Restore scroll position
    window.scrollTo({ top: savedScrollY, behavior: "instant" });
  } catch (error) {
    if (error.status === 409) {
      showWorkbenchToast("版本冲突：实验参数已被修改，请刷新后再提交", "error");
    } else {
      showWorkbenchToast(`批量保存失败：${error.message || error}`, "error");
    }
    window.scrollTo({ top: savedScrollY, behavior: "instant" });
  }
}

function discardPendingParameterChanges() {
  pendingParameterChanges.clear();
  // Re-render workbench to restore original values
  if (currentSpec) {
    for (const p of currentSpec.parameters) {
      updateParameterRowInPlace(p.parameter_id, currentSpec);
    }
  }
  updateDirtyRowStyles();
  renderPendingChangeSummary();
  showWorkbenchToast("已放弃未保存修改", "info");
}

async function acceptAllRecommendations() {
  if (!currentProject || !currentSpec) return;
  const savedScrollY = window.scrollY;
  try {
    const response = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${currentSpec.experiment_id}/accept-recommendations`,
      { method: "POST" },
    );
    const summary = response._acceptance_summary;
    currentSpec = response;
    // Update all parameter rows
    for (const p of response.parameters) {
      updateParameterRowInPlace(p.parameter_id, response);
    }
    updateSpecControls(response);
    if (summary?.summary) {
      showWorkbenchToast(summary.summary, "success");
    } else {
      showWorkbenchToast("已接受所有推荐值", "success");
    }
    window.scrollTo({ top: savedScrollY, behavior: "instant" });
  } catch (error) {
    showWorkbenchToast(`接受推荐值失败：${error.message || error}`, "error");
    window.scrollTo({ top: savedScrollY, behavior: "instant" });
  }
}

function renderBatchPropagation(propagation) {
  const node = byId("spec-propagation");
  if (!node) return;
  node.hidden = false;
  node.replaceChildren();
  const heading = document.createElement("p");
  heading.className = "spec-propagation-title";
  heading.textContent = "参数变更摘要";
  node.append(heading);

  if (propagation.summary) {
    const summary = document.createElement("pre");
    summary.textContent = propagation.summary;
    node.append(summary);
  }

  // Show directly modified parameters with old→new diff
  if (propagation.direct_updates?.length) {
    const section = document.createElement("div");
    section.className = "spec-change-section";
    const title = document.createElement("p");
    title.className = "spec-change-section-title";
    title.textContent = "直接修改";
    section.append(title);
    for (const update of propagation.direct_updates) {
      const row = document.createElement("div");
      row.className = "spec-change-row spec-change-direct";
      const label = document.createElement("span");
      label.className = "spec-change-label";
      label.textContent = update.parameter_id;
      const diff = document.createElement("span");
      diff.className = "spec-change-diff";
      const oldVal = document.createElement("span");
      oldVal.className = "spec-change-old";
      oldVal.textContent = text(update.old_value, "空");
      const arrow = document.createElement("span");
      arrow.className = "spec-change-arrow";
      arrow.textContent = " → ";
      const newVal = document.createElement("span");
      newVal.className = "spec-change-new";
      newVal.textContent = text(update.new_value, "空");
      diff.append(oldVal, arrow, newVal);
      row.append(label, diff);
      section.append(row);
    }
    node.append(section);
  }

  // Show derived updates with old→new diff
  if (propagation.derived_updates?.length) {
    const section = document.createElement("div");
    section.className = "spec-change-section";
    const title = document.createElement("p");
    title.className = "spec-change-section-title";
    title.textContent = "联动更新";
    section.append(title);
    for (const update of propagation.derived_updates) {
      const row = document.createElement("div");
      row.className = "spec-change-row spec-change-derived";
      const label = document.createElement("span");
      label.className = "spec-change-label";
      label.textContent = update.parameter_id;
      const diff = document.createElement("span");
      diff.className = "spec-change-diff";
      const newVal = document.createElement("span");
      newVal.className = "spec-change-new";
      newVal.textContent = text(update.new_value, "空");
      diff.append(newVal);
      if (update.reason) {
        const reason = document.createElement("span");
        reason.className = "spec-change-reason";
        reason.textContent = `（${update.reason}）`;
        diff.append(reason);
      }
      row.append(label, diff);
      section.append(row);
    }
    node.append(section);
  }

  // Show invalidated artifacts
  if (propagation.invalidated?.length) {
    const item = document.createElement("p");
    item.className = "spec-propagation-warning";
    item.textContent = `失效对象：${propagation.invalidated.join("，")}`;
    node.append(item);
  }

  // Show warnings
  if (propagation.warnings?.length) {
    for (const warning of propagation.warnings) {
      const item = document.createElement("p");
      item.className = "spec-propagation-warning";
      item.textContent = warning;
      node.append(item);
    }
  }
}

// WorkbenchTurn: send user message to WorkbenchAgent and render edit proposal
// Legacy endpoint: /natural-language-edit (replaced by /workbench-turn)
async function processWorkbenchTurn() {
  const input = byId("spec-nl-input");
  if (!input) return;
  const message = input.value.trim();
  if (!message) {
    showWorkbenchToast("请输入修改指令", "info");
    return;
  }

  if (!currentSpec) {
    showWorkbenchToast("请先创建实验草案", "warning");
    return;
  }

  const sessionId =
    (currentResearchSession && currentResearchSession.session_id) ||
    localStorage.getItem(storageKeys.researchSessionId) ||
    "default";
  const previewArea = byId("spec-nl-preview");
  if (previewArea) {
    previewArea.hidden = false;
    previewArea.innerHTML = '<div class="nl-loading">正在分析...</div>';
  }

  try {
    const response = await requestJson(
      `/api/research-sessions/${sessionId}/workbench-turn`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          experiment_id: currentSpec.experiment_id,
          experiment_version: currentSpec.experiment_version,
          message: message,
          current_spec_hash: null,
        }),
      },
    );

    currentEditProposal = response;
    renderEditProposalDiff(response);
  } catch (err) {
    if (previewArea) {
      previewArea.innerHTML = `<div class="nl-error">请求失败: ${escapeHtml(err.message || String(err))}</div>`;
    }
  }
}

// Backward-compatible alias for parseNaturalLanguageEdit
// (legacy: natural-language-edit endpoint, now replaced by workbench-turn)
async function parseNaturalLanguageEdit() {
  return processWorkbenchTurn();
}

function renderEditProposalDiff(proposal) {
  const previewArea = byId("spec-nl-preview");
  if (!previewArea) return;
  previewArea.hidden = false;
  previewArea.replaceChildren();

  // Handle clarification required
  if (proposal.edit_intent === "clarification_required" && proposal.clarification_question) {
    const card = document.createElement("div");
    card.className = "edit-proposal clarification";
    const header = document.createElement("div");
    header.className = "proposal-header";
    const icon = document.createElement("span");
    icon.className = "proposal-icon";
    icon.textContent = "\u2753";
    const summary = document.createElement("span");
    summary.className = "proposal-summary";
    summary.textContent = proposal.summary || "";
    header.append(icon, summary);
    const question = document.createElement("div");
    question.className = "clarification-question";
    question.textContent = proposal.clarification_question;
    card.append(header, question);
    previewArea.append(card);
    return;
  }

  const card = document.createElement("div");
  card.className = "edit-proposal";

  // Header
  const header = document.createElement("div");
  header.className = "proposal-header";
  const summary = document.createElement("span");
  summary.className = "proposal-summary";
  summary.textContent = proposal.summary || "";
  const intent = document.createElement("span");
  intent.className = "proposal-intent";
  intent.textContent = proposal.edit_intent || "";
  header.append(summary, intent);
  card.append(header);

  // Operations
  if (proposal.proposed_operations && proposal.proposed_operations.length > 0) {
    const opsContainer = document.createElement("div");
    opsContainer.className = "proposal-operations";
    const opIcons = {
      add_parameter: "\u2795",
      update_parameter: "\u270F\uFE0F",
      remove_parameter: "\u{1F5D1}\uFE0F",
      add_metric: "\u{1F4CA}",
      remove_metric: "\u{1F4CA}",
      set_physics: "\u2699\uFE0F",
      set_boundary_condition: "\u{1F527}",
      accept_recommendation: "\u2705",
    };
    proposal.proposed_operations.forEach((op, idx) => {
      const opDiv = document.createElement("div");
      opDiv.className = "edit-operation";
      opDiv.dataset.opIndex = String(idx);

      const label = document.createElement("label");
      label.className = "op-checkbox";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = true;
      checkbox.dataset.opIdx = String(idx);
      const opIcon = document.createElement("span");
      opIcon.className = "op-icon";
      opIcon.textContent = opIcons[op.operation] || "\u{1F4CB}";
      const opType = document.createElement("span");
      opType.className = "op-type";
      opType.textContent = op.operation || "";
      label.append(checkbox, opIcon, opType);
      if (op.reason) {
        const reasonInline = document.createElement("span");
        reasonInline.className = "op-reason-inline";
        reasonInline.textContent = `(${op.reason})`;
        label.append(reasonInline);
      }
      opDiv.append(label);

      // Detail
      if (op.parameter) {
        const detail = document.createElement("div");
        detail.className = "op-detail";
        const strong = document.createElement("strong");
        strong.textContent = op.parameter.display_name || op.parameter.parameter_id || "";
        detail.append(strong);
        if (op.parameter.value !== null && op.parameter.value !== undefined) {
          const valSpan = document.createElement("span");
          valSpan.textContent = ` = ${op.parameter.value} ${op.parameter.unit || ""}`;
          detail.append(valSpan);
        }
        if (op.parameter.status) {
          const status = document.createElement("span");
          status.className = "op-status";
          status.textContent = op.parameter.status;
          detail.append(status);
        }
        if (op.parameter.reason) {
          const reason = document.createElement("div");
          reason.className = "op-reason";
          reason.textContent = op.parameter.reason;
          detail.append(reason);
        }
        opDiv.append(detail);
      } else if (op.metric) {
        const detail = document.createElement("div");
        detail.className = "op-detail";
        const strong = document.createElement("strong");
        strong.textContent = op.metric.display_name || op.metric.metric_id || "";
        detail.append(strong);
        if (op.metric.required_data && op.metric.required_data.length) {
          const req = document.createElement("div");
          req.className = "op-reason";
          req.textContent = `需要: ${op.metric.required_data.join(", ")}`;
          detail.append(req);
        }
        if (op.metric.reason) {
          const reason = document.createElement("div");
          reason.className = "op-reason";
          reason.textContent = op.metric.reason;
          detail.append(reason);
        }
        opDiv.append(detail);
      } else if (op.value !== null && op.value !== undefined) {
        const detail = document.createElement("div");
        detail.className = "op-detail";
        detail.textContent = `值: ${op.value} ${op.unit || ""}`;
        opDiv.append(detail);
      }

      opsContainer.append(opDiv);
    });
    card.append(opsContainer);
  }

  // Invalidates section
  if (proposal.invalidates && proposal.invalidates.length > 0) {
    const invDiv = document.createElement("div");
    invDiv.className = "edit-invalidates";
    const invLabel = document.createElement("span");
    invLabel.className = "invalidates-label";
    invLabel.textContent = "将失效:";
    invDiv.append(invLabel);
    for (const inv of proposal.invalidates) {
      const tag = document.createElement("span");
      tag.className = "invalidate-tag";
      tag.textContent = inv;
      invDiv.append(tag);
    }
    card.append(invDiv);
  }

  // Warnings section
  if (proposal.warnings_preview && proposal.warnings_preview.length > 0) {
    const warnDiv = document.createElement("div");
    warnDiv.className = "edit-warnings";
    for (const w of proposal.warnings_preview) {
      const item = document.createElement("div");
      item.className = "warning-item";
      item.textContent = `\u26A0\uFE0F ${w.field || ""}: ${w.message || ""}`;
      warnDiv.append(item);
    }
    card.append(warnDiv);
  }

  // Action buttons
  const showApply =
    proposal.requires_confirmation !== false &&
    proposal.edit_intent !== "clarification_required" &&
    proposal.proposed_operations &&
    proposal.proposed_operations.length > 0;

  if (showApply) {
    const actions = document.createElement("div");
    actions.className = "proposal-actions";
    const applyBtn = document.createElement("button");
    applyBtn.type = "button";
    applyBtn.className = "button button-primary";
    applyBtn.textContent = "确认应用";
    applyBtn.addEventListener("click", () => applyEditProposal(proposal.proposal_id));
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "button button-secondary";
    cancelBtn.textContent = "取消";
    cancelBtn.addEventListener("click", () => cancelEditProposal());
    actions.append(applyBtn, cancelBtn);
    card.append(actions);
  }

  previewArea.append(card);
}

async function applyEditProposal(proposalId) {
  if (!currentSpec) return;

  // Get accepted operation indices from checkboxes
  const checkboxes = document.querySelectorAll(
    "#spec-nl-preview input[type=\"checkbox\"][data-op-idx]",
  );
  const acceptedIndices = Array.from(checkboxes)
    .filter((cb) => cb.checked)
    .map((cb) => parseInt(cb.dataset.opIdx, 10));

  if (acceptedIndices.length === 0) {
    showWorkbenchToast("请至少选择一个操作", "warning");
    return;
  }

  try {
    const response = await requestJson(
      `/api/experiment-specs/${currentSpec.experiment_id}/apply-edit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          experiment_version: currentSpec.experiment_version,
          proposal_id: proposalId,
          accepted_operation_indices: acceptedIndices,
        }),
      },
    );

    // Update spec
    currentSpec = response.updated_spec || response.spec;
    renderSpecWorkbench(currentSpec);

    // Show change summary
    if (response.change_summary) {
      renderBatchPropagation(response.change_summary);
    }

    // Clear preview
    const preview = byId("spec-nl-preview");
    if (preview) {
      preview.hidden = true;
      preview.replaceChildren();
    }
    const input = byId("spec-nl-input");
    if (input) input.value = "";

    showWorkbenchToast("修改已应用", "success");
  } catch (err) {
    showWorkbenchToast(`应用失败: ${err.message || err}`, "error");
  }
}

function cancelEditProposal() {
  const preview = byId("spec-nl-preview");
  if (preview) {
    preview.hidden = true;
    preview.replaceChildren();
  }
  const input = byId("spec-nl-input");
  if (input) input.value = "";
  currentEditProposal = null;
}

function renderNLPreview(response) {
  const preview = byId("spec-nl-preview");
  if (!preview) return;
  preview.hidden = false;
  preview.replaceChildren();

  if (!response.proposed_changes?.length) {
    const msg = document.createElement("p");
    msg.className = "spec-nl-no-match";
    msg.textContent = "未识别到可修改的参数";
    preview.append(msg);
    return;
  }

  const heading = document.createElement("p");
  heading.className = "spec-nl-preview-title";
  heading.textContent = `识别到 ${response.proposed_changes.length} 个参数修改：`;
  preview.append(heading);

  for (const change of response.proposed_changes) {
    const row = document.createElement("div");
    row.className = "spec-change-row spec-change-direct";
    const label = document.createElement("span");
    label.className = "spec-change-label";
    label.textContent = `${change.display_name} (${change.parameter_id})`;
    const diff = document.createElement("span");
    diff.className = "spec-change-diff";
    const oldVal = document.createElement("span");
    oldVal.className = "spec-change-old";
    oldVal.textContent = text(change.old_value, "空");
    const arrow = document.createElement("span");
    arrow.className = "spec-change-arrow";
    arrow.textContent = " → ";
    const newVal = document.createElement("span");
    newVal.className = "spec-change-new";
    newVal.textContent = `${change.new_value}${change.unit ? " " + change.unit : ""}`;
    diff.append(oldVal, arrow, newVal);
    row.append(label, diff);
    preview.append(row);
  }

  if (response.derived_updates_preview?.length) {
    const derivedHeading = document.createElement("p");
    derivedHeading.className = "spec-nl-preview-derived";
    derivedHeading.textContent = "将联动更新：";
    preview.append(derivedHeading);
    for (const d of response.derived_updates_preview) {
      const row = document.createElement("div");
      row.className = "spec-change-row spec-change-derived";
      const label = document.createElement("span");
      label.className = "spec-change-label";
      label.textContent = d.display_name;
      const note = document.createElement("span");
      note.className = "spec-change-reason";
      note.textContent = d.reason;
      row.append(label, note);
      preview.append(row);
    }
  }

  if (response.unmatched_segments?.length) {
    const unmatched = document.createElement("p");
    unmatched.className = "spec-propagation-warning";
    unmatched.textContent = `未识别：${response.unmatched_segments.join("；")}`;
    preview.append(unmatched);
  }

  // Add apply button
  const applyBtn = document.createElement("button");
  applyBtn.type = "button";
  applyBtn.className = "button button-primary";
  applyBtn.textContent = "应用这些修改";
  applyBtn.addEventListener("click", () => applyNLChanges(response.proposed_changes));
  preview.append(applyBtn);
}

async function applyNLChanges(proposedChanges) {
  if (!proposedChanges?.length) return;
  // Populate pendingParameterChanges from proposed changes
  for (const change of proposedChanges) {
    pendingParameterChanges.set(change.parameter_id, {
      parameter_id: change.parameter_id,
      value: change.new_value,
      unit: change.unit,
    });
  }
  updateDirtyRowStyles();
  renderPendingChangeSummary();
  // Clear NL input and preview
  const input = byId("spec-nl-input");
  if (input) input.value = "";
  const preview = byId("spec-nl-preview");
  if (preview) { preview.hidden = true; preview.replaceChildren(); }
  showWorkbenchToast(`已解析 ${proposedChanges.length} 个修改，请点击「应用修改」保存`, "info");
}

// 生成 Case：将已确认的实验规格编译为可运行的算例归档
async function compileSpec() {
  if (!currentProject || !currentSpec) return false;
  if (currentSpec.status !== "confirmed") {
    renderError("生成 Case", new Error("仅已确认的实验规格可以生成 Case"));
    return false;
  }
  // Pre-compile validation: surface blocking issues before compiling.
  try {
    const preCheck = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${currentSpec.experiment_id}/pre-check`,
    );
    if (!preCheck.can_compile) {
      currentSpec = { ...currentSpec, _blocking_issues: preCheck.blocking_issues };
      updateDisabledReason(currentSpec);
      const messages = preCheck.blocking_issues.map(i => i.message).join("；");
      showWorkbenchToast(`无法生成 Case：${messages}`, "error");
      return false;
    }
    currentSpec = { ...currentSpec, _blocking_issues: [] };
  } catch (error) {
    showWorkbenchToast(`预检查失败：${error.message || error}`, "error");
    return false;
  }
  specCompiling = true;
  renderSpecWorkbench(currentSpec);
  try {
    currentCompilation = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${currentSpec.experiment_id}/compile`,
      { method: "POST" },
    );
    // 编译端点会将规格状态置为 compiling，本地同步以刷新工作台按钮
    currentSpec = { ...currentSpec, status: "compiling" };
    const preview = $("[data-compile-preview]");
    if (preview) {
      preview.textContent = `SHA-256：${currentCompilation.archive_sha256}\n求解器：${currentCompilation.manifest.solver}\n预处理：${currentCompilation.preprocessing.join(" → ")}`;
    }
    appendConversation("assistant", `确定性编译与安全校验完成：${currentCompilation.archive_sha256}`, "workflow-event");
    renderSpecWorkbench(currentSpec);
    return true;
  } catch (error) {
    renderError("生成 Case", error);
    currentSpec = { ...currentSpec, status: "confirmed" };
    renderSpecWorkbench(currentSpec);
    return false;
  } finally {
    specCompiling = false;
  }
}

// 创建新版本：克隆已确认/不可变实验规格为可编辑草稿
async function cloneSpec() {
  if (!currentProject || !currentSpec) return;
  const ok = window.confirm(
    "当前实验版本已确认。修改参数将创建新版本，不影响当前版本和结果。"
  );
  if (!ok) return;
  try {
    const cloned = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-specs/${currentSpec.experiment_id}/clone`,
      { method: "POST" },
    );
    currentSpec = cloned;
    currentCompilation = null;
    pendingParameterChanges.clear();
    renderSpecWorkbench(cloned);
    showWorkbenchToast(`已创建新版本 v${cloned.experiment_version}`, "success");
  } catch (error) {
    showWorkbenchToast(`创建新版本失败：${error.message || error}`, "error");
  }
}

// 查看运行状态：展示当前运行任务信息
function showRunStatus() {
  if (!currentSpec) return;
  const jobId = currentSpec._job_id || currentSpec._external_job_id || "（未记录）";
  showWorkbenchToast(`运行中任务：${jobId}`, "info");
}

// 查看分析报告：跳转或提示分析报告
function showAnalysisReport() {
  if (!currentSpec) return;
  showWorkbenchToast("分析报告生成中，请稍后查看分析模块。", "info");
}

// 查看缺失能力：展示缺失的代码扩展能力
function showMissingCapabilities() {
  if (!currentSpec) return;
  const exts = currentSpec.code_extensions || [];
  if (exts.length) {
    const names = exts.map(e => e.capability_id || e.id || e.name || "未知").join("，");
    showWorkbenchToast(`缺失能力：${names}`, "info");
  } else {
    showWorkbenchToast("当前无缺失能力记录。", "info");
  }
}

// 提交运行：基于已编译归档执行 Gate 2 审批与远程提交
async function submitSpec() {
  if (!canStartExperiment(activeTask)) {
    const warning = "已有实验正在运行，请等待当前任务结束后再提交运行。";
    setStatus(warning);
    appendConversation("assistant", warning, "workflow-event");
    return false;
  }
  if (!currentProject || !currentSpec || !currentCompilation || !selectedTarget) {
    if (!selectedTarget) renderError("提交运行", new Error("请先选择可用的执行平台"));
    return false;
  }
  confirmationActive = true;
  renderTaskCard({ phase: "submitting", targetId: selectedTarget, lastUpdated: new Date().toLocaleString() });
  try {
    currentProject = await requestJson(`/api/projects/${currentProject.project_id}/approvals`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gate: "GATE_2",
        decision: "approve",
        actor: "researcher",
        subject_version: currentProject.version,
        plan_id: currentSpec.experiment_id,
        plan_version: currentSpec.experiment_version,
        archive_sha256: currentCompilation.archive_sha256,
      }),
    });
    appendConversation("assistant", "Gate 2 已绑定当前实验规格与归档摘要。", "workflow-event");

    const caseId = deterministicSpecCaseId();
    persist(storageKeys.caseId, caseId);
    persist(storageKeys.targetId, selectedTarget);
    renderTaskCard({
      phase: "submitting",
      targetId: selectedTarget,
      lastUpdated: new Date().toLocaleString(),
    });
    console.warn(
      "[DEPRECATED] POST /experiment-plans/{id}/submit is deprecated. " +
      "Use POST /experiment-specs/{id}/ingest for the new analysis pipeline.",
    );
    const response = await requestJson(
      `/api/projects/${currentProject.project_id}/experiment-plans/${currentSpec.experiment_id}/submit`,
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
    currentSpec = { ...currentSpec, status: "running" };
    renderTaskCard({
      phase: "submitted",
      jobId: externalJobId,
      pid: job.pid,
      targetId: selectedTarget,
      submittedAt: job.submitted_at,
      lastUpdated: new Date().toLocaleString(),
    });
    renderSpecWorkbench(currentSpec);
    startPolling(() => pollSpecExperiment(
      externalJobId,
      selectedTarget,
      currentProject.project_id,
      currentSpec.experiment_id,
      caseId,
    ));
    return true;
  } catch (error) {
    const assignedJobId = activeTask?.jobId;
    renderError("提交运行", error, {
      phase: "failed",
      jobId: assignedJobId,
      targetId: selectedTarget,
      lastUpdated: new Date().toLocaleString(),
    });
    return false;
  } finally {
    confirmationActive = false;
  }
}

/** @deprecated 保留兼容：依次执行生成 Case 与提交运行。新流程请使用 compileSpec()/submitSpec()。 */
async function compileAndSubmitSpec() {
  const compiled = await compileSpec();
  if (!compiled) return;
  await submitSpec();
}

async function pollSpecExperiment(jobId, targetId, projectId, experimentId, caseId) {
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
      const results = await requestJson(plannedResultUrl({
        projectId, planId: experimentId, caseId, targetId, action: "results",
      }));
      currentProject = results.project || currentProject;
      renderResultsCard(results, {
        source: "planned",
        identity: { projectId, planId: experimentId, caseId, targetId },
      });
      return;
    }
    schedulePoll(() => pollSpecExperiment(jobId, targetId, projectId, experimentId, caseId));
  } catch (error) {
    renderTaskCard({
      phase: activeTask?.phase || "submitted",
      jobId,
      targetId,
      pid: activeTask?.pid,
      warning: `状态查询暂时失败：${error.message}。将自动重试。`,
      lastUpdated: new Date().toLocaleString(),
    });
    schedulePoll(() => pollSpecExperiment(jobId, targetId, projectId, experimentId, caseId));
  }
}

function renderMetricQualityChecks(card, results) {
  const collection = results?.collection || {};
  const solver = collection.solver || {};
  const observables = collection.observables || results?.summary?.observables || {};
  const finalResiduals = solver.final_residuals || results?.summary?.final_residuals || {};
  const residualEntries = Object.entries(finalResiduals);
  if (!residualEntries.length && !observables.mass_imbalance && !observables.max_courant) return;

  const section = document.createElement("section");
  section.className = "metric-quality-checks";
  const heading = document.createElement("h3");
  heading.textContent = "指标质量检查";
  section.append(heading);

  const grid = document.createElement("div");
  grid.className = "metric-quality-grid";

  if (residualEntries.length) {
    const maxResidual = Math.max(...residualEntries.map(([, v]) => Number(v) || 0));
    const passed = maxResidual <= 1e-4;
    const warning = maxResidual <= 1e-3;
    const item = document.createElement("div");
    item.className = "metric-quality-item";
    item.dataset.status = passed ? "passed" : warning ? "warning" : "failed";
    const label = document.createElement("span");
    label.textContent = "残差容差";
    const value = document.createElement("strong");
    value.textContent = maxResidual.toExponential(2);
    const detail = document.createElement("small");
    detail.textContent = `阈值 1.0e-04 · ${passed ? "通过" : warning ? "警告" : "未通过"}`;
    item.append(label, value, detail);
    grid.append(item);
  }

  const inletFlow = Number(solver.inlet_mass_flow ?? observables.inlet_mass_flow);
  const outletFlow = Number(solver.outlet_mass_flow ?? observables.outlet_mass_flow);
  if (Number.isFinite(inletFlow) && Number.isFinite(outletFlow) && inletFlow !== 0) {
    const imbalance = ((inletFlow - outletFlow) / inletFlow) * 100;
    const passed = Math.abs(imbalance) <= 1.0;
    const warning = Math.abs(imbalance) <= 2.0;
    const item = document.createElement("div");
    item.className = "metric-quality-item";
    item.dataset.status = passed ? "passed" : warning ? "warning" : "failed";
    const label = document.createElement("span");
    label.textContent = "质量守恒";
    const value = document.createElement("strong");
    value.textContent = `${imbalance.toFixed(3)}%`;
    const detail = document.createElement("small");
    detail.textContent = `阈值 1.0% · ${passed ? "通过" : warning ? "警告" : "未通过"}`;
    item.append(label, value, detail);
    grid.append(item);
  }

  const maxCourant = Number(observables.max_courant ?? observables.courant_number);
  if (Number.isFinite(maxCourant)) {
    const passed = maxCourant <= 1.0;
    const warning = maxCourant <= 2.0;
    const item = document.createElement("div");
    item.className = "metric-quality-item";
    item.dataset.status = passed ? "passed" : warning ? "warning" : "failed";
    const label = document.createElement("span");
    label.textContent = "Courant 数";
    const value = document.createElement("strong");
    value.textContent = maxCourant.toFixed(3);
    const detail = document.createElement("small");
    detail.textContent = `阈值 1.0 · ${passed ? "通过" : warning ? "警告" : "未通过"}`;
    item.append(label, value, detail);
    grid.append(item);
  }

  section.append(grid);
  card.append(section);
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
  if (workflowMode !== "legacy") {
    console.warn("confirmAndSubmitPlan is deprecated in V2 workflow; use ExperimentSpec workflow instead");
    return;
  }
  // 如果存在研究会话，使用新的参数工作台流程，无需旧的“确认并提交”流程
  if (currentResearchSession) {
    return; // 新流程不需要这个按钮
  }
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
  try {
    await prepareProjectForGateTwo();
    const spec = await createExperimentSpec(currentPlan.plan_id);
    renderSpecWorkbench(spec);
    setStatus("实验规格已创建。请审阅参数后依次点击「准备就绪」和「确认实验」。");
    appendConversation("assistant", "已从实验计划创建结构化实验规格，请审阅参数工作台。", "workflow-event");
  } catch (error) {
    renderError("创建实验规格", error);
  } finally {
    confirmationActive = false;
    if (button && !currentSpec) button.disabled = false;
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

function postprocessIdentity() {
  const projectId = currentProject?.project_id || localStorage.getItem(storageKeys.projectId) || "";
  const planId = currentPlan?.plan_id || localStorage.getItem(storageKeys.planId) || "";
  const caseId = localStorage.getItem(storageKeys.caseId) || "";
  const targetId = selectedTarget || localStorage.getItem(storageKeys.targetId) || "";
  return { projectId, planId, caseId, targetId };
}

async function fetchCurrentPostprocessResults(expectedSessionKey) {
  const identity = postprocessIdentity();
  if (!identity.projectId || !identity.planId || !identity.caseId || !identity.targetId) {
    throw new Error("missing-result-identity");
  }
  const results = await requestJson(plannedResultUrl({ ...identity, action: "results" }));
  const current = postprocessIdentity();
  const identityChanged = Object.keys(identity).some((key) => identity[key] !== current[key]);
  if (identityChanged || expectedSessionKey !== postprocessSessionKey()) {
    throw new Error("stale-result-session");
  }
  latestResults = normalizeResultPayload(results, {
    source: "planned",
    identity,
  });
  postprocessSessionVersion += 1;
  return latestResults.postprocessPayload;
}

function postprocessSessionKey() {
  const identity = postprocessIdentity();
  return `${identity.projectId}:${identity.planId}:${identity.caseId}:${identity.targetId}:${postprocessSessionVersion}`;
}

function bindPostprocessButton(button, root, results = () => latestResults) {
  return bindPostprocessReveal({
    button,
    root,
    getRequest: () => {
      const resultContext = results();
      const sessionKey = postprocessSessionKey();
      return {
        results: resultContext?.postprocessPayload || resultContext || null,
        fetchResults: () => fetchCurrentPostprocessResults(sessionKey),
        sessionKey,
      };
    },
  });
}

function renderResultsCard(results, { source = "planned", identity = null } = {}) {
  latestResults = normalizeResultPayload(results, { source, identity });
  postprocessSessionVersion += 1;
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
  const summary = latestResults.summary;
  const body = document.createElement("p");
  body.textContent = `网格 ${summary.mesh_passed ? "通过" : "未通过"} · 求解 ${summary.solver_completed ? "完成" : "未完成"} · ${text(summary.cells)} 个单元`;
  const postButton = document.createElement("button");
  postButton.type = "button";
  postButton.className = "button button-secondary";
  postButton.textContent = "查看浏览器后处理";
  const postprocessRoot = document.createElement("section");
  postprocessRoot.className = "postprocess-results";
  postprocessRoot.hidden = true;
  postprocessRoot.setAttribute("aria-live", "polite");
  postprocessRoot.setAttribute("aria-busy", "false");
  bindPostprocessButton(postButton, postprocessRoot);
  const analyzeButton = document.createElement("button");
  analyzeButton.type = "button";
  analyzeButton.className = "button button-primary";
  analyzeButton.textContent = "实验结果分析与报告";
  analyzeButton.addEventListener("click", analyzeExperimentResults);
  const availability = analysisAvailability({
    resultContext: latestResults,
    ...postprocessIdentity(),
  });
  analyzeButton.disabled = !availability.allowed;
  if (!availability.allowed) analyzeButton.title = availability.message;
  const analysisNote = document.createElement("p");
  analysisNote.className = "result-analysis-note";
  analysisNote.textContent = availability.allowed ? "" : availability.message;
  analysisNote.hidden = availability.allowed;
  card.append(body, postButton, analyzeButton, analysisNote, postprocessRoot);
  renderMetricQualityChecks(card, latestResults);
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
      const results = await requestJson(plannedResultUrl({
        projectId, planId, caseId, targetId, action: "results",
      }));
      currentProject = results.project || currentProject;
      renderResultsCard(results, {
        source: "planned",
        identity: { projectId, planId, caseId, targetId },
      });
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
    card = makeCard("work-card analysis-card", "实验结果分析与报告（证据绑定）");
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

async function analyzeExperimentResults(event) {
  const analyzeButton = event?.currentTarget;
  const resultContext = latestResults;
  const identity = resultContext?.boundIdentity;
  const generation = postprocessSessionVersion;
  const sessionKey = `${generation}:${JSON.stringify(identity)}`;
  const isCurrent = () => (
    latestResults === resultContext
    && postprocessSessionVersion === generation
    && analysisAvailability({ resultContext, ...postprocessIdentity() }).allowed
  );
  const availability = analysisAvailability({ resultContext, ...postprocessIdentity() });
  if (!availability.allowed) {
    renderError("模型结果分析", new Error(availability.message));
    return;
  }
  try {
    await analysisRequests.run({
      sessionKey,
      request: () => requestJson(
        plannedResultUrl({ ...identity, action: "analysis" }),
        { method: "POST" },
      ),
      isCurrent,
      onResult: renderExperimentAnalysis,
      onPending: (pending) => {
        if (isCurrent() && analyzeButton) analyzeButton.disabled = pending;
      },
    });
  } catch (error) {
    if (isCurrent()) renderError("模型结果分析", error);
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
  const projectId = localStorage.getItem(storageKeys.projectId)