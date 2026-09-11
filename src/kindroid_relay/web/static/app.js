"use strict";

const csrf = document.querySelector('meta[name="relay-csrf"]').content;
const pollMs = Number(document.querySelector('meta[name="relay-poll-ms"]').content) || 2000;
let activityCursor = 0;
let pollTimer = null;
let currentApproval = null;
let trackedCommand = null;
let latestSnapshot = null;
let refreshPromise = null;
let submitting = false;
let retryAction = null;
let confirmation = null;
let logEtag = null;
let messageCursor = null;
let approvalSignature = "";
let deliverySignature = "";
let disconnected = true;
let initiativeLimitDirty = false;
const commands = new Map();
const activities = new Map();
const messages = new Map();
let pollFailures = 0;
const maxPollBackoffMs = 30000;
const apiClient = new RelayApiClient({ csrf });

const byId = (id) => document.getElementById(id);
const localTime = (value) => value ? new Date(value).toLocaleString() : "—";
const announce = (value) => { byId("announcer").textContent = value; };

const viewTabs = [...document.querySelectorAll('.tabbar [role="tab"]')];
function selectView(id, focus = false) {
  for (const tab of viewTabs) {
    const selected = tab.id === id;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    byId(tab.getAttribute("aria-controls")).hidden = !selected;
    if (selected && focus) tab.focus();
  }
}
for (const [index, tab] of viewTabs.entries()) {
  tab.addEventListener("click", () => selectView(tab.id));
  tab.addEventListener("keydown", event => {
    const next = { ArrowRight: (index + 1) % viewTabs.length,
      ArrowLeft: (index + viewTabs.length - 1) % viewTabs.length,
      Home: 0, End: viewTabs.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    selectView(viewTabs[next].id, true);
  });
}
document.querySelector(".skip-link").addEventListener("click", event => {
  event.preventDefault();
  selectView("tab-info");
  byId("controls-heading").focus();
});

function reportError(error) {
  if (error.name === "AbortError") return;
  byId("action-error").textContent = error.message || "Request failed.";
}

function element(tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  node.className = className;
  return node;
}

function relatedLink(kind, id) {
  const link = element("a", `${kind}: ${id}`);
  link.href = `#${kind}/${encodeURIComponent(id)}`;
  link.addEventListener("click", (event) => {
    event.preventDefault();
    openRelated(kind, id, link).catch(reportError);
  });
  return link;
}

document.addEventListener("memory-source", event => {
  openRelated("message", event.detail.id, event.detail.opener).catch(reportError);
});

function filterRelated(id) {
  selectView("tab-info");
  byId("activity-related").value = id;
  byId("activity-severity").value = "";
  byId("activity-kind").value = "";
  renderActivity();
  byId("activity-related").focus();
}

function activityLink(id) {
  const link = element("a", "Related activity");
  link.href = "#activity-heading";
  link.addEventListener("click", (event) => {
    event.preventDefault();
    if (byId("detail-dialog").open) byId("detail-dialog").close();
    if (byId("garden-dialog").open) byId("garden-dialog").close();
    filterRelated(id);
  });
  return link;
}

async function openRelated(kind, id, opener) {
  const routes = { message: "messages", command: "commands", approval: "approvals", outbox: "deliveries" };
  const record = await api(`/api/v1/${routes[kind]}/${encodeURIComponent(id)}`);
  byId("detail-title").textContent = `${kind}: ${id}`;
  byId("detail-body").textContent = JSON.stringify(record, null, 2);
  const links = byId("detail-links");
  links.replaceChildren(activityLink(id));
  if (record.source_message_id) links.append(relatedLink("message", record.source_message_id));
  showDialog(byId("detail-dialog"), opener);
}

function showDialog(dialog, opener = document.activeElement) {
  dialog.returnFocus = opener;
  if (!dialog.open) dialog.showModal();
  dialog.querySelector("[autofocus]")?.focus();
}

function updateControls() {
  const active = latestSnapshot?.active_command;
  const pending = submitting || Boolean(retryAction) || Boolean(active && RelayUiModel.commandStatusView(commands.get(active.id) || active).pending)
    || [...commands.values()].some((command) => RelayUiModel.commandStatusView(command).pending);
  const unavailable = disconnected || !latestSnapshot || ["stopped", "stale"].includes(RelayUiModel.relayStatusView(latestSnapshot).key);
  const paused = Boolean(latestSnapshot?.relay.paused);
  byId("pause-relay").disabled = paused || submitting;
  byId("resume-relay").disabled = !paused || pending || unavailable;
  byId("start-conversation").disabled = paused || pending || unavailable;
  byId("trigger-initiative").disabled = paused || pending || unavailable;
  byId("execute-approval").disabled = pending || unavailable || currentApproval?.status !== "pending";
  byId("reject-approval").disabled = pending || unavailable || currentApproval?.status !== "pending";
  byId("confirm-action").disabled = pending || unavailable;
  byId("retry-action").disabled = submitting;
  byId("save-initiative-limit").disabled = pending || unavailable;
  byId("sync-history").disabled = pending || unavailable;
}

function renderCurrentActivity() {
  if (!latestSnapshot) return;
  const worker = latestSnapshot.worker;
  const command = [...commands.values()].find((item) => item.status === "running")
    || [...commands.values()].find((item) => item.status === "queued");
  const phase = worker?.phase || "unavailable";
  const labels = { waiting_for_kindroid: "Waiting for Kindroid", running_codex: "Waiting for Codex", sending: "Sending to Kindroid", idle: "Waiting for a check-in or manual start", polling: "Checking conversation", degraded: "Degraded operation", paused: "Paused", stopping: "Worker stopped", executing_approval: "Executing approved action", starting: "Worker starting" };
  byId("current-activity").textContent = `${labels[phase] || "Worker unavailable"}${command ? ` · ${command.command_type.replaceAll("_", " ")} (${command.status})` : ""}`;
  const start = command?.started_at || command?.requested_at || worker?.phase_started_at;
  const end = Date.parse(latestSnapshot.server_time);
  const seconds = Math.max(0, Math.floor((end - Date.parse(start)) / 1000));
  byId("current-elapsed").textContent = Number.isFinite(seconds)
    ? `${command?.status === "queued" ? "Queued" : "Elapsed"}: ${Math.floor(seconds / 60)}m ${seconds % 60}s${worker?.stale ? " · Last reported state; heartbeat is stale" : ""}` : "";
}

function renderEpochTime(element, seconds, fallback) {
  if (!seconds) {
    element.textContent = fallback;
    element.dateTime = "";
    element.title = fallback;
    return;
  }
  const date = new Date(seconds * 1000);
  element.textContent = date.toLocaleString();
  element.dateTime = date.toISOString();
  element.title = date.toISOString();
}

async function api(path, options = {}) {
  return apiClient.request(path, options);
}

async function replaceRead(name, operation) {
  return apiClient.replaceRead(name, operation);
}

function abortReads() {
  apiClient.abortReads();
}

function setConnection(text, kind) {
  const badge = byId("connection");
  badge.textContent = text;
  badge.className = `badge ${kind}`;
}

const commandStatusTargets = {
  set_initiative_limit: "initiative-limit-status",
  start_conversation: "start-conversation-status",
  trigger_initiative: "trigger-initiative-status",
  resume: "resume-status",
  sync_history: "history-sync-status",
};

function renderCommand(command, { announceTerminal = false } = {}) {
  if (!command) return;
  const previous = commands.get(command.id);
  if (previous && JSON.stringify(previous) === JSON.stringify(command)) return;
  if (previous && RelayUiModel.commandStatusView(previous).terminal && !RelayUiModel.commandStatusView(command).terminal) return;
  commands.set(command.id, command);
  document.dispatchEvent(new CustomEvent("relay-command", { detail: command }));
  trackedCommand = command;
  const view = RelayUiModel.commandStatusView(command);
  const globalStatus = byId("command-status");
  globalStatus.textContent = view.text;
  globalStatus.className = `command-summary ${view.tone}`;
  const targetId = command && commandStatusTargets[command.command_type];
  if (targetId) {
    const target = byId(targetId);
    target.textContent = view.detail || command.status;
    target.className = `control-status ${view.tone}`;
  }
  if (["sync_history", "recover_shared"].includes(command.command_type) && view.terminal
      && (!previous || !RelayUiModel.commandStatusView(previous).terminal)) {
    refreshMessages().catch(error => {
      if (error.name !== "AbortError") announce(`History sync finished, but loading saved messages failed: ${error.message}`);
    });
  }
  if (
    announceTerminal
    && view.terminal
    && previous && previous.status !== command.status
  ) {
    announce(view.tone === "success" ? `${view.text}.` : `Command ${view.text}.`);
  }
  const list = byId("command-list");
  list.replaceChildren();
  for (const item of [...commands.values()].slice(-50).reverse()) {
    const row = element("li", "");
    row.append(relatedLink("command", item.id), element("span", ` ${RelayUiModel.commandStatusView(item).text}`));
    if (item.approval_id) row.append(relatedLink("approval", item.approval_id));
    if (item.error) row.append(element("p", item.error, "error"));
    list.append(row);
  }
  if (commands.size > 50) {
    for (const [id, item] of commands) {
      if (commands.size <= 50) break;
      if (RelayUiModel.commandStatusView(item).terminal) commands.delete(id);
    }
  }
  updateControls();
  renderCurrentActivity();
}

async function refreshTrackedCommand() {
  const page = await replaceRead("commands", (signal) => api("/api/v1/commands", { signal }));
  for (const command of [...page.items].reverse()) renderCommand(command, { announceTerminal: true });
}

function renderSnapshot(data) {
  latestSnapshot = data;
  document.dispatchEvent(new CustomEvent("relay-snapshot", { detail: data }));
  const paused = Boolean(data.relay.paused);
  const worker = data.worker;
  const statusView = RelayUiModel.relayStatusView(data);
  if (data.active_command) renderCommand(data.active_command, { announceTerminal: true });
  const stateElement = byId("relay-state");
  stateElement.textContent = statusView.label;
  stateElement.dataset.state = statusView.key;
  byId("worker-note").textContent = paused
    ? (data.relay.pause_reason || "Paused")
    : (worker?.note || (
      statusView.key === "stopped"
        ? "The relay worker is not available."
        : statusView.key === "stale"
          ? "The worker heartbeat is stale."
          : statusView.key === "degraded"
            ? "The relay worker reported degraded operation."
            : "Relay is available."
    ));
  byId("worker-phase").textContent = worker?.phase?.replaceAll("_", " ") || "Unavailable";
  const heartbeat = byId("worker-heartbeat");
  heartbeat.textContent = localTime(worker?.heartbeat_at);
  heartbeat.dateTime = worker?.heartbeat_at || "";
  heartbeat.title = worker?.heartbeat_at || "No worker heartbeat is available";
  const due = Number(data.relay.initiative_due_at || 0);
  renderEpochTime(byId("next-initiative"), due, "Not scheduled");
  const lastInitiativeAt = Number(data.relay.last_initiative_check_at || 0);
  const lastInitiative = byId("last-initiative");
  lastInitiative.textContent = lastInitiativeAt
    ? `${data.relay.last_initiative_outcome.replaceAll("_", " ")} — ${new Date(lastInitiativeAt * 1000).toLocaleString()}`
    : "No checks yet";
  lastInitiative.title = lastInitiativeAt
    ? new Date(lastInitiativeAt * 1000).toISOString()
    : "No initiative check has been recorded";
  const scheduleView = RelayUiModel.scheduleView(data);
  const budget = data.schedule.initiative_budget;
  if (budget) {
    byId("initiative-budget").textContent = `${budget.used} / ${budget.limit} check-in attempts used in the last 24 hours`;
    byId("initiative-availability").textContent = budget.available_at
      ? `Budget slot available ${new Date(budget.available_at * 1000).toLocaleString()}. Other check-in safeguards still apply.`
      : "Budget available now; other check-in safeguards still apply.";
    byId("initiative-availability").title = budget.source === "dashboard" ? "Using saved dashboard limit" : "Using configuration/default limit";
    if (Number(byId("initiative-limit").value) === budget.limit && document.activeElement !== byId("initiative-limit")) {
      initiativeLimitDirty = false;
    }
    if (!initiativeLimitDirty && document.activeElement !== byId("initiative-limit")) {
      byId("initiative-limit").value = String(budget.limit);
    }
  }
  byId("initiative-schedule").textContent = scheduleView.basis;
  const companionQuiet = (data.schedule.companion_quiet_hours || []).filter(person => person.enabled);
  byId("quiet-hours").textContent = companionQuiet.length
    ? companionQuiet.map(person => `${person.name}: ${person.start}–${person.end}`).join(' · ')
    : scheduleView.quietHours;
  const quota = RelayUiModel.quotaView(
    data.relay.history_requests_24h,
    data.schedule.history_request_limit_24h,
  );
  byId("history-budget").textContent = quota.text;
  const quotaNote = byId("history-budget-note");
  quotaNote.textContent = quota.note;
  if (data.relay.history_window_reset_at) {
    quotaNote.textContent += ` Local counter resets at ${new Date(data.relay.history_window_reset_at * 1000).toLocaleString()} (00:00 UTC).`;
  }
  quotaNote.className = `quota-note ${quota.warning ? "warning" : ""}`;
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
  updateControls();
  renderCurrentActivity();
  setConnection(statusView.connection, statusView.tone);
}

function appendActivity(items) {
  if (!items.length) return;
  for (const item of items) activities.set(item.id, item);
  const sorted = [...activities.keys()].sort((a, b) => b - a);
  for (const id of sorted.slice(250)) activities.delete(id);
  const kinds = [...new Set([...activities.values()].map((item) => item.kind))].sort();
  const select = byId("activity-kind");
  const selected = select.value;
  select.replaceChildren(new Option("All kinds", ""), ...kinds.map((kind) => new Option(kind, kind)));
  select.value = kinds.includes(selected) ? selected : "";
  renderActivity();
}

document.querySelectorAll('[data-room-url]').forEach(button => {
  button.addEventListener('click', () => {
    if (button.getAttribute('aria-pressed') === 'true') return;
    window.location.assign(button.dataset.roomUrl);
  });
});

const expandedActivityGroups = new Set();
byId('activity-view').addEventListener('change', renderActivity);

function renderActivity() {
  const list = byId("activity-list");
  list.replaceChildren();
  const severity = byId("activity-severity").value;
  const kind = byId("activity-kind").value;
  const related = byId("activity-related").value.trim();
  const items = [...activities.values()].sort((a, b) => a.id - b.id);
  const groups = new Map();
  let legacy = null;
  for (const item of items) {
    if (byId('activity-view').value === 'grouped' && !severity && !kind && !related
        && ['worker_phase', 'message_processed', 'command_queued'].includes(item.kind)
        && !item.details.session_id) continue;
    if (['command_started', 'relay_cycle_started', 'initiative_due'].includes(item.kind)) legacy = item.details.command_id || `event-${item.id}`;
    const groupKey = item.details.session_id || item.details.command_id || legacy || `event-${item.id}`;
    const endLegacy = ['command_completed', 'command_failed', 'command_interrupted', 'relay_cycle_completed', 'relay_cycle_failed'].includes(item.kind);
    if (endLegacy) legacy = null;
    if (severity && item.severity !== severity || kind && item.kind !== kind) continue;
    if (related && item.correlation_id !== related && !Object.values(item.details).includes(related)) continue;
    const li = document.createElement("li");
    const time = document.createElement("time");
    time.dateTime = item.created_at;
    time.title = localTime(item.created_at);
    time.textContent = new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const text = document.createElement("span");
    text.textContent = `${item.severity} · ${item.kind}: ${item.summary}`;
    text.className = item.severity;
    li.append(time, text);
    const refs = element("div", "", "related-links");
    const types = { message_id: "message", source_message_id: "message", command_id: "command", approval_id: "approval", outbox_id: "outbox" };
    for (const [key, type] of Object.entries(types)) {
      if (item.details[key]) refs.append(relatedLink(type, item.details[key]));
    }
    if (item.correlation_id && !Object.values(item.details).includes(item.correlation_id)) refs.append(activityLink(item.correlation_id));
    li.append(refs);
    if (byId('activity-view').value === 'events') { list.prepend(li); continue; }
    if (!groups.has(groupKey)) groups.set(groupKey, []);
    groups.get(groupKey).push({ item, li });
  }
  const orderedGroups = [...groups.entries()].sort((a, b) => b[1].at(-1).item.id - a[1].at(-1).item.id);
  for (const [key, rows] of orderedGroups) {
    const first = rows[0].item, last = rows.at(-1).item;
    const start = rows.find(row => /session_started|command_started|relay_cycle_started|initiative_due/.test(row.item.kind))?.item;
    const terminal = rows.findLast(row => /session_ended|command_completed|command_failed|command_interrupted|relay_cycle_completed|relay_cycle_failed/.test(row.item.kind))?.item;
    const warning = rows.findLast(row => ['warning', 'error'].includes(row.item.severity))?.item;
    const wrapper = document.createElement('li');
    const details = document.createElement('details');
    details.className = 'activity-group'; details.dataset.groupId = key;
    details.open = expandedActivityGroups.has(key) || !!related;
    details.addEventListener('toggle', () => {
      if (!details.isConnected) return;
      if (details.open) expandedActivityGroups.add(key); else expandedActivityGroups.delete(key);
    });
    const summary = document.createElement('summary');
    summary.append(`${localTime((start || first).created_at)} · ${(start || first).summary}`);
    const result = terminal || warning;
    const outcome = element('span', result ? `${result.summary}${result.details.outcome ? ' · ' + result.details.outcome : ''}`
      : start ? 'No outcome in retained events · latest: ' + last.summary : last.summary, 'group-outcome');
    if (warning) {
      outcome.classList.add(warning.severity);
      if (result !== warning) outcome.append(' · Attention: ' + warning.summary);
    }
    summary.append(outcome, element('small', ` · ${rows.length} events`));
    const children = document.createElement('ol');
    for (const row of [...rows].reverse()) children.append(row.li);
    details.append(summary, children); wrapper.append(details); list.append(wrapper);
  }
  for (const key of expandedActivityGroups) if (!groups.has(key)) expandedActivityGroups.delete(key);
  byId("activity-empty").hidden = list.children.length > 0;
}

async function refreshActivity() {
  return replaceRead("activity", async (signal) => {
    let more = true;
    while (more) {
      const page = await api(
        `/api/v1/activity?after_id=${activityCursor}&limit=100`,
        { signal },
      );
      if (signal.aborted) return;
      appendActivity(page.items);
      activityCursor = page.next_after_id;
      more = page.has_more;
    }
  });
}

async function refreshMessages(older = false) {
  return replaceRead("messages", async (signal) => {
    const cursor = older ? messageCursor : null;
    if (older && !cursor) return;
    const query = cursor ? `&before=${cursor.timestamp}&before_id=${encodeURIComponent(cursor.id)}` : "";
    const page = await api(`/api/v1/messages?limit=50${query}`, { signal });
    if (signal.aborted) return;
    if (!older) messages.clear();
    for (const message of page.items) messages.set(message.id, message);
    messageCursor = page.has_more ? { timestamp: page.next_before, id: page.next_before_id } : null;
    const list = byId("message-list");
    list.replaceChildren();
    for (const message of messages.values()) {
      const article = document.createElement("article");
    const isCodex = message.known_codex_outbound;
      article.className = `message ${message.participant || "unknown"} ${message.identity_fallback ? "fallback" : ""}`;
      const header = document.createElement("header");
      const name = document.createElement("strong");
      name.textContent = `${isCodex ? "Codex" : message.participant === "hannah" ? "Hannah" : message.participant === "companion" ? message.display_name : message.participant === "marc" ? "Marc" : "Unknown participant"} · ${message.history_confirmed === false ? "expected name" : "stored name"}: ${message.display_name || "Unknown"}`;
      const time = document.createElement("time");
      const epoch = message.timestamp > 10000000000 ? message.timestamp : message.timestamp * 1000;
      time.textContent = new Date(epoch).toLocaleString();
      time.dateTime = new Date(epoch).toISOString();
      time.title = time.dateTime;
      const body = document.createElement("p");
      body.textContent = message.body;
      header.append(name, time);
      article.append(header, body);
      const saveObservation = document.createElement("button");
      saveObservation.type = "button";
      saveObservation.className = "text-button";
      saveObservation.textContent = "Save observation";
      saveObservation.addEventListener("click", () => document.dispatchEvent(new CustomEvent("memory-save", { detail: message })));
      article.append(saveObservation);
      article.append(element("small", `Sender: ${message.sender} · Type: ${message.sender_type}`), relatedLink("message", message.id));
      if (message.history_confirmed === false) {
        article.append(element("small", "Received locally; awaiting history confirmation of name and time."));
      }
      if (message.identity_fallback) {
        const note = document.createElement("small");
        note.textContent = "Codex prefix used because stored display attribution differed.";
        article.append(note);
      }
      list.append(article);
    }
    byId("older-messages").disabled = !messageCursor || messages.size >= 500;
    byId("message-page-note").textContent = `${messages.size} messages shown, newest first.${messages.size >= 500 ? " Display limit reached; refresh to return to recent messages." : !messageCursor ? " No older messages." : ""}`;
  });
}

async function refreshApprovals() {
  return replaceRead("approvals", async (signal) => {
    const status = byId("approval-filter").value;
    const data = await api(`/api/v1/approvals${status ? `?status=${status}` : ""}`, { signal });
    if (signal.aborted) return;
    const signature = JSON.stringify([status, data]);
    if (approvalSignature === signature) return;
    approvalSignature = signature;
    const list = byId("approval-list");
    list.replaceChildren();
    if (!data.items.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No matching proposals.";
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
      risk.textContent = `${approval.risk} risk · ${approval.status}`;
      const text = document.createElement("p");
      text.textContent = approval.instruction;
      const review = document.createElement("button");
      review.type = "button";
      review.className = "secondary";
      review.textContent = "Review";
    review.addEventListener("click", () => openApproval(approval, review));
      header.append(title, risk);
      article.append(header, text, review);
      list.append(article);
    }
  });
}

async function openApproval(approval, opener) {
  try {
  approval = await api(`/api/v1/approvals/${encodeURIComponent(approval.id)}`);
  currentApproval = approval;
  byId("approval-summary").textContent = approval.summary;
  byId("approval-instruction").textContent = approval.instruction;
  byId("approval-risk").textContent = approval.risk;
  byId("approval-sandbox").textContent = approval.effective_sandbox;
  byId("approval-post-result").checked = false;
  updateControls();
  showDialog(byId("approval-dialog"), opener);
  } catch (error) { reportError(error); }
}

function confirmApproval(action) {
  if (!currentApproval || currentApproval.status !== "pending") return;
  confirmation = {
    action,
    approval: { ...currentApproval },
    postResult: byId("approval-post-result").checked,
  };
  byId("confirm-title").textContent = action === "execute" ? "Confirm execution" : "Confirm rejection";
  byId("confirm-error").textContent = "";
  byId("confirm-summary").textContent = currentApproval.summary;
  byId("confirm-instruction").textContent = currentApproval.instruction;
  byId("confirm-scope").textContent = `Risk: ${currentApproval.risk} · Sandbox: ${currentApproval.effective_sandbox} · Post result to group: ${confirmation.postResult && action === "execute" ? "Yes" : "No"}`;
  byId("confirm-action").textContent = action === "execute" ? "Execute exactly this action" : "Reject this proposal";
  showDialog(byId("confirm-dialog"));
}

async function performConfirmation() {
  if (!confirmation || submitting) return;
  const choice = confirmation;
  const result = await submit(`/api/v1/approvals/${encodeURIComponent(choice.approval.id)}/${choice.action}`,
    choice.action === "execute" ? { post_result: choice.postResult } : {});
  if (result || retryAction) {
    byId("confirm-dialog").close();
    byId("approval-dialog").close();
    confirmation = null;
  }
}

async function refreshDeliveries() {
  const page = await replaceRead("deliveries", (signal) => api("/api/v1/deliveries", { signal }));
  const count = Number(latestSnapshot?.relay.uncertain_outbox || 0);
  const warning = count > 0 || page.items.length > 0;
  byId("delivery-status").textContent = warning
    ? `${count} uncertain deliveries · ${page.items.length}${page.has_more ? "+" : ""} sending or uncertain items shown.`
    : "No uncertain or in-flight deliveries.";
  byId("delivery-status").className = warning ? "error" : "muted";
  byId("delivery-explanation").hidden = !warning;
  const signature = JSON.stringify(page);
  if (signature === deliverySignature) return;
  deliverySignature = signature;
  const list = byId("delivery-list");
  list.replaceChildren();
  for (const item of page.items) {
    const row = element("article", `${item.status} · ${localTime(item.updated_at)} `, "delivery");
    row.append(relatedLink("outbox", item.id), activityLink(item.id));
    if (item.source_message_id) row.append(relatedLink("message", item.source_message_id));
    list.append(row);
  }
}

async function submit(path, body = {}) {
  if (submitting) return null;
  submitting = true;
  updateControls();
  byId("action-error").textContent = "";
  byId("confirm-error").textContent = "";
  try {
    const result = await apiClient.submit(path, body);
    retryAction = null;
    byId("retry-action").hidden = true;
    renderCommand(result);
    announce(`Command ${result.command_type || "request"} ${result.status}.`);
    if (refreshPromise) await refreshPromise;
    await refreshAll();
    return result;
  } catch (error) {
    const ambiguous = !(error instanceof RelayApiError) || error.status >= 500;
    retryAction = ambiguous ? { path, body } : null;
    byId("retry-action").hidden = !retryAction;
    byId("action-error").textContent = ambiguous
      ? "Submission outcome unknown. Retry the same request to retrieve its status without submitting a second action."
      : error.message;
    byId("confirm-error").textContent = byId("action-error").textContent;
    return null;
  } finally { submitting = false; updateControls(); }
}

async function refreshAll() {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
  try {
    const snapshot = await replaceRead(
      "snapshot",
      (signal) => api("/api/v1/snapshot", { signal }),
    );
    renderSnapshot(snapshot);
    await Promise.all([
      refreshActivity(),
      refreshApprovals(),
      refreshTrackedCommand(),
      refreshDeliveries(),
    ]);
    disconnected = false;
    updateControls();
    pollFailures = 0;
    return true;
  } catch (error) {
    if (error.name === "AbortError") return false;
    pollFailures = Math.min(pollFailures + 1, 5);
    disconnected = true;
    setConnection("Disconnected", "error");
    byId("worker-note").textContent = error.message;
    updateControls();
    return false;
  }
  })();
  try { return await refreshPromise; } finally { refreshPromise = null; }
}

