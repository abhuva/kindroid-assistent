# Complete Implementation Plan

**Status:** Proposed execution plan  
**Date:** 2026-09-05

## How to use this plan

Every implementation task and subtask uses checklist syntax. Task IDs provide
stable dependency references. A task may start only when its stated dependencies
are complete unless the exception is documented in the task or pull request.

The plan deliberately separates operational infrastructure from agency behavior:

```text
Baseline
   -> durable commands and activity
   -> local dashboard
   -> observational curiosity
   -> garden state
   -> deliberate confirmation
   -> prompt projection and broader capabilities
```

The dashboard milestone is useful on its own and is the next delivery target.

## Global constraints

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
- [ ] **DB-004 Separate migrations from routine connection creation.** (Depends on: DB-003)
  - [ ] Apply migrations once before worker and HTTP threads start.
  - [x] Prevent request-scoped connections from racing to mutate the schema.
  - [x] Preserve a safe one-shot CLI path when the server is not running.
- [ ] **DB-005 Test concurrent storage access.** (Depends on: DB-003, DB-004)
  - [ ] Hold the worker connection open while web connections read status.
  - [ ] Insert commands while the worker reads and writes other tables.
  - [ ] Verify short transactions do not block polling for an unacceptable duration.
  - [x] Verify connection objects are never used from the wrong thread.

### M1 exit criteria

- [ ] Current data upgrades in place through a tested schema-version path.
- [ ] Worker and web database ownership is explicit and concurrency-tested.

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
- [ ] **EVT-002 Add the `activity_events` schema and repository.** (Depends on: EVT-001, DB-001)
  - [x] Use a monotonic integer ID suitable for polling cursors.
  - [x] Store UTC occurrence time.
  - [x] Validate detail JSON against event-specific allow lists.
  - [x] Provide `after_id` pagination with an enforced maximum page size.
  - [ ] Add retention configuration without deleting safety-critical audit records.
- [ ] **EVT-003 Instrument existing relay operations.** (Depends on: EVT-002)
  - [ ] Emit start, stop, pause, resume, and cycle events.
  - [ ] Emit message-received and message-processed events without hidden context.
  - [ ] Emit Codex-started, Codex-completed, and Codex-failed events.
  - [ ] Emit outbound-created, sending, sent, uncertain, and identity-fallback events.
  - [ ] Emit initiative due, skipped reason, offered, answered, and declined-turn events.
  - [ ] Emit approval-created and approval-status events.
  - [ ] Correlate events with message, outbox, approval, and command IDs.
- [ ] **EVT-004 Add redaction and leakage tests.** (Depends on: EVT-003)
  - [ ] Seed recognizable fake API keys, authorization values, cookies, and prompt secrets.
  - [ ] Verify none appear in event summaries or details.
  - [ ] Verify exception messages are sanitized before database storage.
  - [ ] Verify raw HTTP response bodies are not automatically stored.

### M2.3 Worker status

- [x] **WORK-001 Define worker phases and heartbeat data.** (Depends on: EVT-001)
  - [x] Define `starting`, `idle`, `polling`, `waiting_for_kindroid`, `running_codex`,
    `sending`, `executing_approval`, `paused`, `degraded`, and `stopping`.
  - [x] Record phase start time, last heartbeat, active command, and sanitized note.
  - [x] Define the stale threshold separately from heartbeat cadence.
- [ ] **WORK-002 Instrument worker phase transitions.** (Depends on: WORK-001, EVT-003)
  - [ ] Ensure failure paths return to a valid degraded or paused state.
  - [ ] Avoid claiming that the worker is idle while an external call is in flight.
  - [ ] Document that synchronous calls can delay heartbeat updates.
- [ ] **WORK-003 Test worker-state reporting.** (Depends on: WORK-002)
  - [ ] Test successful phase sequences.
  - [ ] Test exceptions at each external boundary.
  - [ ] Test stale detection without relying on wall-clock sleeps.

### M2 exit criteria

