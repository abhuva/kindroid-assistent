# Local Dashboard Architecture

**Status:** Accepted direction for the next implementation pass  
**Date:** 2026-09-05

## Scope

The dashboard is a localhost-only operational console served by the same operating
system process that runs the relay. It reads durable relay state and submits
commands to the relay worker. It does not perform Kindroid or Codex operations in
HTTP request threads.

The first version covers existing behavior only. Garden state, self-change,
configuration editing, and automatic prompt modification are not part of the first
dashboard release.

## Process topology

```text
Browser on this machine
        |
        | HTTP/JSON on 127.0.0.1
        v
Web server and static UI
        |                    \
        | read connections    \ append command/event
        v                      v
                  SQLite in WAL mode
                            ^
                            | worker-owned connection
                            |
                     Relay worker thread
                            |
                  Kindroid <-> Codex <-> tools
```

One process owns the process lock for its entire lifetime. The web server and
relay worker have separate SQLite connections. Only the relay worker owns the
long-lived `KindroidClient`, Codex thread lifecycle, persona switching, outbound
delivery, and execution of approved actions.

## Technology choices

- Python remains the application language.
- FastAPI provides explicit JSON contracts and testable request handling.
- Uvicorn hosts the local HTTP application.
- The UI uses repository-local HTML, CSS, and vanilla JavaScript with no Node build
  step and no third-party CDN content.
- SQLite remains the only database and continues using WAL mode.
- Initial live updates use two-second polling. Server-Sent Events may replace or
  supplement polling later; WebSockets are not required.

These choices favor a maintainable personal local service over hypothetical cloud
scale.

## Runtime lifecycle

Add a new command:

```powershell
kindroid-relay serve
```

`serve` performs the following in order:

1. Load and validate configuration without logging secrets.
2. Create runtime directories and configure rotating logs.
3. Acquire the existing process lock.
4. Apply database migrations.
5. Mark commands left `running` by a prior crash as `interrupted`.
6. Start the relay worker with its own state and API connections.
7. Start the HTTP server bound to the configured loopback address.
8. Print the local dashboard address.
9. On Ctrl+C, stop accepting commands, wake and stop the worker at its next safe
   boundary, close clients and database connections, then release the lock.

The existing `run` command remains as a headless recovery and diagnostic mode.
One-shot CLI commands retain their current behavior when the server is not running.
When `serve` owns the lock, mutating CLI commands should explain that the dashboard
or HTTP command path must be used instead of merely reporting a generic lock error.

## Concurrency rule

All operations with external side effects are serialized by one worker. HTTP
handlers may:

- read through short-lived or request-scoped SQLite connections;
- enqueue a command in a short transaction;
- set the local pause flag in a short transaction;
- signal the worker's in-process wake event.

HTTP handlers may not call `Relay`, `KindroidClient`, or `CodexAgent` directly.

The worker replaces the outer polling `sleep` with an interruptible event wait.
Internal waits required by Kindroid rate limits and response discovery remain
synchronous in the first release. The UI reports commands as queued while the
worker is inside such a wait.

Pause prevents the next safe unit of work; it cannot recall an HTTP request already
sent to Kindroid or cancel an already-running Codex execution.

## Persistent command model

Add a `commands` table with at least:

| Field | Purpose |
|---|---|
| `id` | Random stable identifier |
| `command_type` | Allow-listed command discriminator |
| `payload_json` | Validated, command-specific input |
| `idempotency_key` | Prevent accidental duplicate button submissions |
| `status` | `queued`, `running`, `completed`, `failed`, `interrupted`, `cancelled` |
| `requested_at` | Creation timestamp |
| `started_at` | Claim timestamp |
| `completed_at` | Terminal timestamp |
| `result_json` | Sanitized command result |
| `error` | Sanitized failure summary |

The database atomically claims the oldest queued command. At most one command may
be running. A unique index on `idempotency_key` makes retrying a browser request
safe.

Initial commands are:

- `start_conversation`
- `trigger_initiative`
- `resume`
- `execute_approval`, with approval ID and `post_result`
- `reject_approval`, with approval ID

Pause is recorded as an activity event and applied immediately because it is a
local safety control. It also wakes the worker. Resume may be queued so it cannot
race with recovery from an interrupted operation.

If the process dies with a command in `running`, the next startup marks it
`interrupted`; it is never automatically retried. The relay is paused when the
interrupted command might have performed an uncertain external side effect.

## Structured activity model

Add an `activity_events` table:

| Field | Purpose |
|---|---|
| `id` | Monotonic cursor for polling |
| `kind` | Stable machine-readable event type |
| `severity` | `debug`, `info`, `warning`, or `error` |
| `summary` | Safe human-readable text |
| `details_json` | Allow-listed, sanitized details |
| `correlation_id` | Related command, message, outbox, or approval ID |
| `created_at` | UTC timestamp |

Important lifecycle changes write structured activity in the same transaction when
possible. Raw application logs remain available for diagnostics but are not the
canonical dashboard data source. Neither structured events nor raw logs may contain
credentials, authorization headers, browser cookies, environment dumps, or raw
hidden prompts.

The worker updates a heartbeat timestamp and an explicit state such as `idle`,
`polling`, `waiting_for_kindroid`, `running_codex`, `sending`, `paused`, or
`stopping`. The dashboard marks it stale when the heartbeat exceeds a configured
threshold, while recognizing that synchronous external calls may temporarily delay
heartbeats.

## HTTP interface

All application endpoints live under `/api/v1`. Exact response schemas are typed
and stable. Timestamps are transferred as UTC ISO-8601 strings and displayed in
the browser's local timezone.

### Read endpoints

- `GET /api/v1/snapshot`: relay, worker, schedule, quota, outbox, approval, and
  active-command summary.
- `GET /api/v1/activity?after_id=&limit=`: ordered structured activity.
- `GET /api/v1/messages?before=&limit=`: recent synchronized group messages.
- `GET /api/v1/approvals?status=`: approval summaries.
- `GET /api/v1/approvals/{id}`: full proposal and execution history.
- `GET /api/v1/commands/{id}`: command status and sanitized result.
- `GET /api/v1/diagnostics/log-tail?lines=`: bounded, redacted tail of the configured
  application log; the path is never supplied by the browser.
- `GET /health/live`: process health only.
- `GET /health/ready`: database migrated, worker started, and configuration usable.

### Mutation endpoints

- `POST /api/v1/commands/start-conversation`
- `POST /api/v1/commands/trigger-initiative`
- `POST /api/v1/commands/resume`
- `POST /api/v1/control/pause`
- `POST /api/v1/approvals/{id}/execute`
- `POST /api/v1/approvals/{id}/reject`

Enqueued operations return HTTP 202 with `command_id`, `status`, and whether an
existing command was returned for the same idempotency key. Validation conflicts
return HTTP 409 with a safe reason. Missing items return 404. API failures use a
consistent error object and never return tracebacks to the browser.

## First user interface

The first dashboard is a single responsive page with:

1. **Status header:** running/paused/degraded state, worker activity, last
   heartbeat, and active error.
2. **Controls:** Start conversation, Trigger check-in, Pause, and Resume. Buttons
   disable when their operation is invalid or already queued.
3. **Schedule and quota:** next initiative, last initiative result, quiet hours,
   history reads in the rolling 24-hour window, and configured maximum.
4. **Current activity:** active command, current conversation turn count and limit,
   and current worker phase.
5. **Activity feed:** sanitized structured events with time, severity, and related
   entity links.
6. **Messages:** recent group messages with stored speaker/display attribution and
   clear Codex prefix fallback indication.
7. **Approvals:** pending proposals, risk, instruction, review, reject, and explicit
   execute confirmation including the selected sandbox and post-result option.
8. **Delivery health:** uncertain outbox items are prominent and explain why the
   relay paused.
