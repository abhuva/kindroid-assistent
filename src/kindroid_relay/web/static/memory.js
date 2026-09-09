"use strict";
(() => {
  const node = id => document.getElementById(id);
  const client = new RelayApiClient({ csrf: document.querySelector('meta[name="relay-csrf"]').content });
  let source = null;
  let observation = null;
  let busy = false;
  const dialog = node("memory-dialog");
  const error = message => { node("memory-error").textContent = message; };
  async function queue(operation, payload) {
    if (busy) return;
    busy = true;
    try {
      const command = await client.submit(`/api/v1/memory/commands/${operation}`, { payload });
      node("memory-status").textContent = `Queued ${operation}: ${command.id}. Worker completion appears in Activity; search/refresh after completion.`;
      dialog.close();
    } catch (failure) {
      error(failure.message);
      node("memory-status").textContent = failure.message;
    } finally { busy = false; }
  }
  async function search(event) {
    event?.preventDefault();
    try {
      const result = await client.replaceRead("memory", signal => client.request(`/api/v1/memory?q=${encodeURIComponent(node("memory-query").value)}`, { signal }));
      node("memory-status").textContent = `${JSON.stringify(result.progress)} · ${result.mode} · ${result.semantic_status}${result.truncated ? " · Results truncated" : ""}`;
      node("memory-extract").disabled = !result.progress.enabled;
      node("memory-reindex").disabled = !result.progress.enabled;
      node("memory-results").replaceChildren();
      for (const item of result.items) {
        const article = document.createElement("article");
        article.className = "message";
        const title = document.createElement("strong");
        title.textContent = `${item.kind} · ${item.status} · ${item.origin}`;
        const quote = document.createElement("p");
        quote.textContent = item.evidence.map(e => `${e.display_name} (${e.attribution}): ${e.quote}`).join("\n");
        const why = document.createElement("small");
        why.textContent = item.retrieval_reason.join("; ") + (item.uncertainty ? ` · Uncertainty: ${item.uncertainty}` : "");
        const review = document.createElement("button");
        review.type = "button";
        review.className = "text-button";
        review.textContent = "Sources / review / correct";
        review.addEventListener("click", () => inspect(item.id));
        article.append(title, quote, why, review);
        node("memory-results").append(article);
      }
    } catch (failure) { if (failure.name !== "AbortError") node("memory-status").textContent = failure.message; }
  }
  function prepare(review) {
    node("memory-edit-form").reset();
    error("");
    node("memory-detail").textContent = "";
    node("memory-source-links").replaceChildren();
    node("memory-match-review").replaceChildren();
    node("memory-dialog-title").textContent = review ? "Review agent observation" : "Save source observation";
    for (const id of ["memory-quote", "memory-evidence-for", "memory-kind", "memory-entity", "memory-entity-kind", "memory-aliases"]) node(id).disabled = review;
    for (const id of ["memory-record-status", "memory-link-id", "memory-link-relation", "memory-link", "memory-unlink"]) node(id).disabled = !review;
    for (const [ids, hidden] of [
      [["memory-evidence-for", "memory-entity", "memory-entity-kind", "memory-aliases"], review],
      [["memory-record-status", "memory-link-id", "memory-link-relation", "memory-link", "memory-unlink"], !review],
    ]) {
      for (const id of ids) {
        node(id).hidden = hidden;
        const label = document.querySelector(`label[for="${id}"]`);
        if (label) label.hidden = hidden;
      }
    }
    node("memory-submit").textContent = review ? "Queue correction" : "Queue save";
    node("memory-reason").required = review;
  }
  async function inspect(id) {
    try {
      const item = await client.request(`/api/v1/memory/${encodeURIComponent(id)}`);
      prepare(true);
      source = null;
      observation = item;
      node("memory-source-note").textContent = "Quotations are immutable. Corrections and link reviews require a reason and retain an audit trail.";
      node("memory-quote").value = item.evidence.map(e => e.quote).join("\n");
      node("memory-interpretation").value = item.interpretation;
      node("memory-temporal").value = item.temporal_scope;
      node("memory-record-status").value = item.status;
      node("memory-kind").value = item.kind;
      node("memory-detail").textContent = JSON.stringify(item, null, 2);
      for (const evidence of item.evidence) {
        const link = document.createElement("a");
        link.href = `#message/${encodeURIComponent(evidence.message_id)}`;
        link.textContent = `message: ${evidence.message_id}`;
        link.addEventListener("click", event => {
          event.preventDefault();
          document.dispatchEvent(new CustomEvent("memory-source", { detail: { id: evidence.message_id, opener: link } }));
        });
        node("memory-source-links").append(link);
      }
      for (const entity of item.entities) {
        if (entity.identity_review_needed === false) continue;
        for (const id of entity.possible_match_ids || []) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "text-button";
          button.textContent = `Review possible match for ${entity.label}: ${id}`;
          button.addEventListener("click", () => {
            node("memory-link-id").value = id;
            node("memory-link-relation").value = entity.relation;
            error("Verify that these refer to the same entity, provide a reason, then choose Link entity. No match has been applied.");
            node("memory-reason").focus();
          });
          node("memory-match-review").append(button);
        }
      }
      dialog.showModal();
    } catch (failure) { node("memory-status").textContent = failure.message; }
  }
  document.addEventListener("memory-save", event => {
    prepare(false);
    source = event.detail;
    observation = null;
    node("memory-source-note").textContent = `Marc's selection from ${source.display_name || "unresolved speaker"}; source ${source.id}. No identity adoption or permissions.`;
    node("memory-quote").value = source.body.slice(0, 2000);
    dialog.showModal();
  });
  node("memory-edit-form").addEventListener("submit", event => {
    event.preventDefault();
    const interpretation = node("memory-interpretation").value;
    const temporal_scope = node("memory-temporal").value;
    if (observation) {
      return queue("revise", { statement_id: observation.id, status: node("memory-record-status").value,
        interpretation, temporal_scope, reason: node("memory-reason").value });
    }
    const quote = node("memory-quote").value;
    const start = source.body.indexOf(quote);
    if (!quote || start < 0) return error("Choose an exact passage from the source, without rewriting it.");
    const statement_id = node("memory-evidence-for").value.trim();
    if (statement_id) {
      const reason = node("memory-reason").value;
      if (!reason.trim()) return error("A reason is required for additional evidence.");
      return queue("evidence", { statement_id, message_id: source.id, quote, start, reason });
    }
    const label = node("memory-entity").value.trim();
    queue("save", { message_id: source.id, quote, start, kind: node("memory-kind").value, interpretation, temporal_scope,
      entities: label ? [{ kind: node("memory-entity-kind").value, label, aliases: node("memory-aliases").value.split(",").map(x => x.trim()).filter(Boolean) }] : [] });
  });
  for (const [id, remove] of [["memory-link", false], ["memory-unlink", true]]) {
    node(id).addEventListener("click", () => {
      if (!observation || !node("memory-reason").value.trim()) return error("A reason is required for link review.");
      queue("link", { statement_id: observation.id, entity_id: node("memory-link-id").value.trim(),
        relation: node("memory-link-relation").value, reason: node("memory-reason").value, remove });
    });
  }
  node("memory-cancel").addEventListener("click", () => dialog.close());
  node("memory-search-form").addEventListener("submit", search);
  node("memory-panel").addEventListener("toggle", () => { if (node("memory-panel").open) search(); });
  node("memory-extract").addEventListener("click", () => queue("extract", { limit: 3 }));
  node("memory-reindex").addEventListener("click", () => queue("reindex", { limit: 10 }));
})();
