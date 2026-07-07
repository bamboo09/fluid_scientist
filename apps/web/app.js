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
let currentSpec = null;
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
  derived: "派生计算",
  system_recommended: "系统推荐",
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
  } else if (result.type === "unsupported") {
    renderUnsupportedCard(result);
  }
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

    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "实验规格正在生成中，请稍后刷新页面。";
    card.appendChild(note);

    container.appendChild(card);
  }
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
  const question = researchQuestionInput?.value.trim() || "";
  if (!question || !modelConfiguration.configured) return;
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
    input.addEventListener("change", () => updateSpecParameter(param.parameter_id, input.value));
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

  row.append(label, valueContainer, meta);
  return row;
}

function updateSpecControls(spec) {
  const saveBtn = byId("spec-save-btn");
  const readyBtn = byId("spec-ready-btn");
  const confirmBtn = byId("spec-confirm-btn");
  const compileBtn = byId("spec-compile-btn");
  const submitBtn = byId("spec-submit-btn");
  if (!readyBtn || !confirmBtn) return;
  const status = spec?.status;
  const editable = isSpecEditable(spec);
  const hasCompilation = !!currentCompilation;
  const submitted = ["running", "completed", "failed", "rejected"].includes(status);

  // 保存草案：草稿/就绪状态下可保存
  if (saveBtn) {
    saveBtn.hidden = !editable;
    saveBtn.disabled = !editable;
  }

  // 准备就绪：draft → ready
  readyBtn.hidden = status !== "draft";
  readyBtn.disabled = status !== "draft";

  // 确认实验版本：ready → confirmed
  confirmBtn.hidden = status !== "ready";
  confirmBtn.disabled = status !== "ready";

  // 生成 Case：confirmed 时编译；编译进行中显示"正在编译..."
  if (compileBtn) {
    if (specCompiling) {
      compileBtn.hidden = false;
      compileBtn.disabled = true;
      compileBtn.textContent = "正在编译...";
    } else {
      const canCompile = status === "confirmed" && !hasCompilation;
      compileBtn.hidden = !canCompile;
      compileBtn.disabled = !canCompile;
      compileBtn.textContent = "生成 Case";
    }
  }

  // 提交运行：已有编译产物且尚未进入运行态
  if (submitBtn) {
    const canSubmit = hasCompilation && !submitted && !specCompiling;
    submitBtn.hidden = !canSubmit;
    submitBtn.disabled = !canSubmit;
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

  const propagation = document.createElement("div");
  propagation.className = "spec-propagation";
  propagation.id = "spec-propagation";
  propagation.hidden = true;
  card.append(propagation);

  const actions = document.createElement("div");
  actions.className = "spec-actions card-actions";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "button button-quiet";
  saveBtn.id = "spec-save-btn";
  saveBtn.textContent = "保存草案";
  saveBtn.addEventListener("click", () => saveSpecDraft());
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
  actions.append(saveBtn, readyBtn, confirmBtn, compileBtn, submitBtn);
  card.append(actions);

  updateSpecControls(spec);

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
    renderError("参数更新", error);
    updateParameterRowInPlace(parameterId, currentSpec);
    window.scrollTo({ top: savedScrollY, behavior: "instant" });
  }
}

function updateParameterRowInPlace(parameterId, spec) {
  // 只更新参数行，不重建整个工作台
  for (const p of spec.parameters) {
    const existingRow = document.querySelector(
      `.spec-param-row[data-param-id="${p.parameter_id}"]`
    );
    if (existingRow) {
      const freshRow = renderParameterRow(p, spec);
      existingRow.replaceWith(freshRow);
    }
  }

  // 更新版本号
  const versionLabel = document.querySelector(".spec-version-label");
  if (versionLabel) versionLabel.textContent = `v${spec.experiment_version}`;

  // 更新控件状态
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
    renderSpecWorkbench(currentSpec);
    const label = specStatusLabels[targetStatus] || targetStatus;
    appendConversation("assistant", `实验规格已转换到「${label}」状态。`, "workflow-event");
  } catch (error) {
    renderError("状态转换", error);
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
    renderSpecWorkbench(currentSpec);
    appendConversation("assistant", "实验草案已保存。", "workflow-event");
  } catch (error) {
    renderError("保存草案", error);
  }
}

// 生成 Case：将已确认的实验规格编译为可运行的算例归档
async function compileSpec() {
  if (!currentProject || !currentSpec) return false;
  if (currentSpec.status !== "confirmed") {
    renderError("生成 Case", new Error("仅已确认的实验规格可以生成 Case"));
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
  const projectId = localStorage.getItem(storageKeys.projectId);
  const planId = localStorage.getItem(storageKeys.planId);
  const caseId = localStorage.getItem(storageKeys.caseId);
  const targetId = localStorage.getItem(storageKeys.targetId);
  const specId = localStorage.getItem(storageKeys.specId);
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
        currentSpec = null;
        localStorage.removeItem(storageKeys.planId);
        localStorage.removeItem(storageKeys.caseId);
        localStorage.removeItem(storageKeys.specId);
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
    if (specId && currentProject) {
      try {
        currentSpec = await requestJson(
          `/api/projects/${currentProject.project_id}/experiment-specs/${specId}`,
        );
        if (currentSpec && ["draft", "ready", "confirmed", "compiling"].includes(currentSpec.status)) {
          renderSpecWorkbench(currentSpec);
        }
      } catch {
        currentSpec = null;
        localStorage.removeItem(storageKeys.specId);
      }
    }
    if (targetId) {
      selectedTarget = targetId;
      if (targetSelect) targetSelect.value = targetId;
    }
    updateContext();
    const savedSessionId = localStorage.getItem(storageKeys.researchSessionId);
    if (savedSessionId) {
      try {
        const sessionResponse = await fetch(`/api/research-sessions/${savedSessionId}`);
        if (sessionResponse.ok) {
          const session = await sessionResponse.json();
          currentResearchSession = { session_id: savedSessionId, ...session };
          if (session && session.type) handleResearchTurnResult(session);
        }
      } catch {
        // 忽略研究会话恢复失败
      }
    }
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
      renderResultsCard(collection, { source: "legacy_custom" });
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
    if (!modelId) return;
    const current = modelId.value.trim();
    const allDefaults = Object.values(modelDefaults);
    const isEmpty = !current;
    const isOtherProviderDefault = allDefaults.includes(current) && current !== modelDefaults[modelProvider.value];
    if (isEmpty || isOtherProviderDefault) {
      modelId.value = modelDefaults[modelProvider.value] || "";
    }
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
  const staticPostprocessButton = byId("view-postprocess");
  const staticPostprocessRoot = byId("postprocess-results");
  if (staticPostprocessButton && staticPostprocessRoot) {
    bindPostprocessButton(staticPostprocessButton, staticPostprocessRoot);
  }
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
