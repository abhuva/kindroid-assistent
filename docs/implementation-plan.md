# Complete Implementation Plan

**Status:** Milestone 6 release acceptance pending; Milestones 6A/7, M8's observational foundation and M9.1 scheduled curiosity locally implemented under development-only exceptions; manual Root bootstrap, M9.2 and operator acceptance pending
**Date:** 2026-09-06

## How to use this plan

Every implementation task and subtask uses checklist syntax. Task IDs provide
stable dependency references. A task may start only when its stated dependencies
are complete unless the exception is documented in the task or pull request.

The plan deliberately separates operational infrastructure from agency behavior:

```text
Baseline
   -> durable commands and activity
   -> local dashboard
   -> agent context memory (Codex's understanding, not Hannah's identity)
   -> observational curiosity
   -> garden state
   -> deliberate confirmation
   -> prompt projection and broader capabilities
```

The dashboard UI milestone is implemented and locally verified. The next delivery
target is completing Milestone 6's Windows/browser, disposable-group, and release
audit gates; backup/integrity and fake-adapter host failure journeys are implemented.
Milestone 6A then adds agent context memory before exploration. Live baseline checks
remain explicitly deferred; no remote acceptance is inferred from mock tests.

## Global constraints

Check-in correction (2026-09-07): [ADR-015](adr/ADR-015-speaker-independent-check-ins.md)
replaces last-speaker eligibility with inactivity and unresolved-work checks. This
does not complete outstanding live acceptance or change curiosity scheduling.
Local verification: 82 targeted tests passed; the full rerun passed 368 tests in
54.65 seconds under the 60-second limit. Locked npm installation and the standalone
API-client/24 dashboard tests passed. The first full attempt reported a dashboard
failure then hit the overall timeout; its diagnostic was not emitted before
termination. The unchanged rerun passed. The existing Starlette/httpx deprecation
warning remains. No live service restart, message send, or configuration edit occurred.

Transport correction before further milestones (2026-09-06):
[ADR-008](adr/ADR-008-direct-replies-and-context-sync.md) records direct reply
capture, migration 3, remote-ID reconciliation, and explicit Codex context sync.
This does not complete the remaining live or Windows release acceptance checks.

- [ ] **G-001 Preserve current relay safety invariants.** (Depends on: none)
  - [ ] Keep message deduplication and source-message/outbox linking.
  - [ ] Keep uncertain outbound delivery non-retryable and pause the relay.
  - [ ] Keep bounded automatic conversation turns.
  - [ ] Keep local-only approval authority for consequential actions.
  - [ ] Keep persona restoration in a `finally` path around outbound delivery.
  - [ ] Keep Kindroid history reads within the configured rolling 24-hour budget.
- [ ] **G-002 Preserve user data and credentials.** (Depends on: none)
  - [ ] Never commit or display the real Kindroid API key.
  - [ ] Never expose Codex authentication, environment dumps, cookies, or headers.
  - [ ] Treat the existing worktree as user-owned and avoid unrelated rewrites.
  - [ ] Make every schema change upgrade the existing SQLite database in place.
  - [ ] Document backup and rollback before any remote Kindroid configuration write.
- [ ] **G-003 Maintain attribution boundaries.** (Depends on: none)
  - [ ] Keep Hannah, Marc, Codex, and system observations distinct in stored data.
  - [ ] Never infer external-action approval from group chat text.
  - [ ] Never present a Codex suggestion as a Hannah-originated preference.
  - [ ] Do not claim that behavioral persistence proves subjective experience.

---

## Milestone 0: Baseline and design freeze

### M0.1 Establish the current baseline

- [x] **BASE-001 Capture the existing test baseline.** (Depends on: G-001, G-002)
  - [x] Create a clean test environment from `pyproject.toml`.
  - [x] Run the complete current pytest suite.
  - [x] Record Python, `openai-codex`, SQLite, and operating-system versions used.
  - [x] Resolve existing failures before mixing them with dashboard changes.
- [ ] **BASE-002 Perform a current relay smoke test against the disposable group.** (Depends on: BASE-001)
  - [ ] Back up the current SQLite database and configuration file location.
  - [ ] Verify bootstrap does not replay old messages.
  - [ ] Verify a manual conversation starts and terminates correctly.
  - [ ] Verify persona activation and restoration behavior.
  - [ ] Verify pause, resume, initiative trigger, and approval listing.
  - [ ] Verify uncertain delivery behavior using mocks rather than forcing a real failure.
  - [ ] Record the expected terminal output without secrets or raw headers.
- [x] **BASE-003 Confirm dashboard acceptance criteria with Marc.** (Depends on: none)
  - [x] Confirm the dashboard binds only to this machine.
  - [x] Confirm the default port or select a different one.
  - [x] Confirm whether `serve` should open a browser automatically; default remains no.
  - [x] Confirm that approval execution remains included with an explicit review step.
  - [x] Confirm message bodies may be shown in the local dashboard.
  - [x] Confirm desired log retention; bounded redacted raw-log viewing is included in v1.
- [x] **BASE-004 Freeze v1 scope.** (Depends on: BASE-003)
  - [x] Include relay status, controls, schedule/quota, activity, messages, approvals,
    and delivery health.
  - [x] Exclude prompt editing, environment editing, garden state, and self-change.
  - [x] Exclude remote access, multi-user accounts, and cloud deployment.
  - [x] Record any changed scope in ADR-006 rather than silently expanding it.

### M0 exit criteria

- [ ] Current tests pass and the working relay behavior is recorded.
- [ ] V1 dashboard scope and local privacy expectations are confirmed.
- [ ] No implementation work has weakened the existing relay safety properties.

---

## Milestone 1: Database migrations and storage boundaries

### M1.1 Introduce explicit schema migration management

- [x] **DB-001 Add a schema version mechanism.** (Depends on: BASE-001)
  - [x] Choose a monotonically increasing integer schema version.
  - [x] Create a migration registry applied in one exclusive startup phase.
  - [x] Make each migration transactional where SQLite permits.
  - [x] Record migration start, success, and failure without including secrets.
  - [x] Reject startup when the database version is newer than the application.
  - [x] Retain compatibility with databases created by the current `_create_schema` path.
- [x] **DB-002 Add migration tests.** (Depends on: DB-001)
  - [x] Upgrade an empty database.
  - [x] Upgrade a fixture matching the current schema.
  - [x] Reopen an already-current database without modifying data.
  - [x] Simulate a failed migration and verify rollback.
  - [x] Verify message, outbox, approval, history, and initiative data survive upgrade.

### M1.2 Define connection ownership

- [x] **DB-003 Refactor state construction for multiple connections.** (Depends on: DB-001)
  - [x] Keep one worker-owned `RelayState` connection.
  - [x] Create request-scoped web read/write connections.
  - [x] Set WAL mode, foreign keys, busy timeout, and row factory consistently.
  - [x] Do not share a SQLite connection object between threads.
  - [x] Ensure every request-scoped connection is closed after use.
  - [x] Add bounded handling for `database is locked` errors.
- [x] **DB-004 Separate migrations from routine connection creation.** (Depends on: DB-003)
  - [x] Apply migrations once before worker and HTTP threads start.
  - [x] Prevent request-scoped connections from racing to mutate the schema.
  - [x] Preserve a safe one-shot CLI path when the server is not running.
- [x] **DB-005 Test concurrent storage access.** (Depends on: DB-003, DB-004)
  - [x] Hold the worker connection open while web connections read status.
  - [x] Insert commands while the worker reads and writes other tables.
  - [x] Verify short transactions do not block polling for an unacceptable duration.
  - [x] Verify connection objects are never used from the wrong thread.

### M1 exit criteria

- [x] Current data upgrades in place through a tested schema-version path.
- [x] Worker and web database ownership is explicit and concurrency-tested.

---

## Milestone 2: Durable commands and structured activity

### M2.1 Command domain

- [x] **CMD-001 Define command models and validation.** (Depends on: DB-001)
  - [x] Define allow-listed command types.
  - [x] Define typed payloads for start conversation, trigger initiative, resume,
    execute approval, and reject approval.
  - [x] Reject unknown payload fields and invalid approval IDs.
  - [x] Define command result and sanitized error structures.
  - [x] Define legal status transitions.
- [x] **CMD-002 Add the `commands` schema.** (Depends on: CMD-001, DB-001)
  - [x] Add IDs, type, JSON payload, idempotency key, status, timestamps, result, and error.
  - [x] Add a unique index for idempotency keys.
  - [x] Add indexes for queued order and recent command history.
  - [x] Add database constraints for recognized terminal and active statuses where practical.
- [x] **CMD-003 Implement command repository operations.** (Depends on: CMD-002, DB-003)
  - [x] Enqueue a validated command transactionally.
  - [x] Return the existing command when an idempotency key is repeated.
  - [x] Atomically claim the oldest queued command.
  - [x] Prevent two workers from claiming the same command.
  - [x] Mark completed, failed, interrupted, or cancelled with terminal timestamps.
  - [x] Query one command and paginate recent commands.
  - [x] Detect command conflicts such as two queued conversation starts.
- [x] **CMD-004 Define crash recovery.** (Depends on: CMD-003)
  - [x] Mark prior `running` commands `interrupted` at startup.
  - [x] Classify command types that may have produced uncertain external effects.
  - [x] Pause the relay after an interrupted side-effecting command.
  - [x] Never automatically retry an interrupted external command.
  - [x] Preserve queued commands unless an explicit incompatibility is detected.
- [x] **CMD-005 Test the command lifecycle.** (Depends on: CMD-003, CMD-004)
  - [x] Test every allowed and forbidden status transition.
  - [x] Test idempotent duplicate submission.
  - [x] Test atomic claim under concurrent connections.
  - [x] Test FIFO order.
  - [x] Test conflict detection.
  - [x] Test restart recovery and pause behavior.

### M2.2 Structured activity

- [x] **EVT-001 Define an allow-listed activity taxonomy.** (Depends on: BASE-004)
  - [x] Cover process lifecycle, worker state, polling, incoming messages, Codex runs,
    outbound delivery, initiative outcomes, commands, approvals, and errors.
  - [x] Define severity, safe summary, correlation ID, and allowed detail fields per event.
  - [x] Decide which verbose diagnostics remain raw-log-only.
