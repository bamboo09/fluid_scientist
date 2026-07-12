// ==========================================================================
// Fluid Scientist V5 Conversational Workbench
// Three-panel layout: Left (Session/Study list) | Center (Conversation) | Right (Read-only Draft)
// ==========================================================================

// ---- DOM helpers ----
const $ = (s) => document.querySelector(s);
const byId = (id) => document.getElementById(id);
function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  if (attrs && typeof attrs === "object" && !Array.isArray(attrs)) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === false || v == null) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k === "html") e.innerHTML = v;
      else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
      else if (/^[a-zA-Z_][a-zA-Z0-9_-]*$/.test(k)) e.setAttribute(k, v);
    }
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}

// ---- API layer ----
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch {}
    const err = new Error(`API ${res.status}: ${typeof detail === "string" ? detail.slice(0, 200) : JSON.stringify(detail).slice(0, 200)}`);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  if (res.status === 204) return null;
  return res.json();
}

const API = {
  createSession: () => api("/api/v5/sessions", { method: "POST", body: JSON.stringify({}) }),
  getSession: (id) => api(`/api/v5/sessions/${id}`),
  listSessions: () => api("/api/v5/sessions-list"),
  sendMessage: (id, msg) => api(`/api/v5/sessions/${id}/messages`, { method: "POST", body: JSON.stringify({ session_id: id, message: msg }) }),
  selectStudy: (batchId, sessionId, studyId) => api(`/api/v5/batches/${batchId}/select-study`, { method: "POST", body: JSON.stringify({ session_id: sessionId, study_id: studyId }) }),
  getDraft: (id) => api(`/api/v5/drafts/${id}`),
  validateDraft: (id) => api(`/api/v5/drafts/${id}/validate`, { method: "POST" }),
  confirmDraft: (id, sessionId) => api(`/api/v5/drafts/${id}/confirm`, { method: "POST", body: JSON.stringify({ session_id: sessionId, draft_id: id }) }),
  requestChange: (draftId, sessionId, msg) => api(`/api/v5/drafts/${draftId}/changes`, { method: "POST", body: JSON.stringify({ session_id: sessionId, draft_id: draftId, user_message: msg }) }),
  applyProposal: (proposalId, sessionId) => api(`/api/v5/proposals/${proposalId}/apply`, { method: "POST", body: JSON.stringify({ session_id: sessionId, proposal_id: proposalId }) }),
  cancelProposal: (proposalId) => api(`/api/v5/proposals/${proposalId}/cancel`, { method: "POST", body: JSON.stringify({}) }),
  getProposal: (id) => api(`/api/v5/proposals/${id}`),
  generateCasePlan: (sessionId, draftId) => api("/api/v5/case-plans/generate", { method: "POST", body: JSON.stringify({ session_id: sessionId, draft_id: draftId }) }),
  getCasePlan: (id) => api(`/api/v5/case-plans/${id}`),
  compileCasePlan: (id) => api(`/api/v5/case-plans/${id}/compile`, { method: "POST" }),
  reviewCasePlan: (id) => api(`/api/v5/case-plans/${id}/review`, { method: "POST" }),
  fixCasePlan: (id, issues) => api(`/api/v5/case-plans/${id}/fix`, { method: "POST", body: JSON.stringify({ issues }) }),
  submitCase: (casePlanId) => api(`/api/v5/cases/${casePlanId}/submit`, { method: "POST" }),
  getJobStatus: (jobId) => api(`/api/v5/jobs/${jobId}`),
  cancelJob: (jobId) => api(`/api/v5/jobs/${jobId}/cancel`, { method: "POST" }),
  getJobResults: (jobId) => api(`/api/v5/jobs/${jobId}/results`),
  // Codex V5 Pipeline fast-path endpoints (Codex-specific, retained from v5-pipeline.js).
  // The backend PipelineRunRequest expects `user_description` (see v5_router.py).
  runPipeline: (userDescription) => api("/api/v5/pipeline/run", { method: "POST", body: JSON.stringify({ user_description: userDescription }) }),
  modifyPipeline: (pipelineSessionId, modificationText) => api("/api/v5/pipeline/modify", { method: "POST", body: JSON.stringify({ session_id: pipelineSessionId, modification_text: modificationText }) }),
  systemVersion: () => api("/api/system/version"),
  listTargets: () => api("/api/execution-targets"),
  workstationStatus: () => api("/api/workstation/status"),
  detectWorkstation: () => api("/api/workstation/detect"),
  configureWorkstation: (cfg) => api("/api/workstation/configure", { method: "POST", body: JSON.stringify(cfg) }),
  getModelConfig: () => api("/api/v5/model-config"),
  configureModel: (cfg) => api("/api/v5/model-config", { method: "POST", body: JSON.stringify(cfg) }),
  // ---- Workstation discovery & connection (V5) ----
  // These endpoints drive the left-panel workstation configuration UI.
  // No private keys / OpenFOAM paths / remote dirs are sent from
  // the client; discovery and probing happen server-side.
  // The /connect endpoint receives a password for one-time key deployment;
  // the password is never persisted or logged by the backend.
  discoverWorkstations: () => api("/api/v5/workstations/discover"),
  connectWorkstation: (cfg) => api("/api/v5/workstations/connect", { method: "POST", body: JSON.stringify(cfg) }),
  probeWorkstation: (candidateId) => api(`/api/v5/workstations/${candidateId}/probe`, { method: "POST" }),
  confirmHostKey: (candidateId) => api(`/api/v5/workstations/${candidateId}/confirm-host-key`, { method: "POST" }),
  saveWorkstation: (candidateId, displayName) => api(`/api/v5/workstations/${candidateId}/save`, { method: "POST", body: JSON.stringify({ display_name: displayName }) }),
  listWorkstationProfiles: () => api("/api/v5/workstations"),
  testWorkstation: (profileId) => api(`/api/v5/workstations/${profileId}/test`, { method: "POST" }),
  setDefaultWorkstation: (profileId) => api(`/api/v5/workstations/${profileId}/set-default`, { method: "POST" }),
  deleteWorkstation: (profileId) => api(`/api/v5/workstations/${profileId}`, { method: "DELETE" }),
  getDefaultWorkstation: () => api("/api/v5/workstations/default"),
};

// ---- State (frontend only caches display state; backend is source of truth) ----
const state = {
  sessionId: localStorage.getItem("v5_sid") || null,
  session: null,
  batch: null,
  studies: [],
  selectedStudy: null,
  draft: null,
  proposal: null,
  casePlan: null,
  compiledCase: null,
  job: null,
  conversations: [],  // {role, text, timestamp, actions?}
  modelConfigured: false,
  allowedActions: [],
};

// ---- Label helpers ----
const LABELS = {
  readiness: { draftable: "可起草", needs_clarification: "需澄清", not_compilable_yet: "暂不可编译" },
  studyType: {
    cylinder: "圆柱绕流", backward_facing_step: "后台阶流", cavity: "方腔流",
    pipe: "管流", channel: "槽道流", airfoil: "翼型", flat_plate: "平板",
    unknown: "未分类", external: "外流", internal: "内流", cfd_simulation: "CFD仿真",
  },
  draftStatus: { draft: "草稿", ready: "就绪", confirmed: "已确认", compiled: "已编译", running: "运行中", completed: "已完成", failed: "失败" },
  sessionStatus: {
    collecting_intent: "收集意图", batch_review: "任务审阅", clarifying: "澄清中",
    draft_ready: "草案就绪", proposal_pending: "提案待确认", ready: "就绪",
    confirmed: "已确认", case_planning: "算例规划", compiled: "已编译",
    running: "运行中", completed: "已完成", failed: "失败",
  },
};

function label(map, key) { return map[key] || key || "—"; }

// Codex V5 Compile-Ready Pipeline stage labels (mirrors v5-pipeline.js STAGE_LABELS).
// The pipeline fast-path is Codex-specific; these labels keep the inline result
// card readable when /api/v5/pipeline/run is invoked from the action bar.
const PIPELINE_STAGE_LABELS = {
  understanding: "理解意图",
  designing: "设计参数",
  closing: "闭合参数依赖",
  resolving_capabilities: "检查能力",
  generating_case: "生成算例文件",
  validating_case: "验证算例",
  compile_ready: "编译就绪",
  failed: "失败",
};

// ---- Conversation Timeline ----
function addMessage(role, text, extra = {}) {
  const msg = { role, text, timestamp: new Date().toISOString(), ...extra };
  state.conversations.push(msg);
  renderMessage(msg);
  scrollConversationToBottom();
}

