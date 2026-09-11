"use strict";
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const axe = require("axe-core");
const assets = path.join(__dirname, "../src/kindroid_relay/web/static");
const tick = () => new Promise((resolve) => setImmediate(resolve));
const stamp = "2026-09-06T12:00:00Z";

async function dashboard(t) {
  const dom = new JSDOM(fs.readFileSync(path.join(assets, "index.html"), "utf8"), {
    url: "http://127.0.0.1:8765/", runScripts: "outside-only", pretendToBeVisual: true,
  });
  t.after(() => dom.window.close());
  const w = dom.window;
  const timers = new Map();
  let timerId = 0;
  w.setTimeout = (fn, delay) => { timers.set(++timerId, { fn, delay }); return timerId; };
  w.clearTimeout = (id) => timers.delete(id);
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event("close")); };
  // jsdom does not implement native dialog form submission. Exercise the app's
  // close/focus handlers while emulating that browser default only.
  w.document.addEventListener("click", (event) => {
    const button = event.target.closest('form[method="dialog"] button:not([type="button"])');
    if (button) { event.preventDefault(); button.closest("dialog").close(); }
  });
  const fixture = {
    snapshot: { server_time: stamp, relay: { paused: false, consecutive_turns: 1, history_requests_24h: 10, uncertain_outbox: 0 },
      worker: { phase: "idle", phase_started_at: "2026-09-06T11:58:55Z", heartbeat_at: stamp, stale: false, note: "" },
      active_command: null, schedule: { initiative_enabled: true, initiative_interval_seconds: 600, history_request_limit_24h: 100, max_consecutive_turns: 8,
        initiative_budget: { used: 5, limit: 50, available_at: null, source: "configuration" } } },
    commands: [], events: [], deliveries: [], calls: [], offline: false, losePost: false,
    encounters: { enabled: true, mode: "assistant", choice: null, controls_schedule: false, session: null,
      quiet_now: false, quiet_hours: [{ participant: 0, name: 'Hannah', enabled: false, start: '22:00', end: '06:00' }],
      shared_config: { enabled: true, daily_time: "18:00" } },
    daily: { configured: false, revision: null, date: "2026-09-07", timezone: ["Fixture time"], next_due_at: null,
      config: { enabled: true, weights: { check_in: 40, exploration: 60 }, dead_hours: [],
        check_in_limit: 50, exploration_limit: 3, attempt_limit: 2, interpretation_limit: 4 },
      capabilities: { check_in: true, exploration: true }, auto_interpret: true, timeline: [], recent: [] },
    memory: { items: [], progress: { enabled: false, auto_capture: false }, mode: "lexical_fuzzy", semantic_status: "not_configured", truncated: false },
    exploration: { enabled: false, paused: false, budget: { used: 0, limit: 3 }, sessions: [], candidates: [] },
    discovery: { config: { sources: ["catalog", "memory"], local_area: "", follow_through: false, research_limit: 2 }, experiences: [], feeds: [], sources: [] },
    garden: { items: [], counts: { roots: 0, growing: 0, passing: 0 }, has_more: false, next_after: "" },
    gardenDetail: { id: "seed1", layer: "passing", ownership: "self", status: "active", content: "A seed", annotation: "", current_version: 1,
      versions: [], has_more: false, next_after: 1, responses: [], relationships: [], projects: [] },
    approval: { id: "a1", summary: "Write note", instruction: "Create exactly note.txt", risk: "low", effective_sandbox: "workspace-write", status: "pending" },
    messages: [{ id: "m2", display_name: "Hannah", sender: "ai1", sender_type: "ai", participant: "hannah", body: "Hello <script>bad()</script>", timestamp: 1000 }],
  };
  w.fetch = async function (url, options = {}) {
    // Native Window.fetch rejects a receiver such as the API-client instance.
    // An arrow-function mock hides this browser-only invocation error.
    if (this !== w) throw new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation");
    fixture.calls.push({ url, options });
    if (fixture.offline) throw new TypeError("offline");
    const response = (data, status = 200) => ({ ok: status < 400, status, headers: new Map([["etag", '"log-1"']]), json: async () => structuredClone(data) });
    if (options.method === "POST") {
      const body = JSON.parse(options.body);
      if (fixture.postError) return response({ message: fixture.postError }, 409);
      if (url.endsWith("/pause")) { fixture.snapshot.relay.paused = true; return response({ paused: true }); }
      let command = fixture.commands.find((item) => item.key === body.idempotency_key);
      if (!command) {
        command = { id: `c${fixture.commands.length + 1}`, key: body.idempotency_key,
          command_type: url.includes("approvals") ? `${url.endsWith("execute") ? "execute" : "reject"}_approval`
            : url.endsWith("initiative-limit") ? "set_initiative_limit"
            : url.endsWith("settings/schedule") ? "set_schedule"
            : url.endsWith("settings/discovery") ? "set_discovery"
            : url.endsWith("discovery/public-topic") ? "approve_public_topic"
            : url.endsWith("sync-history") ? "sync_history"
            : url.endsWith("recover-shared") ? "recover_shared"
            : url.endsWith("trigger-initiative") ? "trigger_initiative" : "start_conversation",
          approval_id: url.includes("approvals") ? "a1" : null, status: "queued", requested_at: stamp, error: null };
        fixture.commands.unshift(command);
      }
      if (fixture.losePost) { fixture.losePost = false; throw new TypeError("lost response"); }
      return response(command, 202);
    }
    if (url === "/api/v1/snapshot") return response(fixture.snapshot);
    if (url === "/api/v1/encounters") return response(fixture.encounters);
    if (url === "/api/v1/schedule") return response(fixture.daily);
    if (url === "/api/v1/exploration") return response(fixture.exploration);
    if (url === "/api/v1/discovery") return response(fixture.discovery);
    if (url.startsWith("/api/v1/garden?")) return response(fixture.garden);
    if (url.includes("/api/v1/garden/") && url.includes("/trail?")) return response({ items: [], has_more: false, next_after: 0 });
    if (url.startsWith("/api/v1/garden/")) return response(fixture.gardenDetail);
    if (url.startsWith("/api/v1/memory?")) return response(fixture.memory);
    if (url.startsWith("/api/v1/memory/")) return response(fixture.memory.items[0]);
    if (url === "/api/v1/commands") return response({ items: fixture.commands });
    if (url.startsWith("/api/v1/commands/")) return response(fixture.commands[0]);
    if (url === "/api/v1/deliveries") return response({ items: fixture.deliveries, has_more: false });
    if (url.startsWith("/api/v1/deliveries/")) return response(fixture.deliveries[0]);
    if (url.startsWith("/api/v1/activity")) {
      const cursor = Number(new URL(url, w.location.href).searchParams.get("after_id"));
      const events = fixture.events.filter((item) => item.id > cursor);
      return response({ items: events, next_after_id: events.at(-1)?.id || cursor, has_more: false });
    }
    if (url === "/api/v1/approvals/a1") return response(fixture.approval);
    if (url.startsWith("/api/v1/approvals")) return response({ items: [fixture.approval] });
    if (url.startsWith("/api/v1/messages?")) {
      const older = url.includes("before=");
      return response({ items: older ? [{ ...fixture.messages[0], id: "m1", participant: "marc", sender_type: "user", display_name: "Marc" }] : fixture.messages,
        next_before: older ? null : 1000, next_before_id: older ? null : "m2", has_more: !older });
    }
    if (url.startsWith("/api/v1/messages/")) return response(fixture.messages[0]);
    if (url.includes("log-tail")) return response({ lines: ["<b>safe text</b>"], truncated: true }, options.headers?.["If-None-Match"] ? 304 : 200);
    throw new Error(`Unexpected fixture request ${url}`);
  };
  for (const name of [...w.document.querySelectorAll('script[src]')].map(script => path.basename(script.src))) {
    w.eval(fs.readFileSync(path.join(assets, name), "utf8") + (name === "app.js" ? "\nwindow.testApp = { refreshAll, schedulePoll };" : ""));
  }
  const flush = async () => { for (let i = 0; i < 5; i++) await tick(); };
  await flush();
  return { w, fixture, timers, flush, el: (id) => w.document.getElementById(id), refresh: () => w.testApp.refreshAll() };
}