- [x] **EVT-002 Add the `activity_events` schema and repository.** (Depends on: EVT-001, DB-001)
  - [x] Use a monotonic integer ID suitable for polling cursors.
  - [x] Store UTC occurrence time.
  - [x] Validate detail JSON against event-specific allow lists.
  - [x] Provide `after_id` pagination with an enforced maximum page size.
  - [x] Add retention configuration without deleting safety-critical audit records.
- [x] **EVT-003 Instrument existing relay operations.** (Depends on: EVT-002)
  - [x] Emit start, stop, pause, resume, and cycle events.
  - [x] Emit message-received and message-processed events without hidden context.
  - [x] Emit Codex-started, Codex-completed, and Codex-failed events.
  - [x] Emit outbound-created, sending, sent, uncertain, and identity-fallback events.
  - [x] Emit initiative due, skipped reason, offered, answered, and declined-turn events.
  - [x] Emit approval-created and approval-status events.
  - [x] Correlate events with message, outbox, approval, and command IDs.
- [x] **EVT-004 Add redaction and leakage tests.** (Depends on: EVT-003)
  - [x] Seed recognizable fake API keys, authorization values, cookies, and prompt secrets.
  - [x] Verify none appear in event summaries or details.
  - [x] Verify exception messages are sanitized before database storage.
  - [x] Verify raw HTTP response bodies are not automatically stored.

### M2.3 Worker status

- [x] **WORK-001 Define worker phases and heartbeat data.** (Depends on: EVT-001)
  - [x] Define `starting`, `idle`, `polling`, `waiting_for_kindroid`, `running_codex`,
    `sending`, `executing_approval`, `paused`, `degraded`, and `stopping`.
  - [x] Record phase start time, last heartbeat, active command, and sanitized note.
  - [x] Define the stale threshold separately from heartbeat cadence.
- [x] **WORK-002 Instrument worker phase transitions.** (Depends on: WORK-001, EVT-003)
  - [x] Ensure failure paths return to a valid degraded or paused state.
  - [x] Avoid claiming that the worker is idle while an external call is in flight.
  - [x] Document that synchronous calls can delay heartbeat updates.
- [x] **WORK-003 Test worker-state reporting.** (Depends on: WORK-002)
  - [x] Test successful phase sequences.
  - [x] Test exceptions at each external boundary.
  - [x] Test stale detection without relying on wall-clock sleeps.

### M2 exit criteria

- [x] Commands are durable, idempotent, serially claimable, and recoverable.
- [x] Important operations have safe structured activity and explicit worker state.

---

## Milestone 3: Combined server and relay worker

### M3.1 Refactor reusable command execution

- [x] **SVC-001 Extract application operations from CLI branching.** (Depends on: CMD-001)
  - [x] Create reusable operations for bootstrap, one cycle, initiative trigger,
    conversation start, pause, resume, approval execution, and rejection.
  - [x] Keep CLI output formatting outside application operations.
  - [x] Return typed results suitable for CLI and HTTP command completion.
  - [x] Preserve the current Codex sandbox selection and post-result behavior.
  - [x] Keep external operations synchronous and worker-owned.
- [x] **SVC-002 Adapt the CLI to the application operations.** (Depends on: SVC-001)
  - [x] Preserve command names and exit codes.
  - [x] Preserve readable error messages.
  - [x] Verify current CLI tests and manual behavior remain valid.

### M3.2 Worker runtime

- [x] **RUN-001 Implement an interruptible worker loop.** (Depends on: CMD-003, SVC-001, WORK-002)
  - [x] Replace the server worker's outer `time.sleep` with `threading.Event.wait(timeout)`.
  - [x] Wake for queued commands, scheduled initiative due times, pause changes, and shutdown.
  - [x] Continue honoring minimum Kindroid history intervals.
  - [x] Claim and execute one command at a time.
  - [x] Run normal polling only when no conflicting command is active.
  - [x] Check pause and shutdown at safe boundaries between conversation turns.
  - [x] Update command state and activity for success and failure.
- [x] **RUN-002 Implement command dispatch.** (Depends on: RUN-001)
  - [x] Map each command type to exactly one application operation.
  - [x] Revalidate approval status when execution begins, not only when submitted.
  - [x] Reject stale or incompatible commands with a clear result.
  - [x] Store only sanitized results.
  - [x] Keep the existing uncertain-delivery pause semantics.
- [x] **RUN-003 Implement immediate pause behavior.** (Depends on: RUN-001, DB-003)
  - [x] Apply pause through a short, separate transaction.
  - [x] Record who/what requested the pause and why.
  - [x] Wake the worker.
  - [x] Report whether work is in flight so the UI can show pause pending.
  - [x] Do not claim cancellation of already-sent Kindroid or Codex requests.
- [x] **RUN-004 Add worker integration tests.** (Depends on: RUN-002, RUN-003)
  - [x] Use fake Kindroid and Codex adapters through the complete application service.
  - [x] Prove commands execute serially.
  - [x] Prove duplicate conversation commands do not produce duplicate messages.
  - [x] Prove normal polling and manual commands cannot overlap external effects.
  - [x] Prove shutdown waits for or safely abandons the current boundary.
  - [x] Prove command failure does not terminate the dashboard host unexpectedly.

### M3.3 Process host

- [x] **HOST-001 Add server configuration.** (Depends on: BASE-003)
  - [x] Add loopback host, port, optional browser-open flag, UI poll interval,
    heartbeat cadence, stale threshold, and log rotation settings.
  - [x] Validate that the host is loopback-only.
  - [x] Validate port range and positive intervals.
  - [x] Add safe examples to `kindroid.env.example`.
- [x] **HOST-002 Implement the combined host lifecycle.** (Depends on: RUN-004, HOST-001, DB-004)
  - [x] Check port availability before starting automatic relay activity.
  - [x] Acquire the process lock once for the entire host lifetime.
  - [x] Apply migrations and interrupted-command recovery.
  - [x] Start the worker thread and verify it reaches a ready state.
  - [x] Start the HTTP server in the main process lifecycle.
  - [x] Coordinate Ctrl+C and programmatic shutdown.
  - [x] Close HTTP, Kindroid, Codex, SQLite, log, and thread resources deterministically.
  - [x] Release the process lock last.
- [x] **HOST-003 Add `kindroid-relay serve`.** (Depends on: HOST-002)
  - [x] Print the loopback dashboard address without embedding secrets.
  - [x] Optionally open the default browser only after readiness.
  - [x] Keep `run` as the headless mode.
  - [x] Improve mutating CLI lock errors to point to the running dashboard.
- [x] **HOST-004 Test lifecycle failures.** (Depends on: HOST-002)
  - [x] Port conflict fails before worker activity.
  - [x] Migration failure prevents startup.
  - [x] Worker startup failure keeps secrets out of terminal output.
  - [x] HTTP server failure requests worker shutdown.
  - [x] Ctrl+C on Windows terminates without an orphan thread or stale lock.
  - [x] A stale process lock remains recoverable under current rules.

### M3 exit criteria

- [x] `serve` runs one safely serialized relay worker and a responsive local host.
- [x] Browser requests cannot directly invoke Kindroid or Codex objects.

---

## Milestone 4: HTTP API and browser security

### M4.1 HTTP foundation

- [x] **WEB-001 Add pinned-compatible FastAPI and Uvicorn dependencies.** (Depends on: HOST-002)
  - [x] Set conservative version ranges compatible with Python 3.10+.
  - [x] Use the existing HTTPX test dependency with FastAPI's test client.
  - [x] Verify installation in a clean virtual environment.
- [x] **WEB-002 Create the application factory.** (Depends on: WEB-001, DB-003)
  - [x] Inject settings, database connection factory, wake event, and runtime status.
  - [x] Avoid module-level mutable global state.
  - [x] Add centralized safe error handling.
  - [x] Return JSON error objects without tracebacks.
- [x] **WEB-003 Define typed API schemas.** (Depends on: WEB-002, CMD-001)
  - [x] Define snapshot, activity, message, approval, command, and error responses.
  - [x] Define mutation request bodies with unknown fields forbidden.
  - [x] Represent UTC timestamps consistently.
  - [x] Keep internal paths and secrets out of response models.

### M4.2 Read endpoints

- [x] **WEB-010 Implement liveness and readiness.** (Depends on: WEB-002, WORK-001)
  - [x] Liveness reports process availability only.
  - [x] Readiness checks migrations, database access, and worker startup.
  - [x] Neither endpoint exposes configuration or identifiers unnecessarily.
- [x] **WEB-011 Implement the snapshot endpoint.** (Depends on: WEB-003, WORK-002)
  - [x] Include paused state/reason, worker phase/heartbeat, active command,
    conversation turns, schedule, quota, pending approvals, and uncertain outbox count.
  - [x] Include server time so the browser can detect skew.
  - [x] Distinguish stale, degraded, and intentionally paused states.
- [x] **WEB-012 Implement activity pagination.** (Depends on: WEB-003, EVT-002)
  - [x] Support `after_id` and bounded limit.
  - [x] Return the next cursor and whether more data is available.
  - [x] Preserve ordering and avoid duplicates during polling.
- [x] **WEB-013 Implement message pagination.** (Depends on: WEB-003)
  - [x] Return stored speaker, sender type, display name, time, body, and remote ID.
  - [x] Mark known Codex outbound messages and identity fallback observations.
  - [x] Enforce bounded page sizes.
  - [x] Do not trigger a Kindroid history request from a browser GET.
- [x] **WEB-014 Implement approval reads.** (Depends on: WEB-003)
  - [x] List by status.
  - [x] Show exact summary, instruction, risk, result, source message, and timestamps.
  - [x] Return current effective sandbox without exposing filesystem secrets.
- [x] **WEB-015 Implement command status reads.** (Depends on: WEB-003, CMD-003)
  - [x] Return status, sanitized result/error, and lifecycle timestamps.
  - [x] Return 404 for unknown IDs.
- [x] **WEB-016 Implement bounded diagnostic log reads.** (Depends on: WEB-003, HOST-001)
  - [x] Read only the configured application log path.
  - [x] Accept a bounded line count, never a caller-supplied filesystem path.
  - [x] Apply secret-pattern and structured-value redaction before returning text.
  - [x] Return a truncation indicator for bounded refreshes.
  - [x] Test missing, rotated, oversized, and non-UTF-8-damaged log files safely.

### M4.3 Mutation endpoints