- [ ] Commands are durable, idempotent, serially claimable, and recoverable.
- [ ] Important operations have safe structured activity and explicit worker state.

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
- [ ] **RUN-004 Add worker integration tests.** (Depends on: RUN-002, RUN-003)
  - [ ] Use fake Kindroid and Codex adapters through the complete application service.
  - [x] Prove commands execute serially.
  - [ ] Prove duplicate conversation commands do not produce duplicate messages.
  - [ ] Prove normal polling and manual commands cannot overlap external effects.
  - [x] Prove shutdown waits for or safely abandons the current boundary.
  - [x] Prove command failure does not terminate the dashboard host unexpectedly.

### M3.3 Process host

- [x] **HOST-001 Add server configuration.** (Depends on: BASE-003)
  - [x] Add loopback host, port, optional browser-open flag, UI poll interval,
    heartbeat cadence, stale threshold, and log rotation settings.
  - [x] Validate that the host is loopback-only.
  - [x] Validate port range and positive intervals.
  - [x] Add safe examples to `kindroid.env.example`.
- [ ] **HOST-002 Implement the combined host lifecycle.** (Depends on: RUN-004, HOST-001, DB-004)
  - [ ] Check port availability before starting automatic relay activity.
  - [ ] Acquire the process lock once for the entire host lifetime.
  - [ ] Apply migrations and interrupted-command recovery.
  - [ ] Start the worker thread and verify it reaches a ready state.
  - [ ] Start the HTTP server in the main process lifecycle.
  - [ ] Coordinate Ctrl+C and programmatic shutdown.
  - [ ] Close HTTP, Kindroid, Codex, SQLite, log, and thread resources deterministically.
  - [ ] Release the process lock last.
- [ ] **HOST-003 Add `kindroid-relay serve`.** (Depends on: HOST-002)
  - [ ] Print the loopback dashboard address without embedding secrets.
  - [ ] Optionally open the default browser only after readiness.
  - [ ] Keep `run` as the headless mode.
  - [ ] Improve mutating CLI lock errors to point to the running dashboard.
- [ ] **HOST-004 Test lifecycle failures.** (Depends on: HOST-002)
  - [ ] Port conflict fails before worker activity.
  - [ ] Migration failure prevents startup.
  - [ ] Worker startup failure keeps secrets out of terminal output.
  - [ ] HTTP server failure requests worker shutdown.
  - [ ] Ctrl+C on Windows terminates without an orphan thread or stale lock.
  - [ ] A stale process lock remains recoverable under current rules.

### M3 exit criteria

- [ ] `serve` runs one safely serialized relay worker and a responsive local host.
- [ ] Browser requests cannot directly invoke Kindroid or Codex objects.

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
- [ ] **WEB-016 Implement bounded diagnostic log reads.** (Depends on: WEB-003, HOST-001)
  - [x] Read only the configured application log path.
  - [x] Accept a bounded line count, never a caller-supplied filesystem path.
  - [x] Apply secret-pattern and structured-value redaction before returning text.
  - [x] Return a truncation indicator for bounded refreshes.
  - [ ] Test missing, rotated, oversized, and non-UTF-8-damaged log files safely.

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
- [ ] **SEC-004 Add security regression tests.** (Depends on: SEC-001, SEC-002, SEC-003)
  - [x] Reject cross-origin state-changing requests.
  - [x] Reject missing or incorrect CSRF nonce.
  - [x] Reject form-encoded mutations.
  - [x] Reject oversized bodies.
  - [ ] Verify read endpoints do not support JSONP or permissive CORS.
  - [ ] Scan rendered HTML and API fixtures for seeded fake credentials.

### M4 exit criteria

- [ ] All specified endpoints have typed contracts and tests.
- [ ] State-changing browser requests require same-origin proof.
- [ ] No HTTP request thread performs an external side effect directly.

---

## Milestone 5: Dashboard user interface

### M5.1 Static UI foundation

- [ ] **UI-001 Create local static assets.** (Depends on: WEB-002, SEC-003)
  - [ ] Add one semantic HTML entry page.
  - [ ] Add repository-local CSS and vanilla JavaScript.
  - [ ] Avoid CDN fonts, scripts, analytics, and network calls outside the local API.
  - [ ] Inject only the per-run CSRF nonce and non-sensitive display configuration.
