"use strict";

const csrf = document.querySelector('meta[name="relay-csrf"]').content;
const pollMs = Number(document.querySelector('meta[name="relay-poll-ms"]').content) || 2000;
let activityCursor = 0;
let pollTimer = null;
let currentApproval = null;

const byId = (id) => document.getElementById(id);
const localTime = (value) => value ? new Date(value).toLocaleString() : "—";
const announce = (value) => { byId("announcer").textContent = value; };
const newKey = () => crypto.randomUUID();

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.method && options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-Relay-CSRF"] = csrf;
  }
  const response = await fetch(path, { cache: "no-store", ...options, headers });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.message || payload.detail || `Request failed (${response.status})`);
  return payload;
}

function setConnection(text, kind) {
  const badge = byId("connection");
  badge.textContent = text;
  badge.className = `badge ${kind}`;
}

function renderSnapshot(data) {
  const paused = Boolean(data.relay.paused);
  const worker = data.worker;
  const stale = worker ? worker.stale : true;
  const degraded = worker && worker.phase === "degraded";
  const state = paused ? "Paused" : degraded ? "Degraded" : stale ? "Worker stale" : "Running";
  byId("relay-state").textContent = state;
  byId("worker-note").textContent = paused
    ? (data.relay.pause_reason || "Paused")
    : (worker?.note || "Relay is available.");
  byId("worker-phase").textContent = worker?.phase?.replaceAll("_", " ") || "Unavailable";
  const due = Number(data.relay.initiative_due_at || 0);
  byId("next-initiative").textContent = due ? new Date(due * 1000).toLocaleString() : "Not scheduled";
  byId("history-budget").textContent = `${data.relay.history_requests_24h} / ${data.schedule.history_request_limit_24h}`;
  const cooldownUntil = Number(data.relay.history_cooldown_until || 0);
  const cooldownActive = cooldownUntil * 1000 > Date.now();
  byId("history-api").textContent = cooldownActive
    ? `Cooldown until ${new Date(cooldownUntil * 1000).toLocaleString()}`
    : "Available";
  const last429 = Number(data.relay.history_last_429_at || 0);
  byId("last-429").textContent = last429
    ? new Date(last429 * 1000).toLocaleString()
    : "Never";
  byId("turn-count").textContent = `${data.relay.consecutive_turns} / ${data.schedule.max_consecutive_turns}`;
  byId("pause-relay").disabled = paused;
  byId("resume-relay").disabled = !paused;
  byId("start-conversation").disabled = paused || Boolean(data.active_command);
  byId("trigger-initiative").disabled = paused || Boolean(data.active_command);
  byId("command-status").textContent = data.active_command
    ? `${data.active_command.command_type.replaceAll("_", " ")} — ${data.active_command.status}`
    : "No dashboard command running.";
  setConnection(stale ? "Worker stale" : "Connected", stale || degraded ? "warning" : "good");
}

function appendActivity(items) {
  const list = byId("activity-list");
  for (const item of items) {
    const li = document.createElement("li");
    const time = document.createElement("time");
    time.dateTime = item.created_at;
    time.textContent = new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const text = document.createElement("span");
    text.textContent = item.summary;
    text.className = item.severity;
    li.append(time, text);
    list.prepend(li);
  }
  while (list.children.length > 250) list.lastElementChild.remove();
}

async function refreshActivity() {
  let more = true;
  while (more) {
    const page = await api(`/api/v1/activity?after_id=${activityCursor}&limit=100`);
    appendActivity(page.items);
    activityCursor = page.next_after_id;
    more = page.has_more;
  }
}