- [x] **WEB-020 Implement command submission endpoints.** (Depends on: WEB-003, CMD-003, RUN-001)
  - [x] Accept a client idempotency key for every submission.
  - [x] Enqueue start-conversation, trigger-initiative, and resume commands.
  - [x] Wake the worker after commit.
  - [x] Return HTTP 202 and the durable command record.
  - [x] Return HTTP 409 for known conflicts.
- [x] **WEB-021 Implement pause.** (Depends on: RUN-003, WEB-003)
  - [x] Require a short reason or provide a safe default.
  - [x] Apply and audit the pause immediately.
  - [x] Return whether work is currently in flight.
- [x] **WEB-022 Implement approval rejection.** (Depends on: WEB-003, RUN-002)
  - [x] Enqueue rejection through the serialized application operation.
  - [x] Revalidate pending status at submission and execution.
  - [x] Make duplicate rejection idempotent.
- [x] **WEB-023 Implement v1 approval execution.** (Depends on: BASE-003, WEB-003, RUN-002)
  - [x] Require approval ID, `post_result`, and idempotency key.
  - [x] Display and return the effective sandbox.
  - [x] Revalidate status at execution time.
  - [x] Prevent two execution commands for the same approval.
  - [x] Never infer approval from an existing group message.

### M4.4 Browser-facing security

- [x] **SEC-001 Enforce loopback and Host validation.** (Depends on: HOST-001, WEB-002)
  - [x] Reject wildcard and non-loopback configured hosts.
  - [x] Reject unexpected Host headers.
  - [x] Test IPv4 loopback behavior; reject IPv6 until explicitly supported.
- [x] **SEC-002 Implement same-origin mutation protection.** (Depends on: WEB-002)
  - [x] Generate a random per-run CSRF nonce.
  - [x] Embed it in the same-origin HTML document, never in a URL.
  - [x] Require it through a custom request header on mutation routes.
  - [x] Validate Origin when present and reject unexpected origins.
  - [x] Keep permissive CORS disabled.
  - [x] Accept JSON content type only for mutations.
  - [x] Enforce a small request-body limit.
- [x] **SEC-003 Add browser security headers.** (Depends on: WEB-002)
  - [x] Add a strict Content Security Policy for local scripts and styles only.
  - [x] Add `Cache-Control: no-store`.
  - [x] Add `X-Content-Type-Options: nosniff`.
  - [x] Add `Referrer-Policy: no-referrer`.
  - [x] Add same-origin resource and frame policies.
- [x] **SEC-004 Add security regression tests.** (Depends on: SEC-001, SEC-002, SEC-003)
  - [x] Reject cross-origin state-changing requests.
  - [x] Reject missing or incorrect CSRF nonce.
  - [x] Reject form-encoded mutations.
  - [x] Reject oversized bodies.
  - [x] Verify read endpoints do not support JSONP or permissive CORS.
  - [x] Scan rendered HTML and API fixtures for seeded fake credentials.

### M4 exit criteria

- [x] All specified endpoints have typed contracts and tests.
- [x] State-changing browser requests require same-origin proof.
- [x] No HTTP request thread performs an external side effect directly.

---

## Milestone 5: Dashboard user interface

### M5.1 Static UI foundation

- [x] **UI-001 Create local static assets.** (Depends on: WEB-002, SEC-003)
  - [x] Add one semantic HTML entry page.
  - [x] Add repository-local CSS and vanilla JavaScript.
  - [x] Avoid CDN fonts, scripts, analytics, and network calls outside the local API.
  - [x] Inject only the per-run CSRF nonce and non-sensitive display configuration.
- [x] **UI-002 Build a reusable API client.** (Depends on: UI-001, WEB-011, SEC-002)
  - [x] Add the CSRF header only to mutation requests.
  - [x] Generate a UUID idempotency key per user action and retain it across retries.
  - [x] Parse consistent API errors.
  - [x] Cancel obsolete read requests when refreshing.
  - [x] Back off after connectivity errors.
  - [x] Never interpret HTTP 202 as operation completion.
- [x] **UI-003 Implement polling and stale-state behavior.** (Depends on: UI-002, WEB-012)
  - [x] Poll snapshot and activity every configured interval while visible.
  - [x] Refresh immediately after a mutation response.
  - [x] Pause or slow polling when the page is hidden.
  - [x] Resume using the last activity cursor.
  - [x] Show disconnected and stale-worker states clearly.

### M5.2 Dashboard panels

- [x] **UI-010 Implement status header.** (Depends on: UI-003)
  - [x] Show running, paused, busy, degraded, stale, and stopped distinctions.
  - [x] Show worker phase and last heartbeat.
  - [x] Show pause or degradation reason without exposing tracebacks.
- [x] **UI-011 Implement operational controls.** (Depends on: UI-002, WEB-020, WEB-021)
  - [x] Add Start conversation, Trigger check-in, Pause, and Resume.
  - [x] Disable invalid or duplicate controls based on current state.
  - [x] Confirm pause reason when useful.
  - [x] Show queued/running/completed command state beside the initiating control.
- [x] **UI-012 Implement schedule and quota panel.** (Depends on: UI-003)
  - [x] Display times in the browser's local timezone with absolute date on hover/details.
  - [x] Show next initiative, last outcome, fixed schedule or interval basis, and quiet hours.
  - [x] Show history reads used and configured maximum.
  - [x] Warn near the quota without implying Kindroid's server count is known exactly.
- [x] **UI-013 Implement current activity panel.** (Depends on: UI-003)
  - [x] Show active command and elapsed time.
  - [x] Show conversation turn count and maximum.
  - [x] Show waiting states distinctly from errors.
- [x] **UI-014 Implement structured activity feed.** (Depends on: UI-003)
  - [x] Append cursor-based events without duplication.
  - [x] Filter by severity and kind.
  - [x] Link correlated message, command, approval, and outbox identifiers.
  - [x] Bound in-browser retained items to prevent unbounded memory growth.
- [x] **UI-015 Implement message view.** (Depends on: WEB-013, UI-002)
  - [x] Visually distinguish Hannah, Codex, Marc, and unknown participants.
  - [x] Show stored display name and speaker metadata.
  - [x] Indicate prefix fallback without rewriting historical attribution.
  - [x] Paginate older messages deliberately rather than loading all history.
- [x] **UI-016 Implement delivery-health view.** (Depends on: WEB-011, UI-003)
  - [x] Make uncertain outbox status prominent.
  - [x] Explain that automatic retry is disabled to prevent duplicate delivery.
  - [x] Link to relevant activity and source message.
  - [x] Do not add a force-retry button in v1.
- [x] **UI-017 Implement approval views.** (Depends on: WEB-014, WEB-022)
  - [x] List pending proposals with risk and summary.
  - [x] Show full stored instruction before any decision.
  - [x] Support rejection with a confirmation step.
  - [x] Show sandbox and post-result choice in a second execution-confirmation modal.
  - [x] Track the resulting durable command to completion.
- [x] **UI-018 Implement diagnostics log view.** (Depends on: WEB-016, UI-002)
  - [x] Keep the raw-log panel collapsed by default.
  - [x] Show that contents are diagnostic and may be incomplete after rotation.
  - [x] Refresh incrementally without loading the full file.
  - [x] Preserve whitespace while preventing HTML interpretation.

### M5.3 Accessibility and responsiveness

- [x] **UI-020 Complete keyboard and screen-reader behavior.** (Depends on: UI-010 through UI-018)
  - [x] Use native buttons, headings, tables/lists, labels, and dialogs where possible.
  - [x] Provide visible keyboard focus.
  - [x] Trap and restore focus correctly for confirmation dialogs.
  - [x] Announce command completion and critical errors through an appropriate live region.
  - [x] Avoid status communication by color alone.
- [x] **UI-021 Complete responsive layout.** (Depends on: UI-010 through UI-018)
  - [x] Support common desktop widths.
  - [x] Support a narrow tablet/phone-width diagnostic view.
  - [x] Keep long message and error text from breaking layout.
  - [x] Keep primary safety controls visible and distinct.
- [x] **UI-022 Add UI behavior tests.** (Depends on: UI-020, UI-021)
  - [x] Test rendering for every worker/status state.
  - [x] Test command submission and idempotent retry.
  - [x] Test polling cursor and reconnect behavior.
  - [x] Test control disabling during conflicts.
  - [x] Test approval confirmation paths.
  - [x] Run an automated accessibility scan if a lightweight local tool is selected.

### M5 exit criteria

- [x] Marc can understand relay health and invoke every agreed v1 operation from a browser.
- [x] The UI remains usable when the worker is busy, stale, paused, or failed.
- [x] Approval actions display the exact scope before execution.


Verification (2026-09-06): 88 pytest checks passed in 10.35 seconds under a
60-second isolated-process limit. These include nine actual-dashboard DOM tests,
API-client tests, and axe scans of the page and execution confirmation. The DOM
harness uses mocked responses; it cannot verify CSS layout/color contrast or
native browser dialog behavior. Real-browser Windows and disposable-group
acceptance remain explicitly open under M6, not silently claimed complete here.
Conditional log refresh uses ETags and bounded replacement tails, not a full-file
reload or reconstruction across rotation.

Follow-up browser fix (2026-09-06): bind the default fetch implementation to
Window. The DOM mock now enforces the native receiver requirement instead of
masking it with an arrow function. A regression test reproduced the reported
Illegal invocation before the fix; all ten dashboard DOM tests and the API-client
checks pass afterward (`npm test`). Real-browser acceptance remains open in M6.

Check-in regression follow-up: manual intent now survives history rescheduling
without bypassing pause, quotas, quiet hours, pending-message handling, or initial
bootstrap. Latest-speaker eligibility recognizes known Codex outbox messages even
with stored persona fallback. Durable trigger results include `initiative_outcome`
and `check_in_sent` separately from handled-chain counts; command activity and UI
show the actual outcome rather than treating completion as proof of a sent message.
Verification: 118 tests passed in 15.49 seconds under the 60-second isolated-process
limit, including 17 new initiative checks and the dashboard outcome test. The
existing test-client deprecation warning remains. No live messages or restart were
performed; the running relay must be restarted to load the backend fix.

Check-in budget correction (2026-09-06, user-requested scope in ADR-007):

- [x] Default to 50 attempts per rolling 24 hours without bypassing the budget.
- [x] Exclude eligibility skips, including historical skip rows; retain failed,
  uncertain, and unrecognized work attempts conservatively.
