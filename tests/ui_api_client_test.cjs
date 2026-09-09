"use strict";

const assert = require("node:assert/strict");
const {
  ApiError,
  RelayApiClient,
} = require("../src/kindroid_relay/web/static/api-client.js");
const {
  commandStatusView,
  pollDelay,
  quotaView,
  relayStatusView,
  scheduleView,
} = require("../src/kindroid_relay/web/static/ui-model.js");

const success = (payload = {}) => ({
  ok: true,
  status: 200,
  json: async () => payload,
});

async function testHeadersAndIdempotentNetworkRetry() {
  const calls = [];
  let attempt = 0;
  const client = new RelayApiClient({
    csrf: "csrf-test",
    createId: () => "stable-action-id",
    fetchImpl: async (path, options) => {
      calls.push({ path, options });
      if (options.method === "POST" && attempt++ === 0) {
        throw new TypeError("connection lost after send");
      }
      return success({ command_type: "start_conversation" });
    },
  });

  await client.request("/api/v1/snapshot");
  assert.equal(calls[0].options.headers["X-Relay-CSRF"], undefined);

  await assert.rejects(
    client.submit("/api/v1/commands/start-conversation"),
    /connection lost/,
  );
  await client.submit("/api/v1/commands/start-conversation");

  const firstBody = JSON.parse(calls[1].options.body);
  const retryBody = JSON.parse(calls[2].options.body);
  assert.equal(firstBody.idempotency_key, "stable-action-id");
  assert.equal(retryBody.idempotency_key, firstBody.idempotency_key);
  assert.equal(calls[1].options.headers["X-Relay-CSRF"], "csrf-test");
}

async function testHttpFailureStartsANewAction() {
  const keys = ["first-id", "second-id"];
  const bodies = [];
  let attempt = 0;
  const client = new RelayApiClient({
    csrf: "csrf-test",
    createId: () => keys.shift(),
    fetchImpl: async (_path, options) => {
      bodies.push(JSON.parse(options.body));
      if (attempt++ === 0) {
        return {
          ok: false,
          status: 409,
          json: async () => ({ message: "conflict" }),
        };
      }
      return success({});
    },
  });

  await assert.rejects(client.submit("/command"), ApiError);
  await client.submit("/command");
  assert.notEqual(bodies[0].idempotency_key, bodies[1].idempotency_key);
}

async function testObsoleteReadsAreAborted() {
  const client = new RelayApiClient({ csrf: "csrf-test", fetchImpl: async () => success() });
  const first = client.replaceRead("snapshot", (signal) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => {
      const error = new Error("obsolete read");
      error.name = "AbortError";
      reject(error);
    });
  }));
  const second = client.replaceRead("snapshot", async () => "new snapshot");

  assert.equal(await second, "new snapshot");
  await assert.rejects(first, (error) => error.name === "AbortError");
}

async function testTimeoutIsNotAnObsoleteRead() {
  const fs = require("node:fs");
  const vm = require("node:vm");
  let fireTimeout;
  const context = { AbortController, DOMException,
    setTimeout: (fn) => { fireTimeout = fn; return 1; }, clearTimeout() {} };
  vm.runInNewContext(fs.readFileSync(require.resolve("../src/kindroid_relay/web/static/api-client.js"), "utf8"), context);
  const client = new context.RelayApiClient({ csrf: "test", fetchImpl: (_path, { signal }) =>
    new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))) });
  const timedOut = client.request("/snapshot");
  fireTimeout();
  await assert.rejects(timedOut, (error) => error.name === "TimeoutError");
  const controller = new AbortController();
  const obsolete = client.request("/snapshot", { signal: controller.signal });
  controller.abort();
  await assert.rejects(obsolete, (error) => error.name === "AbortError");
}

function testStatusStatesAndPollingBackoff() {
  const snapshot = (phase, options = {}) => ({
    relay: { paused: Boolean(options.paused) },
    worker: phase ? { phase, stale: Boolean(options.stale) } : null,
  });
  assert.equal(relayStatusView(snapshot(null)).key, "stopped");
  assert.equal(relayStatusView(snapshot("stopping")).key, "stopped");
  assert.equal(relayStatusView(snapshot("idle", { stale: true })).key, "stale");
  assert.equal(relayStatusView(snapshot("idle", { paused: true })).key, "paused");
  assert.equal(relayStatusView(snapshot("degraded")).key, "degraded");
  assert.equal(relayStatusView(snapshot("running_codex")).key, "busy");
  assert.equal(relayStatusView(snapshot("idle")).key, "running");
  assert.equal(pollDelay(1000, 0), 1000);
  assert.equal(pollDelay(1000, 3), 8000);
  assert.equal(pollDelay(1000, 10), 30000);
}

function testCommandStatusStates() {
  const command = (status) => ({ command_type: "start_conversation", status });
  assert.equal(commandStatusView(null).pending, false);
  assert.equal(commandStatusView(command("queued")).pending, true);
  assert.equal(commandStatusView(command("running")).terminal, false);
  assert.equal(commandStatusView(command("completed")).tone, "success");
  assert.equal(commandStatusView(command("failed")).tone, "error");
  assert.equal(commandStatusView(command("cancelled")).terminal, true);
  assert.equal(commandStatusView(command("interrupted")).terminal, true);
  const checkIn = { command_type: "trigger_initiative", status: "completed" };
  assert.match(commandStatusView(checkIn).text, /outcome unavailable/);
  for (const outcome of ["paused", "disabled", "daily_budget", "pending_message", "empty_room", "latest_not_codex", "recent_conversation", "delivery_unresolved", "quiet_hours", "bootstrapped"]) {
    const view = commandStatusView({ ...checkIn, result: { initiative_outcome: outcome, check_in_sent: false } });
    assert.match(view.detail, /Skipped:/);
    assert.equal(view.tone, "warning");
  }
}

function testScheduleAndQuotaViews() {
  const interval = {
    schedule: {
      initiative_enabled: true,
      initiative_interval_seconds: 900,
      initiative_schedule_hours: null,
      initiative_quiet_hours: [0, 510],
    },
  };
  assert.equal(scheduleView(interval).basis, "Every 15 minutes after activity");
  assert.equal(scheduleView(interval).quietHours, "00:00–08:30 local");
  const fixed = {
    schedule: {
      ...interval.schedule,
      initiative_schedule_hours: [6, 18],
      initiative_quiet_hours: null,
    },
  };
  assert.equal(scheduleView(fixed).basis, "Fixed daily at 06:00, 18:00 local");
  assert.equal(scheduleView(fixed).quietHours, "None");
  assert.equal(
    scheduleView({ schedule: { ...interval.schedule, initiative_enabled: false } }).basis,
    "Disabled",
  );
  assert.equal(quotaView(79, 100).warning, false);
  assert.equal(quotaView(80, 100).warning, true);
  assert.match(quotaView(80, 100).note, /server-side count may differ/);
}

async function main() {
  await testHeadersAndIdempotentNetworkRetry();
  await testHttpFailureStartsANewAction();
  await testObsoleteReadsAreAborted();
  await testTimeoutIsNotAnObsoleteRead();
  testStatusStatesAndPollingBackoff();
  testCommandStatusStates();
  testScheduleAndQuotaViews();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