function renderMessage(msg) {
  const tl = byId("conversation-timeline");
  const avatarText = msg.role === "user" ? "你" : msg.role === "system" ? "系" : "FS";
  const div = el("div", { class: `conv-msg ${msg.role}` }, [
    el("div", { class: "conv-msg-avatar", text: avatarText }),
    el("div", { class: "conv-msg-body" }, [
      el("div", { class: "conv-msg-meta", text: msg.role === "user" ? "用户" : msg.role === "system" ? "系统" : "研究助手" }),
      el("div", { text: msg.text }),
      ...renderMessageExtra(msg),
    ]),
  ]);
  tl.appendChild(div);
}

function renderMessageExtra(msg) {
  const extras = [];
  // Study cards in conversation
  if (msg.studies?.length) {
    const grid = el("div", { class: "conv-study-cards" });
    for (const s of msg.studies) {
      const known = (s.known_parameters || []).slice(0, 3).map(p => p.display_name || p.canonical_id || "?").join(", ");
      const missing = (s.unknown_required_parameters || []).slice(0, 2).map(p => p.display_name || p.canonical_id || "?").join(", ");
      grid.appendChild(el("div", {
        class: "conv-study-card" + (state.selectedStudy?.study_id === s.study_id ? " selected" : ""),
        "data-study-id": s.study_id,
        onclick: () => selectStudy(s),
      }, [
        el("h4", { text: s.title?.slice(0, 40) || s.research_objective?.slice(0, 40) || "研究任务" }),
        el("span", { class: "type-chip", text: label(LABELS.studyType, s.study_type) }),
        known ? el("div", { class: "mini-params", text: `已知: ${known}` }) : null,
        missing ? el("div", { class: "mini-missing", text: `缺失: ${missing}` }) : null,
        el("div", { style: "margin-top:4px;" }, [
          el("span", { class: `readiness-badge ${s.readiness_level || "draftable"}`, text: label(LABELS.readiness, s.readiness_level) }),
        ]),
      ]));
    }
    extras.push(grid);
  }
  // Proposal in conversation
  if (msg.proposal) {
    const p = msg.proposal;
    const diffDiv = el("div", { class: "conv-proposal" }, [
      el("div", { class: "proposal-header" }, [
        el("h4", { text: "修改提案" }),
        el("span", { class: "proposal-version", text: `基于 Draft v${p.base_draft_version || "?"}` }),
      ]),
    ]);
    if (p.summary) {
      diffDiv.appendChild(el("div", { class: "proposal-summary", text: p.summary }));
    }
    // Change diff table
    if (p.changes?.length) {
      const tbl = el("table", { class: "proposal-diff-table" }, [
        el("thead", {}, [el("tr", {}, [
          el("th", { text: "字段" }),
          el("th", { text: "修改前" }),
          el("th", { text: "修改后" }),
          el("th", { text: "类型" }),
        ])]),
        el("tbody", {}, p.changes.map(c => {
          const path = c.target_path || c.target || c.path || "?";
          const oldVal = c.old_value != null ? String(c.old_value) : "—";
          const newVal = c.new_value != null ? String(c.new_value) : "—";
          const unit = c.unit ? ` ${c.unit}` : "";
          const ct = c.change_type || c.op || "修改";
          const changeLabels = {
            set_parameter: "修改参数", add_parameter: "新增参数", remove_parameter: "删除参数",
            change_boundary_condition: "修改边界", change_initial_condition: "修改初场",
            change_physics_model: "修改物理模型", change_mesh: "修改网格", change_solver: "修改求解器",
            change_geometry: "修改几何", change_numerics: "修改数值格式",
            add_output: "新增观测量", remove_output: "删除观测量",
            add_assumption: "新增假设", question: "提问", clarification_required: "需澄清",
          };
          return el("tr", {}, [
            el("td", { text: path }),
            el("td", { class: "diff-old", text: oldVal + unit }),
            el("td", { class: "diff-new", text: newVal + unit }),
            el("td", { text: changeLabels[ct] || ct }),
          ]);
        })),
      ]);
      diffDiv.appendChild(tbl);
    }
    // Impact summary
    if (p.impact_summary?.length) {
      diffDiv.appendChild(el("div", { class: "proposal-impact" }, [
        el("div", { class: "proposal-impact-title", text: "影响分析" }),
        ...p.impact_summary.map(imp => el("div", { class: "proposal-impact-item", text: `• ${imp}` })),
      ]));
    }
    // Invalidated downstream
    if (p.invalidates?.length) {
      diffDiv.appendChild(el("div", { class: "proposal-invalidates", text: `失效项: ${p.invalidates.join(", ")}` }));
    }
    // Action buttons
    if (p.status === "pending") {
      diffDiv.appendChild(el("div", { class: "proposal-actions" }, [
        el("button", { class: "button button-primary button-small", text: "确认修改", onclick: () => applyProposal(p) }),
        el("button", { class: "button button-secondary button-small", text: "取消修改", onclick: () => cancelProposal(p) }),
      ]));
    } else if (p.status === "applied") {
      diffDiv.appendChild(el("div", { class: "proposal-status applied", text: "✓ 已应用" }));
    } else if (p.status === "cancelled") {
      diffDiv.appendChild(el("div", { class: "proposal-status cancelled", text: "✗ 已取消" }));
    }
    extras.push(diffDiv);
  }
  // CasePlan in conversation
  if (msg.casePlan) {
    const cp = msg.casePlan;
    extras.push(el("div", { style: "margin-top:8px;padding:10px;border:1px solid var(--teal);border-radius:6px;background:var(--teal-pale);" }, [
      el("strong", { text: `算例规划已生成: ${cp.solver || "?"} · ${cp.dimensions || "?"}` }),
      el("br"),
      cp.can_compile ? el("span", { style: "color:#155724;font-size:11px;", text: "✓ 可编译" }) : el("span", { style: "color:#721c24;font-size:11px;", text: "⚠ 有阻塞: " + (cp.blocking_reasons || []).join(", ") }),
    ]));
  }
  // Compiled case in conversation
  if (msg.compiledCase) {
    const cc = msg.compiledCase;
    extras.push(el("div", { style: "margin-top:8px;padding:10px;border:1px solid #155724;border-radius:6px;background:#d4edda;" }, [
      el("strong", { text: `算例编译完成: ${cc.solver || "?"} · ${cc.file_count || 0} 个文件` }),
      el("br"),
      cc.file_list ? el("span", { style: "color:#155724;font-size:11px;", text: `文件: ${cc.file_list}` }) : null,
    ]));
  }
  // Codex V5 Pipeline result card (one-click compile-ready generation)
  if (msg.pipeline) {
    extras.push(renderPipelineCard(msg.pipeline));
  }
  // Error
  if (msg.error) {
    extras.push(el("div", { style: "margin-top:6px;padding:6px 10px;background:#f8d7da;border-radius:4px;font-size:12px;color:#721c24;", text: msg.error }));
  }
  return extras;
}

function scrollConversationToBottom() {
  const tl = byId("conversation-timeline");
  tl.scrollTop = tl.scrollHeight;
}

// ---- Left Panel: Session & Study List ----
function renderSessionList() {
  const container = byId("session-list");
  container.innerHTML = "";
  if (state.session) {
    const item = el("div", { class: "session-item active" }, [
      el("div", { text: state.session.session_id?.slice(0, 20) + "..." }),
      el("div", { class: "session-time", text: `状态: ${label(LABELS.sessionStatus, state.session.status)}` }),
    ]);
    container.appendChild(item);
  } else {
    container.appendChild(el("div", { class: "session-item", text: "点击 + 新建开始" }));
  }
}

function renderStudyList() {
  const container = byId("study-items");
  const section = byId("study-list");
  if (!state.studies.length) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  container.innerHTML = "";
  for (const s of state.studies) {
    const isSelected = state.selectedStudy?.study_id === s.study_id;
    const draftInfo = state.draft && state.selectedStudy?.study_id === s.study_id ? ` · Draft v${state.draft.version}` : "";
    container.appendChild(el("div", {
      class: "study-item" + (isSelected ? " selected" : ""),
      onclick: () => { if (!isSelected) selectStudy(s); },
    }, [
      el("div", { class: "study-item-title", text: s.title?.slice(0, 30) || s.research_objective?.slice(0, 30) || "研究任务" }),
      el("div", { class: `study-item-status ${s.readiness_level || "draftable"}`, text: label(LABELS.readiness, s.readiness_level) + draftInfo }),
    ]));
  }
}