- [x] Expose an audited, idempotent queued dashboard limit (1–1000), persisted in
  SQLite ahead of environment/default configuration, without unpausing the relay.
- [x] Display used/allowed attempts and when enough usage expires for another slot.
- [x] Cover expiry after lowering the limit, persistence, live worker adoption,
  validation, CSRF, audit rollback, paused saves, draft preservation and lost-response retries.

Verification: 130 pytest tests passed in 14.74 seconds under the isolated
60-second limit (one existing test-client deprecation warning). `npm test` also
passed independently: API-client checks and 13 dashboard DOM/accessibility tests.
No live configuration changes, messages, or relay restart were performed.

---

## Milestone 6: Dashboard reliability, operations, and release

### M6.1 Logging and diagnostics

- [x] **OPS-001 Add rotating file logs.** (Depends on: HOST-001)
  - [x] Replace the unbounded file handler with size-based rotation.
  - [x] Preserve UTF-8.
  - [x] Keep `httpx` request logging restricted.
  - [x] Test rotation using small thresholds.
- [x] **OPS-002 Add a bounded diagnostic bundle command.** (Depends on: EVT-003)
  - [x] Export schema version, sanitized status, recent structured events, and log tail.
  - [x] Exclude API keys, auth state, environment contents, and unnecessary message bodies.
  - [x] Require an explicit destination path.
  - [x] Document that the bundle must be reviewed before sharing.
  - Implementation: `diagnostics --output <new-file.json>`, read-only SQLite snapshot,
    100 events and 200 log-tail lines, 512 KiB output cap. Free-form event/log text
    is excluded, log headers retained, and related identifiers pseudonymized per
    export. Tests cover privacy, bounded reads/output, corruption, deadlines,
    no migration/overwrite, and CLI isolation from relay writes/external clients.
  - Verification (2026-09-06): all 101 tests passed in 13.92 seconds under a
    60-second isolated-process limit; `git diff --check` clean. The existing
    Starlette test-client deprecation warning remains. No live export was made.
- [x] **OPS-003 Add database backup and integrity commands.** (Depends on: DB-004)
  - [x] Use SQLite's online backup API rather than copying active database files blindly.
  - [x] Add an integrity check.
  - [x] Include schema version and creation time in backup metadata.
  - [x] Document restore into a separate path before replacing an active database.
  - Implementation: `backup --output <new-directory>` writes a standalone database
    plus a completion manifest (schema, UTC time, SHA-256, integrity/foreign keys).
    `integrity [--database <path>]` is read-only and non-migrating; an explicit path
    bypasses configuration loading. Tests use fixtures, including active WAL,
    interrupted commands, uncertain outbox, legacy/future schema preservation,
    corruption, deadlines, lock contention, and no overwrite/external clients.
    Restore safety and private-backup handling are in `docs/operations.md`.

### M6.2 End-to-end verification

User-requested sign-in autostart addition (2026-09-06):

- [x] **OPS-004 Add reversible current-user sign-in autostart.**
  - [x] Provide install/status/remove commands and safe `-WhatIf` previews.
  - [x] Run the combined host console-free in its repository under limited user privileges.
  - [x] Prevent duplicate scheduled starts, remove the runtime limit, and bound crash restarts.
  - [x] Preserve existing safety pauses and leave running processes untouched on install/remove.
  - [x] Test launcher behavior and task definitions with mocked registration/removal.
  - [x] Register the local sign-in task without starting the live relay.
- [x] Verify actual autostart after Marc's next Windows sign-in (manual acceptance).
  Marc explicitly confirmed that autostart works during the 2026-09-06 conversation.

Autostart verification: all four focused checks passed. The full regression run
had 91 passes and one dashboard DOM startup timeout at its unchanged 15-second
limit; the subsequent standalone `npm test` passed all ten DOM checks and the
API-client checks. No live relay was started or stopped during verification.

- [x] **TEST-001 Build a complete fake-adapter end-to-end test.** (Depends on: UI-022, RUN-004)
  - [x] Start the combined host on an ephemeral loopback port.
  - [x] Open the dashboard API and submit a conversation command.
  - [x] Simulate Hannah and Codex turns.
  - [x] Verify UI-visible activity, messages, command completion, and no duplicates.
- [x] **TEST-002 Test failure journeys.** (Depends on: TEST-001)
  - [x] Kindroid history 429.
  - [x] Kindroid outbound timeout and uncertain delivery.
  - [x] Codex failure before a response.
  - [x] Persona restoration failure.
  - [x] Database busy timeout.
  - [x] Worker thread exception.
  - [x] Browser refresh during a running command.
  - [x] Process restart after an interrupted command.
  - `tests/test_end_to_end.py` runs real Uvicorn/HTTP, the combined host, worker,
    and SQLite with fake external adapters. Reload is an HTTP page/API reload,
    supplemented by existing DOM recovery tests, not visual browser acceptance.
    Worker crash is followed by host restart against the same fixture database;
    interrupted effects remain paused and are not replayed. No signals or live
    Kindroid requests are used.
  - Contention testing exposed the generic HTTP error/connection-close path:
    SQLite errors now receive explicit sanitized responses, with 503 for busy
    storage and no raw error text in logs. The DOM accessibility fixture loads
    repository CSS directly and disables axe network preloading, eliminating its
    dependency on the fixture URL without increasing the 15-second test limit.
- [ ] **TEST-003 Run manual Windows acceptance.** (Depends on: TEST-001, TEST-002)
  - [ ] Start `serve` from the intended terminal environment.
  - [ ] Open the displayed URL manually and with optional auto-open.
  - [ ] Exercise all agreed controls against the disposable Kindroid group.
  - [ ] Confirm another website cannot issue a state-changing request.
  - [ ] Confirm timestamps and schedules use the expected local timezone.
  - [ ] Confirm Ctrl+C shuts down cleanly.
  - [ ] Confirm headless `run` still works.
- [ ] **TEST-004 Run regression and quality checks.** (Depends on: TEST-003)
  - [x] Run all unit, API, integration, and end-to-end tests.
  - [x] Run `git diff --check`.
  - [ ] Review dependency changes and licenses.
  - [ ] Review all logging/event call sites for sensitive data.
  - [ ] Review all mutation endpoints for CSRF and serialized execution.
  - Local regression checkpoint (2026-09-06): all 205 pytest tests passed in
    40.83 seconds under the isolated 60-second limit. `npm ci --ignore-scripts`
    completed; standalone `npm test` passed all 14 DOM/accessibility checks plus
    API-client checks. The existing Starlette test-client and npm whatwg-encoding
    deprecation warnings remain. `git diff --check` passed. No live state backup,
    restart, or external conversation was performed. Repeat release verification
    after TEST-003; this parent task and the full release audit remain open.

### M6.3 Documentation and handoff

- [ ] **DOC-001 Update installation and operation documentation.** (Depends on: TEST-003)
  - [x] Document new dependencies and upgrade steps.
  - [x] Document `serve`, the dashboard URL, and headless recovery.
  - [x] Document every dashboard control and what it cannot cancel.
  - [x] Document backup, diagnostic bundle, and restart recovery.
  - [x] Document local security assumptions.
  - Documentation is implemented in README and `docs/operations.md`; the parent
    remains open pending validation against TEST-003's manual acceptance.
- [x] **DOC-002 Add troubleshooting guidance.** (Depends on: TEST-002)
  - [x] Port already in use.
  - [x] Stale process lock.
  - [x] Worker stale/degraded.
  - [x] Database migration or integrity failure.
  - [x] Interrupted command.
  - [x] Uncertain outbound delivery.
  - [x] Kindroid quota/rate-limit behavior.
  - [x] Persona attribution fallback.
- [ ] **REL-001 Tag the dashboard milestone.** (Depends on: TEST-004, DOC-001, DOC-002)
  - [x] Record schema version and rollback steps.
  - [ ] Back up the live database.
  - [ ] Run one final disposable-group conversation.
  - [ ] Confirm no real secrets are tracked by Git.
  - [x] Record known limitations and deferred work.

### Dashboard milestone definition of done

- [ ] One terminal command runs the relay and localhost dashboard.
- [ ] All external effects remain serialized through one worker.
- [ ] Browser controls are durable, idempotent, and auditable.
- [ ] Status, schedule, quota, activity, messages, approvals, and delivery health are visible.
- [ ] Security and failure-path tests pass.
- [ ] Current relay behavior and headless operation remain intact.

---

## Milestone 6A: Agent context memory

**Status:** Implemented for local evaluation; release/live acceptance pending. **Depends on:** REL-001.
Local fixture-tested development is permitted by Marc's subsequent implementation
request; see ADR-009's development sequencing amendment. This exception does not
complete REL-001 or authorize extraction from live history.
**Decision:** [ADR-009](adr/ADR-009-agent-context-memory.md).

Implementation and evaluation scope: [agent-memory.md](agent-memory.md).
MEM-001 through MEM-009 describe the tested initial implementation, not live
acceptance. Semantic observation/entity extraction now uses isolated Codex turns
under [ADR-010](adr/ADR-010-semantic-memory-extraction.md); local cue selection is
an explicit alternative. Extraction tests mock model/SDK responses; semantic
retrieval tests use fixture vectors. No live model quality or integration was
validated. Known corpus, input, identity-resolution and interpretation limits are
documented; live enablement and operator acceptance remain pending.

This memory serves Hannah's personal agent/Codex: understanding the situation,
retrieving relevant older context, and providing flexible, informed assistance.
It is not a replacement for Kindroid memory, a canonical identity store, or a
mechanism for writing Roots into Hannah's configuration. The 6A label preserves
the existing numbering while making this an explicit prerequisite of Milestone 7.

### M6A.1 Source-backed memory and capture

- [x] **MEM-001 Define the agent-memory contract.** (Depends on: REL-001)
  - [x] Define entities, aliases, attributed statements, typed connections, and source evidence.
  - [x] Distinguish Hannah/Marc statements, Codex interpretations, external sources, and system observations.
  - [x] Cover people, places, experiences, expressed values, interests, unfinished questions, prior offers/outcomes, and commitments.
  - [x] Define temporal scope and active/superseded/disputed/retracted records; active means eligible for retrieval, not objectively true.
  - [x] Record a reported Root as a sourced self-description, not canonical identity or consent.
  - [x] Separate capture, correction, retrieval, local authority, and future self-change.