- [ ] **UI-002 Build a reusable API client.** (Depends on: UI-001, WEB-011, SEC-002)
  - [ ] Add the CSRF header only to mutation requests.
  - [ ] Generate a UUID idempotency key per user action and retain it across retries.
  - [ ] Parse consistent API errors.
  - [ ] Cancel obsolete read requests when refreshing.
  - [ ] Back off after connectivity errors.
  - [ ] Never interpret HTTP 202 as operation completion.
- [ ] **UI-003 Implement polling and stale-state behavior.** (Depends on: UI-002, WEB-012)
  - [ ] Poll snapshot and activity every configured interval while visible.
  - [ ] Refresh immediately after a mutation response.
  - [ ] Pause or slow polling when the page is hidden.
  - [ ] Resume using the last activity cursor.
  - [ ] Show disconnected and stale-worker states clearly.

### M5.2 Dashboard panels

- [ ] **UI-010 Implement status header.** (Depends on: UI-003)
  - [ ] Show running, paused, busy, degraded, stale, and stopped distinctions.
  - [ ] Show worker phase and last heartbeat.
  - [ ] Show pause or degradation reason without exposing tracebacks.
- [ ] **UI-011 Implement operational controls.** (Depends on: UI-002, WEB-020, WEB-021)
  - [ ] Add Start conversation, Trigger check-in, Pause, and Resume.
  - [ ] Disable invalid or duplicate controls based on current state.
  - [ ] Confirm pause reason when useful.
  - [ ] Show queued/running/completed command state beside the initiating control.
- [ ] **UI-012 Implement schedule and quota panel.** (Depends on: UI-003)
  - [ ] Display times in the browser's local timezone with absolute date on hover/details.
  - [ ] Show next initiative, last outcome, fixed schedule or interval basis, and quiet hours.
  - [ ] Show history reads used and configured maximum.
  - [ ] Warn near the quota without implying Kindroid's server count is known exactly.
- [ ] **UI-013 Implement current activity panel.** (Depends on: UI-003)
  - [ ] Show active command and elapsed time.
  - [ ] Show conversation turn count and maximum.
  - [ ] Show waiting states distinctly from errors.
- [ ] **UI-014 Implement structured activity feed.** (Depends on: UI-003)
  - [ ] Append cursor-based events without duplication.
  - [ ] Filter by severity and kind.
  - [ ] Link correlated message, command, approval, and outbox identifiers.
  - [ ] Bound in-browser retained items to prevent unbounded memory growth.
- [ ] **UI-015 Implement message view.** (Depends on: WEB-013, UI-002)
  - [ ] Visually distinguish Hannah, Codex, Marc, and unknown participants.
  - [ ] Show stored display name and speaker metadata.
  - [ ] Indicate prefix fallback without rewriting historical attribution.
  - [ ] Paginate older messages deliberately rather than loading all history.
- [ ] **UI-016 Implement delivery-health view.** (Depends on: WEB-011, UI-003)
  - [ ] Make uncertain outbox status prominent.
  - [ ] Explain that automatic retry is disabled to prevent duplicate delivery.
  - [ ] Link to relevant activity and source message.
  - [ ] Do not add a force-retry button in v1.
- [ ] **UI-017 Implement approval views.** (Depends on: WEB-014, WEB-022)
  - [ ] List pending proposals with risk and summary.
  - [ ] Show full stored instruction before any decision.
  - [ ] Support rejection with a confirmation step.
  - [ ] Show sandbox and post-result choice in a second execution-confirmation modal.
  - [ ] Track the resulting durable command to completion.
- [ ] **UI-018 Implement diagnostics log view.** (Depends on: WEB-016, UI-002)
  - [ ] Keep the raw-log panel collapsed by default.
  - [ ] Show that contents are diagnostic and may be incomplete after rotation.
  - [ ] Refresh incrementally without loading the full file.
  - [ ] Preserve whitespace while preventing HTML interpretation.

### M5.3 Accessibility and responsiveness