test('history threshold warns without disabling work and displays daily reset', async (t) => {
  const { fixture, el, refresh, flush } = await dashboard(t);
  fixture.snapshot.relay.history_requests_24h = 125;
  fixture.snapshot.relay.history_window_reset_at = 1788825600;
  await refresh(); await flush();
  assert.equal(el('history-budget').textContent, '125 / 100');
  assert.match(el('history-budget-note').textContent, /threshold reached; requests remain enabled/);
  assert.match(el('history-budget-note').textContent, /resets at.*00:00 UTC/);
  assert.equal(el('history-budget-note').classList.contains('warning'), true);
  assert.equal(el('trigger-initiative').disabled, false);
  fixture.snapshot.relay.history_requests_24h = 0;
  await refresh(); await flush();
  assert.equal(el('history-budget').textContent, '0 / 100');
  assert.equal(el('history-budget-note').classList.contains('warning'), false);
});

test("encounter cancellation preserves its request key after a lost response", async (t) => {
  const { el, flush, fixture } = await dashboard(t);
  assert.equal(el("encounter-controls").hidden, false);
  fixture.losePost = true;
  el("encounter-cancel").click(); await flush();
  assert.equal(fixture.commands.length, 1);
  assert.equal(el("encounter-retry").hidden, false);
  el("encounter-retry").click(); await flush();
  assert.equal(fixture.commands.length, 1);
  const calls = fixture.calls.filter(c => c.url.endsWith("settings/encounter"));
  assert.equal(calls.length, 2);
  assert.equal(JSON.parse(calls[0].options.body).idempotency_key, JSON.parse(calls[1].options.body).idempotency_key);
  assert.match(el("encounter-result").textContent, /queued/);
});

test('activity sessions collapse, preserve expansion through polling, filter and expose warnings', async (t) => {
  const { w, fixture, el, refresh, flush } = await dashboard(t);
  fixture.events = [
    { id: 1, kind: 'session_started', severity: 'info', summary: 'Conversation session started', details: { session_id: 's1' }, created_at: stamp },
    { id: 2, kind: 'outbound_uncertain', severity: 'warning', summary: 'Inspect delivery', details: { session_id: 's1', outbox_id: 'o1' }, created_at: stamp },
    { id: 3, kind: 'session_ended', severity: 'info', summary: 'Conversation session ended', details: { session_id: 's1', outcome: 'paused' }, created_at: stamp },
  ];
  await refresh(); await flush();
  assert.equal(el('activity-list').children.length, 1);
  let group = el('activity-list').querySelector('details');
  assert.equal(group.open, false);
  assert.match(group.querySelector('summary').textContent, /started.*ended.*paused.*Inspect delivery/);
  assert.equal(group.querySelectorAll('ol > li').length, 3);
  group.open = true; group.dispatchEvent(new w.Event('toggle'));
  await refresh(); await flush();
  assert.equal(el('activity-list').querySelector('details').open, true);
  el('activity-view').value = 'events'; el('activity-view').dispatchEvent(new w.Event('change'));
  assert.equal(el('activity-list').children.length, 3);
  el('activity-view').value = 'grouped'; el('activity-view').dispatchEvent(new w.Event('change'));
  el('activity-related').value = 'o1'; el('activity-related').dispatchEvent(new w.Event('input'));
  assert.equal(el('activity-list').children.length, 1);
  assert.match(el('activity-list').textContent, /outbox: o1/);
});

test('quiet hours validate locally and retry the same queued request after response loss', async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  const form = el('quiet-controls').querySelector('form');
  const checkbox = form.querySelector('[type=checkbox]');
  const [start, end] = form.querySelectorAll('[type=time]');
  checkbox.checked = true; end.value = start.value;
  form.dispatchEvent(new w.Event('submit', { cancelable: true })); await flush();
  assert.match(el('quiet-result').textContent, /different start and end/);
  assert.equal(fixture.calls.filter(c => c.options.method === 'POST').length, 0);
  end.value = '06:00'; fixture.losePost = true;
  form.dispatchEvent(new w.Event('submit', { cancelable: true })); await flush();
  assert.equal(el('encounter-retry').hidden, false);
  el('encounter-retry').click(); await flush();
  const calls = fixture.calls.filter(c => c.url === '/api/v1/settings/quiet-hours');
  assert.equal(calls.length, 2);
  const [first, second] = calls.map(c => JSON.parse(c.options.body));
  assert.equal(first.idempotency_key, second.idempotency_key);
  assert.deepEqual(first.payload, { participant: 0, enabled: true, start: '22:00', end: '06:00' });
});