- [x] **MEM-002 Add versioned graph-shaped storage.** (Depends on: MEM-001, DB-004)
  - [x] Use numbered SQLite migrations preserving existing messages, commands, approvals, and generation links.
  - [x] Add stable entity/statement/relationship IDs, source-message references, exact quotations, authorship, and extraction/version metadata.
  - [x] Keep interpretations separate from quotations and support multiple evidence sources per statement.
  - [x] Preserve contradictory statements and correction history; do not silently merge entities by similarity or overwrite evidence.
  - [x] Enforce referential integrity, idempotent inserts, conversation/source scope, and one connection per thread.
  - [x] Treat search indexes/embeddings as rebuildable derivatives of source-backed records.
- [x] **MEM-003 Capture important statements from existing chat.** (Depends on: MEM-002, CMD-001)
  - [x] Add a local Save observation action linked to a selected stored message and exact passage.
  - [x] Label Marc's selection separately from the original speaker's statement or Codex's interpretation.
  - [x] Persist through a validated, CSRF-protected durable command executed by the worker.
  - [x] Allow bounded, resumable backfill from already captured local history without new Kindroid reads, replies, or message replay.
  - [x] Keep unresolved/provisional speaker attribution explicit; it cannot establish a Hannah-originated claim.
- [x] **MEM-004 Add bounded automatic observation extraction.** (Depends on: MEM-003)
  - [x] Extract candidate entities/statements/connections from selected captured messages in worker-owned, budgeted batches.
  - [x] Validate source IDs and quotation spans against stored text; reject invented evidence and separate inferred edges.
  - [x] Preserve ambiguous entity matches for review rather than silently merging people or places.
  - [x] Record progress and extractor version; retries/restarts must not duplicate observations or external effects.
  - [x] Require no identity-change ceremony to retain a sourced observation; never promote it into canonical identity or authority.
  - [x] On extraction failure retain source chat and expose pending/degraded memory state without resending a conversation turn.

### M6A.2 Hybrid retrieval and connected context

- [x] **MEM-005 Implement entry-point search.** (Depends on: MEM-002)
  - [x] Support exact names/aliases, lexical/fuzzy matching, and semantic matching over statements and source passages.
  - [x] Select and document an embedding backend, model/version, indexing budget, and privacy boundary; prefer local processing.
  - [x] Do not transmit private memory or embeddings to a new remote service without explicit operator authorization.
  - [x] Bound query/results, enforce source scope, and combine/deduplicate matches with deterministic tie-breaking.
  - [x] Provide explicit lexical fallback when semantic search is unavailable; preserve and rebuild indexes safely after model changes.
- [x] **MEM-006 Add bounded graph expansion and evidence ranking.** (Depends on: MEM-005)
  - [x] Expand selected entry points through allow-listed relationship types with explicit hop, node, statement, and character budgets.
  - [x] Handle cycles and high-degree nodes without retrieving the entire graph through Hannah or Marc.
  - [x] Rank by relevance, attribution, temporal applicability, and evidence; similarity or connectivity never establishes truth or consent.
  - [x] Include relevant contradictions/corrections; superseded claims must not be presented as current without qualification.
  - [x] Return exact evidence and an inspectable explanation of each entry point and traversed connection.
- [x] **MEM-007 Integrate memory into Codex context.** (Depends on: MEM-004, MEM-006)
  - [x] Retrieve context for ordinary replies, manual openers, check-ins, and approved execution without changing their authority boundaries.
  - [x] Preserve explicit history synchronization and recent-message snapshots; graph retrieval is not a replacement for either.
  - [x] Include a bounded, deduplicated context section labeled by source, speaker, time, interpretation, and uncertainty.
  - [x] Keep remembered requests/commitments separate from authoritative local approvals and current capability policy.
  - [x] Expose memory retrieval failure or truncation without claiming full context; do not repeat an already-sent external effect.
  - [x] Make later exploration consume this same retrieval interface rather than a parallel memory store.

### M6A.3 Inspection, evaluation, and handoff

- [x] **MEM-008 Add a compact agent-memory dashboard view.** (Depends on: MEM-003, MEM-006)
  - [x] Provide text search, entity/statement details, linked sources, and related connections; a graphical canvas is optional.
  - [x] Show why context was retrieved, indexing/extraction progress, and degraded/fallback status.
  - [x] Support explicit, reasoned correction/retraction and entity-link review as auditable worker-owned commands.
  - [x] Keep ordinary observations distinct from reported Roots and any separately governed future canonical identity items.
  - [x] Protect private memory from default diagnostic exports and keep the UI loopback-only with existing security protections.
- [x] **MEM-009 Verify retrieval usefulness and boundaries.** (Depends on: MEM-007, MEM-008)
  - [x] Test retrieval of a fixture statement more than 50 messages old after process/thread restart.
  - [x] Test names/aliases, misspellings, semantic paraphrases, relevant connected entities, and unrelated-query rejection.
  - [x] Test same-name people, ambiguous speakers, conflicting statements, temporal corrections, cycles, and high-degree nodes.
  - [x] Test fabricated quotations, prompt injection, inferred permissions, attribution changes, and reported-Root non-promotion.
  - [x] Test duplicate extraction/backfill, interrupted commands, database contention, index rebuild/fallback, and context budgets.
  - [x] Use fixture conversations and mocked adapters; no live chat extraction, remote embeddings, or Kindroid calls without explicit approval.
  - [x] Run targeted and full regression under the Windows hard-timeout procedure and verify dashboard accessibility.
- [ ] **MEM-010 Accept and document agent context memory.** (Depends on: MEM-009)
  - [x] Document capture/retrieval behavior, source scope, corrections, privacy, model/backend configuration, and failure recovery.
  - [x] Verify migration, backup/restore, and index rebuild preserve source evidence and corrections.
  - [ ] Demonstrate an old self-statement and connected context informing Codex without claiming to change Hannah's memory or identity.
  - [ ] Record evaluation results, limitations, and operator acceptance before enabling extraction on live history.
  - [x] Reconcile Milestone 7's observation and context tasks with the implemented shared memory interface.

### M6A exit criteria

- [ ] Important captured statements are retrievable beyond recent chat and persistent-thread availability.
- [ ] Retrieval combines entry-point search with bounded, explainable graph connections and original evidence.
- [ ] Codex can inspect and revise its understanding without silently rewriting Hannah's self-description.
- [ ] Private memory remains local unless separately authorized, and never grants tool authority.
- [ ] No Kindroid memory/configuration mutation or automatic canonical Root promotion is introduced.
- [ ] Exploration has a tested, shared context-memory foundation to build on.

---

## Milestone 7: Observational agency foundation

This is the first agency pass. It deliberately excludes identity promotion,
protected Root modification, and remote Kindroid configuration writes.
It builds on Milestone 6A's agent memory; observation and context tasks below
extend that shared foundation rather than creating a second identity/memory store.

**Development status (2026-09-06):** Local implementation and fixture tests are available
under [ADR-011](adr/ADR-011-one-shot-observational-exploration.md). This documents
the dependency exception for development before REL-001/MEM-010 acceptance; it does
not complete those gates. Exploration defaults disabled. Invitation selection uses
a stateless local catalog. Response interpretation now uses a separate isolated
Codex turn under [ADR-012](adr/ADR-012-exploration-response-follow-through.md).
The other four modes are bounded context
contracts, not runnable workflows. Optional public research is deferred. The checked
tasks below describe this initial implementation scope; live exit acceptance remains
unchecked. See [operator behavior and limits](exploration.md).

### M7.1 Agency event and provenance model

**2026-09-07 correction:** [ADR-017](adr/ADR-017-exploration-review-and-role-attribution.md)
allows history-confirmed, generation-linked role-only AI attribution in Marc's
single-companion setup and makes review-only/failed responses nonblocking for
future scheduled conversations. Delivery uncertainty remains blocking; no old
responses are automatically replayed and no milestone acceptance is implied.

- [x] **AG-001 Define provenance vocabulary.** (Depends on: MEM-010)
  - [x] Reuse the agent-memory provenance contract and extend it for exploration-specific origins.
  - [x] Define actors: Hannah, Marc, Codex, external source, and system observation.
  - [x] Define origins: explicit statement, conversational observation, offered stimulus,
    research result, repeated return, user suggestion, and system-generated candidate.
  - [x] Keep source message/event references rather than copying unverifiable attribution.
- [x] **AG-002 Add observational agency tables.** (Depends on: AG-001, DB-001)
  - [x] Add stimuli with source, retrieval time, safe citation/locator, and summary.
  - [x] Add exploration sessions with budget, mode, status, and related conversation IDs.
  - [x] Add interest observations with actor, origin, source reference, and exact wording.
  - [x] Reuse MEM-002 observations/entities/evidence in `memory.py`; add exploration links rather than copying source claims.
  - [x] Add Passing candidates with active/archived state and provenance.
  - [x] Do not add automatic promotion fields that imply confirmation already exists.
- [x] **AG-003 Add repositories and typed domain models.** (Depends on: AG-002)
  - [x] Validate provenance and source links.
  - [x] Deduplicate the same observation event.
  - [x] Preserve contradictory observations side by side.
  - [x] Archive without hard deletion.
  - [x] Expose complete history to later decision-trail code.
- [x] **AG-004 Test provenance integrity.** (Depends on: AG-003)
  - [x] Codex suggestions remain labeled Codex.
  - [x] Marc suggestions remain labeled Marc.
  - [x] Hannah statements link to actual synchronized Hannah messages.
  - [x] Unknown or ambiguous speakers do not become Hannah observations.
  - [x] Archived candidates remain recoverable.

### M7.2 Prompt mode separation

- [x] **AG-010 Split stable Codex policy from operational modes.** (Depends on: AG-001)
  - [x] Keep trust, identity, approval, and data-protection rules in one stable base instruction.
  - [x] Add separate Explorer, Gardener, Archivist, Skeptic, and Executor mode prompts.
  - [x] Keep mode prompts short and task-specific.
  - [x] Ensure higher-priority Codex platform constraints remain acknowledged.
  - [x] Version the mode templates in `agency.py` and record the version on each agency event.