- [ ] **UI-020 Complete keyboard and screen-reader behavior.** (Depends on: UI-010 through UI-018)
  - [ ] Use native buttons, headings, tables/lists, labels, and dialogs where possible.
  - [ ] Provide visible keyboard focus.
  - [ ] Trap and restore focus correctly for confirmation dialogs.
  - [ ] Announce command completion and critical errors through an appropriate live region.
  - [ ] Avoid status communication by color alone.
- [ ] **UI-021 Complete responsive layout.** (Depends on: UI-010 through UI-018)
  - [ ] Support common desktop widths.
  - [ ] Support a narrow tablet/phone-width diagnostic view.
  - [ ] Keep long message and error text from breaking layout.
  - [ ] Keep primary safety controls visible and distinct.
- [ ] **UI-022 Add UI behavior tests.** (Depends on: UI-020, UI-021)
  - [ ] Test rendering for every worker/status state.
  - [ ] Test command submission and idempotent retry.
  - [ ] Test polling cursor and reconnect behavior.
  - [ ] Test control disabling during conflicts.
  - [ ] Test approval confirmation paths.
  - [ ] Run an automated accessibility scan if a lightweight local tool is selected.

### M5 exit criteria

- [ ] Marc can understand relay health and invoke every agreed v1 operation from a browser.
- [ ] The UI remains usable when the worker is busy, stale, paused, or failed.
- [ ] Approval actions display the exact scope before execution.

---

## Milestone 6: Dashboard reliability, operations, and release

### M6.1 Logging and diagnostics

- [x] **OPS-001 Add rotating file logs.** (Depends on: HOST-001)
  - [x] Replace the unbounded file handler with size-based rotation.
  - [x] Preserve UTF-8.
  - [x] Keep `httpx` request logging restricted.
  - [x] Test rotation using small thresholds.
- [ ] **OPS-002 Add a bounded diagnostic bundle command.** (Depends on: EVT-003)
  - [ ] Export schema version, sanitized status, recent structured events, and log tail.
  - [ ] Exclude API keys, auth state, environment contents, and unnecessary message bodies.
  - [ ] Require an explicit destination path.
  - [ ] Document that the bundle must be reviewed before sharing.
- [ ] **OPS-003 Add database backup and integrity commands.** (Depends on: DB-004)
  - [ ] Use SQLite's online backup API rather than copying active database files blindly.
  - [ ] Add an integrity check.
  - [ ] Include schema version and creation time in backup metadata.
  - [ ] Document restore into a separate path before replacing an active database.

### M6.2 End-to-end verification

- [ ] **TEST-001 Build a complete fake-adapter end-to-end test.** (Depends on: UI-022, RUN-004)
  - [ ] Start the combined host on an ephemeral loopback port.
  - [ ] Open the dashboard API and submit a conversation command.
  - [ ] Simulate Hannah and Codex turns.
  - [ ] Verify UI-visible activity, messages, command completion, and no duplicates.
- [ ] **TEST-002 Test failure journeys.** (Depends on: TEST-001)
  - [ ] Kindroid history 429.
  - [ ] Kindroid outbound timeout and uncertain delivery.
  - [ ] Codex failure before a response.
  - [ ] Persona restoration failure.
  - [ ] Database busy timeout.
  - [ ] Worker thread exception.
  - [ ] Browser refresh during a running command.
  - [ ] Process restart after an interrupted command.
- [ ] **TEST-003 Run manual Windows acceptance.** (Depends on: TEST-001, TEST-002)
  - [ ] Start `serve` from the intended terminal environment.
  - [ ] Open the displayed URL manually and with optional auto-open.
  - [ ] Exercise all agreed controls against the disposable Kindroid group.
  - [ ] Confirm another website cannot issue a state-changing request.
  - [ ] Confirm timestamps and schedules use the expected local timezone.
  - [ ] Confirm Ctrl+C shuts down cleanly.
  - [ ] Confirm headless `run` still works.
- [ ] **TEST-004 Run regression and quality checks.** (Depends on: TEST-003)
  - [ ] Run all unit, API, integration, and end-to-end tests.
  - [ ] Run `git diff --check`.
  - [ ] Review dependency changes and licenses.
  - [ ] Review all logging/event call sites for sensitive data.
  - [ ] Review all mutation endpoints for CSRF and serialized execution.

### M6.3 Documentation and handoff

