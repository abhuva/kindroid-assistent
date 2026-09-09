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
            : url.endsWith("trigger-initiative") ? "trigger_initiative" : "start_conversation",
          approval_id: url.includes("approvals") ? "a1" : null, status: "queued", requested_at: stamp, error: null };
        fixture.commands.unshift(command);
      }
      if (fixture.losePost) { fixture.losePost = false; throw new TypeError("lost response"); }
      return response(command, 202);
    }
    if (url === "/api/v1/snapshot") return response(fixture.snapshot);
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
  for (const name of ["api-client.js", "ui-model.js", "app.js", "memory.js", "exploration.js", "garden.js", "schedule.js", "discovery.js"]) {
    w.eval(fs.readFileSync(path.join(assets, name), "utf8") + (name === "app.js" ? "\nwindow.testApp = { refreshAll, schedulePoll };" : ""));
  }
  const flush = async () => { for (let i = 0; i < 5; i++) await tick(); };
  await flush();
  return { w, fixture, timers, flush, el: (id) => w.document.getElementById(id), refresh: () => w.testApp.refreshAll() };
}

test("schedule reallocates overnight and multiple dead blocks and preserves draft on refresh", async (t) => {
  const { w, el, flush, fixture } = await dashboard(t);
  el("tab-schedule").click();
  assert.equal(el("view-schedule").hidden, false);
  assert.equal(el("schedule-timeline").children.length, 24);
  assert.match(el("schedule-counts").textContent, /24 hourly ticks.*10 check-ins \/ 14 exploration/);
  el("schedule-add-dead").click();
  assert.match(el("schedule-counts").textContent, /16 hourly ticks.*6 check-ins \/ 10 exploration/);
  assert.match(el("schedule-dead-blocks").textContent, /22:00–06:00 \(8h\)/);
  assert.equal(el("schedule-timeline").children[22].getAttribute("aria-pressed"), "true");
  el("schedule-dead-start").value = "12:00"; el("schedule-dead-end").value = "14:00";
  el("schedule-add-dead").click();
  assert.equal(el("schedule-dead-blocks").children.length, 2);
  assert.match(el("schedule-counts").textContent, /14 hourly ticks/);
  el("schedule-refresh").click(); await flush();
  assert.match(el("schedule-counts").textContent, /14 hourly ticks.*Unsaved preview/);
  el("schedule-timeline").children[12].click();
  assert.match(el("schedule-counts").textContent, /15 hourly ticks/);
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
  el("schedule-discard").click();
  assert.match(el("schedule-counts").textContent, /24 hourly ticks/);
  el("schedule-balance").value = "25";
  el("schedule-balance").dispatchEvent(new w.Event("input"));
  assert.equal(el("schedule-exploration").value, "75");
  assert.match(el("schedule-counts").textContent, /6 check-ins \/ 18 exploration/);
});

