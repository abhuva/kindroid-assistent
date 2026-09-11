"use strict";
(() => {
  const el = id => document.getElementById(id);
  const api = new RelayApiClient({ csrf: document.querySelector('meta[name="relay-csrf"]').content });
  const when = stamp => stamp ? new Date(stamp * 1000).toLocaleString() : "none";
  let pending = null, retry = null, busy = false, sharedLoaded = false;
  let recovery = null;
  let lastPaused = null;
  const recoveryText = command => {
    const result = command.result || {};
    if (["recovered", "already_recovered"].includes(result.outcome)) return "Relay session recovered. History adopted without retrying the uncertain request. Resume, then Start conversation when ready.";
    if (result.outcome === "recovery_blocked") return `Recovery blocked: ${result.reason || "Inspect room activity."}`;
    return `Recovery ${command.status}${command.error ? ": " + command.error : ""}`;
  };
  function controls() {
    for (const id of ["encounter-save", "encounter-cancel", "session-end", "shared-save"]) el(id).disabled = busy || !!pending || !!retry;
    el("encounter-retry").hidden = !retry;
    el("encounter-retry").disabled = busy;
    el("session-recover").disabled = busy || !!pending || !!retry || !recovery?.eligible;
  }
  async function refresh() {
    try {
      const data = await api.replaceRead("encounters", signal => api.request("/api/v1/encounters", { signal }));
      el("encounter-controls").hidden = !data.enabled || data.mode === "shared";
      el("session-end").hidden = data.mode !== "shared";
      el("shared-schedule-controls").hidden = data.mode !== "shared";
      el("session-recovery").hidden = data.mode !== "shared";
      recovery = data.recovery || null;
      el("session-recovery-note").textContent = recovery?.reason || "Recovery is unavailable until room status is loaded.";
      if (data.mode === "shared") {
        const s = data.session;
        if (!sharedLoaded) {
          el("shared-time").value = data.shared_config.daily_time;
          el("shared-enabled").checked = data.shared_config.enabled;
          sharedLoaded = true;
        }
        el("encounter-status").textContent = `Daily session: ${data.shared_config.enabled ? data.shared_config.daily_time : "off"}. Next start: ${when(data.next_shared_at)}. ` +
          (s ? `${s.status}: ${s.message_count} messages${s.reason ? " · " + s.reason : ""}.` : "No session started yet.");
      } else if (data.choice) {
        const c = data.choice;
        const label = ["pending", "queued"].includes(c.status) ? "Next encounter" : "Last timing choice";
        el("encounter-status").textContent = `${label}: ${when(c.due_at)} · ${c.status} · chosen by ${c.actor}. ` +
          "This governs both check-ins and exploration invitations.";
        if (c.source_message_id) {
          const button = document.createElement("button");
          button.type = "button"; button.textContent = "View scheduling source";
          button.addEventListener("click", () => document.dispatchEvent(new CustomEvent("memory-source", { detail: { id: c.source_message_id, opener: button } })));
          el("encounter-status").append(" ", button);
        }
      } else {
        el("encounter-status").textContent = data.enabled
          ? "Using the saved schedule until the participant chooses the next encounter. Encounters offer exploration as a choice."
          : "Using the existing schedule. Participant-selected timing is not enabled.";
      }
      if (pending) {
        const command = await api.request(`/api/v1/commands/${pending}`);
        el("encounter-result").textContent = command.command_type === "recover_shared" ? recoveryText(command)
          : `Request ${command.status}${command.error ? ": " + command.error : ""}`;
        if (!["queued", "running"].includes(command.status)) pending = null;
      }
    } catch (error) {
      recovery = null;
      if (error.name !== "AbortError") el("encounter-result").textContent = "Encounter status unavailable; will retry.";
    } finally { controls(); }
  }
  async function submit(path, payload) {
    if (busy) return;
    busy = true; controls();
    try {
      const result = await api.submit(path, payload);
      pending = result.id; retry = null;
      el("encounter-result").textContent = "Request queued; waiting for the worker.";
    } catch (error) {
      if (error instanceof RelayApiError && [400, 403, 404, 409, 422].includes(error.status)) {
        retry = null;
        el("encounter-result").textContent = "Request rejected. Check the room settings and entered time, then try again.";
      } else {
        retry = () => submit(path, payload);
        el("encounter-result").textContent = "Request outcome unknown. Retry the same request to check it.";
      }
    } finally { busy = false; controls(); }
    await refresh();
  }
  el("encounter-save").addEventListener("click", () => {
    const date = new Date(el("encounter-time").value);
    if (!Number.isFinite(date.getTime()) || date.getTime() <= Date.now()) {
      el("encounter-result").textContent = "Choose a future date and time."; return;
    }
    submit("/api/v1/settings/encounter", { payload: { due_at: Math.floor(date.getTime() / 1000) } });
  });
  el("encounter-cancel").addEventListener("click", () => submit("/api/v1/settings/encounter", { payload: { due_at: null } }));
  el("session-end").addEventListener("click", () => submit("/api/v1/commands/end-session", {}));
  el("session-recover").addEventListener("click", () => {
    if (busy || pending || retry || !recovery?.eligible) return;
    const payload = { session_id: recovery.session_id, turn_id: recovery.turn_id, confirm: true };
    if (!window.confirm("End this interrupted RELAY session and adopt current Kindroid history? The uncertain request stays in the audit and will not be retried. This does not reset the Kindroid group, send messages, or generate replies. History reads use the normal allowance. The room remains paused; resume/start are separate actions.")) return;
    submit("/api/v1/commands/recover-shared", { payload });
  });
  document.addEventListener("relay-command", event => {
    const command = event.detail;
    if (command?.command_type !== "recover_shared") return;
    el("encounter-result").textContent = recoveryText(command);
    if (pending === command.id && !["queued", "running"].includes(command.status)) {
      pending = null; controls(); refresh();
    }
  });
  document.addEventListener("relay-snapshot", event => {
    const paused = event.detail?.relay?.paused;
    if (paused !== lastPaused) { lastPaused = paused; refresh(); }
  });
  el("shared-save").addEventListener("click", () => submit("/api/v1/settings/shared-session", { payload: {
    enabled: el("shared-enabled").checked, daily_time: el("shared-time").value
  } }));
  el("encounter-retry").addEventListener("click", () => retry?.());
  refresh();
  setInterval(refresh, 5000);
})();
