"use strict";
(() => {
  const el = id => document.getElementById(id);
  const api = new RelayApiClient({ csrf: document.querySelector('meta[name="relay-csrf"]').content });
  const node = (tag, text) => { const n = document.createElement(tag); n.textContent = text; return n; };
  let cursor = "", current = null, versionCursor = 0, trailCursor = 0, opener = null, busy = false;
  const button = (text, action) => {
    const b = node("button", text); b.type = "button"; b.className = "text-button";
    b.addEventListener("click", () => action(b)); return b;
  };
  const date = seconds => new Date(seconds * 1000).toLocaleString();
  function evidence(parent, observation) {
    if (!observation) return;
    parent.append(node("p", `Observation: ${observation.origin} / ${observation.status}. ${observation.uncertainty || ""}`));
    if (observation.interpretation) parent.append(node("p", `Separate interpretation: ${observation.interpretation}`));
    for (const source of observation.evidence || []) {
      parent.append(node("blockquote", source.quote));
      parent.append(node("p", `Stored speaker: ${source.display_name}; ${source.attribution}. ${source.source_matches === false ? "Source text changed; review required." : ""}${source.attribution_changed ? " Speaker attribution changed; review required." : ""}`));
      parent.append(button(`Source: ${source.message_id}`, b => document.dispatchEvent(new CustomEvent("memory-source", { detail: { id: source.message_id, opener: b } }))));
    }
  }
  async function refresh(more = false) {
    try {
      const query = new URLSearchParams({ layer: el("garden-layer").value, status: el("garden-filter").value,
        after: more ? cursor : "", limit: "30" });
      const data = await api.replaceRead("garden-list", signal => api.request(`/api/v1/garden?${query}`, { signal }));
      if (!data) return;
      if (!more) el("garden-items").replaceChildren();
      for (const item of data.items) {
        const row = node("article", ""); row.className = "garden-item";
        row.append(node("h3", item.content));
        row.append(node("p", `${item.layer} / ${item.status} / ${item.ownership}-owned. Origin: ${item.origin_actor} (${item.origin}).`));
        row.append(node("p", `Last recorded session interaction: ${date(item.last_interaction_at)}. Version ${item.current_version}.`));
        if (item.annotation) row.append(node("p", `Marc's administrative annotation: ${item.annotation}`));
        row.append(button("History / sources", b => openItem(item.id, b)));
        el("garden-items").append(row);
      }
      cursor = data.next_after;
      el("garden-more").hidden = !data.has_more;
      el("garden-status").textContent = `Roots ${data.counts.roots} · Growing ${data.counts.growing} · Passing ${data.counts.passing} (including archived). ${data.items.length || more ? "" : "No items match this filter."}`;
      el("garden-error").textContent = "";
    } catch (error) { if (error.name !== "AbortError") el("garden-error").textContent = error.message; }
  }
  function versionRows(data, append = false) {
    if (!append) el("garden-versions").replaceChildren();
    for (const version of data.versions) {
      const row = node("li", "");
      row.append(node("p", `v${version.version} · ${date(version.created_at)} · ${version.actor} / ${version.origin} · ${version.status}`));
      row.append(node("p", version.content), node("p", `Reason: ${version.rationale}`));
      if (version.annotation) row.append(node("p", `Administrative annotation: ${version.annotation}`));
      evidence(row, version.observation); el("garden-versions").append(row);
    }
    versionCursor = data.next_after;
    el("garden-versions-more").hidden = !data.has_more;
  }
  async function loadTrail(more = false) {
    if (!current) return;
    const id = current.id;
    try {
      const data = await api.replaceRead("garden-trail", signal => api.request(`/api/v1/garden/${encodeURIComponent(id)}/trail?after=${more ? trailCursor : 0}`, { signal }));
      if (!data || current?.id !== id) return;
      if (!more) el("garden-trail").replaceChildren();
      for (const event of data.items) {
        const row = node("li", "");
        row.append(node("p", `${date(event.created_at)} · ${event.actor} / ${event.origin} · ${event.kind}`));
        row.append(node("pre", JSON.stringify(event.details, null, 2)));
        evidence(row, event.observation); el("garden-trail").append(row);
      }
      trailCursor = data.next_after; el("garden-trail-more").hidden = !data.has_more;
    } catch (error) { if (error.name !== "AbortError") el("garden-detail-error").textContent = error.message; }
  }
  async function openItem(id, b) {
    try {
      const data = await api.replaceRead("garden-detail", signal => api.request(`/api/v1/garden/${encodeURIComponent(id)}`, { signal }));
      if (!data) return;
      current = data; opener = b;
      el("garden-detail-title").textContent = data.content;
      el("garden-detail-meta").textContent = `${data.layer} / ${data.status} / ${data.ownership}-owned. No promotion or execution controls.`;
      if (data.source) el("garden-detail-meta").append(node("p", `Source: ${data.source.provider} / ${data.source.locator}. ${data.source.selection_kind}.`));
      el("garden-annotation").value = data.annotation;
      el("garden-correction-reason").value = "";
      el("garden-detail-error").textContent = "";
      el("garden-save-status").textContent = "";
      el("garden-annotate").disabled = false;
      el("garden-annotation").disabled = false;
      el("garden-correction-reason").disabled = false;
      versionRows(data);
      el("garden-reactions").replaceChildren();
      for (const response of data.responses) {
        const row = node("li", `Reported ${response.reaction} · ${response.status}. Interpretation, not permanent preference.`);
        evidence(row, response.observation); el("garden-reactions").append(row);
      }
      el("garden-relations").replaceChildren();
      for (const relation of data.relationships) {
        const target = relation.source_id === id ? relation.target_id : relation.source_id;
        el("garden-relations").append(node("p", `${relation.actor} recorded ${relation.relation}: ${target}. ${relation.rationale}`));
      }
      if (!data.relationships.length) el("garden-relations").append(node("p", "No recorded connections. Contradictions are not automatically resolved."));
      if (data.relationships_truncated) el("garden-relations").append(node("p", "First 100 connections shown."));
      el("garden-projects").replaceChildren();
      for (const project of data.projects) {
        const row = node("details", "");
        row.append(node("summary", `${project.title} / ${project.status} — planning record, not authority`));
        row.append(node("p", `Outcome: ${project.outcome}. Next step: ${project.next_step}. Stop: ${project.stopping_condition}.`));
        row.append(node("pre", JSON.stringify({ budgets: project.budgets, history: project.history }, null, 2)));
        if (project.history_truncated) row.append(node("p", "Latest 100 attention events shown."));
        el("garden-projects").append(row);
      }
      if (!data.projects.length) el("garden-projects").append(node("p", "No projects. A Passing reaction does not start work."));
      if (data.projects_truncated) el("garden-projects").append(node("p", "First 20 projects shown."));
      el("garden-trail").replaceChildren();
      el("garden-trail-more").hidden = true;
      el("garden-dialog").showModal();
      el("garden-close").focus();
      await loadTrail();
    } catch (error) { if (error.name !== "AbortError") el("garden-error").textContent = error.message; }
  }
  el("garden-refresh").addEventListener("click", () => refresh());
  el("garden-more").addEventListener("click", () => refresh(true));
  for (const id of ["garden-layer", "garden-filter"]) el(id).addEventListener("change", () => refresh());
  el("garden-panel").addEventListener("toggle", () => { if (el("garden-panel").open) refresh(); });
  el("garden-versions-more").addEventListener("click", async () => {
    if (!current) return;
    const id = current.id;
    try {
      const data = await api.replaceRead("garden-detail", signal => api.request(`/api/v1/garden/${encodeURIComponent(id)}?after=${versionCursor}`, { signal }));
      if (data && current?.id === id) versionRows(data, true);
    } catch (error) { if (error.name !== "AbortError") el("garden-detail-error").textContent = error.message; }
  });
  el("garden-trail-more").addEventListener("click", () => loadTrail(true));
  el("garden-close").addEventListener("click", () => { if (!busy) el("garden-dialog").close(); });
  el("garden-dialog").addEventListener("cancel", e => { if (busy) e.preventDefault(); });
  el("garden-dialog").addEventListener("close", () => { current = null; opener?.focus(); });
  el("garden-correction-form").addEventListener("submit", async e => {
    e.preventDefault();
    if (!current || busy) return;
    const reason = el("garden-correction-reason").value.trim();
    if (!reason) { el("garden-detail-error").textContent = "A correction reason is required."; return; }
    busy = true;
    el("garden-annotate").disabled = true;
    el("garden-annotation").disabled = true;
    el("garden-correction-reason").disabled = true;
    try {
      const command = await api.submit("/api/v1/garden/corrections", { payload: { item_id: current.id,
        expected_version: current.current_version, annotation: el("garden-annotation").value, reason } });
      el("garden-save-status").textContent = `Queued ${command.id}. Refresh the garden after command completion; no identity or source text was changed.`;
      el("garden-detail-error").textContent = "";
    } catch (error) {
      el("garden-detail-error").textContent = `${error.message} Retry preserves the same request. Close and inspect command history before changing it.`;
      el("garden-annotate").disabled = false;
    } finally { busy = false; }
  });
})();