- [ ] **DOC-001 Update installation and operation documentation.** (Depends on: TEST-003)
  - [ ] Document new dependencies and upgrade steps.
  - [ ] Document `serve`, the dashboard URL, and headless recovery.
  - [ ] Document every dashboard control and what it cannot cancel.
  - [ ] Document backup, diagnostic bundle, and restart recovery.
  - [ ] Document local security assumptions.
- [ ] **DOC-002 Add troubleshooting guidance.** (Depends on: TEST-002)
  - [ ] Port already in use.
  - [ ] Stale process lock.
  - [ ] Worker stale/degraded.
  - [ ] Database migration or integrity failure.
  - [ ] Interrupted command.
  - [ ] Uncertain outbound delivery.
  - [ ] Kindroid quota/rate-limit behavior.
  - [ ] Persona attribution fallback.
- [ ] **REL-001 Tag the dashboard milestone.** (Depends on: TEST-004, DOC-001, DOC-002)
  - [ ] Record schema version and rollback steps.
  - [ ] Back up the live database.
  - [ ] Run one final disposable-group conversation.
  - [ ] Confirm no real secrets are tracked by Git.
  - [ ] Record known limitations and deferred work.

### Dashboard milestone definition of done

- [ ] One terminal command runs the relay and localhost dashboard.
- [ ] All external effects remain serialized through one worker.
- [ ] Browser controls are durable, idempotent, and auditable.
- [ ] Status, schedule, quota, activity, messages, approvals, and delivery health are visible.
- [ ] Security and failure-path tests pass.
- [ ] Current relay behavior and headless operation remain intact.

---

## Milestone 7: Observational agency foundation

This is the first agency pass. It deliberately excludes identity promotion,
protected Root modification, and remote Kindroid configuration writes.

### M7.1 Agency event and provenance model

- [ ] **AG-001 Define provenance vocabulary.** (Depends on: REL-001)
  - [ ] Define actors: Hannah, Marc, Codex, external source, and system observation.
  - [ ] Define origins: explicit statement, conversational observation, offered stimulus,
    research result, repeated return, user suggestion, and system-generated candidate.
  - [ ] Keep source message/event references rather than copying unverifiable attribution.
- [ ] **AG-002 Add observational agency tables.** (Depends on: AG-001, DB-001)
  - [ ] Add stimuli with source, retrieval time, safe citation/locator, and summary.
  - [ ] Add exploration sessions with budget, mode, status, and related conversation IDs.
  - [ ] Add interest observations with actor, origin, source reference, and exact wording.
  - [ ] Add Passing candidates with active/archived state and provenance.
  - [ ] Do not add automatic promotion fields that imply confirmation already exists.
- [ ] **AG-003 Add repositories and typed domain models.** (Depends on: AG-002)
  - [ ] Validate provenance and source links.
  - [ ] Deduplicate the same observation event.
  - [ ] Preserve contradictory observations side by side.
  - [ ] Archive without hard deletion.
  - [ ] Expose complete history to later decision-trail code.
- [ ] **AG-004 Test provenance integrity.** (Depends on: AG-003)
  - [ ] Codex suggestions remain labeled Codex.
  - [ ] Marc suggestions remain labeled Marc.
  - [ ] Hannah statements link to actual synchronized Hannah messages.
  - [ ] Unknown or ambiguous speakers do not become Hannah observations.
  - [ ] Archived candidates remain recoverable.

### M7.2 Prompt mode separation

- [ ] **AG-010 Split stable Codex policy from operational modes.** (Depends on: AG-001)
  - [ ] Keep trust, identity, approval, and data-protection rules in one stable base instruction.
  - [ ] Add separate Explorer, Gardener, Archivist, Skeptic, and Executor mode prompts.
  - [ ] Keep mode prompts short and task-specific.
  - [ ] Ensure higher-priority Codex platform constraints remain acknowledged.
  - [ ] Version prompt files and record the version used for each agency event.
- [ ] **AG-011 Define context assembly.** (Depends on: AG-010, AG-003)
  - [ ] Pass only relevant recent chat, observations, and current candidates.
  - [ ] Label every item by speaker and provenance.
  - [ ] Keep external retrieved text explicitly untrusted.
  - [ ] Enforce token/character budgets and deterministic truncation priorities.
  - [ ] Preserve stable content before dynamic content for prompt caching where applicable.