9. **Diagnostics:** a collapsible, bounded, redacted raw-log tail for failures that
   are not fully represented by structured activity.

The UI must support keyboard navigation, visible focus, screen-reader labels,
high-contrast status states that do not rely on color alone, responsive narrow
layouts, and a clear stale/disconnected state.

The page polls the snapshot and activity endpoints every two seconds while visible,
backs off after failures, and performs an immediate refresh after commands. It
never assumes that a successfully submitted command has completed.

## Local security

- Default and production-supported binding is `127.0.0.1`. A non-loopback host is
  rejected unless a future, separately designed remote-access mode is implemented.
- The server validates the `Host` header against its configured loopback host and
  port.
- CORS is disabled; unexpected `Origin` values are rejected.
- The HTML response contains a random per-run CSRF nonce. Mutation requests must
  include it in a custom header. Same-origin policy prevents unrelated sites from
  reading the nonce, and the custom header prevents simple cross-origin form posts.
- All mutation routes accept JSON only and enforce small body limits.
- A strict Content Security Policy permits only repository-local scripts and
  styles. No CDN resources are used.
- Responses use `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and a
  same-origin resource policy.
- Secrets and complete environment/configuration values are never exposed.
- Approval execution requires a review modal showing the exact stored proposal,
  risk, effective sandbox, and whether a result will be posted back to Kindroid.
- The threat model assumes other local operating-system processes are trusted. The
  CSRF mechanism protects against hostile web pages, not local malware.

## Configuration

Add settings with safe defaults:

```text
RELAY_WEB_HOST=127.0.0.1
RELAY_WEB_PORT=8765
RELAY_WEB_OPEN_BROWSER=false
RELAY_WEB_POLL_SECONDS=2
RELAY_WORKER_HEARTBEAT_SECONDS=5
RELAY_WORKER_STALE_SECONDS=20
RELAY_LOG_MAX_BYTES=5242880
RELAY_LOG_BACKUP_COUNT=5
```

Host validation must reject wildcard or non-loopback bindings. Port conflicts fail
startup clearly before the worker begins automatic activity.

## Failure behavior

- **Port unavailable:** fail startup without starting the worker.
- **Database migration failure:** fail startup and leave the previous database
  untouched when possible.
- **Web server failure after worker start:** request a controlled worker shutdown;
  do not leave an invisible background relay unintentionally.
- **Worker failure:** keep the dashboard available in degraded mode, record the
  traceback only in local logs, show a sanitized error, and require explicit
  recovery.
- **Browser disconnect:** no effect on worker execution; commands remain durable.
- **Duplicate button submission:** return the existing command by idempotency key.
- **Interrupted command:** do not retry automatically; show it prominently and
  pause when delivery or external side effects are uncertain.
- **Uncertain Kindroid delivery:** preserve the existing outbox behavior and pause.
- **Malformed stored JSON:** quarantine the affected command/event, record an
  error, and continue serving unrelated status where safe.

## Test strategy

- Repository tests for command state transitions, atomic claiming, idempotency,
  crash recovery, event ordering, pagination, and redaction.
- HTTP contract tests with FastAPI's test client.
- Worker tests with fake Kindroid and Codex adapters proving all side effects remain
  serialized.
- Concurrency tests proving dashboard reads and command inserts work while the
  worker connection is active.
- Security tests for Host, Origin, CSRF, content type, CSP, and accidental secret
  exposure.
- End-to-end tests using fake adapters and a real temporary SQLite database.
- Manual Windows smoke tests for startup, browser controls, Ctrl+C, port conflict,
  restart after interruption, and stale heartbeat display.

## Explicitly deferred

- Remote browser access and multi-user authentication.
- Editing environment configuration from the browser.
- Editing prompt files from the browser.
- Garden and Self Studio views.
- The formal Hannah self-change confirmation protocol.
- WebSockets or a JavaScript framework.
- Multiple simultaneous relay workers.