test("room API requests retain the selected room namespace", async (t) => {
  const { w } = await dashboard(t);
  w.history.replaceState({}, "", "/rooms/miko/");
  const calls = [];
  const client = new w.RelayApiClient({ csrf: "token", fetchImpl: async url => {
    calls.push(url); return { ok: true, status: 200, headers: new Map(), json: async () => ({}) };
  } });
  await client.request("/api/v1/messages");
  assert.deepEqual(calls, ["/rooms/miko/api/v1/messages"]);
});



test("tabs group the requested panels and preserve state without making requests", async (t) => {
  const { w, el, fixture, refresh } = await dashboard(t);
  const doc = w.document;
  assert.equal(doc.querySelector(".topbar").nextElementSibling.getAttribute("role"), "tablist");
  const info = el("tab-info"), operations = el("tab-operations");
  assert.equal(info.textContent, "Info");
  assert.equal(operations.textContent, "Operations");
  for (const [tab, panel] of [[info, el("view-info")], [operations, el("view-operations")]]) {
    assert.equal(tab.getAttribute("aria-controls"), panel.id);
    assert.equal(panel.getAttribute("aria-labelledby"), tab.id);
    assert.equal(panel.getAttribute("role"), "tabpanel");
  }
  for (const id of ["relay-state", "controls-heading", "initiative-budget-heading", "activity-list", "message-list"]) {
    assert.equal(el(id).closest('[role="tabpanel"]').id, "view-info");
  }
  for (const id of ["current-heading", "delivery-heading", "approvals-heading", "memory-panel", "exploration-panel", "garden-panel", "log-tail"]) {
    assert.equal(el(id).closest('[role="tabpanel"]').id, "view-operations");
  }
  assert.equal(el("view-info").hidden, false);
  assert.equal(el("view-operations").hidden, true);
  assert.equal(info.tabIndex, 0);
  assert.equal(operations.tabIndex, -1);
  el("activity-related").value = "preserved-filter";
  el("memory-query").value = "preserved-query";
  const message = el("message-list").firstElementChild;
  const calls = fixture.calls.length;
  operations.click();
  assert.equal(el("view-info").hidden, true);
  assert.equal(el("view-operations").hidden, false);
  assert.equal(operations.getAttribute("aria-selected"), "true");
  assert.equal(info.getAttribute("aria-selected"), "false");
  assert.equal(operations.tabIndex, 0);
  assert.equal(info.tabIndex, -1);
  info.click();
  assert.equal(fixture.calls.length, calls, "Tab switches do not fetch or submit commands");
  assert.equal(el("activity-related").value, "preserved-filter");
  assert.equal(el("memory-query").value, "preserved-query");
  assert.equal(el("message-list").firstElementChild, message);
  operations.click();
  fixture.snapshot.worker.phase = "running_codex";
  await refresh();
  assert.match(el("current-activity").textContent, /Waiting for Codex/);
  assert.equal(el("view-operations").hidden, false, "Polling must not reset the selected tab");
  doc.querySelector(".skip-link").click();
  assert.equal(el("view-info").hidden, false);
  assert.equal(doc.activeElement, el("controls-heading"));
});

test("tabs support arrow-key wrapping, Home and End with roving focus", async (t) => {
  const { w, el } = await dashboard(t);
  el("tab-info").focus();
  for (const [key, target] of [["ArrowRight", "tab-operations"], ["ArrowRight", "tab-schedule"], ["ArrowRight", "tab-info"],
    ["ArrowLeft", "tab-schedule"], ["Home", "tab-info"], ["End", "tab-schedule"]]) {
    const event = new w.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
    w.document.activeElement.dispatchEvent(event);
    assert.equal(event.defaultPrevented, true);
    assert.equal(w.document.activeElement, el(target));
    assert.equal(el(target).getAttribute("aria-selected"), "true");
    assert.equal(el(el(target).getAttribute("aria-controls")).hidden, false);
  }
});

test("compact workspace retains controls, keyboard-accessible feeds and optional details", async (t) => {
  const { w, el, refresh } = await dashboard(t);
  const doc = w.document;
  const style = doc.createElement("style");
  style.textContent = fs.readFileSync(path.join(assets, "styles.css"), "utf8");
  doc.head.append(style);
  assert.equal(doc.querySelectorAll(".workspace > .operations").length, 1);
  assert.equal(doc.querySelectorAll(".workspace > .activity-panel").length, 1);
  assert.equal(doc.querySelectorAll(".workspace > .messages-panel").length, 1);
  const ids = [...doc.querySelectorAll("[id]")].map((node) => node.id);
  assert.equal(new Set(ids).size, ids.length);
  assert.equal(w.getComputedStyle(doc.querySelector("h1")).fontSize, "14px");
  assert.equal(w.getComputedStyle(doc.querySelector(".workspace")).display, "grid");
  assert.equal(w.getComputedStyle(el("initiative-limit")).minHeight, "28px");
  for (const id of ["activity-list", "message-list"]) {
    assert.equal(el(id).tabIndex, 0);
    assert.ok(el(id).getAttribute("aria-label"));
  }
  for (const id of ["command-status", "action-error", "delivery-explanation", "approval-list"]) {
    assert.equal(el(id).closest("details"), null, `${id} must not be hidden in a disclosure`);
  }
  const history = el("command-list").closest("details");
  const help = el("initiative-limit-note").closest("details");
  assert.equal(history.open, false);
  assert.equal(help.open, false);
  history.open = true;
  help.open = true;
  await refresh();
  assert.equal(history.open, true);
  assert.equal(help.open, true);
  assert.equal(el("initiative-limit").getAttribute("aria-describedby"), "initiative-limit-note");
});

test("default fetch preserves the Window receiver for reads and mutations", async (t) => {
  const { fixture, el, flush } = await dashboard(t);
  assert.doesNotMatch(el("worker-note").textContent, /Illegal invocation/);
  assert.equal(el("relay-state").textContent, "Running");
  el("start-conversation").click(); await flush();
  assert.equal(fixture.commands.length, 1);
  assert.equal(el("start-conversation-status").textContent, "queued");
});

test("direct replies label provisional attribution until history confirms it", async (t) => {
  const { fixture, w, el, flush } = await dashboard(t);
  fixture.messages[0].history_confirmed = false;
  el("refresh-messages").click(); await flush();
  assert.match(w.document.querySelector("article.message").textContent, /expected name: Hannah/);
  assert.match(w.document.querySelector("article.message").textContent, /awaiting history confirmation/);
  fixture.messages[0].history_confirmed = true;
  el("refresh-messages").click(); await flush();
  assert.match(w.document.querySelector("article.message").textContent, /stored name: Hannah/);
  assert.doesNotMatch(w.document.querySelector("article.message").textContent, /awaiting history confirmation/);
});