async function refreshMessages() {
  const page = await api("/api/v1/messages?limit=50");
  const list = byId("message-list");
  list.replaceChildren();
  for (const message of page.items) {
    const article = document.createElement("article");
    const isCodex = message.known_codex_outbound;
    article.className = `message ${isCodex ? "codex" : message.sender_type === "ai" ? "hannah" : ""} ${message.identity_fallback ? "fallback" : ""}`;
    const header = document.createElement("header");
    const name = document.createElement("strong");
    name.textContent = message.display_name || "Unknown";
    const time = document.createElement("time");
    const epoch = message.timestamp > 10000000000 ? message.timestamp : message.timestamp * 1000;
    time.textContent = new Date(epoch).toLocaleString();
    const body = document.createElement("p");
    body.textContent = message.body;
    header.append(name, time);
    article.append(header, body);
    if (message.identity_fallback) {
      const note = document.createElement("small");
      note.textContent = "Codex prefix used because stored display attribution differed.";
      article.append(note);
    }
    list.append(article);
  }
}

async function refreshApprovals() {
  const data = await api("/api/v1/approvals?status=pending");
  const list = byId("approval-list");
  list.replaceChildren();
  if (!data.items.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No pending proposals.";
    list.append(empty);
    return;
  }
  for (const approval of data.items) {
    const article = document.createElement("article");
    article.className = "approval";
    const header = document.createElement("header");
    const title = document.createElement("strong");
    title.textContent = approval.summary;
    const risk = document.createElement("span");
    risk.textContent = `${approval.risk} risk`;
    const text = document.createElement("p");
    text.textContent = approval.instruction;
    const review = document.createElement("button");
    review.type = "button";
    review.className = "secondary";
    review.textContent = "Review";
    review.addEventListener("click", () => openApproval(approval));
    header.append(title, risk);
    article.append(header, text, review);
    list.append(article);
  }
}

function openApproval(approval) {
  currentApproval = approval;
  byId("approval-summary").textContent = approval.summary;
  byId("approval-instruction").textContent = approval.instruction;
  byId("approval-risk").textContent = approval.risk;
  byId("approval-sandbox").textContent = approval.effective_sandbox;
  byId("approval-post-result").checked = false;
  byId("approval-dialog").showModal();
}

async function submit(path, body = {}) {
  const result = await api(path, { method: "POST", body: JSON.stringify({ idempotency_key: newKey(), ...body }) });
  announce(`Command ${result.command_type || "request"} submitted.`);
  await refreshAll();
  return result;
}

async function refreshAll() {
  try {
    const snapshot = await api("/api/v1/snapshot");
    renderSnapshot(snapshot);
    await Promise.all([refreshActivity(), refreshApprovals()]);
  } catch (error) {
    setConnection("Disconnected", "error");
    byId("worker-note").textContent = error.message;
  }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (document.hidden) return;
  pollTimer = setTimeout(async () => { await refreshAll(); schedulePoll(); }, pollMs);
}

byId("start-conversation").addEventListener("click", () => submit("/api/v1/commands/start-conversation"));
byId("trigger-initiative").addEventListener("click", () => submit("/api/v1/commands/trigger-initiative"));
byId("resume-relay").addEventListener("click", () => submit("/api/v1/commands/resume"));
byId("pause-relay").addEventListener("click", async () => {
  const reason = window.prompt("Pause reason", "paused from dashboard");
  if (reason === null) return;
  await api("/api/v1/control/pause", { method: "POST", body: JSON.stringify({ reason }) });
  announce("Relay pause requested.");
  await refreshAll();
});
byId("refresh-messages").addEventListener("click", refreshMessages);
byId("refresh-log").addEventListener("click", async () => {
  const data = await api("/api/v1/diagnostics/log-tail?lines=200");
  byId("log-tail").textContent = data.lines.join("\n") || "Log is empty.";
});
byId("reject-approval").addEventListener("click", async (event) => {
  event.preventDefault();
  if (!currentApproval) return;
  await submit(`/api/v1/approvals/${currentApproval.id}/reject`);
  byId("approval-dialog").close();
});
byId("execute-approval").addEventListener("click", async (event) => {
  event.preventDefault();
  if (!currentApproval) return;
  await submit(`/api/v1/approvals/${currentApproval.id}/execute`, { post_result: byId("approval-post-result").checked });
  byId("approval-dialog").close();
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { refreshAll(); schedulePoll(); }
});

Promise.all([refreshAll(), refreshMessages()]).finally(schedulePoll);
