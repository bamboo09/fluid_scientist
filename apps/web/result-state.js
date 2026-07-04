function booleanOr(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

function finiteOr(value, fallback) {
  return Number.isFinite(value) ? value : fallback;
}

export function normalizeResultPayload(payload, { source, planId = null } = {}) {
  const safePayload = payload && typeof payload === "object" ? payload : {};
  const collection = safePayload.collection && typeof safePayload.collection === "object"
    ? safePayload.collection
    : safePayload;
  const suppliedSummary = safePayload.summary && typeof safePayload.summary === "object"
    ? safePayload.summary
    : {};
  const summary = {
    mesh_passed: booleanOr(suppliedSummary.mesh_passed, collection.mesh?.passed === true),
    solver_completed: booleanOr(
      suppliedSummary.solver_completed,
      collection.solver?.completed === true,
    ),
    cells: finiteOr(suppliedSummary.cells, finiteOr(collection.mesh?.cells, null)),
  };
  const normalizedSource = source === "planned" ? "planned" : "legacy_custom";
  return Object.freeze({
    source: normalizedSource,
    payload: safePayload,
    postprocessPayload: safePayload,
    collection,
    summary,
    boundPlanId: normalizedSource === "planned" && planId ? String(planId) : null,
  });
}

export function analysisAvailability({
  resultContext,
  projectId,
  planId,
  caseId,
  targetId,
}) {
  if (resultContext?.source !== "planned") {
    return {
      allowed: false,
      message: "上传的自定义算例未绑定实验计划，仅提供确定性后处理。",
    };
  }
  if (!resultContext.boundPlanId || resultContext.boundPlanId !== planId) {
    return { allowed: false, message: "当前结果与实验计划不匹配，不能发起模型分析。" };
  }
  if (!projectId || !planId || !caseId || !targetId) {
    return { allowed: false, message: "实验结果上下文不完整，不能发起模型分析。" };
  }
  return { allowed: true, message: "" };
}

export function plannedResultUrl({
  projectId,
  planId,
  caseId,
  targetId,
  action = "results",
}) {
  const safeAction = action === "analysis" ? "analysis" : "results";
  const query = new URLSearchParams({ target_id: targetId, case_id: caseId });
  const encodedProjectId = encodeURIComponent(projectId).replaceAll(".", "%2E");
  const encodedPlanId = encodeURIComponent(planId).replaceAll(".", "%2E");
  return `/api/projects/${encodedProjectId}/experiment-plans/${encodedPlanId}/${safeAction}?${query}`;
}
