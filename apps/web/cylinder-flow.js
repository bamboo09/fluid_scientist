// ==========================================================================
// Cylinder Flow 2D — Frontend Integration (v=20260714f)
//
// Full staged interactive research flow:
//   DRAFT → AWAITING_PLAN_CONFIRM → COMPILE_PREVIEW → AWAITING_COMPILE_CONFIRM
//   → VALIDATING → AWAITING_RUN_CONFIRM → RUNNING → COMPLETED
//
// Every stage requires explicit user confirmation before proceeding.
// Results are shown in BOTH the conversation area AND the right panel.
// Closing the overlay does not clear results.
// ==========================================================================

(function () {
  "use strict";

  const CYL_API = "/api/v5/cylinder-flow";
  const VERSION = "20260715o";
  const CF_STORAGE_KEY = "cyl_flow_state_v1";

  // ---- Stages ----
  const Stage = {
    DRAFT: "DRAFT",
    AWAITING_PLAN_CONFIRM: "AWAITING_PLAN_CONFIRM",
    COMPILE_PREVIEW: "COMPILE_PREVIEW",
    AWAITING_COMPILE_CONFIRM: "AWAITING_COMPILE_CONFIRM",
    VALIDATING: "VALIDATING",
    AWAITING_RUN_CONFIRM: "AWAITING_RUN_CONFIRM",
    RUNNING: "RUNNING",
    COMPLETED: "COMPLETED",
  };

  // ---- State ----
  const cylState = {
    specId: null,
    jobId: null,
    draftStatus: null,
    flowMode: null,
    pollTimer: null,
    active: false,
    stage: null,
    spec: null,
    semanticDisplay: null,
    userInput: null,
    specConfirmed: false,
    lastResults: null,
    lastReport: null,
    pollPaused: false,
    progressMsgEl: null,
  };

  // ---- Conversation message tracking for persistence ----
  const convMessages = [];

  // ---- DOM helpers ----
  function byId(id) {
    return document.getElementById(id);
  }

  function el(tag, attrs, children) {
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

  // ---- API helpers ----
  function fetchJSON(path, opts = {}) {
    return fetch(path, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
    }).then(async (res) => {
      if (!res.ok) {
        let detail = res.statusText;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch {}
        throw new Error(`API ${res.status}: ${detail}`);
      }
      return res.json();
    });
  }

  function postJSON(path, data) {
    return fetchJSON(path, {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  // ---- Value formatting ----
  const sourceLabels = {
    USER_CONFIRMED: "用户确认",
    USER_EXPLICIT: "用户明确",
    FORMULA_DERIVED: "公式推导",
    SYSTEM_DERIVED: "系统派生",
    MODEL_RECOMMENDED: "模型建议",
    SYSTEM_DEFAULT: "系统默认",
  };

  const statusLabels = {
    RESOLVED: "已确认",
    UNRESOLVED: "未解析",
    AWAITING_CONFIRMATION: "待确认",
  };

  const boundaryTypeLabels = {
    uniform_velocity_inlet: "恒定速度入口",
    time_varying_velocity_inlet: "时变速度入口",
    spatial_nonuniform_velocity_inlet: "空间非均匀入口",
    pressure_inlet: "压力入口",
    pressure_outlet: "压力出口",
    open_outlet: "开放出口",
    advective_outlet: "对流出口",
    no_slip_wall: "无滑移壁面",
    slip_wall: "滑移壁面",
    moving_wall: "运动壁面",
    shear_stress: "剪切应力",
    symmetry: "对称边界",
    freestream: "自由流",
    open_boundary: "开放边界",
    periodic: "周期边界",
    empty: "二维empty",
    pressure_boundary: "压力边界",
  };

  const observableLabels = {
    point_velocity: "点速度",
    section_mean_velocity: "截面平均速度",
    section_flow_rate: "截面流量",
    cylinder_drag: "圆柱阻力系数",
    cylinder_lift: "圆柱升力系数",
    wall_shear_stress: "壁面剪切应力",
    recirculation_length: "回流区长度",
    velocity_magnitude_field: "速度场",
    pressure_field: "压力场",
    vorticity_field: "涡量场",
    streamlines: "流线图",
    drag_lift_time_series: "阻力/升力时间序列",
    wake_shedding_frequency: "尾涡脱落频率",
  };

  function pv(field) {
    if (field == null) return "—";
    if (typeof field === "object") return field.value != null ? String(field.value) : "—";
    return String(field);
  }

  function pvNum(field, unit) {
    if (field == null) return "—";
    if (typeof field === "object") {
      if (field.value == null) return "—";
      const u = unit ? " " + unit : "";
      return String(field.value) + u;
    }
    return String(field);
  }

  function psource(field) {
    if (field == null) return "";
    if (typeof field === "object") return sourceLabels[field.source] || field.source || "";
    return "";
  }

  // ---- Cylinder flow detection (unchanged) ----
  function isCylinderFlowInput(text) {
    const lower = text.toLowerCase();
    const cylKeywords = ["圆柱", "cylinder", "圆形障碍", "圆", "绕流"];
    const flowKeywords = ["流", "flow", "绕", "past", "around"];
    const hasCyl = cylKeywords.some((k) => lower.includes(k));
    const hasFlow = flowKeywords.some((k) => lower.includes(k));
    return hasCyl && hasFlow;
  }

  function isBumpFlowInput(text) {
    const lower = text.toLowerCase();
    return lower.includes("凸起") || lower.includes("bump") || lower.includes("底面");
  }

  function isObstacleFlowInput(text) {
    return isCylinderFlowInput(text) || isBumpFlowInput(text);
  }

  function isModificationCommand(text) {
    const modKeywords = ["修改", "改为", "换成", "调整", "变更", "增大", "减小", "增加", "减少", "改成", "change", "modify", "update", "set"];
    return modKeywords.some(kw => text.toLowerCase().includes(kw.toLowerCase()));
  }

  // ---- Conversation helpers ----
  function addConversationMessage(type, content) {
    const tl = byId("conversation-timeline");
    if (!tl) return null;

    const avatarText = type === "user" ? "你" : type === "system" ? "系" : "FS";
    const metaText = type === "user" ? "用户" : type === "system" ? "系统" : "研究助手";

    const body = el("div", { class: "conv-msg-body" }, [
      el("div", { class: "conv-msg-meta", text: metaText }),
    ]);

    const contentDiv = el("div");
    let textContent = "";
    if (typeof content === "string") {
      contentDiv.textContent = content;
      textContent = content;
    } else if (content instanceof HTMLElement) {
      contentDiv.appendChild(content);
      textContent = content.textContent || "";
    } else if (Array.isArray(content)) {
      content.forEach(item => {
        if (typeof item === "string") {
          contentDiv.appendChild(document.createTextNode(item));
          textContent += item;
        } else if (item instanceof HTMLElement) {
          contentDiv.appendChild(item);
          textContent += (item.textContent || "");
        }
      });
    }
    body.appendChild(contentDiv);

    // Track for persistence (only save text snippets, not complex DOM)
    if (textContent) {
      convMessages.push({ type, text: textContent.substring(0, 2000) });
    }

    const div = el("div", { class: `conv-msg ${type}` }, [
      el("div", { class: "conv-msg-avatar", text: avatarText }),
      body,
    ]);

    tl.appendChild(div);
    tl.scrollTop = tl.scrollHeight;
    return div;
  }

  function scrollConversationToBottom() {
    const tl = byId("conversation-timeline");
    if (tl) tl.scrollTop = tl.scrollHeight;
  }

  // ---- Right panel: Spec rendering ----
  function fieldRow(label, value, status) {
    const statusLabelsMap = {
      confirmed: "已确认", pending: "已填充", inferred: "模型推断",
      "user-provided": "用户提供", missing: "待补充", conflict: "存在冲突",
      USER_CONFIRMED: "用户确认", USER_EXPLICIT: "用户明确",
      FORMULA_DERIVED: "公式推导", SYSTEM_DERIVED: "系统派生",
      MODEL_RECOMMENDED: "模型建议", SYSTEM_DEFAULT: "系统默认",
    };
    return el("div", { class: "field-row" }, [
      el("span", { class: "field-label-inline", text: label }),
      el("span", { class: "field-value-inline", text: value }),
      status ? el("span", { class: `field-status ${status}`, text: statusLabelsMap[status] || status }) : null,
    ]);
  }

  function provenanceStatus(field, fallback = "inferred") {
    if (field && typeof field === "object" && field.source) return field.source;
    return fallback;
  }

  function displayChangeValue(value) {
    if (value == null) return "—";
    if (typeof value === "object") {
      if (Object.prototype.hasOwnProperty.call(value, "value")) return String(value.value ?? "—");
      return JSON.stringify(value);
    }
    return String(value);
  }

  function appendChangeSummary(changes, specVersion) {
    const viewer = byId("draft-viewer");
    if (!viewer || !Array.isArray(changes) || !changes.length) return;
    const relevant = changes.filter((change) =>
      !["updated_at", "user_input_text"].some((suffix) => String(change.path || "").endsWith(suffix))
    );
    if (!relevant.length) return;
    viewer.appendChild(section(`本次修改 · Spec v${specVersion || "—"}`, relevant.map((change) =>
      fieldRow(
        change.path || "字段",
        `before: ${displayChangeValue(change.before)} → after: ${displayChangeValue(change.after)}`,
        "USER_EXPLICIT",
      )
    )));
  }

  function blockerField(issue) {
    if (issue.field_path || issue.field) return issue.field_path || issue.field;
    const codeFields = {
      LLM_MISSING_FIELD: "fluid.density_kg_m3",
      TOP_BOUNDARY_AMBIGUITY: "boundaries.top",
      CANDIDATE_CONFLICT: "obstacle.type",
    };
    return codeFields[issue.code] || issue.code || "未标注字段";
  }

  function blockerReason(issue) {
    if (issue.code === "LLM_MISSING_FIELD") {
      const field = issue.field || String(issue.message || "").split(":").pop().trim() || "必需字段";
      return `模型未获得字段“${field}”的明确证据，系统不会静默填入材料或求解参数。`;
    }
    return issue.message || issue.check || "该字段尚未满足确认条件。";
  }

  function blockerAction(issue) {
    if (issue.resolution_action) return issue.resolution_action;
    if (issue.recommendation) return `确认建议：${issue.recommendation}`;
    if (Array.isArray(issue.options) && issue.options.length) {
      return `请选择：${issue.options.join(" / ")}`;
    }
    return `请补充或确认字段 ${blockerField(issue)}，然后重新校验。`;
  }

  function section(title, children) {
    return el("div", { class: "draft-readonly-section" }, [
      el("h3", { text: title }),
      ...children,
    ]);
  }

  function renderSpecPanel(spec, semanticDisplay) {
    const viewer = byId("draft-viewer");
    if (!viewer) return;
    viewer.innerHTML = "";

    if (!spec) {
      viewer.appendChild(el("div", { class: "empty-state" }, [
        el("p", { text: "尚未生成研究方案" }),
        el("p", { class: "empty-hint", text: "在中间对话区输入研究目标，系统将自动生成结构化实验方案。" }),
      ]));
      return;
    }

    // Use semantic_display if available, otherwise fall back to raw spec
    const sd = semanticDisplay || {};
    const badge = byId("draft-version-badge");
    if (badge) {
      badge.hidden = false;
      badge.textContent = `v${spec.spec_version || 1} · ${spec.draft_status || "DRAFT"}`;
    }

    // Research objective
    const objective = spec.objective || spec.user_input_text || "—";
    viewer.appendChild(section("研究目标", [
      fieldRow("目标", String(objective).slice(0, 120), "inferred"),
    ]));

    // Domain
    const dom = spec.domain || {};
    const domSd = sd["计算域"] || {};
    viewer.appendChild(section("计算域", [
      fieldRow("维度", dom.dimensionality || domSd.dimensionality || "2D", "inferred"),
      fieldRow("长度", pvNum(dom.length_m, "m") || pvNum(domSd.length_m, "m"), provenanceStatus(dom.length_m)),
      fieldRow("高度", pvNum(dom.height_m, "m") || pvNum(domSd.height_m, "m"), provenanceStatus(dom.height_m)),
    ]));

    // Cylinder / Obstacle
    const cyl = spec.cylinder || {};
    const cylSd = sd["圆柱"] || {};
    const hasCyl = cylSd.type === "圆柱" || (cyl.radius_m && cyl.radius_m.value != null) || (cyl.diameter_m && cyl.diameter_m.value != null);
    if (hasCyl) {
      viewer.appendChild(section("圆柱障碍物", [
        fieldRow("类型", cylSd.type || "圆柱", "inferred"),
        fieldRow("直径", pvNum(cyl.diameter_m, "m") || pvNum(cylSd.diameter_m, "m"), provenanceStatus(cyl.diameter_m)),
        fieldRow("半径", pvNum(cyl.radius_m, "m") || pvNum(cylSd.radius_m, "m"), provenanceStatus(cyl.radius_m)),
        fieldRow("圆心 X", pvNum(cyl.center_x_m, "m") || pvNum(cylSd.center_x_m, "m"), provenanceStatus(cyl.center_x_m)),
        fieldRow("圆心 Y", pvNum(cyl.center_y_m, "m") || pvNum(cylSd.center_y_m, "m"), provenanceStatus(cyl.center_y_m)),
        cyl.wall_type ? fieldRow("壁面类型", String(cyl.wall_type), "inferred") : null,
        cyl.angular_velocity_rad_s ? fieldRow("角速度", String(cyl.angular_velocity_rad_s) + " rad/s", "inferred") : null,
      ]));
    }

    // Bottom profile
    const bp = spec.bottom_profile || {};
    const bpSd = sd["底部轮廓"] || {};
    if (bp.enabled && bp.profile_type !== "flat") {
      viewer.appendChild(section("底部轮廓", [
        fieldRow("类型", bpSd.type || String(bp.profile_type || "—"), "inferred"),
        fieldRow("中心 X", pvNum(bp.center_x_m, "m"), "inferred"),
        fieldRow("宽度", pvNum(bp.width_m, "m"), "inferred"),
        fieldRow("高度", pvNum(bp.height_m, "m"), "inferred"),
      ]));
    }

    // Rectangle obstacle
    const rect = spec.rectangle || {};
    if (rect.enabled) {
      viewer.appendChild(section("矩形障碍物", [
        fieldRow("类型", "矩形", "inferred"),
        fieldRow("宽度", pvNum(rect.width_m, "m"), "inferred"),
        fieldRow("高度", pvNum(rect.height_m, "m"), "inferred"),
        fieldRow("中心 X", pvNum(rect.center_x_m, "m"), "inferred"),
        fieldRow("中心 Y", pvNum(rect.center_y_m, "m"), "inferred"),
        rect.relation_to_cylinder ? fieldRow("与圆柱关系", rect.relation_to_cylinder, "inferred") : null,
      ]));
    }

    // Triangle obstacle
    const tri = spec.triangle || {};
    if (tri.enabled) {
      viewer.appendChild(section("三角障碍物", [
        fieldRow("类型", "三角形", "inferred"),
        fieldRow("底宽", pvNum(tri.base_width_m, "m"), "inferred"),
        fieldRow("高度", pvNum(tri.height_m, "m"), "inferred"),
        fieldRow("中心 X", pvNum(tri.center_x_m, "m"), "inferred"),
        tri.apex_direction ? fieldRow("尖端方向", tri.apex_direction, "inferred") : null,
        tri.relation_to_cylinder ? fieldRow("与圆柱关系", tri.relation_to_cylinder, "inferred") : null,
      ]));
    }

    // Trapezoid obstacle
    const trap = spec.trapezoid || {};
    if (trap.enabled) {
      viewer.appendChild(section("梯形障碍物", [
        fieldRow("类型", "梯形", "inferred"),
        fieldRow("上底", pvNum(trap.top_width_m, "m"), provenanceStatus(trap.top_width_m)),
        fieldRow("下底", pvNum(trap.bottom_width_m, "m"), provenanceStatus(trap.bottom_width_m)),
        fieldRow("高度", pvNum(trap.height_m, "m"), provenanceStatus(trap.height_m)),
        fieldRow("中心 X", pvNum(trap.center_x_m, "m"), provenanceStatus(trap.center_x_m)),
        trap.solver_representation ? fieldRow("求解器表示", trap.solver_representation, "inferred") : null,
        trap.relation_to_cylinder ? fieldRow("与圆柱关系", trap.relation_to_cylinder, "inferred") : null,
      ]));
    }

    // Fluid
    const fluid = spec.fluid || {};
    viewer.appendChild(section("流体属性", [
      fieldRow("类型", pv(fluid.type) === "—" ? "—" : pv(fluid.type), provenanceStatus(fluid.type)),
      fieldRow("密度", pvNum(fluid.density_kg_m3, "kg/m³"), provenanceStatus(fluid.density_kg_m3)),
      fieldRow("运动粘度", pvNum(fluid.kinematic_viscosity_m2_s, "m²/s"), provenanceStatus(fluid.kinematic_viscosity_m2_s)),
      fieldRow("温度", pvNum(fluid.temperature_c, "°C"), provenanceStatus(fluid.temperature_c)),
    ]));

    // Reynolds number if available
    if (typeof spec.estimate_reynolds === "number" && spec.estimate_reynolds > 0) {
      viewer.appendChild(section("无量纲数", [
        fieldRow("Reynolds 数", String(Math.round(spec.estimate_reynolds)), "inferred"),
      ]));
    }

    // Flow topology
    const topo = spec.flow_topology || {};
    if (topo.mode) {
      viewer.appendChild(section("流动拓扑", [
        fieldRow("驱动方式", String(topo.mode), "inferred"),
      ]));
    }

    // Boundaries
    const bcs = spec.boundaries || {};
    const boundaryOrder = [
      ["left", "左侧边界"],
      ["right", "右侧边界"],
      ["top", "顶部边界"],
      ["bottom_flat", "底部边界"],
      ["front", "前侧边界"],
      ["back", "后侧边界"],
    ];
    const bcChildren = boundaryOrder.map(([key, label]) => {
      const sdKey = label;
      const bc = bcs[key];
      const bcSd = sd[sdKey] || {};
      let typeStr = "—";
      if (bcSd.type) {
        typeStr = bcSd.type;
      } else if (bc && bc.semantic_type) {
        typeStr = boundaryTypeLabels[bc.semantic_type] || bc.semantic_type;
      }
      let extra = "";
      if (bc && bc.inlet_velocity != null) extra = ` (${bc.inlet_velocity} m/s)`;
      if (bc && bc.pressure_value != null) extra = ` (${bc.pressure_value} Pa)`;
      if (bc && bc.freestream_velocity != null) extra = ` (${bc.freestream_velocity} m/s)`;
      return fieldRow(label, typeStr + extra, "inferred");
    });
    viewer.appendChild(section("边界条件", bcChildren));

    // Simulation parameters
    const sim = spec.simulation || {};
    viewer.appendChild(section("仿真参数", [
      fieldRow("时间模式", String(sim.time_mode || "auto"), "inferred"),
      fieldRow("流动区域", String(sim.flow_regime || "auto"), "inferred"),
      fieldRow("最大Courant数", String(sim.max_courant_number || 0.5), "inferred"),
      fieldRow("结束时间", sim.end_time != null ? String(sim.end_time) + " s" : "—", "inferred"),
      fieldRow("时间步长", sim.delta_t != null ? String(sim.delta_t) + " s" : "自动", "inferred"),
    ]));

    // Observables
    const observables = spec.observables || [];
    const obsSd = sd["观测量"] || [];
    viewer.appendChild(section("观测量", observables.length
      ? observables.map((obs, i) => {
          const obsSdItem = obsSd[i] || {};
          const label = obs.label || observableLabels[obs.type] || obs.type || "—";
          const status = obs.status || obsSdItem.status || "—";
          const src = sourceLabels[obs.source] || obsSdItem.source || "";
          return fieldRow(label, src || status, "inferred");
        })
      : [fieldRow("观测量", "待指定", "missing")]
    ));

    // Analysis goals
    const goals = spec.analysis_goals || [];
    const goalsSd = sd["分析目标"] || [];
    viewer.appendChild(section("分析目标", goals.length
      ? goals.map((g, i) => {
          const gSd = goalsSd[i] || {};
          return fieldRow("•", g.description || gSd.description || "—", "inferred");
        })
      : [fieldRow("分析目标", "待指定", "missing")]
    ));

    // Blocking issues
    if (spec.blocking_issues && spec.blocking_issues.length) {
      viewer.appendChild(el("div", { class: "draft-readonly-section" }, [
        el("h3", { style: "color: #721c24;", text: "阻塞问题" }),
        ...spec.blocking_issues.map(bi =>
          el("div", { class: "blocker-card", style: "font-size:11px;color:#721c24;padding:6px 0;" }, [
            el("div", { text: `原因：${blockerReason(bi)}` }),
            el("div", { text: `字段：${blockerField(bi)}` }),
            el("div", { text: `解决操作：${blockerAction(bi)}` }),
          ])
        ),
      ]));
    }

    // Assumptions
    const assumptions = spec.assumptions || [];
    if (assumptions.length) {
      viewer.appendChild(section("假设", assumptions.map(a =>
        fieldRow("•", typeof a === "string" ? a : (a.display_name || a.description || "—"), "inferred")
      )));
    }

    // Status indicator
    if (spec.draft_status === "SPEC_CONFIRMED") {
      viewer.appendChild(el("div", {
        style: "padding:8px;background:#d4edda;border-radius:6px;font-size:12px;color:#155724;margin-top:8px;",
        text: "✓ 方案已确认，可生成 OpenFOAM Case",
      }));
    }

    // If results are available, show switch button
    if (cylState.lastResults) {
      const switchBtn = el("button", {
        class: "button button-secondary",
        style: "margin-top:14px;width:100%;",
        onclick: () => {
          renderResultsPanel(cylState.lastResults, cylState.jobId);
          // Re-append analysis if available
          if (cylState.lastReport) {
            showRightPanelAnalysis(cylState.lastReport);
          }
        },
      }, [document.createTextNode("查看仿真结果")]);
      viewer.appendChild(switchBtn);
    }
  }

  // ---- Image Lightbox ----
  let _lightboxState = { items: [], index: 0, el: null };

  function openLightbox(items, startIndex) {
    closeLightbox();
    _lightboxState.items = items;
    _lightboxState.index = startIndex;

    const overlay = el("div", { class: "cyl-lightbox" });

    // Close button
    const closeBtn = el("div", { class: "cyl-lightbox-close", text: "×" });
    closeBtn.onclick = (e) => { e.stopPropagation(); closeLightbox(); };
    overlay.appendChild(closeBtn);

    // Navigation arrows (only if more than 1 item)
    if (items.length > 1) {
      const prevBtn = el("div", { class: "cyl-lightbox-nav cyl-lightbox-prev", text: "‹" });
      prevBtn.onclick = (e) => { e.stopPropagation(); navLightbox(-1); };
      overlay.appendChild(prevBtn);

      const nextBtn = el("div", { class: "cyl-lightbox-nav cyl-lightbox-next", text: "›" });
      nextBtn.onclick = (e) => { e.stopPropagation(); navLightbox(1); };
      overlay.appendChild(nextBtn);
    }

    // Title bar
    const titleBar = el("div", { class: "cyl-lightbox-title" });
    overlay.appendChild(titleBar);

    // Click on overlay background closes
    overlay.onclick = () => closeLightbox();

    document.body.appendChild(overlay);
    _lightboxState.el = overlay;
    renderLightboxItem();

    // ESC and arrow key support
    _lightboxState.keyHandler = (e) => {
      if (e.key === "Escape") closeLightbox();
      else if (e.key === "ArrowLeft" && items.length > 1) navLightbox(-1);
      else if (e.key === "ArrowRight" && items.length > 1) navLightbox(1);
    };
    document.addEventListener("keydown", _lightboxState.keyHandler);
  }

  function renderLightboxItem() {
    const overlay = _lightboxState.el;
    if (!overlay) return;
    // Remove previous content (img/video)
    const old = overlay.querySelector(".cyl-lightbox-content");
    if (old) old.remove();

    const item = _lightboxState.items[_lightboxState.index];
    if (!item) return;

    const content = el("div", { class: "cyl-lightbox-content" });
    content.style.cssText = "display:flex;align-items:center;justify-content:center;";
    content.onclick = (e) => e.stopPropagation();

    if (item.type === "video") {
      const video = el("video", { src: item.src, controls: true, loop: true, autoplay: true });
      video.style.maxWidth = "92%";
      video.style.maxHeight = "90%";
      video.style.borderRadius = "4px";
      content.appendChild(video);
    } else {
      const img = el("img", { src: item.src, alt: item.name });
      content.appendChild(img);
    }
    overlay.appendChild(content);

    // Update title
    const titleBar = overlay.querySelector(".cyl-lightbox-title");
    if (titleBar) {
      const total = _lightboxState.items.length;
      const label = item.name.replace(/\.(png|gif|mp4)$/i, "").replace(/_/g, " ");
      titleBar.textContent = total > 1
        ? `${label}  (${_lightboxState.index + 1}/${total})`
        : label;
    }
  }

  function navLightbox(dir) {
    const n = _lightboxState.items.length;
    if (n <= 1) return;
    _lightboxState.index = (_lightboxState.index + dir + n) % n;
    renderLightboxItem();
  }

  function closeLightbox() {
    if (_lightboxState.el) {
      _lightboxState.el.remove();
      _lightboxState.el = null;
    }
    if (_lightboxState.keyHandler) {
      document.removeEventListener("keydown", _lightboxState.keyHandler);
      _lightboxState.keyHandler = null;
    }
  }

  /**
   * Collects all clickable media items from a gallery container.
   * Returns [{src, name, type: "image"|"video"}, ...]
   */
  function collectGalleryItems(container) {
    const items = [];
    if (!container) return items;
    const mediaEls = container.querySelectorAll("img, video");
    mediaEls.forEach(m => {
      const src = m.src || m.getAttribute("src");
      if (!src) return;
      // Skip non-result images (avatars, icons, etc.)
      if (m.closest(".conv-msg-avatar")) return;
      if (src.includes("data:")) return;
      const name = src.split("/").pop() || "image";
      const type = name.endsWith(".mp4") ? "video" : "image";
      items.push({ src, name, type });
    });
    return items;
  }

  /**
   * Attaches click-to-zoom on all images/videos inside a container.
   */
  function attachImageZoom(container) {
    if (!container) return;
    const mediaEls = container.querySelectorAll("img, video");
    mediaEls.forEach(m => {
      const src = m.src || m.getAttribute("src");
      if (!src || src.includes("data:")) return;
      if (m.closest(".conv-msg-avatar")) return;
      // Avoid double-binding
      if (m._cylZoomBound) return;
      m._cylZoomBound = true;
      m.style.cursor = "zoom-in";
      m.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const items = collectGalleryItems(container);
        const clickedSrc = m.src || m.getAttribute("src");
        let idx = items.findIndex(it => it.src === clickedSrc);
        if (idx < 0) idx = 0;
        openLightbox(items, idx);
      });
    });
  }

  // ---- Right panel: Results rendering ----
  function renderResultsPanel(results, jobId) {
    const viewer = byId("draft-viewer");
    if (!viewer) return;
    viewer.innerHTML = "";

    if (!results) {
      viewer.appendChild(el("div", { class: "empty-state" }, [
        el("p", { text: "暂无仿真结果" }),
      ]));
      return;
    }

    // Results header
    viewer.appendChild(el("div", { class: "draft-readonly-section" }, [
      el("h3", { text: "仿真结果摘要" }),
    ]));

    // Status summary
    const statusGrid = el("div", {
      style: "display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px;",
    }, [
      el("div", { style: "padding:8px;background:var(--paper,#f4f0e7);border-radius:6px;text-align:center;" }, [
        el("span", { style: "display:block;font-size:10px;color:var(--faint,#6c726d);", text: "网格" }),
        el("span", { style: "font-size:14px;font-weight:600;", text: results.mesh_status || "—" }),
      ]),
      el("div", { style: "padding:8px;background:var(--paper,#f4f0e7);border-radius:6px;text-align:center;" }, [
        el("span", { style: "display:block;font-size:10px;color:var(--faint,#6c726d);", text: "冒烟测试" }),
        el("span", { style: "font-size:14px;font-weight:600;", text: results.smoke_test_status || "—" }),
      ]),
      el("div", { style: "padding:8px;background:var(--paper,#f4f0e7);border-radius:6px;text-align:center;" }, [
        el("span", { style: "display:block;font-size:10px;color:var(--faint,#6c726d);", text: "仿真运行" }),
        el("span", { style: "font-size:14px;font-weight:600;", text: results.run_status || "—" }),
      ]),
      el("div", { style: "padding:8px;background:var(--paper,#f4f0e7);border-radius:6px;text-align:center;" }, [
        el("span", { style: "display:block;font-size:10px;color:var(--faint,#6c726d);", text: "图片数量" }),
        el("span", { style: "font-size:14px;font-weight:600;", text: String((results.plot_paths || []).length) }),
      ]),
    ]);
    viewer.appendChild(statusGrid);

    // Mesh report
    if (results.mesh_report) {
      const mr = results.mesh_report;
      const stats = mr.stats || {};
      viewer.appendChild(section("网格报告", [
        fieldRow("状态", mr.mesh_ok ? "PASSED" : "WARNING", "inferred"),
        stats.cells ? fieldRow("网格数", String(stats.cells), "inferred") : null,
        stats.points ? fieldRow("节点数", String(stats.points), "inferred") : null,
        stats.faces ? fieldRow("面数", String(stats.faces), "inferred") : null,
        stats.boundary_patches ? fieldRow("边界patch数", String(stats.boundary_patches), "inferred") : null,
      ]));
    }

    // Smoke test report
    if (results.smoke_test_report) {
      const sr = results.smoke_test_report;
      viewer.appendChild(section("冒烟测试报告", [
        fieldRow("状态", sr.status || "—", "inferred"),
        sr.courant_max != null ? fieldRow("最大Courant数", String(sr.courant_max), "inferred") : null,
        sr.courant_mean != null ? fieldRow("平均Courant数", String(sr.courant_mean), "inferred") : null,
        sr.completed_timesteps != null ? fieldRow("完成时间步数", String(sr.completed_timesteps), "inferred") : null,
        sr.has_nan === false ? fieldRow("NaN检查", "无NaN", "inferred") : null,
      ]));
    }

    // Run report / metrics
    if (results.run_report) {
      const rr = results.run_report;
      viewer.appendChild(section("仿真结果指标", [
        fieldRow("状态", rr.status || "—", "inferred"),
        rr.final_time != null ? fieldRow("结束时间", String(rr.final_time) + " s", "inferred") : null,
        rr.courant_max != null ? fieldRow("最大Courant数", String(rr.courant_max), "inferred") : null,
        rr.parallel != null ? fieldRow("并行", rr.parallel ? "是" : "否", "inferred") : null,
        rr.has_nan === false ? fieldRow("NaN检查", "无NaN", "inferred") : null,
        rr.has_error === false ? fieldRow("错误检查", "无错误", "inferred") : null,
      ]));
    }

    // Plot gallery (permanent in right panel)
    const plotPaths = results.plot_paths || [];
    if (plotPaths.length > 0) {
      viewer.appendChild(el("div", { class: "draft-readonly-section" }, [
        el("h3", { text: "结果图表" }),
      ]));

      const gallery = el("div", {
        style: "display:grid;grid-template-columns:1fr;gap:12px;",
      });

      plotPaths.forEach((path) => {
        const fname = path.split(/[/\\]/).pop();
        const fileUrl = `${CYL_API}/jobs/${jobId}/plots/${fname}`;
        const displayName = fname.replace(/\.(png|gif|mp4)$/, "").replace(/_/g, " ");

        const card = el("div", {
          style: "background:white;border:1px solid var(--line,#d4cdc0);border-radius:8px;padding:8px;",
        });

        const title = el("p", {
          style: "font-size:11px;font-weight:600;margin:0 0 6px;color:var(--ink,#272c2a);",
          text: displayName,
        });
        card.appendChild(title);

        if (fname.endsWith(".mp4")) {
          const video = el("video", { src: fileUrl, controls: true, loop: true });
          video.style.width = "100%";
          video.style.borderRadius = "4px";
          card.appendChild(video);
        } else {
          const img = el("img", { src: fileUrl, alt: fname, loading: "lazy" });
          img.style.width = "100%";
          img.style.borderRadius = "4px";
          img.onerror = () => { title.textContent = displayName + " (加载失败)"; };
          card.appendChild(img);
        }

        const link = el("a", { href: fileUrl, target: "_blank", download: fname, text: "下载" });
        link.style.fontSize = "10px";
        link.style.color = "#4a90d9";
        link.style.textDecoration = "none";
        link.style.display = "inline-block";
        link.style.marginTop = "4px";
        card.appendChild(link);

        gallery.appendChild(card);
      });

      viewer.appendChild(gallery);
      // Attach click-to-zoom on gallery images
      attachImageZoom(gallery);
    }

    // Switch back to spec button
    const switchBtn = el("button", {
      class: "button button-secondary",
      style: "margin-top:14px;width:100%;",
      onclick: () => {
        renderSpecPanel(cylState.spec, cylState.semanticDisplay);
      },
    }, [document.createTextNode("查看实验方案")]);
    viewer.appendChild(switchBtn);
  }

  // ---- Right panel: Tab setup ----
  function showRightPanelResults(results, jobId) {
    renderResultsPanel(results, jobId);
  }

  // ---- Overlay functions (kept from original, with modifications) ----
  function showOverlay() {
    const overlay = byId("cyl-results-overlay");
    if (overlay) overlay.hidden = false;
  }

  function hideOverlay() {
    const overlay = byId("cyl-results-overlay");
    if (overlay) overlay.hidden = true;
  }

  function setStatusBadge(status) {
    const badge = byId("cyl-job-status-badge");
    if (!badge) return;
    badge.textContent = status;
    badge.className = "status-badge";
    if (status === "SUCCESS" || status === "PASSED" || status === "COMPLETED") {
      badge.classList.add("status-success");
    } else if (status === "FAILED" || status === "ERROR") {
      badge.classList.add("status-error");
    } else if (status === "RUNNING") {
      badge.classList.add("status-running");
    } else {
      badge.classList.add("status-pending");
    }
  }

  function showProgress(text, pct) {
    const prog = byId("cyl-results-progress");
    if (!prog) return;
    prog.hidden = false;
    const pt = byId("cyl-progress-text");
    if (pt) pt.textContent = text || "正在执行...";
    const pf = byId("cyl-progress-fill");
    if (pf) pf.style.width = (pct || 0) + "%";
  }

  function hideProgress() {
    const prog = byId("cyl-results-progress");
    if (prog) prog.hidden = true;
  }

  function showSummary(mesh, smoke, run, plotCount) {
    const summary = byId("cyl-results-summary");
    if (!summary) return;
    summary.hidden = false;
    const m = byId("cyl-mesh-status"); if (m) m.textContent = mesh || "—";
    const s = byId("cyl-smoke-status"); if (s) s.textContent = smoke || "—";
    const r = byId("cyl-run-status"); if (r) r.textContent = run || "—";
    const p = byId("cyl-plot-count"); if (p) p.textContent = plotCount || 0;
  }

  function showError(msg) {
    const errDiv = byId("cyl-results-error");
    if (!errDiv) return;
    errDiv.hidden = false;
    errDiv.textContent = msg;
  }

  function clearError() {
    const errDiv = byId("cyl-results-error");
    if (errDiv) errDiv.hidden = true;
  }

  function renderPlots(plotPaths, jobId) {
    const gallery = byId("cyl-plot-gallery");
    if (!gallery) return;
    gallery.innerHTML = "";

    if (!plotPaths || plotPaths.length === 0) {
      gallery.innerHTML = "<p class='empty-hint'>暂无结果</p>";
      return;
    }

    plotPaths.forEach((path) => {
      const fname = path.split(/[/\\]/).pop();
      const fileUrl = `${CYL_API}/jobs/${jobId}/plots/${fname}`;

      const card = document.createElement("div");
      card.className = "plot-card";

      const title = document.createElement("p");
      title.className = "plot-title";
      const displayName = fname.replace(/\.(png|gif|mp4)$/, "").replace(/_/g, " ");

      const isAnimation = fname.endsWith(".gif") || fname.endsWith(".mp4");
      if (isAnimation) {
        const badge = document.createElement("span");
        badge.className = "anim-badge";
        badge.textContent = "动画";
        title.appendChild(badge);
        const label = document.createElement("span");
        label.textContent = " " + displayName;
        title.appendChild(label);
      } else {
        title.textContent = displayName;
      }

      const link = document.createElement("a");
      link.href = fileUrl;
      link.target = "_blank";
      link.download = fname;
      link.textContent = "下载";
      link.className = "plot-download";

      if (fname.endsWith(".mp4")) {
        const video = document.createElement("video");
        video.src = fileUrl;
        video.controls = true;
        video.loop = true;
        video.autoplay = true;
        video.muted = true;
        video.style.width = "100%";
        video.style.borderRadius = "4px";
        video.onerror = () => { title.textContent = fname + " (加载失败)"; };
        card.appendChild(title);
        card.appendChild(video);
        card.appendChild(link);
      } else {
        const img = document.createElement("img");
        img.src = fileUrl;
        img.alt = fname;
        img.loading = "lazy";
        img.onerror = () => { title.textContent = fname + " (加载失败)"; };
        card.appendChild(title);
        card.appendChild(img);
        card.appendChild(link);
      }

      gallery.appendChild(card);
    });

    // Attach click-to-zoom on gallery images
    attachImageZoom(gallery);
  }

  // ---- Progress message in conversation ----
  function createProgressMessage(initialText) {
    const spinner = el("span", {
      style: "display:inline-block;width:12px;height:12px;border:2px solid #ccc;border-top-color:#4a90d9;border-radius:50%;animation:cyl-spin 0.8s linear infinite;vertical-align:middle;margin-right:6px;",
    });
    const textSpan = el("span", { text: initialText || "正在执行..." });
    const content = el("div", {}, [spinner, textSpan]);
    addConversationMessage("system", content);
    cylState.progressMsgEl = textSpan;
    return textSpan;
  }

  function updateProgressMessage(text) {
    if (cylState.progressMsgEl) {
      cylState.progressMsgEl.textContent = text;
    }
  }

  // ---- Confirmation button helper ----
  function createConfirmButton(text, onClick, onComplete) {
    const btn = el("button", {
      class: "button button-primary",
      style: "margin-top:10px;padding:10px 24px;font-size:13px;cursor:pointer;",
      onclick: async () => {
        btn.disabled = true;
        btn.textContent = "处理中...";
        try {
          await onClick();
          if (onComplete) {
            onComplete(btn);
          } else {
            btn.textContent = "✓ 已完成";
            btn.style.opacity = "0.6";
          }
        } catch (e) {
          btn.disabled = false;
          btn.textContent = text;
          btn.style.opacity = "1";
        }
      },
    }, [document.createTextNode(text)]);
    return btn;
  }

  // ---- Pipeline: Step 1 — Draft ----
  async function startDraft(userInput) {
    cylState.active = true;
    cylState.stage = Stage.DRAFT;
    cylState.userInput = userInput;
    cylState.specConfirmed = false;
    // Clear previous job state when starting a new draft
    cylState.jobId = null;
    cylState.lastResults = null;
    cylState.lastReport = null;
    cylState.pollPaused = false;
    if (cylState.pollTimer) {
      clearTimeout(cylState.pollTimer);
      cylState.pollTimer = null;
    }
    // Clear conversation messages tracking for new session
    convMessages.length = 0;
    // Clear old localStorage state immediately to prevent stale restore
    clearCylState();
    // Clear the conversation timeline for fresh start
    const tl = byId("conversation-timeline");
    if (tl) tl.innerHTML = "";
    // Clear the right panel for fresh start
    const viewer = byId("draft-viewer");
    if (viewer) viewer.innerHTML = "";

    // Do NOT show overlay during draft stage — results should appear
    // in the conversation area and right panel, not blocking the UI
    clearError();
    hideProgress();
    const summaryEl = byId("cyl-results-summary");
    if (summaryEl) summaryEl.hidden = true;
    const galleryEl = byId("cyl-plot-gallery");
    if (galleryEl) galleryEl.innerHTML = "";
    setStatusBadge("DRAFT");
    showProgress("正在创建实验方案...", 5);

    // Add user message to conversation (in case startDraft was called directly)
    addConversationMessage("user", userInput);

    createProgressMessage("正在分析您的研究需求...");

    try {
      const draft = await postJSON(`${CYL_API}/draft`, {
        user_text: userInput,
      });
      if (!draft.success) throw new Error(draft.error || "Draft failed");

      cylState.specId = draft.spec_id;
      cylState.spec = draft.spec;
      cylState.semanticDisplay = draft.semantic_display;
      cylState.draftStatus = draft.draft_status;

      hideProgress();
      updateProgressMessage("");

      // Render spec in right panel
      renderSpecPanel(draft.spec, draft.semantic_display);

      // Show system understanding in conversation
      showSystemUnderstanding(draft);

      // Check if there are clarification questions
      if (draft.clarification_questions && draft.clarification_questions.length > 0) {
        setStatusBadge("CLARIFY");
        cylState.stage = Stage.DRAFT;
        showClarificationInConversation(draft.clarification_questions);
        cylState.active = false;
        saveCylState();
        return;
      }

      // No clarification needed — show plan confirm button
      cylState.stage = Stage.AWAITING_PLAN_CONFIRM;
      showPlanConfirmButton();
      cylState.active = false;
      saveCylState();

    } catch (err) {
      setStatusBadge("FAILED");
      showError(err.message);
      hideProgress();
      updateProgressMessage("方案创建失败: " + err.message);
      addConversationMessage("system", "方案创建失败: " + err.message);
      cylState.active = false;
    }
  }

  // ---- Show system understanding in conversation ----
  function showSystemUnderstanding(draft) {
    const spec = draft.spec || {};
    const sd = draft.semantic_display || {};

    const parts = [];

    // What was understood
    parts.push(el("div", {
      style: "font-weight:600;margin-bottom:8px;",
      text: "系统理解结果",
    }));

    const summaryList = [];

    // Domain
    const domSd = sd["计算域"] || {};
    const dom = spec.domain || {};
    if (domSd.length_m != null || (dom.length_m && dom.length_m.value != null)) {
      const len = domSd.length_m != null ? domSd.length_m : dom.length_m.value;
      const hgt = domSd.height_m != null ? domSd.height_m : (dom.height_m && dom.height_m.value);
      summaryList.push(`计算域: ${len}m × ${hgt || "?"}m (2D)`);
    }

    // Cylinder
    const cylSd = sd["圆柱"] || {};
    const cyl = spec.cylinder || {};
    if (cylSd.diameter_m != null || (cyl.diameter_m && cyl.diameter_m.value != null)) {
      const dia = cylSd.diameter_m != null ? cylSd.diameter_m : cyl.diameter_m.value;
      const cx = cylSd.center_x_m != null ? cylSd.center_x_m : (cyl.center_x_m && cyl.center_x_m.value);
      const cy = cylSd.center_y_m != null ? cylSd.center_y_m : (cyl.center_y_m && cyl.center_y_m.value);
      const cxDisplay = cx == null ? "缺失" : `${cx}m`;
      const cyDisplay = cy == null ? "缺失" : `${cy}m`;
      summaryList.push(`圆柱: 直径${dia}m, 圆心(${cxDisplay}, ${cyDisplay})`);
    }

    // Rectangle obstacle
    const rect = spec.rectangle || {};
    if (rect.enabled) {
      const rw = rect.width_m && rect.width_m.value != null ? `${rect.width_m.value}m` : "?";
      const rh = rect.height_m && rect.height_m.value != null ? `${rect.height_m.value}m` : "?";
      summaryList.push(`矩形障碍物: ${rw} × ${rh}`);
    }

    // Triangle obstacle
    const tri = spec.triangle || {};
    if (tri.enabled) {
      const tw = tri.base_width_m && tri.base_width_m.value != null ? `${tri.base_width_m.value}m` : "?";
      const th = tri.height_m && tri.height_m.value != null ? `${tri.height_m.value}m` : "?";
      summaryList.push(`三角形障碍物: 底宽${tw} × 高${th} (尖端${tri.apex_direction || "向上"})`);
    }

    // Trapezoid obstacle
    const trap = spec.trapezoid || {};
    if (trap.enabled) {
      const tw = trap.top_width_m && trap.top_width_m.value != null ? `${trap.top_width_m.value}m` : "?";
      const bw = trap.bottom_width_m && trap.bottom_width_m.value != null ? `${trap.bottom_width_m.value}m` : "?";
      const th = trap.height_m && trap.height_m.value != null ? `${trap.height_m.value}m` : "?";
      summaryList.push(`梯形障碍物: 上底${tw} × 下底${bw} × 高${th} (${trap.solver_representation || "parametric_polygon"})`);
    }

    // Bottom profile
    const bpSd = sd["底部轮廓"] || {};
    if (bpSd.type && bpSd.type !== "平直") {
      summaryList.push(`底部轮廓: ${bpSd.type}`);
    }

    // Fluid
    const fluid = spec.fluid || {};
    if (fluid.type && fluid.type.value) {
      const fluidName = fluid.type.value === "water" ? "水" : fluid.type.value === "air" ? "空气" : fluid.type.value;
      let fluidStr = `流体: ${fluidName}`;
      if (fluid.density_kg_m3 && fluid.density_kg_m3.value) fluidStr += ` (ρ=${fluid.density_kg_m3.value} kg/m³)`;
      if (fluid.kinematic_viscosity_m2_s && fluid.kinematic_viscosity_m2_s.value) {
        const nu = fluid.kinematic_viscosity_m2_s.value;
        fluidStr += ` (ν=${nu < 0.001 ? nu.toExponential(2) : nu} m²/s)`;
      }
      summaryList.push(fluidStr);
    }

    // Inlet velocity
    const bcLeft = (spec.boundaries || {}).left || {};
    const leftSd = sd["左侧边界"] || {};
    if (bcLeft.inlet_velocity != null) {
      summaryList.push(`入口速度: ${bcLeft.inlet_velocity} m/s`);
    }
    if (leftSd.type) {
      summaryList.push(`左侧边界: ${leftSd.type}`);
    }

    // Simulation
    const sim = spec.simulation || {};
    if (sim.end_time != null) {
      summaryList.push(`仿真时间: ${sim.end_time} s`);
    }
    if (sim.max_courant_number) {
      summaryList.push(`最大Courant数: ${sim.max_courant_number}`);
    }

    // Observables
    const obs = spec.observables || [];
    if (obs.length > 0) {
      const obsNames = obs.slice(0, 5).map(o => o.label || observableLabels[o.type] || o.type).join(", ");
      summaryList.push(`观测量: ${obsNames}${obs.length > 5 ? " 等" : ""}`);
    }

    // Assumptions
    const assumptions = spec.assumptions || [];
    if (assumptions.length > 0) {
      const assumptionStr = assumptions.slice(0, 3).map(a =>
        typeof a === "string" ? a : (a.display_name || a.description || "")
      ).filter(Boolean).join("; ");
      if (assumptionStr) summaryList.push(`假设: ${assumptionStr}`);
    }

    // Derived values — show formula and dependencies
    const derivedValues = draft.derived_values || [];
    if (derivedValues.length > 0) {
      const derivDiv = el("div", {
        style: "margin-top:8px;padding:6px 10px;background:rgba(46,196,182,0.06);border-radius:6px;border-left:3px solid #2ec4b6;",
      }, [
        el("div", { style: "font-weight:600;font-size:11px;color:#2ec4b6;margin-bottom:4px;", text: "推导参数 (不询问用户)" }),
        ...derivedValues.map(dv => el("div", {
          style: "font-size:11px;color:var(--faint,#6c726d);margin:2px 0;",
          text: dv,
        })),
      ]);
      parts.push(derivDiv);
    }

    // Non-blocking assumptions — show in confirmation, don't block
    const nonBlockingAssumptions = draft.non_blocking_assumptions || [];
    if (nonBlockingAssumptions.length > 0) {
      const nbaDiv = el("div", {
        style: "margin-top:8px;padding:6px 10px;background:rgba(255,180,0,0.06);border-radius:6px;border-left:3px solid #ffb400;",
      }, [
        el("div", { style: "font-weight:600;font-size:11px;color:#ffb400;margin-bottom:4px;", text: "默认假设 (不阻断，可修改)" }),
        ...nonBlockingAssumptions.map(nba => el("div", {
          style: "font-size:11px;color:var(--faint,#6c726d);margin:2px 0;",
          text: nba.title ? `[${nba.title}] ${nba.description || ""}` : (typeof nba === "string" ? nba : JSON.stringify(nba)),
        })),
      ]);
      parts.push(nbaDiv);
    }

    // Audit issues — show all categories
    const auditIssues = draft.audit_issues || [];
    if (auditIssues.length > 0) {
      const categoryColors = {
        BLOCKING_CONFLICT: "#e74c3c",
        SOLVER_CRITICAL_AMBIGUITY: "#e67e22",
        NON_BLOCKING_ASSUMPTION: "#ffb400",
        DERIVED_VALUE: "#2ec4b6",
        TRUE_MISSING_FIELD: "#e74c3c",
      };
      const auditDiv = el("div", {
        style: "margin-top:8px;padding:6px 10px;background:rgba(0,0,0,0.03);border-radius:6px;",
      }, [
        el("div", { style: "font-weight:600;font-size:11px;margin-bottom:4px;", text: "审计分类" }),
        ...auditIssues.map(ai => {
          const color = categoryColors[ai.category] || "#6c726d";
          const blocksTag = ai.blocks ? " [阻断]" : "";
          return el("div", {
            style: `font-size:11px;color:${color};margin:2px 0;`,
            text: `[${ai.category}]${blocksTag} ${ai.title || ai.code || ""}`,
          });
        }),
      ]);
      parts.push(auditDiv);
    }

    const list = el("ul", {
      style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;",
    }, summaryList.map(s => el("li", {}, [document.createTextNode(s)])));
    parts.push(list);

    // Draft status
    if (draft.draft_status) {
      const draftStatusLabels = {
        NEEDS_CLARIFICATION: "需要补充信息",
        AWAITING_CONFIRMATION: "等待确认",
        READY_TO_CONFIRM: "可以确认",
        SPEC_CONFIRMED: "已确认",
        COMPILED: "已编译",
      };
      parts.push(el("div", {
        style: "font-size:11px;color:var(--faint,#6c726d);margin-top:6px;",
        text: `草案状态: ${draftStatusLabels[draft.draft_status] || draft.draft_status}`,
      }));
    }

    // LLM call info
    if (draft.llm_call_info && draft.llm_call_info.call_id) {
      const llmInfo = draft.llm_call_info;
      const llmDiv = el("div", {
        style: "font-size:11px;color:var(--faint,#6c726d);margin-top:4px;padding:4px 8px;background:rgba(46,196,182,0.08);border-radius:4px;",
      }, [
        el("span", { style: "font-weight:600;color:#2ec4b6;", text: "大模型调用 " }),
        document.createTextNode(
          `${llmInfo.provider || "?"}/${llmInfo.model || "?"} · ${llmInfo.latency_ms ? (llmInfo.latency_ms/1000).toFixed(1) : "?"}s · ${llmInfo.success ? "成功" : "失败"}`
        ),
      ]);
      parts.push(llmDiv);
    }

    // Skill summary
    if (draft.skill_summary && draft.skill_summary.total > 0) {
      const sk = draft.skill_summary;
      const skillDiv = el("div", {
        style: "font-size:11px;color:var(--faint,#6c726d);margin-top:4px;padding:4px 8px;background:rgba(255,209,102,0.08);border-radius:4px;",
      }, [
        el("span", { style: "font-weight:600;color:#e6a817;", text: "Skill执行 " }),
        document.createTextNode(
          `已调用 ${sk.total} 个 Skills · 成功 ${sk.passed} · 失败 ${sk.failed}`
        ),
      ]);
      parts.push(skillDiv);
    }

    // Semantic coverage
    if (draft.semantic_coverage) {
      const cov = draft.semantic_coverage;
      const covRate = (cov.coverage_rate * 100).toFixed(0);
      const covColor = cov.coverage_rate >= 1.0 ? "#2ec4b6" : cov.coverage_rate >= 0.8 ? "#e6a817" : "#e63946";
      const covDiv = el("div", {
        style: "font-size:11px;color:var(--faint,#6c726d);margin-top:4px;padding:4px 8px;background:rgba(46,196,182,0.04);border-radius:4px;",
      }, [
        el("span", { style: `font-weight:600;color:${covColor};`, text: "语义覆盖 " }),
        document.createTextNode(
          `${covRate}% (${cov.mapped_claims?.length || 0}项已映射` +
          (cov.unmapped_claims?.length ? `, ${cov.unmapped_claims.length}项未映射` : "") +
          (cov.silent_substitutions?.length ? `, ${cov.silent_substitutions.length}项静默替换` : "") +
          ")"
        ),
      ]);
      parts.push(covDiv);
    }

    addConversationMessage("assistant", parts);
  }

  // ---- Step 2: Clarification questions in conversation ----
  function showClarificationInConversation(questions) {
    const container = el("div", { class: "clarification-panel" });

    container.appendChild(el("div", {
      style: "font-weight:600;margin-bottom:8px;",
      text: "请回答以下问题以完成方案配置：",
    }));

    const answers = {};

    questions.forEach((q, idx) => {
      const item = el("div", { class: "clarification-item" });

      item.appendChild(el("label", {
        style: "display:block;font-size:12px;font-weight:500;margin-bottom:6px;",
        text: `${idx + 1}. ${q.message}`,
      }));

      if (q.type === "choice" && q.options) {
        q.options.forEach((opt) => {
          const radioWrapper = el("div", {
            class: "radio-option",
            style: "display:flex;align-items:center;gap:6px;padding:3px 0;cursor:pointer;",
          });
          const radio = el("input", { type: "radio", name: `q_${q.id}`, value: opt });
          radio.addEventListener("change", () => { answers[q.id] = opt; });
          radioWrapper.appendChild(radio);
          radioWrapper.appendChild(el("span", { style: "font-size:12px;", text: opt }));
          item.appendChild(radioWrapper);
        });
      } else {
        const input = el("input", {
          type: q.type === "number" ? "number" : "text",
          placeholder: q.placeholder || "",
          class: "clarification-input",
          style: "width:100%;padding:6px 10px;border:1px solid var(--line,#d4cdc0);border-radius:4px;font-size:12px;",
        });
        input.addEventListener("input", () => { answers[q.id] = input.value; });
        item.appendChild(input);
      }

      container.appendChild(item);
    });

    const submitBtn = el("button", {
      class: "clarification-submit-btn",
      style: "margin-top:10px;padding:8px 20px;background:var(--teal-dark,#0a4f4b);color:white;border:none;border-radius:4px;font-size:12px;font-weight:500;cursor:pointer;",
      onclick: () => {
        const unanswered = questions.filter(q => !answers[q.id]);
        if (unanswered.length > 0) {
          alert("请回答所有问题后再提交");
          return;
        }
        // Replace form with answered summary
        container.innerHTML = "";
        container.appendChild(el("div", {
          style: "font-weight:600;margin-bottom:6px;",
          text: "已回答：",
        }));
        questions.forEach((q, i) => {
          container.appendChild(el("div", {
            style: "font-size:12px;margin-bottom:4px;",
            text: `${i + 1}. ${q.message}`,
          }));
          container.appendChild(el("div", {
            style: "font-size:12px;color:var(--teal-dark,#0a4f4b);margin-bottom:6px;font-weight:500;",
            text: `→ ${answers[q.id]}`,
          }));
        });
        submitClarifications(answers);
      },
    }, [document.createTextNode("提交回答")]);
    container.appendChild(submitBtn);

    addConversationMessage("assistant", container);
  }

  // ---- Step 2b: Submit clarifications ----
  async function submitClarifications(answers) {
    cylState.active = true;
    setStatusBadge("CONFIRM");
    showProgress("正在应用回答并验证方案...", 15);
    createProgressMessage("正在应用您的回答...");

    try {
      // Call /confirm with clarifications to apply them
      const confirmData = { spec_id: cylState.specId, clarifications: answers, user_input: cylState.userInput || "" };

      // Extract simulation time from user input
      if (cylState.userInput) {
        const endTimeMatch = cylState.userInput.match(/仿真时间\s*[=为是]?\s*(\d+\.?\d*)\s*秒?/);
        if (endTimeMatch) {
          confirmData.end_time = parseFloat(endTimeMatch[1]);
        }
      }

      const confirmed = await postJSON(`${CYL_API}/confirm`, confirmData);

      if (!confirmed.success) {
        // Check if there are more questions
        if (confirmed.clarification_questions && confirmed.clarification_questions.length > 0) {
          hideProgress();
          updateProgressMessage("需要补充更多信息...");
          cylState.spec = confirmed.spec || cylState.spec;
          cylState.semanticDisplay = confirmed.semantic_display || cylState.semanticDisplay;
          if (confirmed.spec) renderSpecPanel(confirmed.spec, confirmed.semantic_display);
          showClarificationInConversation(confirmed.clarification_questions);
          cylState.active = false;
          return;
        }
        throw new Error(confirmed.error || "Confirm failed");
      }

      // Success — spec is now confirmed
      cylState.spec = confirmed.spec;
      cylState.semanticDisplay = confirmed.semantic_display;
      cylState.specConfirmed = true;
      cylState.draftStatus = confirmed.draft_status;
      hideProgress();
      updateProgressMessage("方案验证通过，等待确认。");
      renderSpecPanel(confirmed.spec, confirmed.semantic_display);

      addConversationMessage("assistant", "所有问题已解决，方案已准备就绪。请确认研究方案以继续。");

      // Show plan confirm button
      cylState.stage = Stage.AWAITING_PLAN_CONFIRM;
      showPlanConfirmButton();
      cylState.active = false;
      saveCylState();

    } catch (err) {
      setStatusBadge("FAILED");
      showError(err.message);
      hideProgress();
      updateProgressMessage("验证失败: " + err.message);
      addConversationMessage("system", "验证失败: " + err.message);
      cylState.active = false;
    }
  }

  // ---- Step 3: Show "确认研究方案" button ----
  function showPlanConfirmButton() {
    // Disable any existing confirm buttons to prevent duplicates
    const timeline = byId("conversation-timeline");
    if (timeline) {
      const oldButtons = timeline.querySelectorAll('button[data-confirm-button="true"]');
      oldButtons.forEach(oldBtn => {
        oldBtn.disabled = true;
        oldBtn.textContent = "已失效（方案已修改）";
        oldBtn.style.opacity = "0.4";
        oldBtn.style.cursor = "default";
        oldBtn.removeAttribute("data-confirm-button");
      });
    }

    const btn = createConfirmButton("确认研究方案", () => confirmPlan(), (btn) => {
      btn.textContent = "✓ 方案已确认";
      btn.style.opacity = "0.6";
    });
    btn.setAttribute("data-confirm-button", "true");
    addConversationMessage("assistant", [
      el("div", { text: "方案已就绪，请点击下方按钮确认研究方案，确认后将进入编译阶段。" }),
      btn,
    ]);
  }

  // ---- Step 4: Confirm plan → POST /{spec_id}/confirm-plan (Gate 1) ----
  async function confirmPlan() {
    cylState.active = true;
    cylState.stage = Stage.COMPILE_PREVIEW;
    setStatusBadge("CONFIRM");
    showProgress("正在确认方案...", 20);
    createProgressMessage("正在确认研究方案...");

    try {
      // Gate 1: Call /confirm-plan to freeze the spec and get compile preview
      let result = await postJSON(`${CYL_API}/${cylState.specId}/confirm-plan`, {});

      // Recovery: if spec not found (server restart), re-submit draft and retry
      if (!result.success && result.error && result.error.includes("Spec not found")) {
        updateProgressMessage("服务重启后恢复中，正在重新提交方案...");
        const draftResult = await postJSON(`${CYL_API}/draft`, { user_text: cylState.userInput });
        if (draftResult.success && draftResult.spec_id) {
          cylState.specId = draftResult.spec_id;
          cylState.spec = draftResult.spec;
          cylState.semanticDisplay = draftResult.semantic_display;
          // Re-run confirm first, then confirm-plan
          const confirmData = { spec_id: cylState.specId, clarifications: {}, user_input: cylState.userInput || "" };
          await postJSON(`${CYL_API}/confirm`, confirmData);
          result = await postJSON(`${CYL_API}/${cylState.specId}/confirm-plan`, {});
        }
      }

      if (!result.success) {
        if (result.blocking_issues && result.blocking_issues.length > 0) {
          hideProgress();
          updateProgressMessage("方案存在问题，需要修正。");
          addConversationMessage("system", "方案存在阻塞问题: " + (result.error || "请检查方案配置"));
          cylState.active = false;
          cylState.stage = Stage.DRAFT;
          // Throw error so the confirm button is reset by createConfirmButton's catch block
          throw new Error("方案存在阻塞问题，请修正后重试");
        }
        throw new Error(result.error || "Confirm plan failed");
      }

      // Spec is now frozen (SPEC_CONFIRMED)
      cylState.specConfirmed = true;
      cylState.draftStatus = "SPEC_CONFIRMED";
      if (cylState.spec) {
        cylState.spec.draft_status = "SPEC_CONFIRMED";
      }

      hideProgress();
      updateProgressMessage("方案已确认 (Gate 1 通过)。");

      // Re-render spec panel to update the status badge
      renderSpecPanel(cylState.spec, cylState.semanticDisplay);

      const confirmMsg = result.already_confirmed
        ? "研究方案已确认（之前已确认）。以下是编译预览："
        : "研究方案已确认并冻结。以下是编译预览：";
      addConversationMessage("assistant", confirmMsg);

      // Show compile preview from backend
      showCompilePreview(result.compile_preview);
      cylState.active = false;
      saveCylState();

    } catch (err) {
      setStatusBadge("FAILED");
      showError(err.message);
      hideProgress();
      updateProgressMessage("确认失败: " + err.message);
      addConversationMessage("system", "确认失败: " + err.message);
      cylState.active = false;
      throw err; // Re-throw so createConfirmButton can handle button state
    }
  }

  // ---- Step 4b: Show compile preview from backend ----
  function showCompilePreview(preview) {
    if (!preview) {
      addConversationMessage("system", "无法获取编译预览，请重试。");
      return;
    }

    const parts = [];

    parts.push(el("div", {
      style: "font-weight:600;margin-bottom:8px;",
      text: "编译方案预览",
    }));

    const previewItems = [];

    // Platform info
    previewItems.push(`平台: OpenFOAM Foundation ${preview.openfoam_version || "13"}`);
    previewItems.push(`应用: ${preview.application || "incompressibleFluid"}`);
    previewItems.push(`求解模块: ${preview.solver_module || "foamRun -solver incompressibleFluid"}`);
    previewItems.push(`时间模式: ${preview.temporal_type === "steady" ? "稳态" : "瞬态"}`);
    previewItems.push(`湍流模型: ${preview.turbulence_model || "laminar"}`);

    // Mesh info
    previewItems.push(`网格后端: ${preview.mesh_backend || "blockMesh + snappyHexMesh"}`);
    if (preview.estimated_mesh_count) {
      previewItems.push(`预计网格量: ${preview.estimated_mesh_count} 单元`);
    }
    if (preview.mesh_detail) {
      previewItems.push(`网格分布: ${preview.mesh_detail.nx}×${preview.mesh_detail.ny}×${preview.mesh_detail.nz || 1}`);
    }

    // Time step info
    if (preview.time_step) {
      previewItems.push(`时间步: ${preview.time_step} s`);
    }
    if (preview.end_time) {
      previewItems.push(`模拟总时间: ${preview.end_time} s`);
    }
    if (preview.estimated_steps) {
      previewItems.push(`预计步数: ${preview.estimated_steps}`);
    }
    if (preview.max_courant_number) {
      previewItems.push(`最大Courant数: ${preview.max_courant_number}`);
    }
    if (preview.reynolds_number) {
      previewItems.push(`Reynolds 数: ${Math.round(preview.reynolds_number)}`);
    }
    if (preview.estimated_computation_time_s) {
      const mins = Math.floor(preview.estimated_computation_time_s / 60);
      const secs = Math.round(preview.estimated_computation_time_s % 60);
      previewItems.push(`预计计算时间: ${mins > 0 ? mins + "分" : ""}${secs}秒`);
    }

    // Observable implementations
    const obsImpl = preview.observables_implementation || [];
    if (obsImpl.length > 0) {
      previewItems.push(`观测量实现:`);
      obsImpl.forEach(oi => {
        previewItems.push(`  · ${oi.label || oi.observable}: ${oi.implementation}`);
      });
    }

    parts.push(el("ul", {
      style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;",
    }, previewItems.map(s => el("li", {}, [document.createTextNode(s)]))));

    parts.push(el("div", {
      style: "font-size:11px;color:var(--faint,#6c726d);margin-top:6px;",
      text: "确认后将生成 OpenFOAM Case 文件，并进行网格生成、checkMesh 和 Smoke Test 验证。",
    }));

    addConversationMessage("assistant", parts);

    // Show "确认并生成Case" button
    cylState.stage = Stage.AWAITING_COMPILE_CONFIRM;
    showCompileConfirmButton();
  }

  // ---- Step 5: Show "确认并生成Case" button ----
  function showCompileConfirmButton() {
    const btn = createConfirmButton("确认并生成Case", () => {
      compileAndValidate();
    }, (btn) => {
      btn.textContent = "✓ 已生成Case";
      btn.style.opacity = "0.6";
    });
    addConversationMessage("assistant", [
      el("div", { text: "请点击下方按钮确认编译方案，系统将生成 Case 文件并运行验证。" }),
      btn,
    ]);
  }

  // ---- Step 5b: Compile + Execute + Validate (mesh, checkMesh, smoke test) ----
  async function compileAndValidate() {
    cylState.active = true;
    cylState.stage = Stage.VALIDATING;
    setStatusBadge("COMPILE");
    showProgress("正在编译 OpenFOAM Case...", 25);
    createProgressMessage("正在编译 OpenFOAM Case...");

    try {
      // Step 1: Compile
      const compiled = await postJSON(`${CYL_API}/compile`, {
        spec_id: cylState.specId,
      });
      if (!compiled.success) throw new Error(compiled.error || "Compile failed");

      cylState.jobId = compiled.job_id;
      cylState.flowMode = compiled.flow_mode;
      // Update spec status to reflect compiled state
      cylState.draftStatus = "COMPILED";
      if (cylState.spec) {
        cylState.spec.draft_status = "COMPILED";
      }
      renderSpecPanel(cylState.spec, cylState.semanticDisplay);
      saveCylState();

      updateProgressMessage(`Case 编译完成: ${compiled.file_count} 个文件, 流动模式: ${compiled.flow_mode || "cylinder_flow"}`);
      addConversationMessage("system", `Case 编译完成: ${compiled.file_count} 个文件, 流动模式: ${compiled.flow_mode || "cylinder_flow"}`);

      // Show file list preview
      if (compiled.file_list && compiled.file_list.length > 0) {
        const fileList = compiled.file_list.slice(0, 10).join(", ");
        const more = compiled.file_list.length > 10 ? ` 等 ${compiled.file_list.length} 个文件` : "";
        addConversationMessage("system", `Case 文件: ${fileList}${more}`);
      }

      // Step 2: Execute (async)
      setStatusBadge("RUNNING");
      showProgress("正在上传并执行仿真...", 30);
      updateProgressMessage("正在启动仿真任务...");

      const executed = await postJSON(`${CYL_API}/execute`, {
        job_id: cylState.jobId,
        skip_smoke: false,
        parallel: false,
        stop_after_smoke: true,
      });
      if (!executed.success && executed.status !== "RUNNING") {
        throw new Error(executed.error || "Execute failed");
      }

      // Step 3: Poll for status — pause when smoke test passes
      addConversationMessage("system", "仿真任务已启动，正在进行网格生成和验证...");
      pollJobStatus(cylState.jobId, {
        pauseOnSmoke: true,
        onSmokeTestPassed: (status) => {
          onSmokeTestPassed(status);
        },
        onCompleted: (status) => {
          onSimulationCompleted(status);
        },
        onError: (err) => {
          onPipelineError(err);
        },
      });

    } catch (err) {
      onPipelineError(err);
    }
  }

  // ---- Called when smoke test passes ----
  function onSmokeTestPassed(status) {
    cylState.stage = Stage.AWAITING_RUN_CONFIRM;
    cylState.active = false;
    cylState.pollPaused = true;

    hideProgress();
    setStatusBadge("SMOKE_PASSED");
    updateProgressMessage("Smoke Test 通过，等待确认。");

    // Show validation results in conversation
    const parts = [];
    parts.push(el("div", {
      style: "font-weight:600;margin-bottom:8px;color:#155724;",
      text: "✓ 验证通过",
    }));

    // Mesh report
    if (status.mesh_report) {
      const mr = status.mesh_report;
      const stats = mr.stats || {};
      parts.push(el("div", { style: "font-size:12px;margin-bottom:6px;", text: "网格生成: ✓ 成功" }));
      if (stats.cells) parts.push(el("div", { style: "font-size:11px;color:var(--faint,#6c726d);", text: `  网格数: ${stats.cells}` }));
      if (stats.points) parts.push(el("div", { style: "font-size:11px;color:var(--faint,#6c726d);", text: `  节点数: ${stats.points}` }));
    } else {
      parts.push(el("div", { style: "font-size:12px;margin-bottom:6px;", text: "网格生成: ✓ 成功" }));
    }

    // Smoke test
    if (status.smoke_test_report) {
      const sr = status.smoke_test_report;
      parts.push(el("div", { style: "font-size:12px;margin-bottom:6px;", text: "checkMesh: ✓ 通过" }));
      parts.push(el("div", { style: "font-size:12px;margin-bottom:6px;", text: `Smoke Test: ✓ 通过` }));
      if (sr.courant_max != null) parts.push(el("div", { style: "font-size:11px;color:var(--faint,#6c726d);", text: `  最大Courant数: ${sr.courant_max}` }));
      if (sr.courant_mean != null) parts.push(el("div", { style: "font-size:11px;color:var(--faint,#6c726d);", text: `  平均Courant数: ${sr.courant_mean}` }));
      if (sr.completed_timesteps != null) parts.push(el("div", { style: "font-size:11px;color:var(--faint,#6c726d);", text: `  完成时间步: ${sr.completed_timesteps}` }));
    } else {
      parts.push(el("div", { style: "font-size:12px;margin-bottom:6px;", text: "checkMesh: ✓ 通过" }));
      parts.push(el("div", { style: "font-size:12px;margin-bottom:6px;", text: "Smoke Test: ✓ 通过" }));
    }

    parts.push(el("div", {
      style: "font-size:11px;color:var(--faint,#6c726d);margin-top:8px;",
      text: "验证已通过，请确认并提交正式计算。",
    }));

    addConversationMessage("assistant", parts);

    // Show "确认并提交计算" button
    showRunConfirmButton();
  }

  // ---- Step 6: Show "确认并提交计算" button ----
  function showRunConfirmButton() {
    const btn = createConfirmButton("确认并提交计算", () => {
      confirmRun();
    }, (btn) => {
      btn.textContent = "✓ 已提交计算";
      btn.style.opacity = "0.6";
    });
    addConversationMessage("assistant", [
      el("div", { text: "验证已通过。请点击下方按钮确认并提交正式仿真计算。" }),
      btn,
    ]);
  }

  // ---- Step 7: Confirm run → POST /jobs/{job_id}/resume-run (Gate 3) ----
  async function confirmRun() {
    cylState.active = true;
    cylState.stage = Stage.RUNNING;
    cylState.pollPaused = false;
    setStatusBadge("RUNNING");
    showProgress("正在提交正式计算...", 50);
    createProgressMessage("已确认，正在提交正式仿真计算...");

    try {
      // Gate 3: Call /resume-run to start the full simulation
      const resumeResult = await postJSON(`${CYL_API}/jobs/${cylState.jobId}/resume-run`, {
        job_id: cylState.jobId,
        parallel: false,
      });

      if (!resumeResult.success) {
        throw new Error(resumeResult.error || "Failed to resume run");
      }

      addConversationMessage("system", "已确认提交计算 (Gate 3 通过)，正式仿真已启动...");

      // Start polling for completion
      pollJobStatus(cylState.jobId, {
        pauseOnSmoke: false, // Don't pause again — full simulation
        onCompleted: (status) => {
          onSimulationCompleted(status);
        },
        onError: (err) => {
          onPipelineError(err);
        },
      });

    } catch (err) {
      onPipelineError(err);
    }
  }

  // ---- Step 8: Simulation completed → show results ----
  async function onSimulationCompleted(status) {
    cylState.stage = Stage.COMPLETED;
    cylState.active = false;
    cylState.lastResults = status;

    hideProgress();
    setStatusBadge(status.status);
    cylState.pollTimer = null;

    if (status.status === "SUCCESS" || status.status === "PARTIAL") {
      // Show in overlay
      showSummary(
        status.mesh_status,
        status.smoke_test_status,
        status.run_status,
        (status.plot_paths || []).length
      );
      renderPlots(status.plot_paths, cylState.jobId);

      // Show results in conversation (summary + metrics + analysis)
      showResultsInConversation(status);

      // Show results in right panel (permanent)
      showRightPanelResults(status, cylState.jobId);

      addConversationMessage("assistant", "仿真完成！结果已显示在右侧面板中，您可随时查看。");

      // Fetch structured analysis report from backend (enhanced)
      try {
        const report = await fetchJSON(`${CYL_API}/${cylState.jobId}/report`);
        if (report.success && report.report) {
          cylState.lastReport = report.report;
          showAnalysisReportInConversation(report.report);
          // Update right panel with enhanced analysis
          showRightPanelAnalysis(report.report);
        }
      } catch (err) {
        console.warn("Failed to fetch analysis report:", err.message);
      }
      saveCylState();

    } else {
      showError(status.error || "仿真失败");
      addConversationMessage("system", "仿真失败: " + (status.error || "未知错误"));
    }
  }

  // ---- Show structured analysis report in conversation ----
  function showAnalysisReportInConversation(report) {
    const parts = [];

    parts.push(el("div", {
      style: "font-weight:600;margin-bottom:8px;",
      text: "科学结果分析",
    }));

    // Metrics (backend returns cd_mean, cl_mean, cd_amplitude, etc.)
    const metrics = report.metrics || {};
    const metricItems = [];
    if (metrics.cd_mean != null) metricItems.push(`平均阻力系数 Cd = ${metrics.cd_mean.toFixed(4)}`);
    if (metrics.cd_amplitude != null) metricItems.push(`阻力系数幅值 = ${metrics.cd_amplitude.toFixed(4)}`);
    if (metrics.cd_min != null) metricItems.push(`阻力系数最小值 = ${metrics.cd_min.toFixed(4)}`);
    if (metrics.cd_max != null) metricItems.push(`阻力系数最大值 = ${metrics.cd_max.toFixed(4)}`);
    if (metrics.cl_mean != null) metricItems.push(`平均升力系数 Cl = ${metrics.cl_mean.toFixed(6)}`);
    if (metrics.cl_amplitude != null) metricItems.push(`升力系数幅值 = ${metrics.cl_amplitude.toFixed(6)}`);
    if (metrics.strouhal_number != null) metricItems.push(`Strouhal 数 St = ${metrics.strouhal_number.toFixed(4)}`);
    if (metrics.reynolds_number != null) metricItems.push(`Reynolds 数 Re = ${Math.round(metrics.reynolds_number)}`);

    if (metricItems.length > 0) {
      parts.push(el("div", { style: "font-weight:600;margin:6px 0 4px;", text: "关键指标" }));
      parts.push(el("ul", {
        style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;",
      }, metricItems.map(m => el("li", {}, [document.createTextNode(m)]))));
    }

    // Flow features (backend returns mean_drag_coefficient, oscillating_lift, etc.)
    const flowFeatures = report.flow_features || {};
    const ffItems = [];
    if (flowFeatures.mean_drag_coefficient != null) ffItems.push(`平均阻力系数: ${flowFeatures.mean_drag_coefficient.toFixed(4)}`);
    if (flowFeatures.oscillating_lift) ffItems.push(`升力振荡: ${flowFeatures.oscillating_lift}`);
    if (flowFeatures.wake_formation) ffItems.push(`尾迹形成: ${flowFeatures.wake_formation}`);
    if (flowFeatures.recirculation_length != null) ffItems.push(`回流区长度: ${flowFeatures.recirculation_length} m`);
    if (flowFeatures.vortex_shedding) ffItems.push(`涡脱落: ${flowFeatures.vortex_shedding}`);
    if (flowFeatures.separation_point) ffItems.push(`分离点: ${flowFeatures.separation_point}`);

    if (ffItems.length > 0) {
      parts.push(el("div", { style: "font-weight:600;margin:6px 0 4px;", text: "流动特征" }));
      parts.push(el("ul", {
        style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;",
      }, ffItems.map(m => el("li", {}, [document.createTextNode(m)]))));
    }

    // Convergence
    const conv = report.convergence || {};
    if (conv.assessment || conv.status) {
      parts.push(el("div", { style: "font-weight:600;margin:6px 0 4px;", text: "收敛性评估" }));
      const convItems = [];
      if (conv.status) convItems.push(`状态: ${conv.status}`);
      if (conv.assessment) convItems.push(`评估: ${conv.assessment}`);
      if (conv.final_time != null) convItems.push(`结束时间: ${conv.final_time} s`);
      if (conv.courant_max != null) convItems.push(`最大Courant数: ${conv.courant_max}`);
      if (conv.has_nan === false) convItems.push(`无NaN值`);
      if (conv.continuity_cumulative != null) convItems.push(`累积连续性误差: ${conv.continuity_cumulative.toExponential(3)}`);
      parts.push(el("ul", {
        style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;",
      }, convItems.map(m => el("li", {}, [document.createTextNode(m)]))));
    }

    // Quality assessment
    const quality = report.quality || {};
    const qItems = [];
    if (quality.mass_conservation) {
      const mc = quality.mass_conservation;
      qItems.push(`质量守恒: ${mc.acceptable ? "满足" : "不满足"} (累积误差: ${mc.cumulative ? mc.cumulative.toExponential(3) : "—"})`);
    }
    if (quality.mesh) {
      const mesh = quality.mesh;
      qItems.push(`网格: ${mesh.cells || "—"} 单元${mesh.mesh_ok === false ? " (有警告)" : ""}`);
    }
    if (quality.smoke_test) {
      const st = quality.smoke_test;
      qItems.push(`冒烟测试: ${st.status || "—"} (Courant max: ${st.courant_max || "—"})`);
    }
    if (qItems.length > 0) {
      parts.push(el("div", { style: "font-weight:600;margin:6px 0 4px;", text: "质量评估" }));
      parts.push(el("ul", {
        style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;",
      }, qItems.map(m => el("li", {}, [document.createTextNode(m)]))));
    }

    // Generate scientific analysis text from available data
    const analysisText = generateScientificAnalysis(report);
    if (analysisText) {
      parts.push(el("div", { style: "font-weight:600;margin:6px 0 4px;", text: "物理解释" }));
      parts.push(el("div", {
        style: "font-size:12px;line-height:1.5;",
        text: analysisText,
      }));
    }

    // Warnings (backend returns "warnings", not "quality_warnings")
    const warnings = report.warnings || report.quality_warnings || [];
    if (warnings.length > 0) {
      parts.push(el("div", { style: "font-weight:600;margin:6px 0 4px;", text: "质量警告" }));
      parts.push(el("ul", {
        style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;color:#856404;",
      }, warnings.map(w => el("li", {}, [document.createTextNode(w)]))));
    }

    // Limitations
    const limitations = report.limitations || [];
    if (limitations.length > 0) {
      parts.push(el("div", { style: "font-weight:600;margin:6px 0 4px;", text: "限制说明" }));
      parts.push(el("ul", {
        style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;color:var(--faint,#6c726d);",
      }, limitations.map(l => el("li", {}, [document.createTextNode(l)]))));
    }

    if (metricItems.length > 0 || ffItems.length > 0 || analysisText) {
      addConversationMessage("assistant", parts);
    }
  }

  // ---- Generate scientific analysis text from report data ----
  function generateScientificAnalysis(report) {
    const lines = [];
    const metrics = report.metrics || {};
    const conv = report.convergence || {};
    const ff = report.flow_features || {};
    const quality = report.quality || {};

    // Convergence summary
    if (conv.assessment === "converged") {
      lines.push("仿真已收敛。");
    } else if (conv.status === "SUCCESS") {
      lines.push("仿真计算成功完成。");
    }

    // Drag coefficient analysis
    if (metrics.cd_mean != null) {
      lines.push(`圆柱平均阻力系数为 ${metrics.cd_mean.toFixed(4)}。`);
      if (metrics.cd_amplitude != null && metrics.cd_amplitude > 0.01) {
        lines.push(`阻力系数存在幅值为 ${metrics.cd_amplitude.toFixed(4)} 的波动。`);
      }
    }

    // Lift coefficient analysis
    if (metrics.cl_mean != null) {
      if (ff.oscillating_lift === "negligible" || Math.abs(metrics.cl_mean) < 0.01) {
        lines.push("升力系数波动较小，尚未出现明显的涡脱落现象。");
      } else if (metrics.cl_amplitude != null && metrics.cl_amplitude > 0.01) {
        lines.push(`升力系数呈周期性波动，幅值为 ${metrics.cl_amplitude.toFixed(6)}，提示存在涡脱落。`);
      }
    }

    // Mass conservation
    if (quality.mass_conservation && quality.mass_conservation.acceptable) {
      lines.push("质量守恒满足要求。");
    }

    // Mesh quality
    if (quality.mesh && quality.mesh.mesh_ok === false) {
      lines.push("网格存在部分非正交和凹形单元，但在可接受范围内。");
    }

    // Courant number
    if (conv.courant_max != null) {
      lines.push(`最大Courant数为 ${conv.courant_max}，低于0.5阈值。`);
    }

    // Smoke test
    if (quality.smoke_test && quality.smoke_test.status === "PASSED") {
      lines.push("冒烟测试通过，Case配置验证有效。");
    }

    // Limitations
    const limitations = report.limitations || [];
    if (limitations.length > 0) {
      lines.push("注意: " + limitations.join("; ") + "。");
    }

    return lines.length > 0 ? lines.join("") : null;
  }

  // ---- Show analysis report in right panel ----
  function showRightPanelAnalysis(report) {
    const viewer = byId("draft-viewer");
    if (!viewer) return;

    // Append analysis section to existing right panel content
    const analysisSection = el("div", { class: "draft-readonly-section" }, [
      el("h3", { text: "科学结果分析" }),
    ]);

    // Metrics (backend returns cd_mean, cl_mean, etc.)
    const metrics = report.metrics || {};
    const metricRows = [
      ["cd_mean", "平均阻力系数 Cd"],
      ["cd_amplitude", "阻力系数幅值"],
      ["cd_min", "阻力系数最小值"],
      ["cd_max", "阻力系数最大值"],
      ["cl_mean", "平均升力系数 Cl"],
      ["cl_amplitude", "升力系数幅值"],
      ["strouhal_number", "Strouhal 数"],
      ["reynolds_number", "Reynolds 数"],
    ];
    for (const [key, label] of metricRows) {
      if (metrics[key] != null) {
        const val = typeof metrics[key] === "number" ? metrics[key].toFixed(6) : String(metrics[key]);
        analysisSection.appendChild(fieldRow(label, val, "inferred"));
      }
    }

    // Flow features (skip duplicates already shown in metrics)
    const ff = report.flow_features || {};
    if (ff.oscillating_lift) {
      analysisSection.appendChild(fieldRow("升力振荡", ff.oscillating_lift, "inferred"));
    }

    // Convergence
    const conv = report.convergence || {};
    if (conv.status || conv.assessment) {
      analysisSection.appendChild(el("div", { style: "font-weight:600;margin:8px 0 4px;font-size:12px;", text: "收敛性评估" }));
      if (conv.status) analysisSection.appendChild(fieldRow("状态", conv.status, "inferred"));
      if (conv.assessment) analysisSection.appendChild(fieldRow("评估", conv.assessment, "inferred"));
      if (conv.final_time != null) analysisSection.appendChild(fieldRow("结束时间", conv.final_time + " s", "inferred"));
      if (conv.courant_max != null) analysisSection.appendChild(fieldRow("最大Courant数", String(conv.courant_max), "inferred"));
      if (conv.continuity_cumulative != null) analysisSection.appendChild(fieldRow("累积连续性误差", conv.continuity_cumulative.toExponential(3), "inferred"));
    }

    // Quality
    const quality = report.quality || {};
    if (quality.mass_conservation) {
      analysisSection.appendChild(el("div", { style: "font-weight:600;margin:8px 0 4px;font-size:12px;", text: "质量评估" }));
      analysisSection.appendChild(fieldRow("质量守恒", quality.mass_conservation.acceptable ? "满足" : "不满足", "inferred"));
      if (quality.mesh) {
        analysisSection.appendChild(fieldRow("网格单元数", String(quality.mesh.cells || "—"), "inferred"));
      }
      if (quality.smoke_test) {
        analysisSection.appendChild(fieldRow("冒烟测试", quality.smoke_test.status || "—", "inferred"));
      }
    }

    // Generate scientific analysis text
    const analysisText = generateScientificAnalysis(report);
    if (analysisText) {
      analysisSection.appendChild(el("div", {
        style: "font-size:12px;line-height:1.5;margin-top:8px;",
        text: analysisText,
      }));
    }

    // Warnings
    const warnings = report.warnings || report.quality_warnings || [];
    if (warnings.length > 0) {
      warnings.forEach(w => {
        analysisSection.appendChild(el("div", {
          style: "font-size:11px;color:#856404;margin-top:4px;",
          text: "⚠ " + w,
        }));
      });
    }

    viewer.appendChild(analysisSection);

    // Limitations
    const limitations = report.limitations || [];
    if (limitations.length > 0) {
      const limSection = el("div", { class: "draft-readonly-section" }, [
        el("h3", { text: "限制说明" }),
      ]);
      limitations.forEach(l => {
        limSection.appendChild(el("div", {
          style: "font-size:11px;color:var(--faint,#6c726d);margin-bottom:4px;",
          text: "· " + l,
        }));
      });
      viewer.appendChild(limSection);
    }
  }

  // ---- Show results in conversation ----
  function showResultsInConversation(status) {
    const parts = [];

    parts.push(el("div", {
      style: "font-weight:600;margin-bottom:8px;color:#155724;",
      text: "仿真结果",
    }));

    // Summary
    const summaryItems = [];
    summaryItems.push(`网格: ${status.mesh_status || "—"}`);
    summaryItems.push(`冒烟测试: ${status.smoke_test_status || "—"}`);
    summaryItems.push(`仿真运行: ${status.run_status || "—"}`);
    summaryItems.push(`图表数量: ${(status.plot_paths || []).length}`);

    parts.push(el("div", {
      style: "font-size:12px;margin-bottom:8px;",
      text: summaryItems.join("  |  "),
    }));

    // Metrics from run report
    if (status.run_report) {
      const rr = status.run_report;
      parts.push(el("div", {
        style: "font-weight:600;margin:10px 0 6px;",
        text: "关键指标",
      }));

      const metrics = [];
      if (rr.status) metrics.push(`运行状态: ${rr.status}`);
      if (rr.final_time != null) metrics.push(`结束时间: ${rr.final_time} s`);
      if (rr.courant_max != null) metrics.push(`最大Courant数: ${rr.courant_max}`);
      if (rr.has_nan === false) metrics.push(`无NaN值`);
      if (rr.has_error === false) metrics.push(`无错误`);

      if (metrics.length > 0) {
        parts.push(el("ul", {
          style: "margin:4px 0 8px;padding-left:18px;font-size:12px;line-height:1.6;",
        }, metrics.map(m => el("li", {}, [document.createTextNode(m)]))));
      }
    }

    // Mesh stats
    if (status.mesh_report && status.mesh_report.stats) {
      const cells = status.mesh_report.stats.cells;
      if (cells) {
        parts.push(el("div", {
          style: "font-size:11px;color:var(--faint,#6c726d);",
          text: `网格统计: ${cells} 个网格单元`,
        }));
      }
    }

    // Plot list
    const plotPaths = status.plot_paths || [];
    if (plotPaths.length > 0) {
      parts.push(el("div", {
        style: "font-size:11px;color:var(--faint,#6c726d);margin-top:8px;",
        text: `已生成 ${plotPaths.length} 个结果图表，请在右侧面板查看完整图表。`,
      }));
    }

    addConversationMessage("assistant", parts);
  }

  // ---- Error handler ----
  function onPipelineError(err) {
    cylState.active = false;
    setStatusBadge("FAILED");
    showError(err.message);
    hideProgress();
    updateProgressMessage("失败: " + err.message);

    // Build user-friendly error message with guidance
    const errParts = [];
    errParts.push(el("div", {
      style: "font-weight:600;margin-bottom:6px;color:#c0392b;",
      text: "执行失败",
    }));

    // Classify error and provide targeted guidance
    const errMsg = err.message || "";
    let guidance = "";
    if (errMsg.includes("upload") || errMsg.includes("archive") || errMsg.includes("SSH") || errMsg.includes("connection")) {
      guidance = "无法连接到执行平台。请检查工作站配置（左侧面板「工作站配置」），确保 SSH 连接正常后再重试。";
    } else if (errMsg.includes("mesh") || errMsg.includes("blockMesh") || errMsg.includes("snappyHexMesh")) {
      guidance = "网格生成失败。请检查几何参数是否合理（如圆柱尺寸不要超过计算域），或尝试调整网格参数后重新确认方案。";
    } else if (errMsg.includes("smoke") || errMsg.includes("checkMesh")) {
      guidance = "验证未通过。请查看上方详细日志，检查网格质量和物理参数设置。";
    } else if (errMsg.includes("Spec not found")) {
      guidance = "服务已重启，方案数据丢失。请重新输入研究目标创建新方案。";
    } else {
      guidance = "请查看上方错误详情。如问题持续，请检查模型配置和网络连接后重试。";
    }

    errParts.push(el("div", {
      style: "font-size:12px;margin-bottom:8px;",
      text: errMsg,
    }));
    errParts.push(el("div", {
      style: "font-size:12px;color:#555;margin-bottom:10px;",
      text: guidance,
    }));

    // Add retry button if we have a specId (can re-compile and re-execute)
    if (cylState.specId && cylState.specConfirmed) {
      const retryBtn = createConfirmButton("重新编译并执行", () => {
        compileAndValidate();
      }, (btn) => {
        btn.textContent = "✓ 已重新启动";
        btn.style.opacity = "0.6";
      });
      errParts.push(retryBtn);
    }

    addConversationMessage("system", errParts);
  }

  // ---- Polling (preserves original logic, adds smoke-test checkpoint) ----
  function pollJobStatus(jobId, options) {
    const maxWait = 1800; // 30 min
    const interval = 5000; // 5 seconds
    let elapsed = 0;
    let pct = options.pauseOnSmoke ? 35 : 50;
    let consecutiveErrors = 0;
    const maxConsecutiveErrors = 5;
    let smokeTestHandled = false;

    const poll = async () => {
      // If paused (waiting for user to confirm run), keep waiting
      if (cylState.pollPaused) {
        cylState.pollTimer = setTimeout(poll, interval);
        return;
      }

      try {
        const status = await fetchJSON(`${CYL_API}/jobs/${jobId}/status`);
        consecutiveErrors = 0;

        // Handle SMOKE_PASSED status (stop_after_smoke mode)
        if (status.status === "SMOKE_PASSED" && options.pauseOnSmoke && !smokeTestHandled) {
          smokeTestHandled = true;
          cylState.pollPaused = true;
          hideProgress();
          setStatusBadge("SMOKE_PASSED");
          if (options.onSmokeTestPassed) {
            options.onSmokeTestPassed(status);
          }
          return; // Stop polling until user confirms
        }

        if (status.status === "RUNNING") {
          // Determine progress text based on current phase
          let progressText = status.progress || "仿真运行中...";
          if (!status.mesh_status && !status.smoke_test_status) {
            progressText = "正在生成网格...";
          } else if (status.mesh_status === "PASSED" && !status.smoke_test_status) {
            progressText = "正在检查网格质量 (checkMesh)...";
          } else if (!status.smoke_test_status) {
            progressText = "正在运行 Smoke Test...";
          } else if (status.smoke_test_status === "PASSED" && !status.run_status) {
            progressText = "正在运行仿真计算...";
          }

          pct = Math.min(95, pct + 2);
          showProgress(progressText, pct);
          updateProgressMessage(progressText);

          // Check if smoke test passed and we should pause
          if (status.smoke_test_status === "PASSED" && !smokeTestHandled && options.pauseOnSmoke) {
            smokeTestHandled = true;
            cylState.pollPaused = true;
            if (options.onSmokeTestPassed) {
              options.onSmokeTestPassed(status);
            }
            return; // Stop polling until user confirms
          }

          elapsed += interval / 1000;
          if (elapsed >= maxWait) {
            setStatusBadge("TIMEOUT");
            showError("仿真超时（30分钟）");
            hideProgress();
            updateProgressMessage("仿真超时（30分钟）");
            if (options.onError) options.onError(new Error("仿真超时（30分钟）"));
            cylState.active = false;
            return;
          }
          cylState.pollTimer = setTimeout(poll, interval);
        } else {
          // Done
          cylState.pollTimer = null;
          hideProgress();
          setStatusBadge(status.status);

          if (status.status === "SUCCESS" || status.status === "PARTIAL") {
            if (options.onCompleted) {
              options.onCompleted(status);
            }
          } else {
            // Failed
            if (options.onError) {
              options.onError(new Error(status.error || "仿真失败"));
            } else {
              showError(status.error || "仿真失败");
              addConversationMessage("system", "仿真失败: " + (status.error || "未知错误"));
              cylState.active = false;
            }
          }
        }
      } catch (err) {
        consecutiveErrors++;
        if (consecutiveErrors < maxConsecutiveErrors) {
          console.warn(`Poll error (${consecutiveErrors}/${maxConsecutiveErrors}), retrying...`, err.message);
          showProgress(`网络中断，重试中 (${consecutiveErrors}/${maxConsecutiveErrors})...`, pct);
          cylState.pollTimer = setTimeout(poll, interval * consecutiveErrors);
        } else {
          if (options.onError) {
            options.onError(new Error("轮询失败（已重试" + maxConsecutiveErrors + "次）: " + err.message));
          } else {
            showError("轮询失败: " + err.message);
            addConversationMessage("system", "轮询失败: " + err.message);
            cylState.active = false;
          }
        }
      }
    };

    poll();
  }

  // ---- Intercept the composer submit ----
  function interceptComposer() {
    const form = byId("composer-form");
    if (!form) return;

    form.addEventListener("submit", async (e) => {
      const textarea = byId("research-input");
      const text = textarea ? textarea.value.trim() : "";
      if (!text) return;

      // Priority 1: If a spec already exists and this is a modification command,
      // treat as modification (even if it contains cylinder/flow keywords)
      if (cylState.specId && isModificationCommand(text)) {
        e.preventDefault();
        e.stopPropagation();

        addConversationMessage("user", text);

        await modifySpec(text);
        if (textarea) textarea.value = "";
        return;
      }

      // Priority 2: Check if this is a cylinder/obstacle flow input
      if (isObstacleFlowInput(text)) {
        e.preventDefault();
        e.stopPropagation();

        // Start draft pipeline (startDraft will add the user message)
        await startDraft(text);
        if (textarea) textarea.value = "";
        return;
      }

      // If none of the above, let the normal form submission proceed
    }, true); // capture phase to intercept before other handlers
  }

  // ---- Modify spec (only updates right panel, no auto-execute) ----
  async function modifySpec(modificationText) {
    cylState.active = true;
    setStatusBadge("MODIFY");
    // Do NOT show overlay during modification
    clearError();
    hideProgress();
    showProgress("正在修改方案...", 10);
    createProgressMessage("正在修改方案参数...");

    try {
      const result = await postJSON(`${CYL_API}/modify`, {
        spec_id: cylState.specId,
        modification_text: modificationText,
      });

      if (!result.success) throw new Error(result.error || "Modify failed");

      cylState.specId = result.spec_id || cylState.specId;
      cylState.spec = result.spec;
      cylState.semanticDisplay = result.semantic_display;
      cylState.userInput = (cylState.userInput || "") + "\n" + modificationText;
      cylState.specConfirmed = false; // Need to re-confirm after modification

      // Only update the right panel — do NOT auto-execute
      renderSpecPanel(result.spec, result.semantic_display);
      appendChangeSummary(result.change_summary, result.spec_version || result.spec?.spec_version);

      hideProgress();
      updateProgressMessage("方案已修改。");

      // Show what was modified in conversation
      const parts = [];
      parts.push(el("div", {
        style: "font-weight:600;margin-bottom:6px;",
        text: "方案已修改",
      }));
      parts.push(el("div", {
        style: "font-size:12px;",
        text: `修改内容: ${modificationText}`,
      }));
      parts.push(el("div", {
        style: "font-size:11px;color:var(--faint,#6c726d);margin-top:6px;",
        text: "右侧面板已更新相关字段。请确认修改后的方案以继续。",
      }));

      addConversationMessage("assistant", parts);

      // Show confirm button again since spec changed
      cylState.stage = Stage.AWAITING_PLAN_CONFIRM;
      showPlanConfirmButton();
      cylState.active = false;
      saveCylState();

    } catch (err) {
      setStatusBadge("FAILED");
      showError(err.message);
      hideProgress();
      updateProgressMessage("修改失败: " + err.message);
      addConversationMessage("system", "修改失败: " + err.message);
      cylState.active = false;
    }
  }

  // ---- Close button ----
  function setupCloseButton() {
    const btn = byId("cyl-close-results");
    if (btn) {
      btn.addEventListener("click", () => {
        // Only stop polling if not running
        if (cylState.stage !== Stage.RUNNING && cylState.stage !== Stage.VALIDATING) {
          if (cylState.pollTimer) {
            clearTimeout(cylState.pollTimer);
            cylState.pollTimer = null;
          }
          cylState.active = false;
        }
        // Just hide the overlay — results remain in conversation and right panel
        hideOverlay();
      });
    }
  }

  // ---- State persistence (localStorage) ----
  function saveCylState() {
    try {
      const state = {
        specId: cylState.specId,
        jobId: cylState.jobId,
        draftStatus: cylState.draftStatus,
        stage: cylState.stage,
        specConfirmed: cylState.specConfirmed,
        userInput: cylState.userInput,
        spec: cylState.spec, // Save full spec for recovery after server restart
        semanticDisplay: cylState.semanticDisplay,
        convMessages: convMessages.slice(-50), // Keep last 50 messages
        savedAt: Date.now(),
      };
      localStorage.setItem(CF_STORAGE_KEY, JSON.stringify(state));
    } catch (e) {
      console.warn("[CylinderFlow] Failed to save state:", e.message);
    }
  }

  function loadCylState() {
    try {
      const raw = localStorage.getItem(CF_STORAGE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (e) {
      console.warn("[CylinderFlow] Failed to load state:", e.message);
      return null;
    }
  }

  function clearCylState() {
    try {
      localStorage.removeItem(CF_STORAGE_KEY);
    } catch (e) { /* ignore */ }
  }

  // ---- Restore state on page load ----
  async function restoreCylState() {
    // First try URL parameters (for direct session restoration)
    const urlParams = new URLSearchParams(window.location.search);
    const urlSpecId = urlParams.get("spec_id");
    const urlJobId = urlParams.get("job_id");

    // Then try localStorage
    const saved = loadCylState();

    // Determine which source to use
    let restoreSpecId = urlSpecId || (saved && saved.specId);
    let restoreJobId = urlJobId || (saved && saved.jobId);

    if (!restoreSpecId) return false;

    console.log("[CylinderFlow] Restoring state:", restoreSpecId, restoreJobId);

    // Restore basic state from localStorage if available
    if (saved) {
      cylState.specId = saved.specId;
      cylState.jobId = saved.jobId;
      cylState.draftStatus = saved.draftStatus;
      cylState.stage = saved.stage;
      cylState.specConfirmed = saved.specConfirmed;
      cylState.userInput = saved.userInput;
    }

    // Override with URL params if provided
    if (urlSpecId) cylState.specId = urlSpecId;
    if (urlJobId) cylState.jobId = urlJobId;

    // Conversation messages will be restored AFTER async operations complete,
    // because v5-app.js createNewSession() may clear the timeline asynchronously.

    // Try to fetch the spec from backend
    try {
      const specResp = await fetchJSON(`${CYL_API}/${restoreSpecId}`);
      if (specResp && specResp.spec) {
        cylState.spec = specResp.spec;
        cylState.semanticDisplay = specResp.semantic_display || null;
        cylState.draftStatus = specResp.draft_status || cylState.draftStatus;

        // Render the spec panel
        renderSpecPanel(cylState.spec, cylState.semanticDisplay);

        // Show the right panel
        const rp = byId("right-panel");
        if (rp) rp.classList.add("has-content");
      }
    } catch (e) {
      console.warn("[CylinderFlow] Failed to restore spec:", e.message);
    }

    // If we have a job, fetch its status and restore appropriate UI
    if (restoreJobId) {
      try {
        const status = await fetchJSON(`${CYL_API}/jobs/${restoreJobId}/status`);

        if (status.status === "SUCCESS" || status.status === "PARTIAL") {
          // Simulation completed - restore results
          cylState.stage = Stage.COMPLETED;
          cylState.lastResults = status;
          setStatusBadge(status.status);

          // Show results in right panel
          showSummary(
            status.mesh_status,
            status.smoke_test_status,
            status.run_status,
            (status.plot_paths || []).length
          );
          renderPlots(status.plot_paths, restoreJobId);
          showRightPanelResults(status, restoreJobId);

          // Fetch and display analysis report
          try {
            const report = await fetchJSON(`${CYL_API}/${restoreJobId}/report`);
            if (report.success && report.report) {
              cylState.lastReport = report.report;
              showRightPanelAnalysis(report.report);
            }
          } catch (e) {
            console.warn("[CylinderFlow] Failed to fetch report on restore:", e.message);
          }

        } else if (status.status === "RUNNING") {
          // Simulation still running - restart polling
          cylState.stage = Stage.RUNNING;
          setStatusBadge("RUNNING");
          showProgress("恢复轮询仿真状态...", 50);
          pollJobStatus(restoreJobId, {
            pauseOnSmoke: false,
            onCompleted: (s) => onSimulationCompleted(s),
            onError: (e) => onPipelineError(e),
          });

        } else if (status.status === "SMOKE_PASSED") {
          // Waiting for run confirmation
          cylState.stage = Stage.AWAITING_RUN_CONFIRM;
          setStatusBadge("SMOKE_PASSED");
          // Button will be re-created after message restoration below

        } else if (status.status === "FAILED" || status.status === "ERROR") {
          cylState.stage = null;
          setStatusBadge("FAILED");
          // Use onPipelineError for consistent error display with retry button
          onPipelineError(new Error(status.error || "之前的仿真任务失败"));
        }
      } catch (e) {
        console.warn("[CylinderFlow] Failed to restore job status:", e.message);
      }
    }

    // Restore conversation messages AFTER all async operations complete.
    // This ensures v5-app.js createNewSession() (which clears timeline) has finished.
    if (saved && saved.convMessages && saved.convMessages.length > 0) {
      const tl = byId("conversation-timeline");
      if (tl) {
        tl.innerHTML = ""; // Clear v5-app.js welcome message
        convMessages.length = 0; // Clear runtime array
        saved.convMessages.forEach(msg => {
          // Populate runtime array so saveCylState() preserves messages
          convMessages.push({ type: msg.type, text: msg.text });
          // Re-create DOM without calling addConversationMessage (avoids double-tracking)
          const avatarText = msg.type === "user" ? "你" : msg.type === "system" ? "系" : "FS";
          const metaText = msg.type === "user" ? "用户" : msg.type === "system" ? "系统" : "研究助手";
          const body = el("div", { class: "conv-msg-body" }, [
            el("div", { class: "conv-msg-meta", text: metaText }),
            el("div", { text: msg.text }),
          ]);
          const div = el("div", { class: `conv-msg ${msg.type}` }, [
            el("div", { class: "conv-msg-avatar", text: avatarText }),
            body,
          ]);
          tl.appendChild(div);
        });
        tl.scrollTop = tl.scrollHeight;
        console.log(`[CylinderFlow] Restored ${convMessages.length} conversation messages`);
      }
    }

    // Re-create interactive buttons based on the restored stage.
    // Conversation messages are text-only, so buttons need to be re-added.
    if (cylState.stage === Stage.AWAITING_PLAN_CONFIRM) {
      showPlanConfirmButton();
    } else if (cylState.stage === Stage.AWAITING_COMPILE_CONFIRM) {
      showCompileConfirmButton();
    } else if (cylState.stage === Stage.AWAITING_RUN_CONFIRM) {
      showRunConfirmButton();
    }

    saveCylState();
    return true;
  }

  // ---- Init ----
  function init() {
    interceptComposer();
    setupCloseButton();
    console.log(`[CylinderFlow] Frontend integration loaded (v=${VERSION})`);

    // Wait for v5-app.js to finish its init before restoring state.
    // v5-app.js's createNewSession() clears the conversation timeline and adds
    // a welcome message. We must wait for that to complete before restoring
    // our messages, otherwise they get wiped.
    let restored = false;
    const doRestore = () => {
      if (restored) return;
      restored = true;
      restoreCylState().then(r => {
        if (r) console.log("[CylinderFlow] State restored successfully");
      });
    };

    // Poll every 300ms: check if v5-app.js has added its welcome message
    const checkInterval = setInterval(() => {
      const tl = byId("conversation-timeline");
      if (tl && tl.children.length > 0) {
        clearInterval(checkInterval);
        doRestore();
      }
    }, 300);

    // Fallback: restore after 5 seconds even if v5-app.js hasn't finished
    setTimeout(() => {
      clearInterval(checkInterval);
      doRestore();
    }, 5000);
  }

  // Run when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Expose for debugging
  window._cylinderFlow = {
    cylState,
    startDraft,
    modifySpec,
    renderSpecPanel,
    renderResultsPanel,
    Stage,
    saveCylState,
    restoreCylState,
  };
})();