// ---- Right Panel: Read-only Draft Viewer ----
function renderDraftViewer() {
  const viewer = byId("draft-viewer");
  const badge = byId("draft-version-badge");
  viewer.innerHTML = "";

  if (!state.draft) {
    badge.hidden = true;
    viewer.appendChild(el("div", { class: "empty-state" }, [
      el("p", { text: "尚未生成研究方案" }),
      el("p", { class: "empty-hint", text: "在中间对话区输入研究目标，系统将自动分解为研究任务并生成结构化草案。" }),
    ]));
    return;
  }

  const d = state.draft;
  badge.hidden = false;
  badge.textContent = `v${d.version} · ${label(LABELS.draftStatus, d.status)}`;

  // Objective
  viewer.appendChild(section("研究目标", [
    fieldRow("目标", d.objective?.slice(0, 100) || "—", "inferred"),
  ]));

  // Study type
  viewer.appendChild(section("研究类型", [
    fieldRow("类型", label(LABELS.studyType, d.study_type), "inferred"),
    fieldRow("维度", d.physics_models?.dimension || "—", "inferred"),
    fieldRow("时间", d.physics_models?.temporal || "—", "inferred"),
    fieldRow("湍流", d.physics_models?.turbulent ? "是" : "否", "inferred"),
  ]));

  // Geometry
  const geo = d.geometry || {};
  viewer.appendChild(section("几何配置", Object.keys(geo).length
    ? Object.entries(geo).map(([k, v]) => fieldRow(k, formatValue(v), "inferred"))
    : [fieldRow("几何", "待填充", "missing")]
  ));

  // Control parameters
  const params = d.control_parameters || [];
  if (params.length) {
    const tbl = el("table", { class: "draft-param-table-mini" }, [
      el("thead", {}, [el("tr", {}, [el("th", { text: "参数" }), el("th", { text: "值" }), el("th", { text: "单位" }), el("th", { text: "来源" })])]),
      el("tbody", {}, params.map(p => el("tr", {}, [
        el("td", { text: p.display_name || p.parameter_id || "?" }),
        el("td", { text: p.value != null ? String(p.value) : "—" }),
        el("td", { text: p.unit || "—" }),
        el("td", { text: p.source || "—" }),
      ]))),
    ]);
    viewer.appendChild(el("div", { class: "draft-readonly-section" }, [
      el("h3", { text: `控制参数 (${params.length})` }),
      tbl,
    ]));
  } else {
    viewer.appendChild(section("控制参数", [fieldRow("参数", "未提取到参数", "missing")]));
  }

  // Boundary conditions
  const bcs = d.boundary_conditions || {};
  const bcKeys = Object.keys(bcs);
  viewer.appendChild(section("边界条件", bcKeys.length
    ? bcKeys.map(k => fieldRow(k, typeof bcs[k] === "object" ? JSON.stringify(bcs[k]).slice(0, 50) : String(bcs[k]), "inferred"))
    : [fieldRow("边界条件", "待填充（入口/出口/壁面）", "missing")]
  ));

  // Initial conditions
  const ics = d.initial_conditions || {};
  viewer.appendChild(section("初始条件", Object.keys(ics).length
    ? Object.entries(ics).map(([k, v]) => fieldRow(k, formatValue(v), "inferred"))
    : [fieldRow("初始条件", "待填充", "missing")]
  ));

  // Mesh
  const mesh = d.mesh || {};
  viewer.appendChild(section("网格要求", Object.keys(mesh).length
    ? Object.entries(mesh).map(([k, v]) => fieldRow(k, formatValue(v), "inferred"))
    : [fieldRow("网格", "待配置", "missing")]
  ));

  // Solver
  const solver = d.solver || {};
  viewer.appendChild(section("求解器", Object.keys(solver).length
    ? Object.entries(solver).map(([k, v]) => fieldRow(k, formatValue(v), "inferred"))
    : [fieldRow("求解器", "待选择", "missing")]
  ));

  // Observables / requested outputs
  const outputs = d.requested_outputs || [];
  viewer.appendChild(section("观测量", outputs.length
    ? outputs.map(o => fieldRow(typeof o === "string" ? o : o.name || "?", "", "inferred"))
    : [fieldRow("观测量", "待指定", "missing")]
  ));

  // Analysis goals
  const goals = d.analysis_goals || [];
  viewer.appendChild(section("分析目标", goals.length
    ? goals.map(g => fieldRow("•", g, "inferred"))
    : [fieldRow("分析目标", "待指定", "missing")]
  ));

  // Blocking issues
  if (d.blocking_issues?.length) {
    viewer.appendChild(el("div", { class: "draft-readonly-section" }, [
      el("h3", { style: "color: var(--red);", text: "阻塞问题" }),
      ...d.blocking_issues.map(bi => el("div", { style: "font-size:11px;color:#721c24;padding:2px 0;", text: `⚠ ${bi.message || bi.check || JSON.stringify(bi)}` })),
    ]));
  }

  // Assumptions
  const assumptions = d.assumptions || [];
  if (assumptions.length) {
    viewer.appendChild(section("假设", assumptions.map(a => fieldRow("•", typeof a === "string" ? a : a.display_name || a.description || "—", "inferred"))));
  }

  // Draft status indicator
  if (d.status === "confirmed") {
    viewer.appendChild(el("div", { style: "padding:8px;background:#d4edda;border-radius:6px;font-size:12px;color:#155724;margin-top:8px;", text: "✓ 草案已确认，可生成 CasePlan" }));
  }
}

function section(title, children) {
  return el("div", { class: "draft-readonly-section" }, [
    el("h3", { text: title }),
    ...children,
  ]);
}

function formatValue(v) {
    if (v == null) return "—";
    if (typeof v === "object") {
      // For nested objects, show key=value pairs
      const entries = Object.entries(v);
      if (entries.length <= 3) {
        return entries.map(([k, val]) => `${k}=${formatValue(val)}`).join(", ");
      }
      return JSON.stringify(v).slice(0, 80);
    }
    return String(v);
  }

  function fieldRow(label, value, status) {
  const statusLabels = { confirmed: "已确认", pending: "已填充", inferred: "模型推断", "user-provided": "用户提供", missing: "待补充", conflict: "存在冲突" };
  return el("div", { class: "field-row" }, [
    el("span", { class: "field-label-inline", text: label }),
    el("span", { class: "field-value-inline", text: value }),
    status ? el("span", { class: `field-status ${status}`, text: statusLabels[status] || status }) : null,
  ]);
}

