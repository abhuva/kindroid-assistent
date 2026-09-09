"use strict";
(() => {
  const el = id => document.getElementById(id);
  const api = new RelayApiClient({ csrf: document.querySelector('meta[name="relay-csrf"]').content });
  let action = null;
  let opener = null;
  let busy = false;
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  const source = id => {
    const button = node("button", `Source: ${id}`);
    button.type = "button";
    button.className = "text-button";
    button.addEventListener("click", () => document.dispatchEvent(new CustomEvent("memory-source", { detail: { id, opener: button } })));
    return button;
  };
  function confirm(next, button, scope) {
    action = next; opener = button;
    el("exploration-confirm-scope").textContent = scope;
    el("exploration-reason").value = "";
    el("exploration-reason").disabled = ["start", "interpret"].includes(next.kind);
    el("exploration-reason").required = !el("exploration-reason").disabled;
    el("exploration-dialog-error").textContent = "";
    el("exploration-dialog").showModal();
  }
  async function refresh() {
    try {
      const data = await api.replaceRead("exploration", signal => api.request("/api/v1/exploration", { signal }));
      el("exploration-start").disabled = !data.enabled || data.paused || data.budget.used >= data.budget.limit;
      el("exploration-status").textContent = `${data.enabled ? "Manual exploration enabled" : "Disabled pending operator acceptance"}. ${data.budget.used}/${data.budget.limit} attempts in 24h. Sources and optional research follow-through are configured under Schedule; observations are not preferences.`;
      const schedule = data.curiosity;
      const when = value => value ? new Date(value * 1000).toLocaleString() : "not scheduled";
      const quiet = value => value ? value.map(n => `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`).join("–") : "none";
      el("curiosity-history").replaceChildren();
      if (schedule) {
        const cadence = schedule.shared_schedule ? "shared tick allocation; see Schedule tab" : schedule.hours?.length ? `local hours ${schedule.hours.join(", ")}`
          : `every ${schedule.interval_seconds / 3600}h, jitter ±${schedule.jitter_seconds / 60}m`;
        el("curiosity-status").textContent = `${schedule.enabled ? "Scheduled curiosity enabled" : "Scheduled curiosity off; manual invitations only"}. ${cadence}. ${schedule.shared_schedule ? "Next shared tick" : "Next"}: ${schedule.initializing ? "awaiting worker initialization" : when(schedule.next_due_at)}.`;
        const spacing = schedule.shared_schedule
          ? "No conversation cooldown; Schedule dead time and unresolved-work safeguards apply."
          : `Requires ${schedule.idle_seconds / 60}m quiet conversation; curiosity quiet hours ${quiet(schedule.quiet_hours)} (social quiet hours also apply).`;
        el("curiosity-budget").textContent = `Scheduled attempts: ${schedule.budget.attempts}/${schedule.attempt_limit} in 24h. Reserved interpretation calls: ${schedule.budget.interpretations}/${schedule.interpretation_limit}. ${spacing} Missed or blocked slots are skipped, not retried. Refresh to update.`;
        for (const slot of schedule.recent) {
          el("curiosity-history").append(node("li", `${when(slot.scheduled_at)} · ${slot.outcome}${slot.command_status ? ` · command ${slot.command_status}` : ""}${slot.session_status ? ` · session ${slot.session_status}` : ""}${slot.command_id ? ` · ${slot.command_id}` : ""}`));
        }
      } else {
        el("curiosity-status").textContent = "Schedule status unavailable; restart the updated relay host.";
        el("curiosity-budget").textContent = "";
      }
      const sessions = el("exploration-sessions"); sessions.replaceChildren();
      for (const session of data.sessions) {
        const item = node("details", "");
        const latestResponse = (session.responses || []).at(-1);
        item.append(node("summary", `${session.status}${latestResponse ? ` / ${latestResponse.classification || "interpretation"}: ${latestResponse.status}` : ""} · ${session.id} · budget ${session.budget} invitation`));
        item.append(node("p", `Mode: ${session.mode}; version: ${session.version}. Seed: ${session.seed}`));
        if (session.response_message_id) item.append(source(session.response_message_id));
        if (["observed", "awaiting_sync"].includes(session.status) && !(session.responses || []).length) {
          const interpret = node("button", "Interpret response"); interpret.type = "button";
          interpret.disabled = !data.enabled || data.paused;
          interpret.addEventListener("click", () => confirm({ kind: "interpret", session_id: session.id }, interpret,
            "Interpret the existing response without resending the invitation. May clarify or acknowledge it. An invitation already opted into bounded research may continue under the saved Discovery settings. Old invitations are not automatically opted in. No publishing or identity promotion."));
          item.append(interpret);
        }
        for (const response of session.responses || []) {
          item.append(node("p", `Response ${response.step + 1}: ${response.classification || "pending interpretation"} · ${response.status} · ${response.note || ""}`));
          if (response.decision.quote) item.append(node("blockquote", response.decision.quote));
          if (response.decision.interpretation) item.append(node("p", `Codex interpretation: ${response.decision.interpretation}`));
          if (response.message_id) item.append(source(response.message_id));
        }
        if (["uncertain", "interrupted"].includes(session.status) || (session.responses || []).some(r => ["uncertain", "failed", "needs_review", "awaiting_reply"].includes(r.status))) {
          const close = node("button", "Close after inspection"); close.type = "button";
          close.addEventListener("click", () => confirm({ kind: "close", session_id: session.id }, close,
            "After inspecting the Kindroid app and delivery record, close this exploration without retrying it. This does not clear delivery uncertainty, resume the relay, or send anything."));
          item.append(close);
        }
        for (const event of session.events) {
          item.append(node("p", `${event.actor} · ${event.origin} · ${event.kind}`));
          if (event.observation) for (const evidence of event.observation.evidence) {
            item.append(node("blockquote", evidence.quote));
            item.append(source(evidence.message_id));
          }
          if (event.details.reason) item.append(node("p", event.details.reason));
        }
        sessions.append(item);
      }
      const candidates = el("exploration-candidates"); candidates.replaceChildren();
      for (const candidate of data.candidates) {
        const item = node("div", ""); item.className = "memory-result";
        item.append(node("p", `${candidate.status} · ${candidate.origin_actor === "hannah" ? "Hannah's reported alternative (Codex interpretation)" : "Codex suggestion"} · ${candidate.summary}`));
        item.append(node("p", `${candidate.selection_kind} · ${candidate.provider} · ${candidate.locator}`));
        for (const response of candidate.responses || []) {
          item.append(node("p", `Reported response: ${response.reaction} · ${response.observation_status || "active"} observation · source-backed interpretation, not a permanent preference`));
          if (response.message_id) item.append(source(response.message_id));
        }
        const button = node("button", candidate.status === "active" ? "Archive candidate" : "Restore candidate");
        button.type = "button"; button.className = "secondary";
        button.addEventListener("click", () => confirm({ kind: "review", candidate_id: candidate.id,
          status: candidate.status === "active" ? "archived" : "active" }, button,
          "Changes only local candidate visibility. Evidence and event history remain; this does not adopt an interest or send a message."));
        item.append(button); candidates.append(item);
      }
      el("exploration-error").textContent = "";
    } catch (error) {
      if (error.name !== "AbortError") {
        el("exploration-start").disabled = true;
        el("exploration-error").textContent = error.message;
      }
    }
  }
  el("exploration-panel").addEventListener("toggle", () => { if (el("exploration-panel").open) refresh(); });
  el("exploration-refresh").addEventListener("click", refresh);
  el("exploration-start").addEventListener("click", () => confirm({ kind: "start" }, el("exploration-start"),
    "Sends one [CODEX] invitation and offers one optional response turn. Automatic interpretation may clarify or acknowledge it. If enabled under Schedule, a selected topic can receive one bounded public lookup, one result and one optional reflection. No publishing, accounts or identity changes. Uses the configured history, exploration and research budgets."));
  el("exploration-cancel").addEventListener("click", () => { if (!busy) el("exploration-dialog").close(); });
  el("exploration-dialog").addEventListener("cancel", event => { if (busy) event.preventDefault(); });
  el("exploration-dialog").addEventListener("close", () => { opener?.focus(); action = null; });
  el("exploration-confirm-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (!action || busy) return;
    busy = true; el("exploration-confirm").disabled = true;
    try {
      const command = action.kind === "start"
        ? await api.submit("/api/v1/commands/start-exploration")
        : action.kind === "interpret"
        ? await api.submit("/api/v1/exploration/interpret", { payload: { session_id: action.session_id } })
        : action.kind === "close"
        ? await api.submit("/api/v1/exploration/close", { payload: { session_id: action.session_id, reason: el("exploration-reason").value.trim() } })
        : await api.submit("/api/v1/exploration/candidates/review", { payload: {
          candidate_id: action.candidate_id, status: action.status, reason: el("exploration-reason").value.trim() } });
      el("exploration-command").textContent = `Queued ${command.id}. Check command activity for completion; refresh this panel for its result.`;
      el("exploration-dialog").close();
      await refresh();
    } catch (error) { el("exploration-dialog-error").textContent = `${error.message} Retry retains the same command key.`; }
    finally { busy = false; el("exploration-confirm").disabled = false; }
  });
})();
