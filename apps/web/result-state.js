function booleanOr(value, fallback) {
  return typeof value === "boolean" ? value : fallback;
}

function finiteOr(value, fallback) {
  return Number.isFinite(value) ? value : fallback;
}

const IDENTITY_KEYS = Object.freeze(["projectId", "planId", "caseId", "targetId"]);

function normalizeIdentity(identity) {
  if (!identity || typeof identity !== "object") return null;
  const normalized = {};
  for (const key of IDENTITY_KEYS) {
    const value = identity[key];
    if (value === null || value === undefined || String(value).trim() === "") return null;
    normalized[key] = String(value);
  }
  return Object.freeze(normalized);
}

export function normalizeResultPayload(payload, { source, identity = null } = {}) {
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
  const boundIdentity = normalizedSource === "planned" ? normalizeIdentity(identity) : null;
  return Object.freeze({
    source: normalizedSource,
    payload: safePayload,
    postprocessPayload: safePayload,
    collection,
    summary,
    boundIdentity,
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
  if (!projectId || !planId || !caseId || !targetId) {
    return { allowed: false, message: "实验结果上下文不完整，不能发起模型分析。" };
  }
  const currentIdentity = normalizeIdentity({ projectId, planId, caseId, targetId });
  if (!resultContext.boundIdentity || IDENTITY_KEYS.some(
    (key) => resultContext.boundIdentity[key] !== currentIdentity?.[key],
  )) {
    return { allowed: false, message: "当前结果与实验身份不匹配，不能发起模型分析。" };
  }
  return { allowed: true, message: "" };
}

export class AnalysisRequestController {
  constructor() {
    this.pending = new Map();
  }

  run({ sessionKey, request, isCurrent, onResult, onPending = () => {} }) {
    if (this.pending.has(sessionKey)) return this.pending.get(sessionKey);
    onPending(true, sessionKey);
    let requested;
    try {
      requested = request();
    } catch (error) {
      requested = Promise.reject(error);
    }
    const promise = Promise.resolve(requested)
      .then((result) => {
        if (!isCurrent(sessionKey)) return { ok: false, stale: true };
        onResult(result);
        return { ok: true, stale: false, result };
      })
      .finally(() => {
        this.pending.delete(sessionKey);
        onPending(false, sessionKey);
      });
    this.pending.set(sessionKey, promise);
    return promise;
  }
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