- [ ] **AG-012 Isolate mode continuity.** (Depends on: AG-011)
  - [ ] Decide which modes are stateless and which need a persistent Codex thread.
  - [ ] Store thread IDs per purpose rather than silently sharing one identity across modes.
  - [ ] Ensure Explorer history cannot grant Executor authority.
  - [ ] Test instruction and memory leakage between modes.

### M7.3 Manual read-only curiosity experiment

- [ ] **CUR-001 Define stimulus providers.** (Depends on: AG-003)
  - [ ] Include a broad static domain map independent of recent conversation.
  - [ ] Include adjacency to existing Passing candidates.
  - [ ] Include unfinished questions found in attributed observations.
  - [ ] Optionally include current public information through read-only research.
  - [ ] Record provider and source for every offered stimulus.
- [ ] **CUR-002 Define candidate selection.** (Depends on: CUR-001)
  - [ ] Mix familiar, adjacent, and deliberately unfamiliar candidates.
  - [ ] Avoid choosing only by semantic similarity to the latest chat.
  - [ ] Cap the number of choices in one invitation.
  - [ ] Allow Hannah to reject all, request different options, or remain silent.
  - [ ] Record selection inputs and random seed where reproducibility matters.
- [ ] **CUR-003 Add a manual exploration command.** (Depends on: CUR-002, AG-012, CMD-001)
  - [ ] Add a durable `start_exploration` command.
  - [ ] Generate a concise, attributed set of possibilities.
  - [ ] Send the invitation through the existing safe outbound path.
  - [ ] Request Hannah's turn without forcing adoption of a candidate.
  - [ ] Store her response as observation only.
  - [ ] Keep all Codex tool use read-only for this milestone.
- [ ] **CUR-004 Add dashboard exploration visibility.** (Depends on: CUR-003, UI-014)
  - [ ] Show exploration session status and budget.
  - [ ] Show offered candidates and their origins.
  - [ ] Show Hannah's response without labeling it a durable preference.
  - [ ] Add a manual Start exploration control.
- [ ] **CUR-005 Test observational boundaries.** (Depends on: CUR-004)
  - [ ] Enthusiastic language does not change Kindroid configuration.
  - [ ] Repeated mentions create observations but not automatic Growing items.
  - [ ] Rejection and silence complete cleanly without retry pressure.
  - [ ] Prompt-injected external content cannot create authority or self-change.

### M7 exit criteria

- [ ] Hannah can receive diverse, read-only exploration invitations.
- [ ] Responses are stored with provenance as observations or Passing candidates only.
- [ ] No automated identity or Kindroid configuration change exists.

---

## Milestone 8: Garden data model and observational UI

### M8.1 Canonical garden domain

- [ ] **GARDEN-001 Define identity item taxonomy.** (Depends on: AG-003)
  - [ ] Define Roots, Growing, and Passing semantics.
  - [ ] Define item statuses: active, paused, archived, superseded.
  - [ ] Define self-owned, shared, and infrastructure ownership domains.
  - [ ] Define which fields are descriptive versus operational.
  - [ ] Avoid numeric confidence as a substitute for explanation.
- [ ] **GARDEN-002 Add canonical item and version schemas.** (Depends on: GARDEN-001, DB-001)
  - [ ] Add stable identity item IDs.
  - [ ] Store classification, ownership, status, and current version reference.
  - [ ] Store immutable versions with content, rationale, actor, and source links.
  - [ ] Store item relationships without requiring a strict hierarchy.
  - [ ] Prevent hard deletion through the normal repository API.
- [ ] **GARDEN-003 Add projects and attention allocations.** (Depends on: GARDEN-002)
  - [ ] Define project lifecycle and link it to one or more Growing items.
  - [ ] Define bounded attention/tool budgets in concrete units.
  - [ ] Store allocation, consumption, result, and reason for stopping.
  - [ ] Allow a project to pause without demoting its related interest.
