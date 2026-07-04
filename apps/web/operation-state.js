const stageViews = Object.freeze({
  queued: Object.freeze({ label: "已进入队列", percent: 8 }),
  model_planning: Object.freeze({ label: "模型正在设计实验", percent: 36 }),
  schema_correction: Object.freeze({ label: "正在校正实验结构", percent: 58 }),
  storing_plan: Object.freeze({ label: "正在保存实验计划", percent: 86 }),
  complete: Object.freeze({ label: "实验计划已保存", percent: 100 }),
});

const stateViews = Object.freeze({
  queued: Object.freeze({ label: "等待规划", tone: "queued" }),
  running: Object.freeze({ label: "规划中", tone: "active" }),
  succeeded: Object.freeze({ label: "规划完成", tone: "success" }),
  failed: Object.freeze({ label: "规划未完成", tone: "danger" }),
  cancelled: Object.freeze({ label: "已取消规划", tone: "muted" }),
});

const terminalStates = new Set(["succeeded", "failed", "cancelled"]);

export function operationView(operation = {}) {
  const state = stateViews[operation.state];
  if (!state) {
    return {
      label: "状态更新中",
      stageLabel: "正在读取最新状态",
      tone: "muted",
      percent: 0,
      indeterminate: false,
      terminal: false,
      canCancel: false,
      canRetry: false,
    };
  }

  const terminal = terminalStates.has(operation.state);
  const stage = stageViews[operation.stage] || {
    label: terminal ? "操作已结束" : "正在处理",
    percent: terminal ? 100 : 18,
  };
  return {
    ...state,
    stageLabel: stage.label,
    percent: terminal ? 100 : stage.percent,
    indeterminate: operation.state === "queued",
    terminal,
    canCancel: operation.state === "queued" || operation.state === "running",
    canRetry: operation.state === "failed" || operation.state === "cancelled",
  };
}

export function operationAnnouncement(operation = {}, message = operation.safe_error || "") {
  const view = operationView(operation);
  return [view.label, view.stageLabel, message]
    .filter((item) => typeof item === "string" && item.trim())
    .join("。");
}

export function elapsedLabel(operation = {}, now = Date.now()) {
  const startedAt = Date.parse(operation.created_at || "");
  if (!Number.isFinite(startedAt)) return "用时未知";
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000));
  if (elapsed < 1) return "刚刚开始";
  const minutes = Math.floor(elapsed / 60);
  const seconds = String(elapsed % 60).padStart(2, "0");
  return minutes ? `已用时 ${minutes}分${seconds}秒` : `已用时 ${elapsed}秒`;
}

export function planningComposerView({
  empty = true,
  modelConfigured = false,
  targetSelected = false,
  requestActive = false,
  operation = null,
} = {}) {
  const planning = requestActive || (
    Boolean(operation) && !operationView(operation).terminal
  );
  const disabled = empty || !modelConfigured || !targetSelected || planning;
  let hint = "模型仅设计结构化实验；执行仍需您的确认。";
  if (!modelConfigured) hint = "请先在模型设置中连接 OpenAI、GLM 或 DeepSeek。";
  else if (!targetSelected) hint = "请选择执行平台；平台连通性不会阻塞模型规划。";
  else if (planning) hint = "当前实验设计正在处理中，请等待状态更新。";
  else if (empty) hint = "请输入研究问题。";
  return { disabled, hint };
}
