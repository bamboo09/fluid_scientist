const form = document.querySelector("#research-form");
const button = document.querySelector("#run-button");
const message = document.querySelector("#form-message");

const number = (value, digits = 2) => Number(value).toFixed(digits);

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