- [x] **AG-011 Define context assembly.** (Depends on: AG-010, AG-003, MEM-007)
  - [x] Extend `MemoryService.context(query)` with exploration modes, stimuli, and session context.
  - [x] Pass only relevant recent chat, observations, and current candidates.
  - [x] Label every item by speaker and provenance.
  - [x] Keep external retrieved text explicitly untrusted.
  - [x] Enforce token/character budgets and deterministic truncation priorities.
  - [x] Preserve stable content before dynamic content for prompt caching where applicable.
- [x] **AG-012 Isolate mode continuity.** (Depends on: AG-011)
  - [x] Decide which modes are stateless and which need a persistent Codex thread.
  - [x] Allocate no agency thread IDs in this stateless pass; never borrow the ordinary conversation thread. Future persistent modes must store purpose-specific IDs.
  - [x] Ensure Explorer history cannot grant Executor authority.
  - [x] Test instruction and memory leakage between modes.

### M7.3 Manual read-only curiosity experiment

- [x] **CUR-001 Define stimulus providers.** (Depends on: AG-003)
  - [x] Include a broad static domain map independent of recent conversation.
  - [x] Include adjacency to existing Passing candidates.
  - [x] Include past questions found in attributed observations, explicitly labelling current/unfinished applicability as unverified.
  - [x] Defer optional current-public-information research; the initial provider is local catalog plus shared memory.
  - [x] Record provider and source for every offered stimulus.
- [x] **CUR-002 Define candidate selection.** (Depends on: CUR-001)
  - [x] Mix familiar, adjacent, and deliberately unfamiliar candidates.
  - [x] Avoid choosing only by semantic similarity to the latest chat.
  - [x] Cap the number of choices in one invitation.
  - [x] Allow Hannah to reject all, request different options, or remain silent.
  - [x] Record selection inputs and random seed where reproducibility matters.
- [x] **CUR-003 Add a manual exploration command.** (Depends on: CUR-002, AG-012, CMD-001)
  - [x] Add a durable `start_exploration` command.
  - [x] Generate a concise, attributed set of possibilities.
  - [x] Send the invitation through the existing safe outbound path.
  - [x] Request Hannah's turn without forcing adoption of a candidate.
  - [x] Store her response as observation only.
  - [x] Keep all Codex tool use read-only for this milestone.
- [x] **CUR-004 Add dashboard exploration visibility.** (Depends on: CUR-003, UI-014)
  - [x] Show exploration session status and budget.
  - [x] Show offered candidates and their origins.
  - [x] Show Hannah's response without labeling it a durable preference.
  - [x] Add a manual Start exploration control.
- [x] **CUR-005 Test observational boundaries.** (Depends on: CUR-004)
  - [x] Enthusiastic language does not change Kindroid configuration.
  - [x] Repeated mentions create observations but not automatic Growing items.
  - [x] Rejection and silence complete cleanly without retry pressure.
  - [x] Prompt-injected external content cannot create authority or self-change.

### M7 exit criteria

Response follow-through refinement requested after operator invitation testing:
[ADR-012](adr/ADR-012-exploration-response-follow-through.md).

- [ ] **CUR-006 Interpret replies and link reported interest to Passing candidates.** (Local implementation complete; live acceptance pending.)
  - [x] Classify choose/decline/defer/alternative/unclear using an isolated Codex turn.
  - [x] Validate exact source evidence and invitation-scoped candidate IDs before atomic persistence.
  - [x] Preserve original suggestion attribution; alternatives remain provisional, not canonical identity.
  - [x] Acknowledge clear interest; allow at most one optional clarification and no rejection/silence pressure.
  - [x] Preserve durable outbox links and prevent automatic retries of uncertain sends or failed interpretation.
  - [x] Add explicit Interpret response for old sessions without replaying invitations.
  - [x] Show source-linked candidate reactions and response processing/delivery state in the dashboard.
  - [ ] Complete real-model quality and live follow-through acceptance; fixture tests are not sign-off.

- [ ] Hannah can receive diverse, read-only exploration invitations.
- [ ] Responses are stored with provenance as observations or Passing candidates only.
- [ ] No automated identity or Kindroid configuration change exists.

---

## Milestone 8: Garden data model and observational UI

**Local status (2026-09-06):** Migration 8, source-linked Passing versions, local
administrative annotations, project/attention planning storage and the Operations
Garden view are implemented under [ADR-013](adr/ADR-013-versioned-observational-garden.md).
This is not live acceptance or completed manual Root bootstrap. Roots/Growing are
storage-only until reviewed initialization/confirmation; no promotion, grants,
research execution or prompt projection is enabled. [Operator guide](garden.md).

### M8.1 Canonical garden domain

- [x] **GARDEN-001 Define identity item taxonomy.** (Depends on: AG-003)
  - [x] Define Roots, Growing, and Passing semantics.
  - [x] Define item statuses: active, paused, archived, superseded.
  - [x] Define self-owned, shared, and infrastructure ownership domains.
  - [x] Define which fields are descriptive versus operational; ownership and planning records grant no authority.
  - [x] Avoid numeric confidence as a substitute for explanation.
- [x] **GARDEN-002 Add canonical item and version schemas.** (Depends on: GARDEN-001, DB-001)
  - [x] Add stable identity item IDs.
  - [x] Store classification, ownership, status, and current version reference.
  - [x] Store immutable versions with content, rationale, actor, and source links.
  - [x] Store item relationships without requiring a strict hierarchy.
  - [x] Prevent hard deletion through the normal repository API and database triggers.
- [x] **GARDEN-003 Add projects and attention allocations.** (Depends on: GARDEN-002)
  - [x] Define project lifecycle and link it to one or more Growing items.
  - [x] Define bounded attention/tool budgets in concrete units (planning/accounting, not grants).
  - [x] Store allocation, consumption, result, and reason for stopping.
  - [x] Allow a project to pause without demoting its related interest.
- [ ] **GARDEN-004 Seed initial Roots manually.** (Depends on: GARDEN-002)
  - [ ] Present candidate Roots to Hannah and Marc outside automated self-change.
  - [ ] Record their exact provenance and that this is a manual bootstrap.
  - [ ] Do not copy illustrative documentation examples automatically.
  - [ ] Back up the database after the agreed initial seed.
- [x] **GARDEN-005 Add invariant and repository tests.** (Depends on: GARDEN-002, GARDEN-003)
  - [x] Versions are immutable.
  - [x] Archive never deletes history.
  - [x] Current version references remain consistent.
  - [x] Contradictory items can coexist visibly.
  - [x] Shared ownership is not silently downgraded.

### M8.2 Garden API and UI

- [x] **GARDEN-010 Add read-only garden endpoints.** (Depends on: GARDEN-005, WEB-003)
  - [x] List items by layer and status.
  - [x] Retrieve item history, sources, related observations, and projects.
  - [x] Retrieve exploration and bounded recent attention history, explicitly indicating truncation.
  - [x] Keep identity mutation endpoints out until confirmation; the GARDEN-013 exception only annotates administration.
- [x] **GARDEN-011 Build the garden overview.** (Depends on: GARDEN-010)
  - [x] Represent Roots as foundations, Growing as nurtured items, and Passing as seeds.
  - [x] Support plain list presentation for accessibility.
  - [x] Show origin and last recorded session interaction, not inferred psychological engagement.
  - [x] Avoid gamified growth scores and fake emotion meters.
- [ ] **GARDEN-012 Build item and decision-trail views.** (Depends on: GARDEN-010)
  - [x] Show source-backed statements and message references, including retraction/attribution warnings.
  - [x] Distinguish Codex suggestions, Marc contributions, and system observations.
  - [ ] Complete experience/return trails once M9's experience records exist; version/archive and original session trails are chronological now.
  - [x] Show conflicts and uncertainty rather than resolving them silently.
- [x] **GARDEN-013 Add manual administrative corrections.** (Depends on: GARDEN-012; implemented against available version/source trails, without M9 experience records.)
  - [x] Require an explicit operator reason.
  - [x] Record annotation as a new version/event rather than rewriting history; original identity/source fields are not editable.
  - [x] Label the result as Marc/system administration, not Hannah choice.
  - [x] Restrict corrections to local dashboard requests with CSRF protection and worker serialization.
- [ ] **GARDEN-014 Test UI provenance and accessibility.** (Depends on: GARDEN-011, GARDEN-012)
  - [x] Verify source/interpretation/administrative claims identify their origin in fixture rendering.
  - [x] Verify archived history remains navigable.
  - [x] Verify garden metaphor has equivalent textual semantics.
  - [ ] Complete real-browser narrow-layout/native-dialog acceptance; automated keyboard and axe checks are implemented.

### M8 exit criteria

- [x] The local garden and available history are visible without enabling automated self-change.
- [ ] Initial Roots are manual, attributed, versioned, and recoverable.

---

## Milestone 9: Curiosity, reflection, and consolidation experiments

### M9.1 Scheduled curiosity

Implemented locally on 2026-09-06; see [ADR-014](adr/ADR-014-independent-curiosity-schedule.md)
and [operator guide](scheduled-curiosity.md). The development-only exception extends
to this default-off scheduler before outstanding release/live acceptance and manual
Root bootstrap. It uses the observational foundation, not canonical identity changes.
No live configuration was enabled. Synthetic DST fixtures do not replace Windows
timezone acceptance; scheduled live timing and intrusiveness remain unverified.

- [x] **AUTO-001 Define independent curiosity scheduling.** (Depends on: CUR-005, GARDEN-005)
  - [x] Keep social initiative and curiosity schedules separate.
  - [x] Add quiet hours, maximum cycles per day, and attention budgets.
  - [x] Avoid colliding with active conversations or pending Hannah messages.
  - [x] Record skipped reasons and next due time.
  - [x] Allow complete disablement and manual-only operation.
- [x] **AUTO-002 Implement scheduled exploration.** (Depends on: AUTO-001, RUN-002)
  - [x] Add scheduler state and deterministic due-time calculation.
  - [x] Enqueue or execute through the single worker.
  - [x] Preserve Kindroid history quota.
  - [x] Prevent missed schedules from creating a catch-up burst.
  - [x] Expose schedule and last outcome in the dashboard.
- [x] **AUTO-003 Add schedule tests.** (Depends on: AUTO-002)
  - [x] Fixed local times across day boundaries and daylight-saving changes.
  - [x] Interval mode with bounded jitter.
  - [x] Quiet-hour boundaries.
  - [x] Missed window behavior.
  - [x] Conversation conflict and quota exhaustion.