test("actual dashboard renders worker states, waiting time and stale controls", async (t) => {
  const { fixture, el, refresh } = await dashboard(t);
  for (const [phase, label] of [["idle", "Running"], ["running_codex", "Busy"], ["waiting_for_kindroid", "Busy"], ["degraded", "Degraded"], ["stopping", "Stopped"]]) {
    fixture.snapshot.worker.phase = phase;
    await refresh();
    assert.equal(el("relay-state").textContent, label);
    if (phase === "idle") {
      assert.match(el("current-activity").textContent, /Waiting for a check-in or manual start/);
      assert.match(el("history-sync-note").textContent, /Background chat monitoring is off/);
      assert.match(el("history-sync-note").textContent, /Each startup syncs history once per room/);
    }
    if (phase === "running_codex") {
      assert.match(el("current-activity").textContent, /Waiting for Codex/);
      assert.match(el("current-elapsed").textContent, /1m 5s/);
    }
  }
  fixture.snapshot.worker.phase = "paused";
  fixture.snapshot.relay.paused = true;
  await refresh();
  assert.equal(el("relay-state").textContent, "Paused");
  assert.equal(el("resume-relay").disabled, false);
  fixture.snapshot.worker.stale = true;
  await refresh();
  assert.equal(el("relay-state").textContent, "Worker stale");
  assert.equal(el("resume-relay").disabled, true);
});

test("check-in completion shows skipped reason or sent outcome beside its control", async (t) => {
  const { fixture, el, flush, refresh } = await dashboard(t);
  el("trigger-initiative").click(); await flush();
  assert.equal(fixture.commands[0].command_type, "trigger_initiative");
  assert.equal(el("trigger-initiative-status").textContent, "queued");
  fixture.commands[0].status = "completed";
  fixture.commands[0].result = { handled: 0, initiative_outcome: "quiet_hours", check_in_sent: false };
  await refresh();
  assert.match(el("trigger-initiative-status").textContent, /Skipped: quiet hours/);
  assert.match(el("command-list").textContent, /Skipped: quiet hours/);
  assert.match(el("announcer").textContent, /Skipped: quiet hours/);
  assert.match(el("trigger-initiative-status").className, /warning/);
  fixture.commands[0].result = { handled: 0, initiative_outcome: "check_in_sent_user_turn", check_in_sent: true };
  await refresh();
  assert.match(el("trigger-initiative-status").textContent, /Check-in sent; companion did not take/);
  assert.match(el("trigger-initiative-status").className, /success/);
});

test("manual history sync works while paused, retries the same request and reloads messages on completion", async (t) => {
  const { fixture, el, flush, refresh } = await dashboard(t);
  fixture.snapshot.relay.paused = true;
  fixture.snapshot.worker.phase = "paused";
  await refresh();
  assert.equal(el("sync-history").disabled, false);
  el("refresh-messages").click(); await flush();
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
  fixture.losePost = true;
  el("sync-history").click(); await flush();
  assert.equal(el("sync-history").disabled, true);
  assert.match(el("history-sync-status").textContent, /outcome unknown/);
  el("retry-action").click(); await flush();
  const posts = fixture.calls.filter(c => c.url.endsWith("sync-history"));
  assert.equal(posts.length, 2);
  assert.equal(JSON.parse(posts[0].options.body).idempotency_key, JSON.parse(posts[1].options.body).idempotency_key);
  assert.equal(fixture.commands.length, 1);
  fixture.messages.push({ ...fixture.messages[0], id: "new-chat", body: "Newly synchronized fixture" });
  fixture.commands[0].status = "completed";
  fixture.commands[0].result = { sync_outcome: "synced", messages_added: 1, paused: true };
  await refresh(); await flush();
  assert.match(el("history-sync-status").textContent, /1 new messages.*Relay remains paused/);
  assert.match(el("message-list").textContent, /Newly synchronized fixture/);
  assert.equal(el("sync-history").disabled, false);
  assert.equal(fixture.snapshot.relay.paused, true);
  assert.equal(fixture.calls.filter(c => c.options.method === "POST" && !c.url.endsWith("sync-history")).length, 0);
});

test("history sync blockers remain visible after command recovery without claiming success", async (t) => {
  const { fixture, el, refresh, flush } = await dashboard(t);
  for (const [outcome, expected] of [["history_cooldown", /API cooldown/], ["history_budget", /allowance exhausted/],
    ["history_pending", /incomplete/], ["reconciliation_unresolved", /ambiguous/], ["history_error", /request failed/]]) {
    fixture.commands = [{ id: outcome, command_type: "sync_history", status: "completed", requested_at: stamp,
      result: { sync_outcome: outcome, paused: true, retry_at: 1800000000,
        reason_detail: "Fixed diagnostic reason <b>literal</b>" } }];
    await refresh(); await flush();
    assert.match(el("history-sync-status").textContent, expected);
    assert.match(el("history-sync-status").className, /warning/);
    assert.match(el("command-status").textContent, /blocked/);
    assert.doesNotMatch(el("command-status").textContent, /completed/);
    assert.match(el("history-sync-status").textContent, /Fixed diagnostic reason <b>literal<\/b>/);
    assert.equal(el("history-sync-status").querySelector("b"), null);
  }
  fixture.snapshot.worker.stale = true;
  await refresh();
  assert.equal(el("sync-history").disabled, true);
});

