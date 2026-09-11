"use strict";
(() => {
  const el = id => document.getElementById(id);
  const api = new RelayApiClient({ csrf: document.querySelector('meta[name="relay-csrf"]').content });
  const kinds = ["check_in", "exploration"];
  const labels = { check_in: "Check-in", exploration: "Exploration invitation", dead: "Dead time" };
  const limits = ["check_in_limit", "exploration_limit", "attempt_limit", "interpretation_limit"];
  const input = key => el(`schedule-${key.replaceAll("_", "-")}`);
  const clock = hour => `${String(hour).padStart(2, "0")}:00`;
  const tickClock = minute => `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
  let data = null, draft = null, dirty = false, busy = false, retry = null, pending = null;
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };

  function plan(config) {
    if (data?.participant_scheduling) config = { ...config, weights: { check_in: 100, exploration: 0 } };
    const interval = config.tick_minutes || 60;
    const hours = Array.from({ length: 1440 / interval }, (_, i) => i * interval)
      .filter(m => !config.dead_hours.includes(Math.floor(m / 60)));
    const counts = Object.fromEntries(kinds.map(k => [k, Math.floor(hours.length * config.weights[k] / 100)]));
    const remainder = [...kinds].sort((a, b) => (hours.length * config.weights[b] % 100) - (hours.length * config.weights[a] % 100));
    for (const k of remainder.slice(0, hours.length - Object.values(counts).reduce((a, b) => a + b, 0))) counts[k]++;
    const score = { check_in: 0, exploration: 0 }, assignments = {};
    for (const hour of hours) {
      for (const k of kinds) score[k] += counts[k];
      const chosen = score.check_in >= score.exploration ? "check_in" : "exploration";
      score[chosen] -= hours.length; assignments[hour] = chosen;
    }
    return { counts, assignments, hours };
  }

  function blocks(hours) {
    if (hours.length === 24) return [Array.from({ length: 24 }, (_, i) => i)];
    const result = [];
    for (let h = 0; h < 24; h++) {
      if (!hours.includes(h) || hours.includes((h + 23) % 24)) continue;
      const block = [h];
      while (hours.includes((block.at(-1) + 1) % 24)) block.push((block.at(-1) + 1) % 24);
      result.push(block);
    }
    return result;
  }

  function controls() {
    el("schedule-fields").disabled = !draft || busy || !!retry || !!pending;
    el("schedule-save").disabled = !draft || busy || !!pending;
    el("schedule-save").textContent = retry ? "Retry same save" : "Save schedule";
    el("schedule-discard").disabled = !dirty || busy || !!retry || !!pending;
  }

  function edit() { dirty = true; el("schedule-error").textContent = ""; render(); }
  function render() {
    if (!draft) return;
    el("schedule-enabled").checked = draft.enabled;
    const interval = draft.tick_minutes || 60;
    document.querySelectorAll("[data-tick-minutes]").forEach(button => {
      button.setAttribute("aria-pressed", String(Number(button.dataset.tickMinutes) === interval));
    });
    for (const k of kinds) input(k).value = draft.weights[k];
    el("schedule-balance").value = draft.weights.check_in;
    for (const k of limits) input(k).value = draft[k];
    const p = plan(draft);
    el("schedule-counts").textContent = `${24 - draft.dead_hours.length} available hours · ${p.hours.length} ${interval === 60 ? "hourly ticks" : `ticks (${interval} mins)`} · ${p.counts.check_in} check-ins / ${p.counts.exploration} exploration invitations${dirty ? " · Unsaved preview" : ""}`;
    const graph = el("schedule-timeline");
    graph.classList.toggle("dense", interval < 60);
    if (!graph.children.length) {
      for (let h = 0; h < 24; h++) {
        const b = node("button", ""); b.type = "button"; b.dataset.hour = h;
        b.addEventListener("focus", () => { el("schedule-slot-detail").textContent = b.title; });
        b.addEventListener("pointerenter", () => { el("schedule-slot-detail").textContent = b.title; });
        b.addEventListener("click", () => {
          draft.dead_hours = draft.dead_hours.includes(h) ? draft.dead_hours.filter(x => x !== h) : [...draft.dead_hours, h].sort((a, b) => a - b);
          edit(); el("schedule-slot-detail").textContent = b.title;
        });
        graph.append(b);
      }
    }
    for (let h = 0; h < 24; h++) {
      const b = graph.children[h], dead = draft.dead_hours.includes(h);
      const firstActivity = p.assignments[h * 60] || "dead";
      b.className = `schedule-hour ${dead ? "dead" : interval === 60 ? firstActivity : "multiple-ticks"}${interval < 60 ? " compact-ticks" : ""}`;
      b.setAttribute("aria-pressed", String(dead));
      b.replaceChildren(node("span", clock(h)));
      const descriptions = [];
      for (let m = h * 60; m < (h + 1) * 60; m += interval) {
        const activity = p.assignments[m] || "dead";
        const actual = data.timeline?.find(t => (t.minute ?? t.hour * 60) === m)?.actual;
        const description = `${tickClock(m)}: ${labels[activity]}${actual ? `. Recorded today: ${labels[actual.activity]}, ${actual.outcome.replaceAll("_", " ")}` : ". Planned opportunity"}`;
        descriptions.push(description);
        const mark = node("strong", activity === "dead" ? "—" : activity === "check_in" ? "C" : "E");
        if (interval === 60) {
          b.append(mark);
          if (actual) b.append(node("small", actual.outcome === "queued" ? actual.command_status || "queued" : actual.outcome.replaceAll("_", " ")));
        } else {
          const tick = node("span", ""); tick.className = `schedule-tick ${activity}`;
          tick.title = description;
          tick.append(node("span", tickClock(m)), mark); b.append(tick);
        }
      }
      b.title = `${descriptions.join("; ")}. Select to ${dead ? "activate" : "exclude"} this whole hour.`;
      b.setAttribute("aria-label", b.title);
    }
    const list = el("schedule-dead-blocks"); list.replaceChildren();
    for (const block of blocks(draft.dead_hours)) {
      const li = node("li", `${clock(block[0])}–${clock((block.at(-1) + 1) % 24)} (${block.length}h) `);
      const remove = node("button", "Remove"); remove.type = "button";
      remove.setAttribute("aria-label", `Remove dead time starting ${clock(block[0])}`);
      remove.addEventListener("click", () => { draft.dead_hours = draft.dead_hours.filter(h => !block.includes(h)); edit(); el("schedule-add-dead").focus(); });
      li.append(remove); list.append(li);
    }
    const warnings = [];
    if (data?.participant_scheduling) warnings.push("All automatic slots open as check-ins offering exploration. A participant's next-time choice supersedes this tick schedule.");
    if (data?.room_mode === "shared") warnings.push("This room uses Daily companion session controls above. These legacy tick allocations do not start shared sessions.");
    if (!data.capabilities.check_in && p.counts.check_in) warnings.push("Check-ins are disabled in startup configuration.");
    if (!data.capabilities.exploration && p.counts.exploration) warnings.push("Exploration is disabled. Enable RELAY_EXPLORATION_ENABLED and restart before invitations can run.");
    if (draft.check_in_limit < p.counts.check_in) warnings.push("The check-in limit is below the planned allocation.");
    if (Math.min(draft.attempt_limit, draft.exploration_limit) < p.counts.exploration) warnings.push("Exploration limits are below the planned allocation.");
    if (data.auto_interpret && draft.interpretation_limit < p.counts.exploration * 2) warnings.push("The interpretation-call limit is below two calls per planned invitation.");
    if (!p.hours.length) warnings.push("All hours are dead time; no scheduled activities will start.");
    el("schedule-warnings").textContent = warnings.join(" ");
    controls();
    for (const k of kinds) input(k).disabled = !!data?.participant_scheduling;
    el("schedule-balance").disabled = !!data?.participant_scheduling;
    if (data?.room_mode === "shared") {
      el("schedule-fields").disabled = true;
      el("schedule-save").disabled = true;
    }
  }

  function receive(snapshot) {
    if (!snapshot) return;
    data = snapshot;
    if (!dirty && !retry && !pending) draft = { tick_minutes: 60, ...JSON.parse(JSON.stringify(data.config)) };
    const due = data.next_due_local || (data.next_due_at ? new Date(data.next_due_at * 1000).toLocaleString() : "none");
    el("schedule-state").textContent = `${data.configured ? data.config.enabled ? "Shared schedule active" : "Scheduled activities off" : "Using legacy timing until you save this schedule"}. ${data.date} · ${data.timezone.join(" / ")}. Next tick: ${due}.`;
    const history = el("schedule-history"); history.replaceChildren();
    for (const slot of data.recent) history.append(node("li", `${slot.wall_slot.replace("T", " ")}${slot.wall_slot.length === 13 ? ":00" : ""} · ${labels[slot.activity]} · ${slot.outcome.replaceAll("_", " ")}${slot.command_status ? ` · ${slot.command_status}` : ""}`));
    if (!data.recent.length) history.append(node("li", "No shared schedule ticks recorded yet."));
    render();
  }

  async function refresh() {
    try { receive(await api.replaceRead("schedule", signal => api.request("/api/v1/schedule", { signal }))); }
    catch (error) { if (error.name !== "AbortError") el("schedule-error").textContent = error.message; }
  }
  el("schedule-refresh").addEventListener("click", refresh);
  el("schedule-discard").addEventListener("click", () => { dirty = false; receive(data); });
  el("schedule-enabled").addEventListener("change", event => { draft.enabled = event.target.checked; edit(); });
  document.querySelectorAll("[data-tick-minutes]").forEach(button => {
    button.addEventListener("click", () => { draft.tick_minutes = Number(button.dataset.tickMinutes); edit(); });
  });
  for (const k of kinds) input(k).addEventListener("input", event => {
    if (event.target.value === "" || !event.target.checkValidity()) return;
    draft.weights[k] = Number(event.target.value);
    draft.weights[kinds.find(x => x !== k)] = 100 - draft.weights[k]; edit();
  });
  el("schedule-balance").addEventListener("input", event => {
    draft.weights.check_in = Number(event.target.value); draft.weights.exploration = 100 - draft.weights.check_in; edit();
  });
  for (const k of limits) input(k).addEventListener("input", event => {
    if (event.target.value === "" || !event.target.checkValidity()) return;
    draft[k] = Number(event.target.value); edit();
  });
  el("schedule-add-dead").addEventListener("click", () => {
    const start = el("schedule-dead-start"), end = el("schedule-dead-end");
    if (!start.reportValidity() || !end.reportValidity()) return;
    const a = Number(start.value.slice(0, 2)), b = Number(end.value.slice(0, 2));
    const hours = Array.from({ length: (b - a + 24) % 24 || 24 }, (_, i) => (a + i) % 24);
    draft.dead_hours = [...new Set([...draft.dead_hours, ...hours])].sort((x, y) => x - y); edit();
  });
  el("schedule-clear-dead").addEventListener("click", () => { draft.dead_hours = []; edit(); });
  el("schedule-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || pending || !draft || (!retry && !event.target.reportValidity())) return;
    const payload = retry || JSON.parse(JSON.stringify(draft));
    busy = true; controls(); el("schedule-error").textContent = "";
    try {
      const command = await api.submit("/api/v1/settings/schedule", { payload });
      retry = null; pending = command.id;
      el("schedule-command").textContent = "Schedule queued; waiting for the worker to apply it.";
      handleCommand(command);
      document.dispatchEvent(new CustomEvent("schedule-submitted", { detail: command }));
    } catch (error) {
      retry = error instanceof RelayApiError && [400, 403, 404, 409, 422].includes(error.status) ? null : payload;
      el("schedule-error").textContent = `${error.message}${retry ? " Outcome unknown. Retry same save to reuse its request key; keep this page open." : ""}`;
    } finally { busy = false; controls(); }
  });
  function handleCommand(c) {
    // Until the same-key retry identifies our command, history alone cannot
    // associate a schedule command with an unknown submission response.
    if (retry) return;
    if (c.command_type !== "set_schedule" || (pending && c.id !== pending)) return;
    if (!pending && !["queued", "running"].includes(c.status)) return;
    if (["queued", "running"].includes(c.status)) {
      pending = c.id; el("schedule-command").textContent = `Schedule ${c.status}; waiting for the worker.`;
    } else {
      pending = null;
      if (c.status === "completed") { dirty = false; el("schedule-command").textContent = "Schedule saved. Future ticks use these settings."; refresh(); }
      else el("schedule-command").textContent = `Schedule ${c.status}. ${c.error || "Review command activity before saving again."}`;
    }
    controls();
  }
  document.addEventListener("relay-command", event => handleCommand(event.detail));
  document.addEventListener("relay-snapshot", event => receive(event.detail.schedule.daily));
  refresh();
})();