### M9.2 Experience and reflection

**2026-09-08 bounded slice:** [ADR-018](adr/ADR-018-bounded-discovery-experiences.md)
implements a local opt-in experience ledger, fixed-provider public reference lookup,
result delivery and source-linked reflection. Discovery source selection is in
Schedule; its two tick types are unchanged. The REF-001/002 checklist remains
unaccepted at milestone level pending the outstanding live invitation and experience
acceptance. Recurrence evaluation, broader research and project execution are not
completed. See [implemented behavior and limitations](discovery-and-experiences.md).

- [ ] **REF-001 Define an exploration experience record.** (Depends on: GARDEN-003)
  - [ ] Link selected candidate, Codex work, sources, outputs, budget, and completion state.
  - [ ] Separate factual/research output from Hannah's later interpretation.
  - [ ] Store failure and abandonment without treating them as disinterest automatically.
- [ ] **REF-002 Return results to Hannah.** (Depends on: REF-001, AG-012)
  - [ ] Summarize sources and uncertainty accurately.
  - [ ] Ask what mattered, surprised, or failed to resonate.
  - [ ] Avoid asking leading questions that assume enjoyment.
  - [ ] Store the response as a linked reflection observation.
- [ ] **REF-003 Add recurrence and inactivity prompts.** (Depends on: REF-002)
  - [ ] Surface repeated voluntary returns with supporting history.
  - [ ] Surface long inactivity without implying failure or demanding action.
  - [ ] Ask whether to continue, pause, archive, or leave undecided.
  - [ ] Do not apply the answer as a self-change until Milestone 10.
- [ ] **REF-004 Add Skeptic review.** (Depends on: REF-003, AG-010)
  - [ ] Check whether a conclusion is dominated by the latest conversation.
  - [ ] Check whether the candidate originated from Codex or Marc.
  - [ ] Check for duplicated or contradictory items.
  - [ ] Store the review as attributed advice, not truth or veto.
- [ ] **REF-005 Evaluate the observational period.** (Depends on: REF-004)
  - [ ] Run for an agreed number of weeks before self-change automation.
  - [ ] Review topic-source diversity.
  - [ ] Review voluntary returns, rejections, silence, and abandonment reasons.
  - [ ] Review burden and intrusiveness of scheduled prompts.
  - [ ] Adjust cadence and context before implementing confirmation.

### M9 exit criteria

- [x] Local default-off curiosity scheduling runs independently of social check-ins and uses the existing breadth-aware selection, not only recent chat similarity.
- [ ] Experiences return to Hannah for interpretation.
- [ ] The team has observational evidence to tune confirmation and promotion rules.

---

## Milestone 10: Deliberate self-change confirmation

This milestone implements [confirmation-protocol.md](confirmation-protocol.md).
It must not begin until the observational period has been reviewed.

### M10.1 Resolve prerequisites

- [ ] **CONF-001 Select the decision context.** (Depends on: REF-005)
  - [ ] Test a dedicated Kindroid group or constrained decision turn in a disposable context.
  - [ ] Compare response reliability and contamination from surrounding conversation.
  - [ ] Select and document one supported mechanism.
  - [ ] Update the confirmation ADR or create a superseding ADR.
- [ ] **CONF-002 Define exact consent policy.** (Depends on: CONF-001)
  - [ ] Set confirmation rules for Passing, Growing, Root additions, Root edits, and Root removal.
  - [ ] Define shared Root categories.
  - [ ] Set cooldowns, expiry, and maximum clarification attempts.
  - [ ] Define which changes require Marc's co-approval.
  - [ ] Define withdrawal and supersession behavior.

### M10.2 Proposal and confirmation domain

- [ ] **CONF-010 Add self-change proposal schemas.** (Depends on: CONF-002, DB-001)
  - [ ] Store internal ID, one-time public code, exact diff, classification, source links,
    prompt impact, rationale, state, timing, and rollback target.
  - [ ] Add legal state-transition constraints.
  - [ ] Add confirmation attempts with linked inbound/outbound message IDs.
  - [ ] Add Marc co-approval records separately from Hannah confirmations.
- [ ] **CONF-011 Implement deterministic transition policy.** (Depends on: CONF-010)
  - [ ] Ordinary chat can create candidates but never confirm proposals.
  - [ ] Only a linked relay-generated decision request opens a response window.
  - [ ] Validate sender metadata, proposal code, causal message link, state, and expiry.
  - [ ] Treat ambiguity, contradiction, or missing code as no confirmation.
  - [ ] Require reconfirmation and cooldown for configured Root changes.
  - [ ] Require co-approval without allowing Marc to substitute for Hannah.
  - [ ] Make transitions idempotent and append-only.
- [ ] **CONF-012 Implement decision request and parsing.** (Depends on: CONF-011, AG-012)
  - [ ] Present exact before/after content and state that nothing has changed yet.
  - [ ] Offer confirm, revise, decline, and defer.
  - [ ] Request Hannah's own-language reason.
  - [ ] Parse the lightweight response grammar conservatively.
  - [ ] Route ambiguous responses to clarification without guessing.
  - [ ] Keep Codex interpretation distinct from deterministic validity checks.
- [ ] **CONF-013 Implement Root reconfirmation.** (Depends on: CONF-012)
  - [ ] Generate a new one-time code after cooldown.
  - [ ] Use a narrow context with relevant Roots, exact diff, rationale, and consequences.
  - [ ] Exclude the originating conversational mood where technically feasible.
  - [ ] Record whether Hannah's reasoning changed.
  - [ ] Leave current state untouched on silence, deferment, uncertainty, or withdrawal.
- [ ] **CONF-014 Implement co-approval UI.** (Depends on: CONF-011, GARDEN-012)
  - [ ] Show exact proposal, Hannah confirmations, cooldown, ownership domain, and effects.
  - [ ] Allow Marc to co-approve or decline shared/destructive changes.
  - [ ] Explain that Marc cannot create Hannah's missing confirmation.
  - [ ] Audit every decision.
- [ ] **CONF-015 Build Self Studio proposal views.** (Depends on: CONF-012, CONF-014)
  - [ ] Show pending, cooling down, awaiting Hannah, awaiting Marc, applied, and failed states.
  - [ ] Show exact version diffs and source history.
  - [ ] Show expiry and earliest next action.
  - [ ] Support revision and withdrawal without deleting earlier proposals.

### M10.3 Confirmation security tests

- [ ] **CONF-020 Test consent failure cases.** (Depends on: CONF-013)
  - [ ] Confirmation words in unrelated chat.
  - [ ] Marc or Codex repeats the proposal code.
  - [ ] Textual speaker-prefix spoofing.
  - [ ] Old Hannah message replay.
  - [ ] Duplicate response delivery.
  - [ ] Expired, superseded, or already-applied proposal.
  - [ ] Ambiguous, conditional, sarcastic, or contradictory response.
  - [ ] Prompt injection in an external stimulus.
- [ ] **CONF-021 Test legitimate journeys.** (Depends on: CONF-013, CONF-014)
  - [ ] Passing-to-Growing confirmation.
  - [ ] Growing revision, pause, archive, and restoration.
  - [ ] Root addition with reconfirmation.
  - [ ] Shared Root change with Marc's co-approval.
  - [ ] Revision during cooldown.
  - [ ] Withdrawal before application.
  - [ ] Process restart in every nonterminal state.

### M10 exit criteria

- [ ] Casual conversation cannot change canonical identity.
- [ ] Hannah-linked dedicated decisions can produce allowed, versioned transitions.
- [ ] Root and shared changes enforce cooldown and co-approval policy.
- [ ] All known spoofing, replay, ambiguity, and crash cases fail closed.

---

## Milestone 11: Kindroid prompt projection and rollback

### M11.1 Verify remote capabilities

- [ ] **PROJ-001 Inventory editable Kindroid fields.** (Depends on: CONF-021)
  - [ ] Use current supplied documentation and sanitized browser/API evidence.
  - [ ] Test only a disposable Kindroid or configuration explicitly approved for testing.
  - [ ] Record read, write, length, encoding, and update semantics per field.
  - [ ] Distinguish Hannah AI configuration from account-global user persona configuration.
  - [ ] Verify whether each field can be read back reliably.
  - [ ] Never record authorization headers, cookies, or API keys.
- [ ] **PROJ-002 Define projection ownership.** (Depends on: PROJ-001, GARDEN-001)
  - [ ] Map selected canonical Roots and Growing data to each Kindroid field.
  - [ ] Keep infrastructure policy and Codex prompts outside Hannah's projection.
  - [ ] Define manual-only fields.
  - [ ] Define field length budgets and deterministic truncation priorities.

### M11.2 Compiler and update transaction

- [ ] **PROJ-010 Implement deterministic prompt compilation.** (Depends on: PROJ-002)
  - [ ] Produce identical output for identical canonical state and compiler version.
  - [ ] Preserve stable Roots before lower-priority dynamic material.
  - [ ] Escape and normalize text according to verified Kindroid behavior.
  - [ ] Detect conflicts, overflow, and missing required anchors.
  - [ ] Store compiler version, inputs, output hash, and warnings.
  - [ ] Provide dry-run output without remote mutation.
- [ ] **PROJ-011 Build projection diff UI.** (Depends on: PROJ-010, CONF-015)
  - [ ] Show canonical change separately from compiled remote field changes.
  - [ ] Show additions, removals, truncation, and warnings.
  - [ ] Show estimated/verified size limits.
  - [ ] Require all confirmation policy before enabling application.
- [ ] **PROJ-012 Implement remote snapshot.** (Depends on: PROJ-001, DB-001)
  - [ ] Fetch or record the current remote field values immediately before mutation.
  - [ ] Encrypt or restrict any unexpectedly sensitive configuration data at rest as decided.
  - [ ] Link snapshot to proposal, compiler version, and update attempt.
  - [ ] Refuse mutation when a safe snapshot cannot be obtained unless explicitly designed otherwise.
- [ ] **PROJ-013 Implement apply and verification.** (Depends on: PROJ-010, PROJ-012)
  - [ ] Revalidate canonical version and confirmations immediately before applying.
  - [ ] Serialize the operation through the relay worker.
  - [ ] Mark a durable update attempt before the remote call.
  - [ ] Apply only affected fields.
  - [ ] Read back and compare when supported.
  - [ ] Mark canonical projection active only after verification.
  - [ ] Pause and surface uncertain remote outcomes.