function schedulePoll() {
  clearTimeout(pollTimer);
  if (document.hidden) return;
  const delay = RelayUiModel.pollDelay(pollMs, pollFailures, maxPollBackoffMs);
  pollTimer = setTimeout(async () => { await refreshAll(); schedulePoll(); }, delay);
}

function submitFromControl(path) {
  submit(path).catch((error) => announce(`Command submission failed: ${error.message}`));
}

byId("start-conversation").addEventListener("click", () => submitFromControl("/api/v1/commands/start-conversation"));
byId("trigger-initiative").addEventListener("click", () => submitFromControl("/api/v1/commands/trigger-initiative"));
byId("initiative-limit").addEventListener("input", () => { initiativeLimitDirty = true; });
byId("initiative-limit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (byId("save-initiative-limit").disabled) return;
  const input = byId("initiative-limit");
  if (!input.reportValidity()) return;
  const value = input.value;
  await submit("/api/v1/settings/initiative-limit", { limit: Number(value) });
});
byId("resume-relay").addEventListener("click", () => submitFromControl("/api/v1/commands/resume"));
byId("pause-relay").addEventListener("click", async () => {
  const reason = window.prompt("Pause reason", "paused from dashboard");
  if (reason === null) return;
  try {
    await api("/api/v1/control/pause", { method: "POST", body: JSON.stringify({ reason }) });
    announce("Relay pause requested.");
    await refreshAll();
  } catch (error) { reportError(error); }
});
byId("retry-action").addEventListener("click", () => {
  if (retryAction) submit(retryAction.path, retryAction.body);
});
byId("older-messages").addEventListener("click", () => refreshMessages(true).catch(reportError));
for (const id of ["activity-severity", "activity-kind", "activity-related"]) {
  byId(id).addEventListener("input", renderActivity);
}
byId("approval-filter").addEventListener("change", () => refreshApprovals().catch(reportError));
byId("refresh-messages").addEventListener("click", () => {
  refreshMessages().catch((error) => {
    if (error.name !== "AbortError") announce(`Message refresh failed: ${error.message}`);
  });
});
byId("sync-history").addEventListener("click", async () => {
  const result = await submit("/api/v1/commands/sync-history");
  if (!result) byId("history-sync-status").textContent = byId("action-error").textContent;
});
byId("refresh-log").addEventListener("click", async () => {
  try {
    const data = await replaceRead(
      "log",
      (signal) => api("/api/v1/diagnostics/log-tail?lines=200", { signal, headers: logEtag ? { "If-None-Match": logEtag } : {} }),
    );
    if (data) {
      logEtag = apiClient.etags.get("/api/v1/diagnostics/log-tail?lines=200");
      byId("log-tail").textContent = data.lines.join("\n") || "Log is empty.";
      byId("log-note").textContent = data.truncated ? "Showing a bounded tail; earlier content is omitted." : "Current log tail.";
    }
  } catch (error) {
    if (error.name !== "AbortError") announce(`Log refresh failed: ${error.message}`);
  }
});
byId("reject-approval").addEventListener("click", () => confirmApproval("reject"));
byId("execute-approval").addEventListener("click", () => confirmApproval("execute"));
byId("confirm-action").addEventListener("click", performConfirmation);

for (const dialog of document.querySelectorAll("dialog")) {
  dialog.addEventListener("close", () => {
    if (dialog.returnFocus?.isConnected && !dialog.returnFocus.disabled && !dialog.returnFocus.closest("[hidden]")) dialog.returnFocus.focus();
    else if (byId("approval-dialog").open) byId("approval-dialog").querySelector("[autofocus]").focus();
    else document.querySelector('.tabbar [aria-selected="true"]').focus();
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const controls = [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]')]
      .filter(control => !control.closest('[hidden]') && ![...dialog.querySelectorAll('details:not([open])')].some(details => details.contains(control)));
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearTimeout(pollTimer);
    abortReads();
    return;
  }
  refreshAll().finally(schedulePoll);
});

document.addEventListener("schedule-submitted", event => {
  renderCommand(event.detail);
  refreshAll();
});
updateControls();
Promise.allSettled([refreshAll(), refreshMessages()]).finally(schedulePoll);