test("schedule save survives a lost response, locks the draft and follows worker completion", async (t) => {
  const { w, el, fixture, flush, refresh } = await dashboard(t);
  el("tab-schedule").click(); el("schedule-add-dead").click();
  fixture.losePost = true;
  const save = () => el("schedule-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  save(); await flush();
  assert.equal(el("schedule-fields").disabled, true);
  assert.equal(el("schedule-save").textContent, "Retry same save");
  save(); await flush();
  const posts = fixture.calls.filter(c => c.url === "/api/v1/settings/schedule");
  assert.equal(posts.length, 2);
  const body = JSON.parse(posts[0].options.body);
  assert.equal(body.idempotency_key, JSON.parse(posts[1].options.body).idempotency_key);
  assert.deepEqual(body.payload.dead_hours, [0, 1, 2, 3, 4, 5, 22, 23]);
  assert.equal(el("schedule-save").disabled, true);
  assert.match(el("schedule-command").textContent, /queued/);
  fixture.daily.config = body.payload; fixture.daily.configured = true;
  fixture.commands[0].status = "completed";
  fixture.commands[0].result = { revision: fixture.commands[0].id };
  await refresh(); await flush();
  assert.match(el("schedule-command").textContent, /Schedule saved/);
  assert.equal(el("schedule-fields").disabled, false);
  assert.doesNotMatch(el("schedule-counts").textContent, /Unsaved/);
});

test("tick buttons recalculate the preview and persist their selected interval", async (t) => {
  const { w, el, fixture, flush } = await dashboard(t);
  el("tab-schedule").click(); el("schedule-add-dead").click();
  const interval = n => w.document.querySelector(`[data-tick-minutes="${n}"]`);
  assert.equal(interval(60).getAttribute("aria-pressed"), "true");
  interval(15).click();
  assert.match(el("schedule-counts").textContent, /16 available hours.*64 ticks \(15 mins\).*26 check-ins \/ 38 exploration/);
  assert.equal(el("schedule-timeline").querySelectorAll(".schedule-tick").length, 96);
  assert.equal(el("schedule-timeline").classList.contains("dense"), true);
  assert.match(el("schedule-timeline").children[6].title, /06:15.*06:30.*06:45/);
  assert.equal(interval(15).getAttribute("aria-pressed"), "true");
  assert.equal(interval(60).getAttribute("aria-pressed"), "false");
  interval(30).click();
  assert.match(el("schedule-counts").textContent, /32 ticks \(30 mins\).*13 check-ins \/ 19 exploration/);
  assert.equal(el("schedule-timeline").querySelectorAll(".schedule-tick").length, 48);
  el("schedule-refresh").click(); await flush();
  assert.equal(interval(30).getAttribute("aria-pressed"), "true", "Polling preserves the interval draft");
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
  el("schedule-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  const post = fixture.calls.find(c => c.url === "/api/v1/settings/schedule");
  assert.equal(JSON.parse(post.options.body).payload.tick_minutes, 30);
  assert.equal(el("schedule-fields").disabled, true);
});

test("schedule explains limits and all-day exclusion without unsafe HTML", async (t) => {
  const { el, fixture, flush } = await dashboard(t);
  el("tab-schedule").click();
  assert.match(el("schedule-warnings").textContent, /Exploration limits are below/);
  fixture.daily.capabilities.exploration = false;
  fixture.daily.recent = [{ wall_slot: "2026-09-07T12", activity: "exploration", outcome: "<img src=x>", command_status: "failed" }];
  el("schedule-refresh").click(); await flush();
  assert.match(el("schedule-warnings").textContent, /Exploration is disabled/);
  assert.equal(el("schedule-history").querySelector("img"), null);
  el("schedule-dead-start").value = "00:00"; el("schedule-dead-end").value = "00:00";
  el("schedule-add-dead").click();
  assert.match(el("schedule-counts").textContent, /0 hourly ticks/);
  assert.match(el("schedule-warnings").textContent, /All hours are dead time/);
});

test("unknown schedule save can be retried after command polling already saw completion", async (t) => {
  const { w, el, fixture, flush, refresh } = await dashboard(t);
  el("tab-schedule").click(); el("schedule-add-dead").click();
  fixture.losePost = true;
  const save = () => el("schedule-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  save(); await flush();
  await refresh(); await flush();
  assert.equal(el("schedule-save").disabled, false, "Unknown response still offers its same-key retry");
  fixture.commands[0].status = "completed";
  await refresh(); await flush();
  save(); await flush();
  assert.equal(el("schedule-fields").disabled, false);
  assert.equal(el("schedule-save").textContent, "Save schedule");
  assert.match(el("schedule-command").textContent, /Schedule saved/);
  assert.equal(fixture.commands.length, 1);
});

test("exploration is gated, confirmed, idempotent, and renders source text safely", async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  el("tab-operations").click();
  el("exploration-refresh").click(); await flush();
  assert.equal(el("exploration-start").disabled, true);
  fixture.exploration.enabled = true;
  fixture.exploration.sessions = [{ id: "session1", mode: "explorer", status: "observed", budget: 1,
    version: "v1", seed: "seed", response_message_id: "m2", events: [{ actor: "hannah",
      origin: "conversational_observation", kind: "response_observed", details: {},
      observation: { evidence: [{ quote: "<img src=x onerror=bad()>", message_id: "m2" }] } }] }];
  fixture.exploration.candidates = [{ id: "seed1", status: "active", summary: "<script>bad()</script>",
    selection_kind: "adjacent", provider: "local-catalog", locator: "catalog:test" }];
  el("exploration-refresh").click(); await flush();
  assert.equal(el("exploration-start").disabled, false);
  assert.equal(el("exploration-panel").querySelector("img,script"), null);
  const src = el("exploration-sessions").querySelector("button");
  src.click(); await flush();
  assert.equal(el("detail-dialog").open, true);
  el("detail-dialog").close();
  el("exploration-start").click();
  assert.equal(el("exploration-dialog").open, true);
  assert.match(el("exploration-confirm-scope").textContent, /one optional response/);
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
  fixture.losePost = true;
  el("exploration-confirm-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  assert.equal(el("exploration-dialog").open, true);
  el("exploration-confirm-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  const posts = fixture.calls.filter(c => c.url === "/api/v1/commands/start-exploration");
  assert.equal(posts.length, 2);
  assert.equal(JSON.parse(posts[0].options.body).idempotency_key, JSON.parse(posts[1].options.body).idempotency_key);
  assert.match(el("exploration-command").textContent, /Queued/);
  el("exploration-candidates").querySelector("button").click();
  el("exploration-reason").value = "Not useful currently";
  el("exploration-confirm-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  const review = fixture.calls.find(c => c.url === "/api/v1/exploration/candidates/review");
  assert.deepEqual(JSON.parse(review.options.body).payload, { candidate_id: "seed1", status: "archived", reason: "Not useful currently" });
  fixture.exploration.budget.used = 3;
  el("exploration-refresh").click(); await flush();
  assert.equal(el("exploration-start").disabled, true);
});

test("curiosity status is separate from manual exploration and never enables scheduling from the browser", async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  el("tab-operations").click();
  fixture.exploration.enabled = true;
  fixture.exploration.curiosity = { enabled: false, hours: [10, 16], next_due_at: null,
    interval_seconds: 21600, jitter_seconds: 1800, quiet_hours: [1320, 480], idle_seconds: 1800,
    attempt_limit: 2, interpretation_limit: 4, budget: { attempts: 0, interpretations: 0 }, recent: [] };
  el("exploration-refresh").click(); await flush();
  assert.equal(el("exploration-start").disabled, false);
  assert.match(el("curiosity-status").textContent, /off; manual invitations only/);
  assert.match(el("curiosity-budget").textContent, /0\/2/);
  fixture.exploration.curiosity.enabled = true;
  fixture.exploration.curiosity.next_due_at = 1800000000;
  fixture.exploration.curiosity.recent = [{ scheduled_at: 1700000000, outcome: "recent_conversation", command_id: null },
    { scheduled_at: 1700003600, outcome: "failed_inspect_delivery", command_status: "failed", command_id: "<img src=x>" }];
  el("exploration-refresh").click(); await flush();
  assert.match(el("curiosity-status").textContent, /enabled.*local hours 10, 16/);
  assert.match(el("curiosity-history").textContent, /recent_conversation/);
  assert.equal(el("curiosity-history").querySelector("img"), null);
  assert.equal(fixture.calls.filter(c => c.options.method === "POST").length, 0);
  fixture.exploration.curiosity.hours = null;
  el("exploration-refresh").click(); await flush();
  assert.match(el("curiosity-status").textContent, /every 6h, jitter/);
  assert.match(el("curiosity-budget").textContent, /Requires 30m/);
  fixture.exploration.curiosity.shared_schedule = true;
  fixture.exploration.curiosity.idle_seconds = 0;
  el("exploration-refresh").click(); await flush();
  assert.match(el("curiosity-budget").textContent, /No conversation cooldown/);
  assert.doesNotMatch(el("curiosity-budget").textContent, /Requires .*m quiet/);
  assert.equal(el("view-operations").hidden, false);
});

test("existing exploration response can be interpreted without resending its invitation", async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  el("tab-operations").click();
  fixture.exploration.enabled = true;
  fixture.exploration.sessions = [{ id: "old-session", mode: "explorer", status: "observed", budget: 1,
    version: "v1", seed: "seed", response_message_id: "m2", responses: [], events: [] }];
  el("exploration-refresh").click(); await flush();
  const interpret = [...el("exploration-sessions").querySelectorAll("button")].find(b => b.textContent === "Interpret response");
  interpret.click();
  assert.match(el("exploration-confirm-scope").textContent, /without resending/);
  assert.equal(el("exploration-reason").required, false);
  el("exploration-confirm-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  const posts = fixture.calls.filter(c => c.options.method === "POST");
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url, "/api/v1/exploration/interpret");
  assert.equal(JSON.parse(posts[0].options.body).payload.session_id, "old-session");
  fixture.exploration.sessions[0].responses = [{ step: 0, status: "complete", classification: "alternative", note: "Acknowledgement sent",
    decision: { quote: "<img src=x onerror=bad()>", interpretation: "Provisional interest" }, message_id: "m2" }];
  fixture.exploration.candidates = [{ id: "alternative", status: "active", summary: "Tide pools", origin_actor: "hannah",
    selection_kind: "reported_alternative", provider: "response-observation", locator: "memory:source", responses: [{ reaction: "alternative", message_id: "m2" }] }];
  el("exploration-refresh").click(); await flush();
  assert.match(el("exploration-candidates").textContent, /Hannah's reported alternative/);
  assert.match(el("exploration-candidates").textContent, /not a permanent preference/);
  assert.equal(el("exploration-panel").querySelector("img"), null);
  assert.ok(![...el("exploration-sessions").querySelectorAll("button")].some(b => b.textContent === "Interpret response"));
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
      result: { sync_outcome: outcome, paused: true, retry_at: 1800000000 } }];
    await refresh(); await flush();
    assert.match(el("history-sync-status").textContent, expected);
    assert.match(el("history-sync-status").className, /warning/);
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

test("discovery saves opt-in without immediate work and retries the same unknown request", async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  el("tab-schedule").click(); el("discovery-panel").open = true;
  el("discovery-refresh").click(); await flush();
  assert.equal(el("discovery-follow").checked, false);
  el("discovery-follow").checked = true;
  w.document.querySelector('[name="discovery-source"][value="creative"]').checked = true;
  el("discovery-form").dispatchEvent(new w.Event("input", { bubbles: true }));
  fixture.losePost = true;
  el("discovery-form").dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  assert.equal(el("discovery-fields").disabled, true);
  assert.equal(el("discovery-retry").hidden, false);
  el("discovery-retry").click(); await flush();
  const posts = fixture.calls.filter(c => c.options.method === "POST");
  assert.equal(posts.length, 2);
  assert.equal(posts[0].options.body, posts[1].options.body);
  assert.equal(JSON.parse(posts[0].options.body).payload.follow_through, true);
  assert.ok(JSON.parse(posts[0].options.body).payload.sources.includes("creative"));
  assert.equal(fixture.commands.length, 1);
  fixture.commands[0].status = "completed";
  w.document.dispatchEvent(new w.CustomEvent("relay-command", { detail: fixture.commands[0] })); await flush();
  assert.equal(el("discovery-fields").disabled, false);
});

test("discovery keeps unsafe sources inert and public-topic approvals explicit", async (t) => {
  const { w, fixture, el, flush } = await dashboard(t);
  fixture.discovery.experiences = [{ session_id: "session1", status: "needs_public_topic", topic: "", note: "<script>bad()</script>",
    sources: [{ title: "<img src=x>", excerpt: "Public source text", url: "javascript:alert(1)", retrieved_at: 1 }] }];
  el("tab-schedule").click(); el("discovery-panel").open = true;
  el("discovery-refresh").click(); await flush();
  assert.equal(el("discovery-experiences").querySelector("script, img, a"), null);
  const form = el("discovery-experiences").querySelector("form");
  form.querySelector("input").value = "Tide pools";
  form.dispatchEvent(new w.Event("submit", { cancelable: true })); await flush();
  const post = fixture.calls.find(c => c.options.method === "POST");
  assert.equal(post.url, "/api/v1/discovery/public-topic");
  assert.deepEqual(JSON.parse(post.options.body).payload, { session_id: "session1", topic: "Tide pools" });
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
  el("tab-schedule").click();
  w.document.querySelector('[data-tick-minutes="15"]').click();
  el("discovery-panel").open = true;
  el("discovery-refresh").click(); await flush();
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
  el("exploration-start").click();
  await scan(el("exploration-dialog"));
  el("exploration-dialog").close();
  fixture.garden.items = [{ id: "seed1", layer: "passing", status: "active", ownership: "self", content: "Seed", origin_actor: "codex", origin: "system_candidate", last_interaction_at: 1, current_version: 1 }];
  el("garden-panel").open = true;
  el("garden-refresh").click(); await flush();
  el("garden-items").querySelector("button").click(); await flush();
  await scan(el("garden-dialog"));
});