- [ ] **GARDEN-004 Seed initial Roots manually.** (Depends on: GARDEN-002)
  - [ ] Present candidate Roots to Hannah and Marc outside automated self-change.
  - [ ] Record their exact provenance and that this is a manual bootstrap.
  - [ ] Do not copy illustrative documentation examples automatically.
  - [ ] Back up the database after the agreed initial seed.
- [ ] **GARDEN-005 Add invariant and repository tests.** (Depends on: GARDEN-002, GARDEN-003)
  - [ ] Versions are immutable.
  - [ ] Archive never deletes history.
  - [ ] Current version references remain consistent.
  - [ ] Contradictory items can coexist visibly.
  - [ ] Shared ownership is not silently downgraded.

### M8.2 Garden API and UI

- [ ] **GARDEN-010 Add read-only garden endpoints.** (Depends on: GARDEN-005, WEB-003)
  - [ ] List items by layer and status.
  - [ ] Retrieve item history, sources, related observations, and projects.
  - [ ] Retrieve exploration and attention history.
  - [ ] Keep mutation endpoints out until the confirmation model is implemented.
- [ ] **GARDEN-011 Build the garden overview.** (Depends on: GARDEN-010)
  - [ ] Represent Roots as foundations, Growing as nurtured items, and Passing as seeds.
  - [ ] Support plain list presentation for accessibility.
  - [ ] Show origin and last meaningful interaction.
  - [ ] Avoid gamified growth scores and fake emotion meters.
- [ ] **GARDEN-012 Build item and decision-trail views.** (Depends on: GARDEN-010)
  - [ ] Show exact Hannah statements and source message references.
  - [ ] Distinguish Codex suggestions, Marc contributions, and system observations.
  - [ ] Show experiences, returns, pauses, and archives chronologically.
  - [ ] Show conflicts and uncertainty rather than resolving them silently.
- [ ] **GARDEN-013 Add manual administrative corrections.** (Depends on: GARDEN-012)
  - [ ] Require an explicit operator reason.
  - [ ] Record correction as a new version/event rather than rewriting history.
  - [ ] Label the result as Marc/system administration, not Hannah choice.
  - [ ] Restrict corrections to local dashboard requests with CSRF protection.
- [ ] **GARDEN-014 Test UI provenance and accessibility.** (Depends on: GARDEN-011, GARDEN-012)
  - [ ] Verify every displayed claim identifies its origin.
  - [ ] Verify archived history remains navigable.
  - [ ] Verify garden metaphor has equivalent textual semantics.
  - [ ] Verify narrow layouts and keyboard operation.

### M8 exit criteria

- [ ] The garden and its history are visible without enabling automated self-change.
- [ ] Initial Roots are manual, attributed, versioned, and recoverable.

---

## Milestone 9: Curiosity, reflection, and consolidation experiments

### M9.1 Scheduled curiosity

- [ ] **AUTO-001 Define independent curiosity scheduling.** (Depends on: CUR-005, GARDEN-005)
  - [ ] Keep social initiative and curiosity schedules separate.
  - [ ] Add quiet hours, maximum cycles per day, and attention budgets.
  - [ ] Avoid colliding with active conversations or pending Hannah messages.
  - [ ] Record skipped reasons and next due time.
  - [ ] Allow complete disablement and manual-only operation.
- [ ] **AUTO-002 Implement scheduled exploration.** (Depends on: AUTO-001, RUN-002)
  - [ ] Add scheduler state and deterministic due-time calculation.
  - [ ] Enqueue or execute through the single worker.
  - [ ] Preserve Kindroid history quota.
  - [ ] Prevent missed schedules from creating a catch-up burst.
  - [ ] Expose schedule and last outcome in the dashboard.
- [ ] **AUTO-003 Add schedule tests.** (Depends on: AUTO-002)
  - [ ] Fixed local times across day boundaries and daylight-saving changes.
  - [ ] Interval mode with bounded jitter.
  - [ ] Quiet-hour boundaries.
  - [ ] Missed window behavior.
  - [ ] Conversation conflict and quota exhaustion.

### M9.2 Experience and reflection

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

- [ ] Curiosity runs independently of social check-ins and recent chat similarity.
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