test("budget setting validates, preserves drafts and queues while paused without resuming", async (t) => {
  const { fixture, el, flush, refresh, w } = await dashboard(t);
  const input = el("initiative-limit");
  assert.equal(input.value, "50");
  assert.match(el("initiative-budget").textContent, /5 \/ 50/);
  fixture.snapshot.relay.paused = true;
  fixture.snapshot.worker.phase = "paused";
  await refresh();
  for (const value of ["0", "1001", "1.5", ""]) {
    input.value = value;
    el("save-initiative-limit").click(); await flush();
    assert.equal(fixture.commands.length, 0);
  }
  input.value = "75";
  input.dispatchEvent(new w.Event("input"));
  await refresh();
  assert.equal(input.value, "75");
  el("save-initiative-limit").click(); await flush();
  assert.equal(fixture.commands[0].command_type, "set_initiative_limit");
  assert.equal(el("initiative-limit-status").textContent, "queued");
  assert.equal(el("save-initiative-limit").disabled, true);
  assert.equal(input.value, "75"); // Still queued; old snapshot must not erase the draft.
  const post = fixture.calls.find((item) => item.options.method === "POST");
  assert.equal(JSON.parse(post.options.body).limit, 75);
  fixture.commands[0].status = "completed";
  fixture.commands[0].result = { limit: 75, previous_limit: 50 };
  fixture.snapshot.schedule.initiative_budget.limit = 75;
  fixture.snapshot.schedule.initiative_budget.source = "dashboard";
  await refresh();
  assert.match(el("initiative-limit-status").textContent, /Saved daily check-in limit: 75/);
  assert.match(el("initiative-budget").textContent, /5 \/ 75/);
  assert.equal(el("relay-state").textContent, "Paused");
  assert.equal(el("save-initiative-limit").disabled, false);
  fixture.snapshot.worker.stale = true;
  await refresh();
  assert.equal(el("save-initiative-limit").disabled, true);
});

test("budget exhaustion shows recovery time and lost save retries the original value", async (t) => {
  const { fixture, el, flush, refresh, w } = await dashboard(t);
  const availableAt = 1788700000;
  fixture.snapshot.schedule.initiative_budget = { used: 5, limit: 5, available_at: availableAt, source: "configuration" };
  await refresh();
  assert.equal(el("initiative-limit").value, "5");
  assert.ok(el("initiative-availability").textContent.includes(new w.Date(availableAt * 1000).toLocaleString()));
  el("initiative-limit").value = "50";
  el("initiative-limit").dispatchEvent(new w.Event("input"));
  fixture.losePost = true;
  el("save-initiative-limit").click(); await flush();
  assert.equal(el("retry-action").hidden, false);
  el("initiative-limit").value = "60";
  el("initiative-limit").dispatchEvent(new w.Event("input"));
  el("retry-action").click(); await flush();
  const posts = fixture.calls.filter((item) => item.options.method === "POST").map((item) => JSON.parse(item.options.body));
  assert.equal(posts.length, 2);
  assert.deepEqual(posts[0], posts[1]);
  assert.equal(posts[1].limit, 50);
  assert.equal(fixture.commands.length, 1);
  fixture.commands[0].status = "completed";
  fixture.commands[0].result = { limit: 50, previous_limit: 5 };
  fixture.snapshot.schedule.initiative_budget = { used: 5, limit: 50, available_at: null, source: "dashboard" };
  await refresh();
  assert.match(el("initiative-availability").textContent, /Budget available now/);
  assert.equal(el("initiative-limit").value, "60"); // New unsaved draft survives completion.
  assert.match(el("initiative-limit-status").textContent, /Saved daily check-in limit: 50/);
});

test("lost command response retries the same action and recovers completion without snapshot active command", async (t) => {
  const { fixture, el, flush, refresh } = await dashboard(t);
  fixture.losePost = true;
  el("start-conversation").click();
  el("start-conversation").click();
  await flush();
  assert.equal(fixture.commands.length, 1);
  assert.equal(el("retry-action").hidden, false);
  el("retry-action").click();
  await flush();
  const posts = fixture.calls.filter((item) => item.options.method === "POST");
  assert.equal(JSON.parse(posts[0].options.body).idempotency_key, JSON.parse(posts[1].options.body).idempotency_key);
  assert.equal(el("start-conversation-status").textContent, "queued");
  assert.equal(el("start-conversation").disabled, true);
  fixture.commands[0].status = "running";
  await refresh();
  fixture.commands[0].status = "completed";
  await refresh();
  assert.equal(el("start-conversation-status").textContent, "completed");
  assert.equal(el("start-conversation").disabled, false);
  assert.match(el("announcer").textContent, /completed/);
});

test("poll reconnect keeps its activity cursor; severity/kind and related links work", async (t) => {
  const { fixture, el, refresh, w, timers } = await dashboard(t);
  fixture.events.push({ id: 1, kind: "message_received", severity: "info", created_at: stamp, summary: "message", details: { message_id: "m2" } });
  await refresh();
  fixture.offline = true;
  await refresh();
  w.testApp.schedulePoll();
  assert.ok([...timers.values()].some((item) => item.delay === 4000));
  fixture.offline = false;
  fixture.events.push({ id: 2, kind: "outbound_uncertain", severity: "warning", created_at: stamp, summary: "uncertain", details: { outbox_id: "o1" } });
  await refresh();
  assert.equal(el("activity-list").children.length, 2);
  assert.match(fixture.calls.filter((item) => item.url.includes("activity?")).at(-1).url, /after_id=1/);
  el("activity-severity").value = "warning";
  el("activity-severity").dispatchEvent(new w.Event("input"));
  assert.equal(el("activity-list").children.length, 1);
  assert.match(el("activity-list").textContent, /outbox: o1/);
});

test("message paging preserves attribution, source links and literal HTML", async (t) => {
  const { fixture, el, flush } = await dashboard(t);
  assert.equal(el("message-list").querySelectorAll("script").length, 0);
  assert.match(el("message-list").textContent, /stored name: Hannah/);
  el("older-messages").click();
  await flush();
  assert.equal(el("message-list").children.length, 2);
  assert.ok(el("message-list").querySelector(".marc"));
  assert.ok(fixture.calls.some((item) => item.url.includes("before_id=m2")));
  assert.equal(el("older-messages").disabled, true);
});

test("approval review and confirmation require two steps, preserve scope and restore focus", async (t) => {
  const { fixture, el, flush, w, refresh } = await dashboard(t);
  el("tab-operations").click();
  const review = el("approval-list").querySelector("button");
  review.focus(); review.click(); await flush();
  assert.equal(el("approval-dialog").open, true);
  el("approval-post-result").checked = true;
  el("execute-approval").focus(); el("execute-approval").click();
  assert.equal(el("confirm-dialog").open, true);
  assert.match(el("confirm-scope").textContent, /workspace-write.*Yes/);
  assert.equal(el("confirm-instruction").textContent, fixture.approval.instruction);
  assert.equal(fixture.calls.filter((item) => item.options.method === "POST").length, 0);
  const back = el("confirm-dialog").querySelector("[autofocus]");
  el("confirm-action").focus();
  el("confirm-action").dispatchEvent(new w.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true }));
  assert.equal(w.document.activeElement, back);
  back.click();
  assert.equal(w.document.activeElement, el("execute-approval"));
  el("reject-approval").focus(); el("reject-approval").click();
  assert.match(el("confirm-title").textContent, /rejection/);
  el("confirm-dialog").querySelector("[autofocus]").click();
  el("execute-approval").click();
  el("confirm-action").click(); await flush();
  assert.equal(el("confirm-dialog").open, false);
  assert.equal(el("approval-dialog").open, false);
  assert.equal(w.document.activeElement, review);
  const post = fixture.calls.find((item) => item.options.method === "POST");
  assert.equal(JSON.parse(post.options.body).post_result, true);
  fixture.commands[0].status = "failed";
  fixture.commands[0].error = "Execution failed";
  await refresh();
  assert.match(el("command-list").textContent, /Execution failed/);
});

