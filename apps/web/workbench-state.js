export const storageKeys = Object.freeze({
  projectId: "fluid-scientist-project-id",
  planId: "fluid-scientist-plan-id",
  caseId: "fluid-scientist-case-id",
  targetId: "fluid-scientist-target-id",
  operationId: "fluid-scientist-operation-id",
  specId: "fluid-scientist-spec-id",
});

const phaseViews = Object.freeze({
  preparing: Object.freeze({
    label: "准备中",
    tone: "active",
    percent: 10,
  }),
  submitting: Object.freeze({
    label: "正在提交",
    tone: "active",
    percent: 30,
  }),
  submitted: Object.freeze({
    label: "已到达工作站",
    tone: "active",
    percent: 40,
  }),
  mesh_check: Object.freeze({
    label: "网格检查",
    tone: "active",
    percent: 55,
  }),
  solving: Object.freeze({
    label: "正在求解",
    tone: "active",
    percent: 70,
  }),
  collecting: Object.freeze({
    label: "正在收集结果",
    tone: "active",
    percent: 85,
  }),
  completed: Object.freeze({
    label: "已完成",
    tone: "success",
    percent: 100,
  }),
  failed: Object.freeze({
    label: "失败",
    tone: "danger",
    percent: 100,
  }),
  cancelled: Object.freeze({
    label: "已取消",
    tone: "muted",
    percent: 100,
  }),
});

const remotePhases = new Set([
  "submitted",
  "mesh_check",
  "solving",
  "collecting",
  "completed",
  "cancelled",
]);

const activePhases = new Set([
  "preparing",
  "submitting",
  "submitted",
  "mesh_check",
  "solving",
  "collecting",
]);

const terminalRunPhases = new Set(["completed", "failed", "cancelled"]);

export function shouldCreateFreshProject(project, task) {
  return (
    project?.workflow_state === "PILOT_VERIFIED" ||
    terminalRunPhases.has(task?.phase)
  );
}

export function canStartExperiment(task) {
  return !activePhases.has(task?.phase);
}

export function buildPlanRequest(question, projectId, targetId) {
  const request = {
    question,
    project_id: projectId,
  };
  if (targetId) {
    request.target_id = targetId;
  }
  return request;
}

export function restoredPlanForProject(plan, project) {
  return plan?.project_id === project?.project_id ? plan : null;
}

function taskDetail(task) {
  if (task.phase === "preparing") {
    return "正在准备编译与审批";
  }
  if (task.phase === "submitting") {
    return task.targetId ? `目标：${task.targetId}` : "正在等待远程任务标识";
  }

  const details = task.jobId ? [`任务 ${task.jobId}`] : [];
  if (task.pid !== undefined && task.pid !== null) {
    details.push(`远程 PID ${task.pid}`);
  }
  if (task.phase === "failed" && task.error) {
    details.push(String(task.error));
  }
  if (task.phase === "failed" && !task.error) {
    details.push("未提供错误详情");
  }
  return details.join(" · ");
}

export function taskView(task) {
  const view = phaseViews[task?.phase];
  if (!view) {
    throw new Error(`Unknown task phase: ${task?.phase ?? "missing"}`);
  }
  if (remotePhases.has(task.phase) && !task.jobId) {
    throw new Error("Remote state requires Job ID");
  }

  return {
    ...view,
    detail: taskDetail(task),
  };
}
