"use strict";
(() => {
  const el = id => document.getElementById(id);
  const api = new RelayApiClient({ csrf: document.querySelector('meta[name="relay-csrf"]').content });
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  let loaded = false, dirty = false, busy = false, pending = null, retry = null;
  const when = n => n ? new Date(n * 1000).toLocaleString() : "date unavailable";
  function controls() {
    el("discovery-fields").disabled = !loaded || busy || !!pending || !!retry;
    el("discovery-retry").hidden = !retry;
    el("discovery-retry").disabled = busy;
    for (const field of el("discovery-experiences").querySelectorAll("input, button")) field.disabled = busy || !!pending || !!retry;
  }
  function sourceButton(id) {
    const button = node("button", "View reflection source"); button.type = "button";
    button.addEventListener("click", () => document.dispatchEvent(new CustomEvent("memory-source", { detail: { id, opener: button } })));
    return button;
  }
  async function refresh() {
    try {
      const data = await api.replaceRead("discovery", signal => api.request("/api/v1/discovery", { signal }));
      if (!loaded || (!dirty && !pending && !retry)) {
        document.querySelectorAll('[name="discovery-source"]').forEach(n => { n.checked = data.config.sources.includes(n.value); });
        el("discovery-area").value = data.config.local_area;
        el("discovery-follow").checked = data.config.follow_through;
        el("discovery-limit").value = data.config.research_limit;
      }
      loaded = true;
      const feeds = el("discovery-feeds"); feeds.replaceChildren();
      for (const feed of data.feeds) feeds.append(node("p", `${feed.kind}: ${feed.outcome} · checked ${when(feed.attempted_at)}`));
      for (const source of data.sources || []) {
        if (!source.url) continue;
        const item = node("p", `${source.kind} · ${source.title} · published ${when(source.published_at)} · `);
        try {
          const url = new URL(source.url);
          if (["https:", "http:"].includes(url.protocol) && !url.username && !url.password) {
            const link = node("a", "Source"); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; item.append(link);
          }
        } catch (_) { /* Untrusted source links are inert when invalid. */ }
        feeds.append(item);
      }
      const list = el("discovery-experiences"); list.replaceChildren();
      for (const job of data.experiences) {
        const item = node("details", "");
        item.append(node("summary", `${job.status} · ${job.topic || "Awaiting a choice/public topic"} · ${job.session_id}`));
        item.append(node("p", job.note));
        for (const source of job.sources) {
          item.append(node("p", `${source.title} · retrieved ${when(source.retrieved_at)}`));
          item.append(node("blockquote", source.excerpt));
          try {
            const url = new URL(source.url);
            if (url.protocol === "https:" && !url.username && !url.password) {
              const a = node("a", "Open public source"); a.href = url.href; a.target = "_blank"; a.rel = "noopener noreferrer";
              item.append(a);
            }
          } catch (_) { /* Invalid source links remain inert. */ }
        }
        if (job.reflection_message_id) item.append(sourceButton(job.reflection_message_id));
        if (job.status === "needs_public_topic") {
          const form = node("form", "");
          const label = node("label", "Public search phrase (sent to Wikipedia, not private conversation): ");
          const input = document.createElement("input"); input.required = true; input.maxLength = 180; label.append(input);
          const button = node("button", "Approve public topic"); button.type = "submit";
          form.append(label, button);
          form.addEventListener("submit", event => { event.preventDefault(); if (form.reportValidity()) submit("/api/v1/discovery/public-topic", { session_id: job.session_id, topic: input.value.trim() }); });
          item.append(form);
        }
        if (!["complete", "cancelled"].includes(job.status)) {
          const form = node("form", "");
          const label = node("label", "Inspection / cancellation reason: ");
          const input = document.createElement("input"); input.required = true; input.maxLength = 500; label.append(input);
          const button = node("button", "Close after inspection"); button.type = "submit";
          form.append(node("p", "Closes this exploration locally without retrying, clearing delivery uncertainty, or resuming."), label, button);
          form.addEventListener("submit", event => { event.preventDefault(); if (form.reportValidity()) submit("/api/v1/exploration/close", { session_id: job.session_id, reason: input.value.trim() }); });
          item.append(form);
        }
        list.append(item);
      }
      if (!pending && !retry) el("discovery-status").textContent = dirty ? "Draft retained; save to apply." : "Discovery status loaded. Changes apply to future invitations.";
    } catch (error) { if (error.name !== "AbortError") el("discovery-error").textContent = error.message; }
    controls();
  }
  async function submit(path, payload) {
    if (busy || pending || (retry && (retry.path !== path || JSON.stringify(retry.payload) !== JSON.stringify(payload)))) return;
    busy = true; controls(); el("discovery-error").textContent = "";
    try {
      const command = await api.submit(path, { payload });
      retry = null; pending = command.id;
      handle(command);
      el("discovery-status").textContent = `Command ${command.status}; refresh to check completion.`;
    } catch (error) {
      retry = error instanceof RelayApiError && [400, 403, 404, 409, 422].includes(error.status) ? null : { path, payload };
      el("discovery-error").textContent = `${error.message}${retry ? " Outcome unknown. Keep this page open and retry the same request." : ""}`;
    } finally { busy = false; controls(); }
  }
  function handle(command) {
    if (retry) return;
    if (!pending && ["set_discovery", "approve_public_topic"].includes(command.command_type) && ["queued", "running"].includes(command.status)) pending = command.id;
    if (!pending || command.id !== pending) return;
    el("discovery-status").textContent = `Command ${command.status}. ${command.error || ""}`;
    if (!["queued", "running"].includes(command.status)) {
      pending = null;
      if (command.status === "completed") { dirty = false; refresh(); }
    }
    controls();
  }
  el("discovery-form").addEventListener("input", () => { dirty = true; });
  el("discovery-form").addEventListener("submit", event => {
    event.preventDefault();
    if (!event.target.reportValidity()) return;
    submit("/api/v1/settings/discovery", {
      sources: [...document.querySelectorAll('[name="discovery-source"]:checked')].map(n => n.value),
      local_area: el("discovery-area").value, follow_through: el("discovery-follow").checked,
      research_limit: Number(el("discovery-limit").value),
    });
  });
  el("discovery-retry").addEventListener("click", () => { if (retry) submit(retry.path, retry.payload); });
  el("discovery-panel").addEventListener("toggle", () => { if (el("discovery-panel").open) refresh(); });
  el("discovery-refresh").addEventListener("click", async () => {
    if (pending) { try { handle(await api.request(`/api/v1/commands/${pending}`)); } catch (e) { el("discovery-error").textContent = e.message; } }
    refresh();
  });
  document.addEventListener("relay-command", event => handle(event.detail));
})();