test("uncertain delivery is linked without retry; log refresh uses conditional bounded reads", async (t) => {
  const { fixture, el, refresh, flush } = await dashboard(t);
  el("tab-operations").click();
  fixture.snapshot.relay.uncertain_outbox = 1;
  fixture.deliveries = [{ id: "o1", status: "uncertain", source_message_id: "m2", updated_at: stamp }];
  await refresh();
  assert.equal(el("delivery-explanation").hidden, false);
  assert.match(el("delivery-list").textContent, /message: m2/);
  assert.equal(el("delivery-list").querySelectorAll("button").length, 0);
  el("delivery-list").querySelector("a").click(); await flush();
  assert.equal(el("detail-dialog").open, true);
  assert.match(el("detail-body").textContent, /uncertain/);
  el("detail-links").querySelector("a").click();
  assert.equal(el("detail-dialog").open, false);
  assert.equal(el("activity-related").value, "o1");
  assert.equal(el("view-info").hidden, false);
  assert.equal(el("view-operations").hidden, true);
  assert.equal(el("activity-related").ownerDocument.activeElement, el("activity-related"));
  el("tab-operations").click();
  el("refresh-log").click(); await flush();
  el("refresh-log").click(); await flush();
  assert.equal(el("log-tail").textContent, "<b>safe text</b>");
  assert.equal(fixture.calls.filter((item) => item.url.includes("log-tail")).at(-1).options.headers["If-None-Match"], '"log-1"');
});

test("approval conflicts remain visible inside confirmation and rejection queues only on final confirmation", async (t) => {
  const { fixture, el, flush, refresh } = await dashboard(t);
  el("tab-operations").click();
  el("approval-list").querySelector("button").click(); await flush();
  el("reject-approval").click();
  fixture.offline = true;
  await refresh();
  assert.equal(el("confirm-action").disabled, true);
  fixture.offline = false;
  await refresh();
  fixture.postError = "Proposal is no longer pending";
  el("confirm-action").click(); await flush();
  assert.equal(el("confirm-dialog").open, true);
  assert.match(el("confirm-error").textContent, /no longer pending/);
  assert.equal(el("retry-action").hidden, true);
  fixture.postError = null;
  el("confirm-action").click(); await flush();
  assert.equal(fixture.commands.length, 1);
  assert.equal(fixture.commands[0].command_type, "reject_approval");
  assert.equal(el("confirm-dialog").open, false);
});

test("hidden page cancels polling and resumes with retained cursor and recovered queued commands", async (t) => {
  const { fixture, el, refresh, flush, w, timers } = await dashboard(t);
  fixture.events = [{ id: 7, severity: "info", kind: "command_queued", summary: "Queued", details: {}, created_at: stamp }];
  fixture.commands = [{ id: "recovered", command_type: "start_conversation", status: "queued", requested_at: stamp }];
  await refresh();
  assert.equal(el("start-conversation").disabled, true);
  assert.match(el("command-list").textContent, /queued/);
  w.testApp.schedulePoll();
  Object.defineProperty(w.document, "hidden", { configurable: true, value: true });
  w.document.dispatchEvent(new w.Event("visibilitychange"));
  assert.equal(timers.size, 0);
  Object.defineProperty(w.document, "hidden", { configurable: true, value: false });
  w.document.dispatchEvent(new w.Event("visibilitychange")); await flush();
  assert.match(fixture.calls.filter((item) => item.url.includes("activity?")).at(-1).url, /after_id=7/);
  assert.ok(timers.size > 0);
});