// ---- Action Bar ----
function updateActionBar() {
  const bar = byId("action-bar");
  bar.innerHTML = "";
  const actions = [];

  // Proposal pending: handle confirm/cancel first
  if (state.proposal?.status === "pending") {
    actions.push({ text: "确认修改", class: "button-primary", fn: () => applyProposal(state.proposal) });
    actions.push({ text: "取消修改", class: "button-secondary", fn: () => cancelProposal(state.proposal) });
  } else if (state.draft) {
    if (state.draft.status === "confirmed") {
      // Draft is confirmed — show next steps
      if (state.casePlan) {
        if (state.casePlan.can_compile && !state.compiledCase) {
          actions.push({ text: "编译算例", class: "button-primary", fn: () => compileCase() });
        } else if (state.compiledCase) {
          if (!state.job) {
            // Check if there are review errors to fix
            const reviewMsg = [...state.messages].reverse().find(m => m.reviewResult);
            const hasReviewErrors = reviewMsg?.reviewResult?.has_issues &&
              reviewMsg.reviewResult.issues?.some(i => i.severity === "error");
            // Check if fix was already applied
            const fixMsg = [...state.messages].reverse().find(m => m.fixResult);
            const alreadyFixed = fixMsg?.fixResult?.fixed;
            if (hasReviewErrors && !alreadyFixed) {
              actions.push({ text: "AI 修复问题", class: "button-primary", fn: () => fixCaseIssues() });
              actions.push({ text: "AI 预检查", class: "button-secondary", fn: () => reviewCase() });
            } else {
              actions.push({ text: "AI 预检查", class: "button-secondary", fn: () => reviewCase() });
              actions.push({ text: "提交工作站", class: "button-primary", fn: () => submitToWorkstation() });
            }
          } else if (state.job.state === "running" || state.job.state === "queued") {
            actions.push({ text: "刷新状态", class: "button-secondary", fn: () => pollJobStatus() });
            actions.push({ text: "取消任务", class: "button-secondary", fn: () => cancelJob() });
          } else if (state.job.state === "succeeded") {
            actions.push({ text: "查看结果", class: "button-primary", fn: () => fetchJobResults() });
          }
        }
      } else {
        actions.push({ text: "生成 CasePlan", class: "button-primary", fn: () => generateCasePlan() });
      }
      actions.push({ text: "重新校验", class: "button-secondary", fn: () => validateDraft() });
    } else {
      // Draft not yet confirmed — primary action is confirm
      actions.push({ text: "确认草案", class: "button-primary", fn: () => confirmDraft() });
      actions.push({ text: "重新校验", class: "button-secondary", fn: () => validateDraft() });
    }
  }

  if (actions.length === 0) {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  for (const a of actions) {
    bar.appendChild(el("button", { class: `button ${a.class}`, text: a.text, onclick: a.fn }));
  }
}

// ---- Composer ----
function updateComposer() {
  const input = byId("research-input");
  const sendBtn = byId("send-button");
  const hint = byId("composer-hint");

  if (state.proposal?.status === "pending") {
    input.placeholder = "输入\"确认\"应用提案，或\"取消\"放弃";
    hint.textContent = "当前有待确认的修改提案";
  } else if (state.draft) {
    input.placeholder = "对草案提出修改（自然语言），如：将雷诺数改为5000";
    hint.textContent = "修改将通过提案确认后才生效";
  } else if (state.batch) {
    input.placeholder = "补充信息，或点击上方卡片选择研究";
    hint.textContent = "请选择一个研究任务以生成草案";
  } else {
    input.placeholder = "描述研究目标，或对当前草案提出修改...";
    hint.textContent = "输入研究目标开始 · 多个任务请编号（1. 2. 3. ...）";
  }
  sendBtn.disabled = input.value.trim().length < 2;
}

// ---- Actions ----
async function initSession() {
  if (state.sessionId) {
    try {
      const data = await API.getSession(state.sessionId);
      state.session = data.session;
      if (state.session.current_draft_id) {
        try { state.draft = await API.getDraft(state.session.current_draft_id); } catch {}
      }
      addMessage("assistant", `已恢复会话 ${state.sessionId.slice(0, 16)}…\n状态: ${label(LABELS.sessionStatus, state.session.status)}\n\n请继续描述研究目标，或对当前草案提出修改。`);
      renderAll();
      return;
    } catch {
      state.sessionId = null;
      localStorage.removeItem("v5_sid");
    }
  }
  await createNewSession();
}

async function createNewSession() {
  const data = await API.createSession();
  state.sessionId = data.session.session_id;
  state.session = data.session;
  localStorage.setItem("v5_sid", state.sessionId);
  state.conversations = [];
  state.batch = null;
  state.studies = [];
  state.selectedStudy = null;
  state.draft = null;
  state.proposal = null;
  state.casePlan = null;
  byId("conversation-timeline").innerHTML = "";
  addMessage("assistant", "你好！我是 Fluid Scientist 研究助手。请描述你想研究的流体力学问题，我会帮你分解为具体的实验任务并生成结构化草案。\n\n你可以输入多个编号的研究任务（如\"1. 圆柱绕流 2. 后台阶流\"），也可以输入单个研究问题。");
  renderAll();
}

async function sendUserMessage(text) {
  const msg = text.trim();
  if (!msg) return;
  addMessage("user", msg);
  byId("research-input").value = "";

  // Proposal pending: handle confirm/cancel
  if (state.proposal?.status === "pending") {
    if (/^(确认|confirm|yes|y|应用|apply|好的|可以)/i.test(msg)) {
      await applyProposal(state.proposal);
      return;
    }
    if (/^(取消|cancel|no|n|放弃)/i.test(msg)) {
      await cancelProposal(state.proposal);
      return;
    }
  }

  // Draft change request
  if (state.draft && state.draft.status !== "confirmed") {
    try {
      byId("send-button").disabled = true;
      const proposal = await API.requestChange(state.draft.draft_id, state.sessionId, msg);
      state.proposal = proposal;
      addMessage("assistant", `已生成修改提案：${proposal.summary || ""}`, { proposal });
      renderAll();
    } catch (e) {
      addMessage("system", `修改请求失败: ${e.message}`, { error: e.message });
    } finally {
      updateComposer();
    }
    return;
  }

  // Regular session message
  try {
    byId("send-button").disabled = true;
    const result = await API.sendMessage(state.sessionId, msg);
    for (const action of result.actions || []) {
      await processAction(action);
    }
    // Refresh session
    const sdata = await API.getSession(state.sessionId);
    state.session = sdata.session;
    renderAll();
  } catch (e) {
    addMessage("system", `请求失败: ${e.message}`, { error: e.message });
  } finally {
    updateComposer();
  }
}

async function processAction(action) {
  switch (action.action) {
    case "batch_review": {
      state.batch = action.batch;
      state.studies = action.batch.studies || [];
      const count = state.studies.length;
      addMessage("assistant", `已识别到 ${count} 个研究任务。请在左侧或下方卡片中选择一个研究任务来生成实验草案。`, { studies: state.studies });
      break;
    }
    case "study_decomposed": {
      // Always update batch and show the study, even if one already exists.
      state.batch = { batch_id: action.study.batch_id, studies: [action.study] };
      state.studies = [action.study];
      addMessage("assistant", `已识别到 1 个研究任务。请选择该任务来生成实验草案。`, { studies: state.studies });
      break;
    }
    case "clarification_required": {
      const q = action.question || action.message || "请补充更多信息。";
      addMessage("assistant", q);
      break;
    }
    case "clarification_questions": {
      const qs = action.questions || [];
      addMessage("assistant", `需要澄清以下问题：\n${qs.map((q, i) => `${i + 1}. ${q.question || q.text || JSON.stringify(q)}`).join("\n")}`);
      break;
    }
    case "apply_proposal": {
      if (action.proposal_id) {
        state.proposal = await API.getProposal(action.proposal_id);
        addMessage("assistant", `修改提案已生成：${state.proposal.summary || ""}`, { proposal: state.proposal });
      }
      break;
    }
    default:
      console.log("Unknown action:", action);
  }
}

async function selectStudy(study) {
  state.selectedStudy = study;
  const batchId = state.batch?.batch_id;
  if (!batchId) {
    addMessage("system", "无法确定批次 ID，请刷新页面重试");
    return;
  }
  try {
    addMessage("user", `选择研究任务: ${study.title?.slice(0, 50) || study.study_id}`);
    addMessage("assistant", "正在生成实验草案...");
    const result = await API.selectStudy(batchId, state.sessionId, study.study_id);
    if (result.type === "pipeline_failed" || !result.draft) {
      const failMsg = result.failure?.message || result.failure?.user_facing_message || "未知错误";
      const failStage = result.failure?.failed_stage || result.current_stage || "";
      addMessage("system", `生成草案失败: ${failMsg}`);
      addMessage("assistant", `草案生成流程在「${failStage}」阶段失败：${failMsg}\n\n请尝试补充更多研究细节后重试，或检查模型配置。`);
      return;
    }
    state.draft = result.draft;
    addMessage("assistant", `实验草案已生成（版本 v${result.draft.version}）。请在右侧查看结构化方案。\n\n你可以通过对话提出修改（如"将雷诺数改为5000"），修改将通过提案确认后才生效。`);
    renderAll();
  } catch (e) {
    addMessage("system", `生成草案失败: ${e.message}`, { error: e.message });
    if (e.status === 422 && e.detail?.blocking_issues) {
      addMessage("assistant", `该研究暂不可生成草案：${e.detail.blocking_issues.map(i => i.message || JSON.stringify(i)).join("；")}`);
    }
  }
}

async function confirmDraft() {
  if (!state.draft) return;
  try {
    addMessage("assistant", "正在确认草案...");
    const confirmed = await API.confirmDraft(state.draft.draft_id, state.sessionId);
    state.draft = confirmed;
    addMessage("assistant", "草案已确认。你可以继续生成 CasePlan 进行算例规划，或通过对话提出修改。");
    renderAll();
  } catch (e) {
    addMessage("system", `确认失败: ${e.message}`, { error: e.message });
  }
}

async function validateDraft() {
  if (!state.draft) return;
  try {
    addMessage("assistant", "正在重新校验...");
    const result = await API.validateDraft(state.draft.draft_id);
    state.draft = await API.getDraft(state.draft.draft_id);
    // Show validation summary
    const checks = result.checks || [];
    const passed = checks.filter(c => c.passed).length;
    const total = checks.length;
    const failed = checks.filter(c => !c.passed);
    let msg = `校验完成：${passed}/${total} 项通过。`;
    if (failed.length > 0) {
      msg += `\n未通过项：${failed.map(c => `${c.check_name}: ${c.message}`).join("；")}`;
    }
    if (result.openfoam_available === false) {
      msg += `\n\n注意：本地未检测到 OpenFOAM 运行时，运行时验证将在远程工作站上执行。`;
    }
    addMessage("assistant", msg);
    renderAll();
  } catch (e) {
    addMessage("system", `校验失败: ${e.message}`, { error: e.message });
  }
}

function updateProposalInConversation(proposalId, status) {
  for (const msg of state.conversations) {
    if (msg.proposal && msg.proposal.proposal_id === proposalId) {
      msg.proposal.status = status;
    }
  }
  // Re-render conversation timeline
  const tl = byId("conversation-timeline");
  tl.innerHTML = "";
  for (const msg of state.conversations) {
    renderMessage(msg);
  }
  scrollConversationToBottom();
}

async function applyProposal(proposal) {
  if (proposal.status && proposal.status !== "pending") {
    addMessage("system", `该提案状态为 ${proposal.status}，无法重复操作。`);
    return;
  }
  let newDraft;
  try {
    newDraft = await API.applyProposal(proposal.proposal_id, state.sessionId);
  } catch (e) {
    addMessage("system", `应用提案失败: ${e.message}`, { error: e.message });
    return;
  }
  state.proposal = null;
  state.draft = newDraft;
  updateProposalInConversation(proposal.proposal_id, "applied");
  addMessage("assistant", `修改已应用，草案更新为版本 v${newDraft.version}。`);
  try { renderAll(); } catch (e) { console.error("Render error:", e); }
}

async function cancelProposal(proposal) {
  if (proposal.status && proposal.status !== "pending") {
    addMessage("system", `该提案状态为 ${proposal.status}，无法重复操作。`);
    return;
  }
  try {
    await API.cancelProposal(proposal.proposal_id);
    state.proposal = null;
    updateProposalInConversation(proposal.proposal_id, "cancelled");
    addMessage("assistant", "修改提案已取消，草案未变化。");
    renderAll();
  } catch (e) {
    addMessage("system", `取消失败: ${e.message}`, { error: e.message });
  }
}

async function generateCasePlan() {
  if (!state.draft) return;
  try {
    addMessage("assistant", "正在生成 CasePlan...");
    const cp = await API.generateCasePlan(state.sessionId, state.draft.draft_id);
    state.casePlan = cp;
    addMessage("assistant", `CasePlan 已生成: ${cp.solver} · ${cp.dimensions} · ${cp.case_type}`, { casePlan: cp });
    renderAll();
  } catch (e) {
    addMessage("system", `生成 CasePlan 失败: ${e.message}`, { error: e.message });
  }
}

async function compileCase() {
  if (!state.casePlan) return;
  try {
    addMessage("assistant", "正在编译算例...");
    const result = await API.compileCasePlan(state.casePlan.case_plan_id);
    state.compiledCase = result;
    const fc = result.file_count || 0;
    const fileList = result.files?.length ? result.files.join(", ") : "";
    if (fc > 0) {
      addMessage("assistant", `算例编译完成: ${state.casePlan.solver} · ${fc} 个文件`, { compiledCase: { ...result, solver: state.casePlan.solver, file_list: fileList } });
    } else {
      addMessage("system", `编译完成但生成了 0 个文件，请检查配置。`);
    }
    renderAll();
  } catch (e) {
    addMessage("system", `编译失败: ${e.message}`, { error: e.message });
  }
}

async function reviewCase() {
  if (!state.casePlan) return;
  try {
    addMessage("assistant", "正在用 AI 审查算例文件...");
    const result = await API.reviewCasePlan(state.casePlan.case_plan_id);
    const issues = result.issues || [];
    const errors = issues.filter(i => i.severity === "error");
    const warnings = issues.filter(i => i.severity === "warning");

    if (result.has_issues && errors.length > 0) {
      const errorList = errors.map(e => `  • [${e.file}] ${e.description}\n    修复建议: ${e.suggestion}`).join("\n");
      addMessage("system", `AI 预检查发现 ${errors.length} 个错误, ${warnings.length} 个警告:\n${errorList}`, { reviewResult: result });
    } else if (warnings.length > 0) {
      const warnList = warnings.map(w => `  • [${w.file}] ${w.description}`).join("\n");
      addMessage("assistant", `AI 预检查通过, ${warnings.length} 个警告:\n${warnList}`, { reviewResult: result });
    } else {
      addMessage("assistant", `AI 预检查通过, 未发现问题。${result.summary || ""}`, { reviewResult: result });
    }
    renderAll();
  } catch (e) {
    addMessage("system", `AI 预检查失败: ${e.message}`, { error: e.message });
  }
}

async function fixCaseIssues() {
  if (!state.casePlan) return;
  // Get issues from the last review result in state
  const reviewMsg = state.messages.findLast?.(m => m.reviewResult) ||
    [...state.messages].reverse().find(m => m.reviewResult);
  const issues = reviewMsg?.reviewResult?.issues || [];
  if (issues.length === 0) {
    addMessage("system", "没有可修复的问题，请先运行 AI 预检查");
    return;
  }
  try {
    addMessage("assistant", `正在用 AI 修复 ${issues.length} 个问题...`);
    const result = await API.fixCasePlan(state.casePlan.case_plan_id, issues);
    const fixedFiles = result.fixed_files || [];
    const remaining = result.remaining_issues || [];
    const postReview = result.post_fix_review || {};

    if (result.fixed && fixedFiles.length > 0) {
      const fileList = fixedFiles.join(", ");
      if (postReview.has_issues && postReview.issues?.length > 0) {
        const remainingErrors = postReview.issues.filter(i => i.severity === "error");
        const remainingWarnings = postReview.issues.filter(i => i.severity === "warning");
        if (remainingErrors.length > 0) {
          const errList = remainingErrors.map(e => `  • [${e.file}] ${e.description}`).join("\n");
          addMessage("system", `AI 修复了 ${fixedFiles.length} 个文件 (${fileList})，但仍有 ${remainingErrors.length} 个错误:\n${errList}`, { fixResult: result });
        } else {
          addMessage("assistant", `AI 修复了 ${fixedFiles.length} 个文件 (${fileList})。剩余 ${remainingWarnings.length} 个警告，不影响提交。`, { fixResult: result });
        }
      } else {
        addMessage("assistant", `AI 修复完成，已修复 ${fixedFiles.length} 个文件 (${fileList})。重新检查通过，可以提交工作站。`, { fixResult: result });
      }
    } else {
      addMessage("system", `AI 修复未能解决问题: ${result.summary}`, { fixResult: result });
    }
    renderAll();
  } catch (e) {
    addMessage("system", `AI 修复失败: ${e.message}`, { error: e.message });
  }
}

async function submitToWorkstation() {
  if (!state.casePlan) return;
  if (!state.compiledCase) {
    addMessage("system", "请先编译算例再提交。");
    return;
  }
  try {
    addMessage("assistant", "正在提交到工作站...");
    const result = await API.submitCase(state.casePlan.case_plan_id);
    state.job = result.job || { job_id: result.job_id, state: "queued" };
    addMessage("assistant", `任务已提交。Job ID: ${state.job.job_id}\n状态: ${state.job.state || "queued"}`, { job: state.job });
    renderAll();
    // Auto-poll after 5 seconds
    setTimeout(() => pollJobStatus(), 5000);
  } catch (e) {
    addMessage("system", `提交失败: ${e.message}`, { error: e.message });
  }
}

async function pollJobStatus() {
  if (!state.job?.job_id) return;
  try {
    const status = await API.getJobStatus(state.job.job_id);
    state.job = status;
    const stateLabel = { queued: "排队中", running: "运行中", succeeded: "已完成", failed: "失败", cancelled: "已取消" }[status.state] || status.state;
    addMessage("assistant", `任务状态: ${stateLabel}${status.error ? `\n错误: ${status.error}` : ""}`);
    renderAll();
    // Continue polling if running
    if (status.state === "running" || status.state === "queued") {
      setTimeout(() => pollJobStatus(), 10000);
    }
  } catch (e) {
    addMessage("system", `查询状态失败: ${e.message}`, { error: e.message });
  }
}

async function cancelJob() {
  if (!state.job?.job_id) return;
  try {
    const result = await API.cancelJob(state.job.job_id);
    state.job = result;
    addMessage("assistant", "任务已取消。");
    renderAll();
  } catch (e) {
    addMessage("system", `取消失败: ${e.message}`, { error: e.message });
  }
}

async function fetchJobResults() {
  if (!state.job?.job_id) return;
  try {
    const results = await API.getJobResults(state.job.job_id);
    addMessage("assistant", `结果已获取。${JSON.stringify(results).slice(0, 200)}...`, { results });
    renderAll();
  } catch (e) {
    addMessage("system", `获取结果失败: ${e.message}`, { error: e.message });
  }
}

// ---- Codex V5 Pipeline fast-path (compile-ready case generation) ----
// Preserves the Codex one-click pipeline by invoking /api/v5/pipeline/run
// directly and rendering the CompileReadyDraftView as an inline card in the
// conversation timeline. This keeps the Codex fast path reachable without
// loading v5-pipeline.js / app.js.
async function runCodexPipeline() {
  // Derive the research question from the last user message; fall back to the
  // draft objective so the button always has something to send.
  const lastUserMsg = [...state.conversations].reverse().find((m) => m.role === "user");
  const researchQuestion = (lastUserMsg?.text || state.draft?.objective || "").trim();
  if (researchQuestion.length < 10) {
    addMessage("system", "请先输入至少10个字符的研究问题描述，再使用一键生成。", { error: "研究问题过短，无法启动流水线" });
    return;
  }
  addMessage("assistant", `正在通过 Codex V5 流水线一键生成可编译算例...\n研究问题: ${researchQuestion.slice(0, 80)}`);
  try {
    const resp = await API.runPipeline(researchQuestion);
    addMessage("assistant", pipelineResultSummary(resp), { pipeline: resp });
  } catch (e) {
    addMessage("system", `一键生成失败: ${e.message}`, { error: e.message });
  }
}

function pipelineResultSummary(resp) {
  if (resp.status === "compile_ready" && resp.compile_ready_view) {
    const view = resp.compile_ready_view;
    const files = view.case_manifest?.generated_files || [];
    const checks = view.validation_results?.checks || [];
    const passed = checks.filter((c) => c.passed).length;
    const solver = view.solver?.name || view.solver?.solver_name || "—";
    return `编译就绪 ✓ · 求解器 ${solver} · 已生成 ${files.length} 个文件 · 验证 ${passed}/${checks.length} 通过`;
  }
  if (resp.failure) {
    return `流水线在 "${resp.current_stage || "?"}" 阶段失败：${resp.failure.message || resp.failure.failure_category || "未知错误"}`;
  }
  return `流水线返回状态：${resp.status || "—"}`;
}

function renderPipelineCard(resp) {
  const card = el("div", { class: "conv-pipeline-card" });
  const isReady = resp.status === "compile_ready" && resp.compile_ready_view;
  card.appendChild(el("div", { class: "pipeline-card-header" }, [
    el("h4", { text: "编译就绪流水线 (Codex V5)" }),
    el("span", { class: `type-chip ${isReady ? "type-chip-teal" : ""}`, text: resp.status || "—" }),
  ]));

  // Stage history
  if (resp.stage_history?.length) {
    const stages = el("ul", { class: "pipeline-stages" });
    for (const stage of resp.stage_history) {
      const name = PIPELINE_STAGE_LABELS[stage.stage] || stage.stage;
      stages.appendChild(el("li", { class: stage.stage === "compile_ready" ? "stage-done" : "", text: `${name}${stage.detail ? " — " + stage.detail : ""}` }));
    }
    card.appendChild(stages);
  }

  if (isReady) {
    const view = resp.compile_ready_view;
    if (view.research_objective) {
      card.appendChild(el("p", { class: "pipeline-objective", text: `研究目标: ${view.research_objective}` }));
    }
    const solver = view.solver || {};
    card.appendChild(el("div", { class: "pipeline-kv" }, [
      el("span", { class: "kv-key", text: "求解器" }),
      el("span", { class: "kv-val", text: solver.name || solver.solver_name || "—" }),
    ]));

    // Validation status
    const checks = view.validation_results?.checks || [];
    if (checks.length) {
      const list = el("ul", { class: "pipeline-checks" });
      for (const c of checks) {
        list.appendChild(el("li", { class: `check check-${c.passed ? "pass" : "fail"}` }, [
          el("span", { class: "check-icon", text: c.passed ? "✓" : "✗" }),
          el("span", { class: "check-name", text: c.check_name || "?" }),
          c.message ? el("span", { class: "check-msg", text: c.message }) : null,
        ]));
      }
      card.appendChild(el("div", { class: "pipeline-section" }, [el("h5", { text: "算例验证" }), list]));
    }

    // Generated case files
    const files = view.case_manifest?.generated_files || [];
    if (files.length) {
      const fl = el("ul", { class: "pipeline-files" });
      for (const f of files) fl.appendChild(el("li", { text: f }));
      card.appendChild(el("div", { class: "pipeline-section" }, [el("h5", { text: `已生成文件 (${files.length})` }), fl]));
    }
  } else if (resp.failure) {
    card.appendChild(el("p", { class: "pipeline-error", text: `失败：${resp.failure.message || resp.failure.failure_category || "未知错误"}` }));
  }
  return card;
}

// ---- System loading ----
// Version display mirrors Codex app.js loadSystemVersion so the header badge
// and workflow footer stay consistent across both UIs: the badge uses
// git_commit (7 chars) and wf-mode carries the " Beta" suffix, with wf-git
// showing the 12-char commit in the footer.
async function loadSystemVersion() {
  try {
    const info = await API.systemVersion();
    const badge = byId("system-version");
    if (badge) {
      const sha = info.git_commit || "unknown";
      const wf = info.workflow || "v5";
      badge.textContent = `Workflow ${wf} · ${sha.substring(0, 7)}`;
    }
    const wfMode = byId("wf-mode");
    if (wfMode) wfMode.textContent = (info.workflow || "v5").toUpperCase() + " Beta";
    const wfGit = byId("wf-git");
    if (wfGit) wfGit.textContent = info.git_commit ? info.git_commit.substring(0, 12) : "—";
    const wfSchema = byId("wf-schema");
    if (wfSchema) wfSchema.textContent = info.schema_version || "—";
    const wfApi = byId("wf-api");
    if (wfApi) wfApi.textContent = info.api_version || "—";
  } catch {
    // Version display is non-critical
  }
}

async function loadModelConfig() {
  try {
    const c = await API.getModelConfig();
    if (c.configured) {
      state.modelConfigured = true;
      byId("header-model-status").textContent = `${c.provider}/${c.model}`;
    }
    if (c.suggested_models) window._suggestedModels = c.suggested_models;
  } catch {}
}

async function loadTargets() {
  try {
    const caps = await API.listTargets();
    const sel = byId("execution-target");
    sel.innerHTML = "";
    if (!caps?.length) {
      sel.innerHTML = '<option value="">无可用平台</option>';
      byId("header-target-status").textContent = "未配置";
      return;
    }
    for (const c of caps) {
      // Build a user-friendly label: show target_id and resource info.
      let label = c.target_id;
      if (c.foam_version) label += ` · OpenFOAM ${c.foam_version}`;
      if (c.cpu_count) label += ` · ${c.cpu_count}核`;
      if (c.memory_gb) label += ` · ${Math.round(c.memory_gb)}GB`;
      label += c.available ? " (可用)" : " (不可用)";
      sel.appendChild(el("option", { value: c.target_id, text: label }));
    }
    const avail = caps.find(c => c.available);
    if (avail) {
      byId("header-target-status").textContent = avail.target_id;
    } else {
      byId("header-target-status").textContent = `${caps.length}个平台(待验证)`;
    }
  } catch {
    byId("header-target-status").textContent = "检查失败";
  }
}

async function loadWorkstationStatus() {
  const mini = byId("workstation-panel-mini");
  if (!mini) return;
  const ind = mini.querySelector(".ws-indicator");
  const txt = mini.querySelector(".ws-text-mini");
  try {
    const s = await API.workstationStatus();
    if (s.connected) {
      ind.dataset.state = "ok";
      txt.textContent = `已连接 ${s.host}`;
    } else {
      ind.dataset.state = "error";
      txt.textContent = s.error || "未连接";
    }
  } catch {
    ind.dataset.state = "unknown";
    txt.textContent = "检查失败";
  }
}

// ---- Workstation Configuration Panel ----
// Auto-discovery, probe, host-key confirmation, save, set-default, delete.
// Inserted dynamically into the left panel after #workstation-panel-mini so
// the three-column HTML layout in index.html is left untouched.
// No input fields for private keys / OpenFOAM paths / remote dirs / passwords;
// the only user input is an optional display name via prompt() when saving.

const wsState = {
  candidates: [],
  profiles: [],
  probing: new Set(),
  error: null,
  expanded: false,
  connecting: false,
  showConnectForm: false,
};

function wsStatusKey(status) {
  const s = String(status || "").toLowerCase();
  if (["connected", "ok", "online", "reachable", "active", "ready"].includes(s)) return "ok";
  if (["disconnected", "error", "failed", "offline", "unreachable", "rejected"].includes(s)) return "error";
  if (["probing", "pending", "queued", "saving", "testing"].includes(s)) return "pending";
  return "unknown";
}

function wsStatusLabel(status) {
  const map = {
    connected: "已连接", ok: "已连接", online: "在线", reachable: "可达", active: "活跃", ready: "就绪",
    disconnected: "未连接", error: "错误", failed: "失败", offline: "离线", unreachable: "不可达", rejected: "已拒绝",
    pending: "检测中", probing: "检测中", queued: "排队中", saving: "保存中", testing: "测试中",
    unknown: "未知",
  };
  return map[String(status || "").toLowerCase()] || status || "未知";
}

function wsMetaRow(label, value) {
  return el("div", { class: "ws-meta-row" }, [
    el("span", { class: "ws-meta-label", text: label }),
    el("span", { class: "ws-meta-value", text: value == null ? "—" : String(value) }),
  ]);
}

function updateCandidate(candidateId, patch) {
  const idx = wsState.candidates.findIndex((c) => (c.candidate_id || c.id) === candidateId);
  if (idx >= 0) {
    wsState.candidates[idx] = {
      ...wsState.candidates[idx],
      ...patch,
      ...(patch && patch.candidate ? patch.candidate : {}),
      probe_result: (patch && patch.probe_result) || patch,
    };
  }
}

function updateProfile(profileId, patch) {
  const idx = wsState.profiles.findIndex((p) => (p.profile_id || p.id) === profileId);
  if (idx >= 0) {
    wsState.profiles[idx] = {
      ...wsState.profiles[idx],
      ...patch,
      ...(patch && patch.profile ? patch.profile : {}),
      probe_result: (patch && patch.probe_result) || patch,
    };
  }
}

function renderWorkstationPanel() {
  const mini = byId("workstation-panel-mini");
  if (!mini) return;
  let panel = byId("ws-config-panel");
  if (!panel) {
    panel = el("div", { id: "ws-config-panel", class: "ws-config-panel" });
    mini.parentNode.insertBefore(panel, mini.nextSibling);
  }
  panel.innerHTML = "";

  // Collapsible header (click to toggle)
  panel.appendChild(el("button", {
    type: "button",
    class: "ws-config-header" + (wsState.expanded ? " expanded" : ""),
    onclick: () => { wsState.expanded = !wsState.expanded; renderWorkstationPanel(); },
  }, [
    el("span", { class: "ws-config-title", text: "工作站配置" }),
    el("span", { class: "ws-config-count-inline", text: `${wsState.profiles.length} 个已保存` }),
    el("span", { class: "ws-config-toggle", text: wsState.expanded ? "▾" : "▸" }),
  ]));

  if (!wsState.expanded) return;

  const body = el("div", { class: "ws-config-body" });

  // Toolbar
  body.appendChild(el("div", { class: "ws-config-toolbar" }, [
    el("button", {
      type: "button",
      class: "ws-btn ws-btn-primary",
      text: wsState.probing.has("__discover__") ? "发现中…" : "自动发现",
      disabled: wsState.probing.has("__discover__"),
      onclick: () => handleDiscover(),
    }),
    el("button", {
      type: "button",
      class: "ws-btn",
      text: wsState.showConnectForm ? "取消连接" : "添加工作站",
      onclick: () => { wsState.showConnectForm = !wsState.showConnectForm; wsState.error = null; renderWorkstationPanel(); },
    }),
    el("span", { class: "ws-config-count", text: `${wsState.candidates.length} 个候选 · ${wsState.profiles.length} 个已保存` }),
  ]));

  // Connect form
  if (wsState.showConnectForm) {
    const form = el("div", { class: "ws-connect-form" });

    const hostInput = el("input", {
      type: "text", class: "ws-input", placeholder: "主机 IP 或域名",
      id: "ws-connect-host",
    });
    const userInput = el("input", {
      type: "text", class: "ws-input", placeholder: "SSH 用户名",
      id: "ws-connect-user",
    });
    const passInput = el("input", {
      type: "password", class: "ws-input", placeholder: "SSH 密码",
      id: "ws-connect-pass",
    });
    const nameInput = el("input", {
      type: "text", class: "ws-input", placeholder: "显示名称（可选）",
      id: "ws-connect-name",
    });

    form.appendChild(el("div", { class: "ws-form-row" }, [
      el("label", { class: "ws-form-label", text: "主机" }),
      hostInput,
    ]));
    form.appendChild(el("div", { class: "ws-form-row" }, [
      el("label", { class: "ws-form-label", text: "用户名" }),
      userInput,
    ]));
    form.appendChild(el("div", { class: "ws-form-row" }, [
      el("label", { class: "ws-form-label", text: "密码" }),
      passInput,
    ]));
    form.appendChild(el("div", { class: "ws-form-row" }, [
      el("label", { class: "ws-form-label", text: "显示名" }),
      nameInput,
    ]));

    const progressEl = wsState.connecting
      ? el("div", { class: "ws-connect-progress", text: "正在连接并配置…" })
      : null;

    form.appendChild(el("div", { class: "ws-form-actions" }, [
      el("button", {
        type: "button",
        class: "ws-btn ws-btn-primary",
        text: wsState.connecting ? "配置中…" : "一键连接",
        disabled: wsState.connecting,
        onclick: () => handleConnect(hostInput.value, userInput.value, passInput.value, nameInput.value),
      }),
      progressEl,
    ]));

    body.appendChild(form);
  }

  // Error banner
  if (wsState.error) {
    body.appendChild(el("div", { class: "ws-error", text: wsState.error }));
  }

  // Discovered candidates
  if (wsState.candidates.length) {
    body.appendChild(el("div", { class: "ws-section-title", text: "发现的候选工作站" }));
    const list = el("div", { class: "ws-candidate-list" });
    for (const c of wsState.candidates) list.appendChild(renderCandidateCard(c));
    body.appendChild(list);
  }

  // Saved profiles
  if (wsState.profiles.length) {
    body.appendChild(el("div", { class: "ws-section-title", text: "已保存的工作站" }));
    const list = el("div", { class: "ws-profile-list" });
    for (const p of wsState.profiles) list.appendChild(renderProfileCard(p));
    body.appendChild(list);
  }

  // Empty hint
  if (!wsState.candidates.length && !wsState.profiles.length && !wsState.error) {
    body.appendChild(el("div", { class: "ws-empty-hint", text: "点击「自动发现」扫描可用工作站，或在已保存列表中管理连接。" }));
  }

  panel.appendChild(body);
}

function renderCandidateCard(candidate) {
  const cid = candidate.candidate_id || candidate.id;
  const isBusy = wsState.probing.has(cid);
  const probe = candidate.probe_result || {};
  const name = candidate.display_name || candidate.name || candidate.host_alias || cid || "未命名工作站";
  const hostAlias = candidate.host_alias || candidate.host || probe.host_alias || "—";
  const status = candidate.connection_status || candidate.status || (probe.connected ? "connected" : "unknown");
  const ofVersion = candidate.openfoam_version || probe.openfoam_version || "—";
  const scheduler = candidate.scheduler || probe.scheduler || "—";
  const fingerprint = candidate.host_key_fingerprint || probe.host_key_fingerprint || candidate.fingerprint || probe.fingerprint;

  return el("div", { class: "ws-candidate-card" }, [
    el("div", { class: "ws-card-header" }, [
      el("span", { class: "ws-card-name", text: name }),
      el("span", { class: `ws-status-badge ws-status-${wsStatusKey(status)}`, text: wsStatusLabel(status) }),
    ]),
    el("div", { class: "ws-card-meta" }, [
      wsMetaRow("Host", hostAlias),
      wsMetaRow("OpenFOAM", ofVersion),
      wsMetaRow("调度器", scheduler),
      fingerprint ? wsMetaRow("指纹", fingerprint) : null,
    ]),
    candidate.probe_error ? el("div", { class: "ws-card-error", text: candidate.probe_error }) : null,
    el("div", { class: "ws-card-actions" }, [
      el("button", {
        type: "button",
        class: "ws-btn",
        text: isBusy ? "检测中…" : "测试连接",
        disabled: isBusy,
        onclick: () => handleProbe(cid),
      }),
      el("button", {
        type: "button",
        class: "ws-btn",
        text: "确认指纹",
        disabled: isBusy,
        onclick: () => handleConfirmHostKey(cid),
      }),
      el("button", {
        type: "button",
        class: "ws-btn ws-btn-primary",
        text: "保存",
        disabled: isBusy,
        onclick: () => handleSave(cid),
      }),
    ]),
  ]);
}

function renderProfileCard(profile) {
  const pid = profile.profile_id || profile.id;
  const isBusy = wsState.probing.has(pid);
  const probe = profile.probe_result || {};
  const name = profile.display_name || profile.name || profile.host_alias || pid || "未命名工作站";
  const hostAlias = profile.host_alias || profile.host || probe.host_alias || "—";
  const status = profile.connection_status || profile.status || (probe.connected ? "connected" : "unknown");
  const ofVersion = profile.openfoam_version || probe.openfoam_version || "—";
  const scheduler = profile.scheduler || probe.scheduler || "—";
  const isDefault = profile.is_default || profile.default;

  return el("div", { class: "ws-profile-card" + (isDefault ? " ws-profile-default" : "") }, [
    el("div", { class: "ws-card-header" }, [
      el("span", { class: "ws-card-name", text: name }),
      isDefault ? el("span", { class: "ws-default-badge", text: "默认" }) : null,
      el("span", { class: `ws-status-badge ws-status-${wsStatusKey(status)}`, text: wsStatusLabel(status) }),
    ]),
    el("div", { class: "ws-card-meta" }, [
      wsMetaRow("Host", hostAlias),
      wsMetaRow("OpenFOAM", ofVersion),
      wsMetaRow("调度器", scheduler),
    ]),
    profile.probe_error ? el("div", { class: "ws-card-error", text: profile.probe_error }) : null,
    el("div", { class: "ws-card-actions" }, [
      el("button", {
        type: "button",
        class: "ws-btn",
        text: isBusy ? "检测中…" : "重新检测",
        disabled: isBusy,
        onclick: () => handleRetest(pid),
      }),
      el("button", {
        type: "button",
        class: "ws-btn",
        text: "设为默认",
        disabled: isBusy || isDefault,
        onclick: () => handleSetDefault(pid),
      }),
      el("button", {
        type: "button",
        class: "ws-btn ws-btn-danger",
        text: "删除",
        disabled: isBusy,
        onclick: () => handleDeleteProfile(pid),
      }),
    ]),
  ]);
}

async function loadWorkstationProfiles() {
  try {
    const resp = await API.listWorkstationProfiles();
    wsState.profiles = Array.isArray(resp) ? resp : (resp.profiles || resp.workstations || []);
  } catch (e) {
    wsState.profiles = [];
    wsState.error = `加载工作站列表失败: ${e.message}`;
  }
  renderWorkstationPanel();
}

async function handleDiscover() {
  wsState.error = null;
  wsState.probing.add("__discover__");
  wsState.expanded = true;  // Auto-expand to show progress
  renderWorkstationPanel();
  try {
    const resp = await API.discoverWorkstations();
    wsState.candidates = Array.isArray(resp) ? resp : (resp.candidates || resp.workstations || []);
    // Keep panel expanded if we found candidates
    if (wsState.candidates.length > 0) {
      wsState.expanded = true;
    }
  } catch (e) {
    wsState.error = `自动发现失败: ${e.message}`;
    wsState.candidates = [];
  } finally {
    wsState.probing.delete("__discover__");
    renderWorkstationPanel();
  }
}

async function handleConnect(host, username, password, displayName) {
  if (!host || !username || !password) {
    wsState.error = "请填写主机、用户名和密码";
    renderWorkstationPanel();
    return;
  }
  wsState.error = null;
  wsState.connecting = true;
  renderWorkstationPanel();
  try {
    const resp = await API.connectWorkstation({
      host: host.trim(),
      username: username.trim(),
      password: password,
      display_name: displayName ? displayName.trim() : null,
    });
    wsState.showConnectForm = false;
    wsState.connecting = false;
    // Refresh profiles list to include the new workstation.
    await loadWorkstationProfiles();
    // Refresh execution-target dropdown so the new workstation appears.
    await loadTargets();
    // Show success summary.
    if (resp.profile) {
      const p = resp.profile;
      const steps = (resp.steps || []).filter(s => s.success).length;
      const totalSteps = (resp.steps || []).length;
      const ofStatus = p.openfoam_available ? `OpenFOAM ${p.openfoam_version || ""}`.trim() : "OpenFOAM 未检测到";
      wsState.error = null;
      addMessage("system", `工作站「${p.display_name}」连接成功！${ofStatus}，${p.cpu_count} 核 CPU，${Math.round((p.memory_bytes || 0) / 1073741824)} GB 内存。完成 ${steps}/${totalSteps} 步配置。`);
    }
  } catch (e) {
    wsState.connecting = false;
    let errMsg = e.message;
    try {
      const detail = JSON.parse(e.message);
      if (detail.error_message) errMsg = detail.error_message;
    } catch (_) {}
    wsState.error = `连接失败: ${errMsg}`;
    renderWorkstationPanel();
  } finally {
    wsState.connecting = false;
    renderWorkstationPanel();
  }
}

async function handleProbe(candidateId) {
  wsState.probing.add(candidateId);
  renderWorkstationPanel();
  try {
    const result = await API.probeWorkstation(candidateId);
    updateCandidate(candidateId, result);
  } catch (e) {
    updateCandidate(candidateId, { probe_error: e.message, connection_status: "error" });
  } finally {
    wsState.probing.delete(candidateId);
    renderWorkstationPanel();
  }
}

async function handleConfirmHostKey(candidateId) {
  wsState.probing.add(candidateId);
  renderWorkstationPanel();
  try {
    const result = await API.confirmHostKey(candidateId);
    updateCandidate(candidateId, result);
  } catch (e) {
    wsState.error = `确认指纹失败: ${e.message}`;
  } finally {
    wsState.probing.delete(candidateId);
    renderWorkstationPanel();
  }
}

async function handleSave(candidateId) {
  // The only allowed user input: an optional display name via prompt().
  const displayName = prompt("请输入工作站显示名称（可选，直接确定可跳过）：", "");
  if (displayName === null) return; // user cancelled
  wsState.probing.add(candidateId);
  renderWorkstationPanel();
  try {
    const trimmed = displayName.trim();
    await API.saveWorkstation(candidateId, trimmed || undefined);
    await loadWorkstationProfiles();
  } catch (e) {
    wsState.error = `保存失败: ${e.message}`;
  } finally {
    wsState.probing.delete(candidateId);
    renderWorkstationPanel();
  }
}

async function handleSetDefault(profileId) {
  wsState.probing.add(profileId);
  renderWorkstationPanel();
  try {
    await API.setDefaultWorkstation(profileId);
    await loadWorkstationProfiles();
  } catch (e) {
    wsState.error = `设为默认失败: ${e.message}`;
  } finally {
    wsState.probing.delete(profileId);
    renderWorkstationPanel();
  }
}

async function handleDeleteProfile(profileId) {
  if (!confirm("确认删除该工作站配置？")) return;
  wsState.probing.add(profileId);
  renderWorkstationPanel();
  try {
    await API.deleteWorkstation(profileId);
    await loadWorkstationProfiles();
  } catch (e) {
    wsState.error = `删除失败: ${e.message}`;
  } finally {
    wsState.probing.delete(profileId);
    renderWorkstationPanel();
  }
}

async function handleRetest(profileId) {
  wsState.probing.add(profileId);
  renderWorkstationPanel();
  try {
    const result = await API.testWorkstation(profileId);
    updateProfile(profileId, result);
  } catch (e) {
    updateProfile(profileId, { probe_error: e.message });
    wsState.error = `重新检测失败: ${e.message}`;
  } finally {
    wsState.probing.delete(profileId);
    renderWorkstationPanel();
  }
}

// ---- Render all ----
function renderAll() {
  renderSessionList();
  renderStudyList();
  renderDraftViewer();
  updateActionBar();
  updateComposer();
}

// ---- Event bindings ----
function bindEvents() {
  byId("composer-form").addEventListener("submit", (e) => {
    e.preventDefault();
    sendUserMessage(byId("research-input").value);
  });
  byId("research-input").addEventListener("input", updateComposer);
  byId("new-session-btn").addEventListener("click", createNewSession);

  // Dialogs
  byId("open-model-settings").addEventListener("click", () => byId("model-settings").showModal());
  byId("open-target-settings").addEventListener("click", () => byId("target-settings").showModal());

  byId("configure-model").addEventListener("click", async () => {
    const provider = byId("model-provider").value;
    const key = byId("model-api-key").value;
    const model = byId("model-id").value;
    if (!key || key.length < 5) { byId("model-config-state").textContent = "API Key 太短"; return; }
    try {
      byId("model-config-state").textContent = "正在连接...";
      const resp = await API.configureModel({ provider, model, api_key: key });
      if (resp.configured && !resp.is_mock) {
        byId("model-config-state").textContent = `配置成功: ${resp.provider}/${resp.model}`;
        byId("header-model-status").textContent = `${resp.provider}/${resp.model}`;
        state.modelConfigured = true;
        setTimeout(() => byId("model-settings").close(), 1000);
      } else {
        byId("model-config-state").textContent = "已保存但仍在 Mock 模式（可能 openai 库未安装）";
      }
    } catch (e) { byId("model-config-state").textContent = "配置失败: " + e.message; }
  });

  byId("model-provider").addEventListener("change", () => {
    const provider = byId("model-provider").value;
    const models = (window._suggestedModels || {})[provider] || [];
    const input = byId("model-id");
    if (models.length) { input.value = models[0]; input.placeholder = `如 ${models.join(", ")}`; }
  });

  // Workstation config
  byId("ws-save-config").addEventListener("click", async () => {
    try {
      await API.configureWorkstation({
        host: byId("ws-input-host").value, username: byId("ws-input-user").value,
        port: parseInt(byId("ws-input-port").value) || 22,
        identity_file: byId("ws-input-key").value, known_hosts_file: byId("ws-input-knownhosts").value,
      });
      byId("ws-config-state").textContent = "配置成功";
      setTimeout(() => { byId("workstation-settings").close(); loadWorkstationStatus(); }, 800);
    } catch (e) { byId("ws-config-state").textContent = "配置失败: " + e.message; }
  });
}

// ---- Init ----
async function init() {
  bindEvents();
  await Promise.all([initSession(), loadSystemVersion(), loadModelConfig(), loadTargets(), loadWorkstationStatus()]);
  updateComposer();
}

document.addEventListener("DOMContentLoaded", init);

// Auto-load saved workstation profiles and render the configuration panel on
// page load. This runs alongside init() (separate listener) so existing
// startup logic is untouched.
document.addEventListener("DOMContentLoaded", () => {
  renderWorkstationPanel(); // create collapsed panel header immediately
  loadWorkstationProfiles(); // populate saved profiles in the background
});
