// Plan preview.
//
// Renders the steps Pearl intends to run and waits for approval. The
// approval carries a plan id back to /api/run, so the server executes
// the plan it showed rather than generating a second one. The browser
// never sends the steps themselves.
//
// The wording matters here. These are the *planned* steps, and only the
// opening plan is fixed: the executor still replans on failure, and
// those replans arrive on the progress stream. The copy must not imply
// the approved list is the whole run.

import { escapeHtml as esc, toolLabel } from "./format.js";
import { mk, msgInner } from "./state.js";

// Stage wording, replaced by the configured personality when
// /api/personality is wired in. Plain English until then.
const STAGES = {
  planning: "Planning",
  plan_ready: "Plan ready",
  waiting_approval: "Waiting for approval",
  running_tool: "Running",
  completed: "Completed",
};

export function setStageLabels(labels) {
  Object.assign(STAGES, labels ?? {});
}

export function stageLabel(stage) {
  return STAGES[stage] ?? stage;
}

function argSummary(args) {
  const entries = Object.entries(args ?? {});
  if (!entries.length) return "";
  return entries
    .map(([k, v]) => {
      const text = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}: ${text.length > 60 ? text.slice(0, 60) + "…" : text}`;
    })
    .join(" · ");
}

/**
 * Show `steps` and resolve to true if the user approves them.
 *
 * Resolves rather than calling back so the caller reads as a straight
 * line: plan, await approval, run.
 */
export function showPlanPreview(steps) {
  const card = mk("div", "plan-card msg-row msg-system");

  const list = steps
    .map(
      (s, i) => `
      <div class="plan-step">
        <span class="plan-n">${i + 1}</span>
        <span class="plan-tool">${esc(toolLabel(s.tool))}</span>
        <span class="plan-args">${esc(argSummary(s.arguments))}</span>
      </div>`,
    )
    .join("");

  card.innerHTML = `
    <div class="plan-hd">
      <span aria-hidden="true">◇</span>
      <span>${esc(stageLabel("plan_ready"))} — ${steps.length} planned step${
        steps.length !== 1 ? "s" : ""
      }</span>
    </div>
    <div class="plan-body">${list}</div>
    <div class="plan-acts">
      <span class="plan-note">Pearl may replan if a step fails, so this is the
      opening plan rather than the whole run. Nothing is written without a
      separate approval.</span>
      <button class="btn-reject plan-cancel">Cancel</button>
      <button class="btn-approve plan-go">Run these steps</button>
    </div>`;

  msgInner.appendChild(card);
  msgInner.parentElement.scrollTop = msgInner.parentElement.scrollHeight;

  return new Promise((resolve) => {
    const finish = (approved) => {
      card.querySelector(".plan-acts").remove();
      const outcome = mk("div", "plan-note plan-outcome");
      outcome.textContent = approved
        ? `${stageLabel("running_tool")}…`
        : "Plan cancelled. Nothing ran.";
      card.appendChild(outcome);
      resolve(approved);
    };

    card.querySelector(".plan-go").addEventListener("click", () => finish(true));
    card.querySelector(".plan-cancel").addEventListener("click", () => finish(false));
  });
}