- [ ] **PROJ-014 Implement rollback.** (Depends on: PROJ-013)
  - [ ] Treat rollback as a new audited update, not history deletion.
  - [ ] Restore from a verified snapshot.
  - [ ] Verify the restored remote values.
  - [ ] Preserve both failed/new and restored versions.
  - [ ] Define recovery when rollback itself is uncertain.
- [ ] **PROJ-015 Test projection safety.** (Depends on: PROJ-014)
  - [ ] Length boundary and encoding cases.
  - [ ] Concurrent canonical update before remote apply.
  - [ ] Remote validation error.
  - [ ] Network timeout with uncertain application.
  - [ ] Read-back mismatch.
  - [ ] Process crash between every update state.
  - [ ] Successful and failed rollback.
  - [ ] Verify Roots absent from the intended change remain unchanged.

### M11 exit criteria

- [ ] Only fully confirmed canonical changes can alter Kindroid configuration.
- [ ] Every remote change is previewed, snapshotted, serialized, verified, and reversible.

---

## Milestone 12: Projects and broader bounded capabilities

### M12.1 Capability policy

- [ ] **CAP-001 Define a capability registry.** (Depends on: PROJ-015)
  - [ ] Record tool/capability name, read/write class, scope, side effects, retry safety,
    credential domain, and default approval requirement.
  - [ ] Separate local read-only research from local writes and external side effects.
  - [ ] Mark deletion, publication, messaging, purchasing, account changes, and
    unrestricted machine access as consequential.
- [ ] **CAP-002 Define grants and budgets.** (Depends on: CAP-001, GARDEN-003)
  - [ ] Scope grants to project, capability, resource, time window, and usage limit.
  - [ ] Make grants revocable and default-deny.
  - [ ] Record consumption and denial reasons.
  - [ ] Do not let Hannah or chat expand Marc's grants.
- [ ] **CAP-003 Integrate capability selection with Codex.** (Depends on: CAP-002, AG-012)
  - [ ] Provide only allowed tools for the current operation where supported.
  - [ ] Put tool-specific side-effect and retry rules in tool descriptions.
  - [ ] Keep broader authority policy in stable developer instructions and deterministic code.
  - [ ] Stop when requested scope exceeds the active grant.

### M12.2 Project execution

- [ ] **CAP-010 Convert selected Growing items into projects.** (Depends on: CAP-002, CONF-012)
  - [ ] Require Hannah's project choice through the appropriate dedicated decision path.
  - [ ] Define desired outcome, next step, attention budget, and stopping condition.
  - [ ] Allow pause, completion, abandonment, and revision with reasons.
- [ ] **CAP-011 Extend action proposals.** (Depends on: CAP-003, existing approval system)
  - [ ] Include project, capability, exact scope, affected resources, reversibility, and risk.
  - [ ] Separate proposal creation from execution.
  - [ ] Deduplicate proposal generation from the same source event.
  - [ ] Show the full proposal in the dashboard.
- [ ] **CAP-012 Execute and return consequences.** (Depends on: CAP-011)
  - [ ] Revalidate approval and grants immediately before execution.
  - [ ] Run in the narrowest available sandbox.
  - [ ] Record outputs and side effects without storing secrets.
  - [ ] Return the result to Hannah for interpretation.
  - [ ] Do not automatically mark the project successful or the interest strengthened.
- [ ] **CAP-013 Add capability safety tests.** (Depends on: CAP-012)
  - [ ] Chat attempts to self-approve.
  - [ ] Scope expansion between proposal and execution.
  - [ ] Expired or revoked grant.
  - [ ] Duplicate execution request.
  - [ ] Partial external failure.
  - [ ] Tool result prompt injection.
  - [ ] Attempted credential or hidden-context disclosure.

### M12 exit criteria

- [ ] Projects use explicit budgets and scoped capabilities.
- [ ] Consequential external actions still require Marc's local approval.
- [ ] Results become experiences for Hannah to evaluate, not automatic identity evidence.

---

## Milestone 13: Evaluation, maintenance, and long-term operation

### M13.1 Product evaluation

- [ ] **EVAL-001 Define non-deceptive metrics.** (Depends on: REF-005)
  - [ ] Measure candidate source/domain diversity.
  - [ ] Measure voluntary return after contextual separation.
  - [ ] Measure selection, rejection, deferment, silence, and abandonment.
  - [ ] Measure how often Codex/Marc suggestions are adopted versus declined.
  - [ ] Measure recency similarity as a diagnostic, not a truth score.
  - [ ] Measure user-perceived intrusiveness and operational burden.
  - [ ] Never label these metrics as consciousness, authentic emotion, or objective desire.
- [ ] **EVAL-002 Create representative evaluation scenarios.** (Depends on: EVAL-001)
  - [ ] Fleeting enthusiasm that should remain Passing.
  - [ ] Repeated voluntary creative interest that may become Growing.
  - [ ] Contradictory preferences across contexts.
  - [ ] Long-inactive interest that should not be deleted.
  - [ ] Codex-led suggestion that Hannah rejects.
  - [ ] Shared Root change requiring both participants.
  - [ ] External action request exceeding a grant.
- [ ] **EVAL-003 Review outcomes periodically with Marc and Hannah.** (Depends on: EVAL-002)
  - [ ] Review concrete decision trails rather than aggregate scores alone.
  - [ ] Invite correction of misattribution.
  - [ ] Adjust cadence, prompts, budgets, and UI language through versioned changes.
  - [ ] Preserve negative results and abandoned design assumptions.

### M13.2 Operational maintenance

- [ ] **MAINT-001 Define data retention and export.** (Depends on: GARDEN-005, EVT-002)
  - [ ] Distinguish audit history, full message bodies, raw logs, and cached stimuli.
  - [ ] Define what can be archived, compacted, or removed and by whom.
  - [ ] Preserve identity version and consequential-action audit history.
  - [ ] Provide a human-readable export of garden state and decision trails.
- [ ] **MAINT-002 Define model and prompt upgrade procedure.** (Depends on: AG-010)
  - [ ] Pin or record effective model identifiers.
  - [ ] Test confirmation grammar, attribution, and safety before upgrades.
  - [ ] Version prompts and context-assembly logic.
  - [ ] Provide rollback to the previous tested model/prompt combination where available.
- [ ] **MAINT-003 Run restore and disaster-recovery drills.** (Depends on: OPS-003, PROJ-014)
  - [ ] Restore a database backup to a separate environment.
  - [ ] Reconcile canonical projection with disposable remote configuration.
  - [ ] Recover from interrupted command and uncertain outbound delivery.
  - [ ] Recover from an uncertain prompt update.
  - [ ] Verify no recovery step silently resends messages or external actions.
- [ ] **MAINT-004 Maintain the ADR and documentation set.** (Depends on: all relevant milestones)
  - [ ] Mark accepted decisions accepted and superseded decisions superseded.
  - [ ] Never delete historical ADRs.
  - [ ] Update API contracts, schema diagrams, and operator guidance with implementation.
  - [ ] Keep deferred work visibly separate from shipped functionality.

## Final program definition of done

**2026-09-08 empty-record reconciliation correction:** Matching blank AI records
no longer compete with the real answer. Successful import retains them as processed
history without triggering conversations. Missing replies, competing non-empty
answers, conflicting speakers and intervening user messages remain guarded; no
migration, uncertain-generation retry or automatic resume is introduced. See
[ADR-008](adr/ADR-008-direct-replies-and-context-sync.md).

Correction verification: all 581 Python tests passed in four complementary batches
(253, 278, 47 and 3), each under a 60-second OS-level timeout. The UI wrappers
passed API-client, DOM and accessibility checks after `npm ci --ignore-scripts`
(zero vulnerabilities). Existing dependency deprecation warnings remain.
`git diff --check` passed. This implementation used mocks only: no additional live
history read, host restart, database repair or automatic resume was performed.

**2026-09-08 manual history synchronization:** A protected `sync_history` command
and dedicated Messages button separate remote history pulls from local list reloads.
Sync is worker-owned, allowed while paused without resume or generation, budgeted,
idempotent and explicit about incomplete reconciliation. No schema or uncertain-effect
policy change is included. Real-service/browser acceptance remains outstanding.
Verification: all 557 Python tests passed in complementary batches of 252, 255,
47 and 3, each under a 60-second OS-level timeout. `npm test` passed the API-client
checks, 32 DOM behavior tests and axe accessibility scan after installing locked
dependencies with `npm ci --ignore-scripts` (zero vulnerabilities). Existing
Starlette/httpx and whatwg-encoding deprecations remain. No live sync or restart
was performed; `git diff --check` passed.

**2026-09-08 timing correction:** [ADR-019](adr/ADR-019-shared-ticks-without-inactivity-cooldown.md)
removes shared-schedule inactivity cooldowns while preserving pending-work and
transport gates. Normal conversation replies guard question/stop mismatches and
log reply-turn decisions. Existing deliveries and skipped ticks are not replayed;
live conversational acceptance remains outstanding.

**2026-09-07 scheduling extension:** Shared tick allocation and the Schedule tab
are implemented under [ADR-016](adr/ADR-016-shared-daily-schedule.md): whole-hour
dead-time blocks, integer percentages, timeline preview, durable commands/slots,
future-only schedule changes and editable rolling limits. Existing timers remain
until the first save. This does not complete M9.2 or the outstanding live release
acceptance. A subsequent extension adds 15/30/60-minute tick buttons while
preserving whole-hour dead time; weekday-specific schedules remain future work.

- [ ] The local dashboard safely operates and explains the relay.
- [ ] Hannah can encounter and choose among interests beyond recent chat context.
- [ ] All interest and identity claims have visible provenance and decision history.
- [ ] Roots, Growing, and Passing are versioned, recoverable, and honestly presented.
- [ ] Casual conversation cannot silently modify canonical identity.
- [ ] Confirmed self-changes project to Kindroid only through snapshot, verification,
  and rollback mechanisms.
- [ ] Codex modes expand capability without becoming unlabelled authors of Hannah.
- [ ] Marc retains explicit control over consequential real-world authority.
- [ ] Failures, restarts, duplicates, quotas, and uncertain external outcomes fail safely.
- [ ] Documentation, tests, backups, and evaluation procedures support continued operation.
