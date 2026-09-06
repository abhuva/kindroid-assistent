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
            : url.endsWith("trigger-initiative") ? "trigger_initiative" : "start_conversation",
          approval_id: url.includes("approvals") ? "a1" : null, status: "queued", requested_at: stamp, error: null };
        fixture.commands.unshift(command);
      }
      if (fixture.losePost) { fixture.losePost = false; throw new TypeError("lost response"); }
      return response(command, 202);
    }
    if (url === "/api/v1/snapshot") return response(fixture.snapshot);
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
  for (const name of ["api-client.js", "ui-model.js", "app.js"]) {
    w.eval(fs.readFileSync(path.join(assets, name), "utf8") + (name === "app.js" ? "\nwindow.testApp = { refreshAll, schedulePoll };" : ""));
  }
  const flush = async () => { for (let i = 0; i < 5; i++) await tick(); };
  await flush();
  return { w, fixture, timers, flush, el: (id) => w.document.getElementById(id), refresh: () => w.testApp.refreshAll() };
}

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
  assert.match(el("trigger-initiative-status").textContent, /Check-in sent; Hannah did not take/);
  assert.match(el("trigger-initiative-status").className, /success/);
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
  el("refresh-log").click(); await flush();
  el("refresh-log").click(); await flush();
  assert.equal(el("log-tail").textContent, "<b>safe text</b>");
  assert.equal(fixture.calls.filter((item) => item.url.includes("log-tail")).at(-1).options.headers["If-None-Match"], '"log-1"');
});

test("approval conflicts remain visible inside confirmation and rejection queues only on final confirmation", async (t) => {
  const { fixture, el, flush, refresh } = await dashboard(t);
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

test("axe scans actual dashboard and confirmation markup", async (t) => {
  const { w, el, flush } = await dashboard(t);
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
  const scan = async () => {
    const result = await w.axe.run(w.document, { preload: false, rules: { "color-contrast": { enabled: false } } });
    assert.equal(networkAttempts, 0);
    assert.equal(result.violations.length, 0, JSON.stringify(result.violations.map((item) => ({ id: item.id, nodes: item.nodes.map((node) => node.target) }))));
  };
  await scan();
  el("approval-list").querySelector("button").click(); await flush();
  el("execute-approval").click();
  await scan();
});