test("memory selection uses exact evidence, durable queue and safe text rendering", async (t) => {
  const { w, el, fixture, flush } = await dashboard(t);
  assert.equal(fixture.calls.some(c => c.url.includes("/memory")), false);
  el("message-list").querySelector("button").click();
  assert.equal(el("memory-dialog").open, true);
  assert.equal(el("memory-quote").value, fixture.messages[0].body);
  el("memory-quote").value = "invented quote";
  el("memory-edit-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  assert.match(el("memory-error").textContent, /exact passage/);
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
  el("memory-quote").value = fixture.messages[0].body;
  fixture.losePost = true;
  el("memory-edit-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  assert.equal(el("memory-dialog").open, true);
  el("memory-edit-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  const posts = fixture.calls.filter(c => c.options.method === "POST");
  assert.equal(posts.length, 2);
  assert.equal(JSON.parse(posts[0].options.body).idempotency_key, JSON.parse(posts[1].options.body).idempotency_key);
  assert.equal(JSON.parse(posts[1].options.body).payload.quote, fixture.messages[0].body);
  assert.equal(el("memory-dialog").open, false);
  assert.match(el("memory-status").textContent, /Queued save/);
  fixture.memory.items = [{ id: "s1", kind: "reported_root", status: "active", origin: "marc_selection", interpretation: "", temporal_scope: "", entities: [], revisions: [],
    evidence: [{ display_name: "Hannah", attribution: "provisional", quote: "<img src=x onerror=bad()>" }], retrieval_reason: ["exact match"] }];
  el("memory-search-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  assert.equal(el("memory-results").querySelector("img"), null);
  assert.match(el("memory-results").textContent, /provisional/);
  assert.equal(el("memory-extract").disabled, true);
  el("memory-results").querySelector("button").click(); await flush();
  assert.equal(el("memory-quote").disabled, true);
  el("memory-record-status").value = "retracted";
  el("memory-reason").value = "Incorrect attribution";
  el("memory-edit-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  const last = fixture.calls.filter(c => c.options.method === "POST").at(-1);
  assert.match(last.url, /commands\/revise$/);
  assert.equal(JSON.parse(last.options.body).payload.reason, "Incorrect attribution");
});

test("semantic entity matches require explicit operator review rather than auto-linking", async (t) => {
  const { w, el, fixture, flush } = await dashboard(t);
  fixture.memory.items = [{ id: "s1", kind: "experience", status: "active", origin: "codex_extraction", interpretation: "A formative experience", temporal_scope: "childhood",
    uncertainty: "The name may refer to a different person", revisions: [], retrieval_reason: ["exact passage"],
    entities: [{ label: "Alex <b>untrusted</b>", relation: "about", possible_match_ids: ["e1", "e2"], identity_review_needed: true }],
    evidence: [{ message_id: "m2", quote: "Alex showed me the constellations.", display_name: "Hannah", attribution: "provisional" }] }];
  el("memory-search-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  assert.match(el("memory-results").textContent, /different person/);
  el("memory-results").querySelector("button").click(); await flush();
  assert.equal(el("memory-match-review").querySelectorAll("button").length, 2);
  assert.equal(el("memory-dialog").open, true);
  assert.equal(el("memory-match-review").querySelector("b"), null);
  el("memory-match-review").querySelector("button").click();
  assert.equal(el("memory-link-id").value, "e1");
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
  el("memory-link").click();
  assert.match(el("memory-error").textContent, /reason is required/);
  el("memory-reason").value = "Reviewed source; same person";
  el("memory-link").click(); await flush();
  const last = fixture.calls.filter(c => c.options.method === "POST").at(-1);
  assert.match(last.url, /commands\/link$/);
  assert.equal(JSON.parse(last.options.body).payload.entity_id, "e1");
});

test("garden shows provenance and immutable history; corrections are local, reasoned and idempotent", async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  assert.equal(fixture.calls.some(c => c.url.includes("/garden")), false);
  el("tab-operations").click();
  fixture.garden.items = [{ id: "seed1", layer: "passing", status: "archived", ownership: "self", content: "<img src=x onerror=bad()>",
    annotation: "", origin_actor: "codex", origin: "system_candidate", last_interaction_at: 1, current_version: 1 }];
  fixture.garden.counts.passing = 1;
  el("garden-refresh").click(); await flush();
  assert.match(el("garden-items").textContent, /codex/);
  assert.match(el("garden-items").textContent, /archived/);
  assert.equal(el("garden-items").querySelector("img"), null);
  fixture.gardenDetail.versions = [{ version: 1, created_at: 1, actor: "codex", origin: "system_candidate", status: "archived", content: "Original suggestion", rationale: "Provisional" }];
  fixture.gardenDetail.responses = [{ reaction: "choose", status: "complete", observation: {
    origin: "codex_extraction", status: "retracted", interpretation: "Uncertain", uncertainty: "Needs review",
    evidence: [{ message_id: "m2", display_name: "Hannah", attribution: "synchronized", source_matches: false, quote: "<script>untrusted</script>" }] } }];
  el("garden-items").querySelector("button").click(); await flush();
  assert.equal(el("garden-dialog").open, true);
  assert.match(el("garden-versions").textContent, /Original suggestion/);
  assert.match(el("garden-reactions").textContent, /retracted/);
  assert.match(el("garden-reactions").textContent, /Source text changed/);
  assert.equal(el("garden-dialog").querySelector("script"), null);
  el("garden-reactions").querySelector("button").click(); await flush();
  assert.equal(el("detail-dialog").open, true);
  el("detail-dialog").close();
  el("garden-correction-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  assert.match(el("garden-detail-error").textContent, /reason is required/);
  assert.equal(fixture.calls.some(c => c.options.method === "POST"), false);
  el("garden-annotation").value = "Potential duplicate, please review";
  el("garden-correction-reason").value = "Reviewed the source";
  fixture.losePost = true;
  el("garden-correction-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  assert.match(el("garden-detail-error").textContent, /Retry preserves/);
  assert.equal(el("garden-annotation").disabled, true, "Unknown outcome freezes the original payload");
  el("garden-correction-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  const posts = fixture.calls.filter(c => c.options.method === "POST");
  assert.equal(posts.length, 2);
  const a = JSON.parse(posts[0].options.body), b = JSON.parse(posts[1].options.body);
  assert.deepEqual(a, b);
  assert.equal(a.payload.expected_version, 1);
  assert.equal(a.payload.annotation, "Potential duplicate, please review");
  assert.equal(posts[0].url, "/api/v1/garden/corrections");
  assert.equal(el("garden-annotate").disabled, true);
  el("garden-close").click();
  assert.equal(w.document.activeElement, el("garden-items").querySelector("button"));
});

test("garden filters and pagination use bounded reads and preserve the selected tab", async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  el("tab-operations").click();
  el("garden-layer").value = "roots";
  el("garden-layer").dispatchEvent(new w.Event("change")); await flush();
  assert.match(el("garden-status").textContent, /No items match/);
  assert.match(fixture.calls.at(-1).url, /layer=roots/);
  el("garden-layer").value = "passing";
  fixture.garden.has_more = true; fixture.garden.next_after = "seed1";
  el("garden-refresh").click(); await flush();
  assert.equal(el("garden-more").hidden, false);
  el("garden-more").click(); await flush();
  assert.match(fixture.calls.at(-1).url, /after=seed1/);
  assert.equal(el("view-operations").hidden, false);
});

test("Kin scheduling removes daily controls and invitation actions globally", async (t) => {
  const { fixture, el, flush } = await dashboard(t);
  el("tab-schedule").click();
  assert.equal(el("schedule-heading"), null);
  assert.equal(el("schedule-form"), null);
  assert.equal(el("shared-schedule-controls"), null);
  assert.ok(el("quiet-controls"));
  assert.equal(el("discovery-panel"), null);
  fixture.exploration.retired = true;
  fixture.exploration.sessions = [{ id: "old", status: "observed", events: [], responses: [] }];
  el("exploration-refresh").click(); await flush();
  assert.equal(el("exploration-start").hidden, true);
  assert.match(el("exploration-status").textContent, /retired/);
  assert.ok(![...el("exploration-sessions").querySelectorAll("button")].some(b => b.textContent === "Interpret response"));
  assert.ok(!fixture.calls.some(c => c.url === "/api/v1/schedule"));
  assert.ok(!fixture.calls.some(c => c.options.method === "POST"));
});

test("shared room displays its Kin choice and keeps quiet hours and overrides", async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  fixture.encounters.mode = "shared";
  fixture.encounters.choice = { status: "pending", actor: "participant", due_at: 1800000000 };
  fixture.snapshot.relay.paused = true;
  await w.testApp.refreshAll(); await flush();
  assert.equal(el("encounter-controls").hidden, false);
  assert.equal(el("session-end").hidden, false);
  assert.match(el("encounter-status").textContent, /Kin-chosen/);
});


test("encounter rejection leaves controls available for a corrected request", async (t) => {
  const { fixture, el, flush } = await dashboard(t);
  fixture.postError = "Invalid encounter";
  el("encounter-cancel").click(); await flush();
  assert.equal(el("encounter-cancel").disabled, false);
  assert.equal(el("encounter-retry").hidden, true);
  assert.match(el("encounter-result").textContent, /rejected/);
  fixture.postError = null;
  el("encounter-cancel").click(); await flush();
  assert.equal(fixture.commands.length, 1);
});

test("shared recovery requires explicit confirmation, retries one request and stays paused", async (t) => {
  const { w, fixture, el, flush, refresh } = await dashboard(t);
  fixture.snapshot.relay.paused = true;
  fixture.encounters.mode = "shared";
  fixture.encounters.session = { id: "session", status: "active", message_count: 2 };
  fixture.encounters.recovery = { eligible: true, session_id: "session", turn_id: "turn", reason: "An interrupted relay generation." };
  await refresh(); await flush();
  assert.equal(el("session-recovery").hidden, false);
  assert.equal(el("session-recover").disabled, false);
  let confirmations = 0;
  w.confirm = text => { confirmations++; assert.match(text, /does not reset the Kindroid group/); return false; };
  el("session-recover").click(); await flush();
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
  w.confirm = () => { confirmations++; return true; };
  fixture.losePost = true;
  el("session-recover").click(); el("session-recover").click(); await flush();
  assert.equal(confirmations, 2);
  assert.equal(fixture.commands.length, 1);
  assert.equal(el("encounter-retry").hidden, false);
  el("encounter-retry").click(); await flush();
  const posts = fixture.calls.filter(c => c.options.method === "POST");
  assert.equal(posts.length, 2);
  assert.ok(posts.every(c => c.url.endsWith("recover-shared")));
  const first = JSON.parse(posts[0].options.body), second = JSON.parse(posts[1].options.body);
  assert.equal(first.idempotency_key, second.idempotency_key);
  assert.deepEqual(first.payload, { session_id: "session", turn_id: "turn", confirm: true });
  fixture.commands[0].status = "completed";
  fixture.commands[0].result = { outcome: "recovered", paused: true };
  fixture.encounters.recovery.eligible = false;
  fixture.encounters.recovery.reason = "This session was already recovered.";
  await refresh(); await flush();
  assert.match(el("encounter-result").textContent, /Resume, then Start conversation/);
  assert.equal(el("session-recover").disabled, true);
  assert.equal(fixture.snapshot.relay.paused, true);
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 2);
});

test("shared recovery eligibility and blocked outcome cannot look like success", async (t) => {
  const { w, fixture, el, flush, refresh } = await dashboard(t);
  assert.equal(el("session-recovery").hidden, true);
  fixture.snapshot.relay.paused = true;
  fixture.encounters.mode = "shared";
  fixture.encounters.recovery = { eligible: false, reason: "Inspect uncertain outgoing delivery." };
  await refresh(); await flush();
  assert.equal(el("session-recover").disabled, true);
  assert.match(el("session-recovery-note").textContent, /uncertain outgoing/);
  fixture.commands = [{ id: "blocked-recovery", command_type: "recover_shared", status: "completed", requested_at: stamp,
    result: { outcome: "recovery_blocked", reason: "History allowance exhausted." } }];
  await refresh(); await flush();
  assert.match(el("command-status").textContent, /blocked/);
  assert.match(el("command-status").className, /warning/);
  assert.match(el("encounter-result").textContent, /Recovery blocked/);
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
});

test("axe scans dashboard and each confirmation, memory, exploration and garden dialog", async (t) => {
  const { w, el, flush, fixture } = await dashboard(t);
  w.setTimeout = setTimeout;
  w.clearTimeout = clearTimeout;
  // Feed the real CSS locally. Axe's default preload can otherwise issue XHRs
  // to the fixture URL (possibly a live relay) and wait 10 seconds per scan.
  const style = w.document.createElement("style");
  style.textContent = fs.readFileSync(path.join(assets, "styles.css"), "utf8");
  w.document.querySelector('link[rel="stylesheet"]').replaceWith(style);
  let networkAttempts = 0;
  w.XMLHttpRequest = class {
    constructor() { networkAttempts += 1; throw new Error("Accessibility tests must stay offline"); }
  };
  w.eval(axe.source);
  const scan = async (context = w.document) => {
    const result = await w.axe.run(context, { preload: false, rules: { "color-contrast": { enabled: false } } });
    assert.equal(networkAttempts, 0);
    assert.equal(result.violations.length, 0, JSON.stringify(result.violations.map((item) => ({ id: item.id, nodes: item.nodes.map((node) => node.target) }))));
  };
  fixture.exploration.enabled = true;
  await scan();
  fixture.encounters.mode = "shared";
  fixture.encounters.recovery = { eligible: true, session_id: "session", turn_id: "turn", reason: "End the interrupted relay session without changing the Kindroid group." };
  fixture.snapshot.relay.paused = true;
  await w.testApp.refreshAll(); await flush();
  assert.equal(el("session-recovery").hidden, false);
  await scan();
  el("tab-schedule").click();
  assert.ok(el("quiet-controls").querySelector('form'));
  await scan(el("view-schedule"));
  el("tab-operations").click();
  el("exploration-panel").open = true;
  el("exploration-refresh").click(); await flush();
  await scan();
  el("approval-list").querySelector("button").click(); await flush();
  el("execute-approval").click();
  // The unchanged page was checked above; scan each newly exposed dialog.
  await scan(el("confirm-dialog"));
  el("confirm-dialog").close();
  el("approval-dialog").close();
  el("message-list").querySelector("button").click();
  await scan(el("memory-dialog"));
  el("memory-dialog").close();
  // Review-only historical records still use this confirmation dialog.
  el("exploration-dialog").showModal();
  await scan(el("exploration-dialog"));
  el("exploration-dialog").close();
  fixture.garden.items = [{ id: "seed1", layer: "passing", status: "active", ownership: "self", content: "Seed", origin_actor: "codex", origin: "system_candidate", last_interaction_at: 1, current_version: 1 }];
  el("garden-panel").open = true;
  el("garden-refresh").click(); await flush();
  el("garden-items").querySelector("button").click(); await flush();
  await scan(el("garden-dialog"));
});
