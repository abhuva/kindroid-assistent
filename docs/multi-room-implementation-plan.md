# Multi-room conversations and participant-chosen encounters

Status: implementation and local verification complete. Agreed with Marc on 2026-09-10.

## Intended behavior

One host and worker manage Hannah/Codex, Miko/Codex, and Hannah/Miko/Codex.
Room context stays isolated. Private encounters begin as check-ins offering
exploration as a choice. A participant's requested next encounter replaces both
automatic check-ins and automatic exploration. The shared room starts on a daily
schedule, alternates companions silently, invokes Codex on case-insensitive
`codex` mentions, and checks occasionally for a natural end. Local authority,
durable effect recovery and shared account limits remain enforced.

## Tasks

- [x] Record the accepted architecture and compatibility policy.
  - [x] Document isolated rooms, one worker and account-wide history accounting.
  - [x] Document participant scheduling permission, precedence and no-follow-up.
  - [x] Document silent shared sessions, termination and generation recovery.
- [x] Implement room configuration and isolation.
  - [x] Preserve existing single-room configuration/database and upgrade data.
  - [x] Validate explicit room IDs, participant IDs/names and room modes.
  - [x] Isolate transcripts, Codex threads, approvals, schedules and memory.
  - [x] Share history spacing, rolling allowance and cooldown across rooms.
  - [x] Serialize room work and yield between conversation turns.
- [x] Implement durable participant-selected encounters.
  - [x] Add numbered migrations preserving all existing records.
  - [x] Parse bounded structured next-time/no-follow-up choices with provenance.
  - [x] Validate time, attribution and local opt-in before persisting a choice.
  - [x] Suppress both automatic activity types while a choice governs the room.
  - [x] Ask for next timing before closing; acknowledge only persisted choices.
  - [x] Offer exploration within check-ins; execute the existing bounded invitation
        path only after a clear choice, preserving capability limits.
  - [x] Handle replacement, cancellation, missed appointments and restart safely.
- [x] Implement shared-room sessions.
  - [x] Schedule explicit once-daily starts using the durable command worker.
  - [x] Generate for the configured alternating participant without get-turn.
  - [x] Persist generation intent anchored to the preceding message.
  - [x] Reconcile consecutive AI replies, empty bodies and interrupted effects.
  - [x] Wake Codex on new participant mentions, deduplicating old/self mentions.
  - [x] Support assistance follow-ups without requiring repeated keyword use.
  - [x] Check for natural endings periodically without posting commentary.
  - [x] Persist session end, enforce duration/message budgets and manual end.
- [x] Expose operator controls.
  - [x] Provide room navigation and per-room status, schedule and approvals.
  - [x] Show chosen next encounter, source and outcome; allow override/cancel.
  - [x] Show shared-session state and an End session control.
  - [x] Preserve loopback, Host, CSRF, bounded-input and idempotency defenses.
  - [x] Provide CLI room selection and operational recovery behavior.
- [x] Verify with mocks and fixtures under hard timeouts.
  - [x] Migration preservation and cross-room isolation tests.
  - [x] Scheduling/choice/decline/duplicate/restart/failure tests.
  - [x] Shared alternation/keyword/silent-stop/uncertain generation tests.
  - [x] Shared-budget, worker fairness and dashboard security tests.
  - [x] Install locked UI dependencies and run DOM/accessibility checks.
  - [x] Run the complete Python suite in bounded batches if necessary.
  - [x] Review the final diff and reconcile every checkbox with evidence.
- [x] Update README, documentation map and operator/upgrade instructions.
  - [x] Include an example configuration without credentials or live IDs.
  - [x] Explain rollout, backup, manual Kindroid selection and remaining acceptance.

## Verification record

Completed locally on 2026-09-10:

- Complete Python suite: **614 passed**, in three isolated batches of 230, 231
  and 153 tests. Each child had a 60-second hard timeout.
- New room/encounter regression coverage: **33 passed**, included in that suite.
- `npm ci --ignore-scripts`: locked dependencies installed; zero vulnerabilities.
- `npm test`: API-client checks, **35 DOM tests** and **one multi-view axe test**
  passed. Browser network calls were mocked; axe color-contrast is excluded by
  the existing jsdom harness.
- `git diff --check`: clean.
- Existing dependency warnings: Starlette/httpx test-client deprecation, and the
  npm dependency's whatwg-encoding deprecation. No live provider tests were run.

The architecture-review and architecture-decision skills informed the isolated
room-store approach and ADR-020's recorded alternatives. Implementation and
operator documentation are reconciled; older current-system and milestone
documents remain explicitly historical where noted.

## Stability follow-up: manual shared-room participation

- [x] Reproduce the permanent history-boundary failure with synthetic app input.
  - [x] Cover captured, empty and interrupted generation responses.
  - [x] Preserve ambiguity protection for duplicate or missing responses.
- [x] Continue from reconciled app context without extra polling/model calls.
  - [x] Preserve session age, limits, speaker identity and stable message links.
  - [x] Count external companion replies once; ignore stale history snapshots.
  - [x] Supersede stale attention and cancel only never-sent answers.
  - [x] Preserve delivered/uncertain effects and specific room pause reasons.
- [x] Document restart, successful sync, then resume recovery without resetting state.
  - [x] Record alternatives and safety boundaries in ADR-021 using the
        architecture-decision skill.
- [x] Complete full-suite verification and final diff checks for this follow-up.

Completed on 2026-09-11:

- 18 new offline regression cases passed, including restart and uncertain effects.
- Every one of the 632 suite cases has a passing result. Full-suite batches were
  230 passed, 231 passed, and 170 passed with one dashboard DOM timeout. The
  isolated `tests/test_ui_assets.py` rerun passed all three checks in 18.22 seconds,
  including the previously timed-out DOM harness and axe accessibility check.
  Every Python child had a hard OS-level timeout of at most 60 seconds.
- `npm ci --ignore-scripts` completed with zero vulnerabilities. Existing
  whatwg-encoding and Starlette/httpx deprecation warnings remain. No UI timeout
  thresholds were increased; the first-run DOM timeout remains a test-flakiness
  warning, not evidence of a clean first-pass suite.
- `git diff --check` passed. No live state, remote chats or running relay changed.

Implementation files: `turns.py`, `relay.py`, `shared.py`, `room_worker.py` and
`activity.py`; regression coverage is in `tests/test_shared_interjections.py`.
README, this checklist, the operator guide, documentation index and ADR-008/020
links were updated alongside the new ADR-021. Older system/milestone snapshots
remain historical; this follow-up does not implement the requested UI redesign.

## Follow-up: misleading completed sync and unresolved live mismatch

- [x] Inspect only redacted localhost status/command results, without fetching chat.
  - [x] Confirm the two latest sync attempts returned `reconciliation_unresolved`
        with zero messages added, despite their completed command lifecycle status.
- [x] Surface fixed, privacy-safe matching reasons in sync results and pause activity.
  - [x] Cover absent predecessors/replies, changed text, speaker and duplicate cases.
  - [x] Never publish raw exception text or unknown diagnostic reason codes.
- [x] Render blocked syncs as blocked and emit warning completion activity.
  - [x] Retain existing durable command transitions and same-request semantics.
- [x] Explain why blocked reconciliation leaves saved history behind the app.
- [x] Complete full-suite verification of diagnostic/UI changes.
- [x] Identify the exact remaining live mismatch from an explicitly approved
      read-only diagnostic or the next operator-run sync with detailed diagnostics.
- [ ] Verify recovery of the affected shared room; the earlier interjection fix
      did not resolve this live instance, and no state reset is authorized.

Verification on 2026-09-11: **638 passed**, in isolated batches of 230, 231, 174
and three UI checks, each under a 60-second hard timeout. All batches passed on
their first attempt in this follow-up. `npm ci --ignore-scripts` completed with
zero vulnerabilities; existing whatwg-encoding and Starlette/httpx deprecations
remain. `git diff --check` passed. Six additional backend diagnostic regression
cases and the expanded existing DOM blocker test are included.

Changed files for this follow-up: `turns.py`, `relay.py`, `application.py`,
`worker.py`, `web/static/ui-model.js`, `tests/test_shared_interjections.py`,
`tests/ui_dashboard_test.cjs`, README, dashboard architecture, operator guide and
this checklist. No new schema, matching relaxation or stale-message replacement
was introduced. The remaining live diagnosis/recovery tasks above are not done.
No Kindroid request, relay restart/resume or database mutation was performed as
part of the localhost inspection. Existing-message edit support is not implemented;
it remains a separate limitation, not an established explanation of this failure.

The subsequent operator-approved one-page diagnostic identified the actual
remaining blocker: **one pending generation with no captured response text and
two matching replies from its requested companion** (`multiple_matching_replies`).
The fetched window contained six messages and reported no more pages. These
window counts do not establish that older saved messages were deleted remotely.
The comparison ran against an in-memory SQLite snapshot; no conversation records,
generation links, sessions or pause state were changed. Exactly one history read
was reserved in the account ledger, with spacing/cooldown/allowance checks. No
second fetch, message send, generation, restart or resume occurred. Provider text,
IDs and credentials were neither printed nor saved as diagnostic artifacts.

This is genuine ambiguous attribution, not evidence for relaxing matching further.
Recovery of this instance remains open: an operator-directed session abandonment
and fresh-baseline workflow needs explicit scope/approval rather than silently
binding one of the replies or deleting the uncertain generation record.

## Operator recovery implementation (2026-09-11)

- [x] Record the approved relay-only scope and alternatives in ADR-022, using the
      architecture-decision skill to preserve the uncertainty/authority distinction.
- [x] Add migration 13 with recovery audit records and an active-generation view.
  - [x] Preserve every existing generation, session, message and source link.
  - [x] Exclude only operator-abandoned generations from continuation/reconciliation.
- [x] Implement a confirmed, session/turn-scoped worker recovery command.
  - [x] Require a paused shared room and an uncaptured interrupted generation.
  - [x] Block other unresolved replies, outgoing deliveries and stale targets.
  - [x] Fetch complete fresh history within normal shared quota/cooldown/spacing.
  - [x] Commit imported processed history, session end, baseline and audit atomically.
  - [x] Retain the uncertain original request without assigning a reply or retrying it.
  - [x] Keep the room paused, with manual resume/start and unchanged daily schedule.
  - [x] Handle duplicate requests and interruption after commit idempotently.
- [x] Expose room-scoped eligibility, confirmation, command results and history refresh.
  - [x] Preserve Host/CSRF, queue ownership and same-request retry protections.
- [x] Add offline regression coverage for successful recovery/restart, guards,
      pagination, rollback, interruption, migration and UI confirmation/cancellation.
- [x] Update README, operator guide, dashboard contract and documentation index.
- [x] Complete full-suite verification and final diff checks.
- [ ] Operator deploy/restart and recovery of the live room; no live changes or
      provider requests were made while implementing this action.

Verification on 2026-09-11: **669 passed** across isolated batches of 230, 231,
205 and three UI asset checks, each with a 60-second hard OS timeout. The first
batch initially exposed three stale expected migration-version lists; those and
the equivalent older upgrade fixtures were updated through schema 13, retaining
their original data-preservation assertions, before the passing full rerun.

- 31 new recovery cases cover the observed ambiguity, restart/idempotency,
  room isolation/shared allowance, pagination, no partial commits, API/authority
  guards and migration preservation.
- UI tests cover explicit confirmation/cancellation, lost-response retry with
  the same key, blocked outcomes, local message refresh and retained pause. The
  existing axe harness also scans the visible shared recovery controls.
- `npm ci --ignore-scripts` completed with zero vulnerabilities. Existing
  whatwg-encoding and Starlette/httpx deprecation warnings remain.
- `git diff --check` passed. Real-browser native confirmation/visual acceptance
  and live operator recovery remain unverified; tests use fake providers only.

Implementation: new `shared_recovery.py`, migration 13, active-generation gates in
`turns.py`/`schedule.py`/`curiosity.py`/`experience.py`, shared history-fetch boundary
in `relay.py`, and queue/worker/activity plus dashboard API/controls. Regression
coverage is in `tests/test_shared_recovery.py`, `tests/ui_dashboard_test.cjs` and
the updated migration-version fixtures. README, operator/dashboard guides,
documentation index and ADR-022 describe the new behavior. Older system snapshots
remain explicitly historical. This control intentionally does not repair changed
confirmed message text or uncertain outgoing Codex sends.

## Responsive shared endings (2026-09-11)

- [x] Add local farewell hints that request silent attention before generation.
  - [x] Keep semantic stopping with the model; preserve periodic/hard-limit fallbacks.
  - [x] Coalesce overlapping mention, assistance, farewell and periodic triggers.
- [x] Make every attention prompt explicitly assess ending and return a bounded reason.
  - [x] Validate verdict/flag agreement; retain existing saved-decision compatibility.
  - [x] Log source-correlated check triggers and decisions without raw model rationale.
- [x] Add offline regression cases for farewell routing, false positives, restart,
      cooldown, stale context, invalid verdicts and actual adapter prompt/schema inputs.
- [x] Document behavior, cost tradeoffs and unchanged shared daily scheduling in ADR-023
      and the operator guide (architecture-decision skill).
- [x] Pass targeted and full-suite bounded verification and final diff checks.
- [ ] Marc verifies real-model ending recognition; no live calls authorized by this work.

Verification: **706 passed** across isolated backend batches of 230, 214 and 259,
plus three UI asset/DOM/accessibility checks, each with a 60-second hard OS limit.
The 37 new ending cases use fake model verdicts and provider adapters. The first
targeted run found a missing prompt fixture in three adapter tests; it was fixed
before the passing rerun. A UI run concurrent with backend verification hit its
internal 20-second timeout; all three UI checks passed on an unchanged, isolated
rerun in 20.14 seconds. No UI changes were made for this milestone.

`npm ci --ignore-scripts` completed with zero vulnerabilities; existing
whatwg-encoding and Starlette/httpx deprecation warnings remain. `git diff --check`
passed. No live Kindroid/model calls, relay restarts or live-state edits occurred.
Earlier operator/deployment acceptance checkboxes are historical and have not
been reverified as part of this implementation.

Changed runtime files: `shared.py`, new `shared_ending.py`, `codex_agent.py` and
`activity.py`; tests: new `test_shared_ending.py` and updated shared-agent fixtures
in `test_room_encounters.py`. README, this plan, the operator guide, documentation
index and ADR-020/ADR-023 describe the behavior. OpenAI Docs informed the explicit
examples and bounded verdict contract; [official Structured Outputs guidance](https://developers.openai.com/api/docs/guides/structured-outputs)
also notes that schema-conforming output can still make semantic mistakes, hence
the separate real-model acceptance item rather than claiming fixture tests prove
goodbye recognition. The architecture-decision skill guided ADR-023 and its
alternatives/cost tradeoffs.

## Development dashboard and timing recovery (2026-09-11)

- [x] Render mutually exclusive room buttons with the active room marked, including the root route.
- [x] Group Activity by durable session IDs; retain expandable details, warnings, filters and all-events view.
- [x] Save missed appointments as one recovery check-in offering now or rescheduling.
- [x] Save system six-hour retries for absent/invalid timing and declined turns; honor explicit no-follow-up.
- [x] Move retries to the next permitted minute and preserve pause, budget and uncertain-effect gates.
- [x] Add validated per-companion quiet-hour controls, shared across that companion's rooms.
- [x] Hold private/shared work and queued external commands while allowing local settings and explicit history sync.
- [x] Document the changed scheduling decision in ADR-024 using the architecture-decision skill.
- [x] Verify offline behavior, durable command ownership, room isolation and dashboard interactions.
- [ ] Marc restarts the development host and accepts the controls visually and with real-model scheduling.

Backend verification covered the complete suite in three isolated batches (230,
220 and 292 cases) with a 60-second hard OS limit per child. The third batch found
two mocked-room error-path failures in new activity instrumentation; a type guard
fixed them. A subsequent 148-case targeted run passed, covering those cases and
the final scheduling/relay changes, including a new no-clarification-loop case.
The resulting suite contains 743 backend cases plus three UI verification cases.
The final shared-session grouping checks passed all 70 cases; the final isolated
UI run passed all three verification cases in 17.89 seconds. `git diff --check`
passed after implementation and documentation updates.

Locked UI dependencies were installed with `npm ci --ignore-scripts` (zero
vulnerabilities). DOM checks cover collapse/expansion persistence, warning summaries,
filtering and quiet-hours validation/same-request retries. The existing axe scan
includes the new controls. One UI run hit the existing internal 20-second timeout;
an isolated rerun passed. Existing Starlette/httpx and whatwg-encoding deprecation
warnings remain. Native browser, actual Kindroid calls and Pi outage behavior are
not verified; runtime configuration, live databases and processes were not changed.

Files: `quiet_hours.py`, `encounters.py`, `relay.py`, `shared.py`, `application.py`,
`room_worker.py`, `worker.py`, `schedule.py`, `curiosity.py`, `command_queue.py`,
`activity.py`, `models.py`, `codex_agent.py`, `web/app.py`, and dashboard
`app.js`, `encounters.js`, `index.html`, `styles.css`. Tests are in
`test_encounter_recovery.py`, `test_room_encounters.py`, and `ui_dashboard_test.cjs`.
README, the documentation index, room operator guide, dashboard architecture and
ADR-020/024 were updated. Historical `current-system.md` and older milestone
verification records remain dated snapshots. Schema remains 13.

## Global Kin-driven scheduling and invitation retirement (2026-09-11)

- [x] Make runtime configuration use participant timing in every private/shared room.
- [x] Remove scripted exploration offers, invitations and automatic follow-ups.
- [x] Remove Daily Schedule, shared daily-start and Discovery configuration panels.
- [x] Retain per-Kin quiet hours, room-specific next-time overrides and check-in budgets.
- [x] Preserve six-hour fallback, missed-time recovery and explicit no-follow-up.
- [x] Accept source-linked current-session timing from either shared companion.
- [x] Stop legacy queued timers/invitations without external effects or deletion.
- [x] Retire safe old follow-ups with audit; preserve uncertain delivery for inspection.
- [x] Record the policy, alternatives and upgrade behaviour in ADR-025 and operator docs.
- [x] Verify backend regressions in hard-timeout batches; verify dashboard DOM/accessibility.
- [ ] Accept real-model timing choices and native-browser appearance after operator restart.

Implementation files: `config.py`, `rooms.py`, `schedule.py`, `encounters.py`,
`relay.py`, `shared.py`, `application.py`, `room_worker.py`, `worker.py`,
`command_queue.py`, `activity.py`, new `retired_exploration.py`, `codex_agent.py`,
`web/app.py`, and dashboard `index.html`, `encounters.js`, `exploration.js`,
`ui-model.js`. Tests: new `test_kin_scheduling.py`, updated room/recovery cases,
the historical `test_relay.py` settings fixture, and `ui_dashboard_test.cjs`.

The historical fixture explicitly retains the old internal settings mode to test
preserved storage/transport contracts. Production `Settings.load()` ignores the
old opt-out; dedicated current-policy tests cover that and all room modes. Removed
daily/invitation UI interaction tests were replaced with retirement, retained
history and shared-choice tests. The DOM harness now loads the HTML's actual script
list, so removed scripts cannot silently remain part of UI verification.

Architecture Decision preserved the global-policy rationale and alternatives;
OpenAI Docs informed required nullable structured timing fields with worker-side
source validation. No new model calls are added to shared attention checks.
Schema remains 13; no live state, provider calls or running host was changed.
Older system/milestone snapshots remain historical where explicitly marked.

Verification: **778 pytest cases passed** across isolated batches of 230, 233,
312 and three UI asset checks, each with a hard OS timeout of at most 60 seconds.
The UI harness passes 31 DOM cases plus its multi-view axe scan; `npm test` also
passed independently after `npm ci --ignore-scripts` (zero vulnerabilities).
Initial runs exposed obsolete feature expectations and a dispatch dependency on
test application settings; those were corrected before the passing reruns.
`git diff --check` passed. Existing Starlette/httpx and whatwg-encoding deprecation
warnings remain. Native-browser visual, real-model and live-provider acceptance
are not established by these mocked tests.

## Advisory daily history measurement (2026-09-11)

Implemented under [ADR-026](adr/ADR-026-advisory-daily-history-measurement.md):

- [x] Replace the hard rolling history limit with account-wide UTC-day measurement;
      requests above the configured warning threshold remain permitted.
- [x] Remove local count gates from chosen encounters and legacy scheduler/recovery
      paths; preserve provider cooldown, spacing and separate check-in budgets.
- [x] Display the warning and next daily reset without disabling controls.
- [x] Fail active sessions and commands on actual history HTTP 429, retaining
      successful effects, uncertain generations and already-paused recovery targets.
- [x] Verify pagination above threshold, midnight/downtime/restart behavior,
      operation counts across reset, incomplete pagination and redaction.

Changed runtime files: `state.py`, `relay.py`, `application.py`, `worker.py`,
`room_worker.py`, `encounters.py`, `schedule.py`, `curiosity.py`, `shared_recovery.py`.
Dashboard files: `web/static/index.html`, `app.js`, `ui-model.js`. Added
`tests/test_history_measurement.py` and updated affected history/recovery/room/UI
tests. Updated README, environment example, operator docs and supersession notes
in ADR-008/020/022. No schema migration or live database reset.

Verification: **781 pytest cases passed** in bounded backend batches of 228, 232
and 318 plus three UI wrappers. `npm test` also passed independently: API-client
checks, 32 DOM checks and one axe scan, after `npm ci --ignore-scripts` (zero
vulnerabilities). Initial targeted failures were obsolete auto-continuation
expectations and new fixture errors, corrected before passing reruns.
`git diff --check` passed. Existing Starlette/httpx and whatwg-encoding deprecation
warnings remain. No live provider requests, process restart, or native-browser
acceptance was performed. Older hard-history-budget descriptions are historical
and superseded by ADR-026.

## No passive chat monitoring (2026-09-11)

Implemented under [ADR-027](adr/ADR-027-no-passive-chat-monitoring.md):

- [x] Idle startup, restarts and elapsed polling intervals make no history requests
      in either worker mode. Saved pending messages alone cannot start a session.
- [x] Kin-chosen/fallback check-ins, explicit starts and active cooperative replies
      remain operational; completed sessions return to read-free idle.
- [x] First requested check-in bootstraps context and proceeds without replaying
      old messages or performing a redundant immediate sync.
- [x] Manual sync alone does not start a reply; pending imports can be handled by
      a subsequent explicit trigger or chosen check-in without blocking it forever.
- [x] Retain required context/empty-response recovery, approval-result continuation,
      pause/quiet-hour guards, provider cooldown and uncertain-effect evidence.
- [x] Update UI idle wording, sync guidance and snapshot monitoring flag.

Changed runtime files: `state.py`, `room_worker.py`, `worker.py`, `relay.py`,
`encounters.py`, `web/app.py`, `web/static/app.js`, `web/static/index.html`.
Added 16 regression cases in `tests/test_no_monitoring.py`; updated active-session
worker, end-to-end HTTP, room-failure and dashboard fixtures. Updated README,
environment example, operator/dashboard docs and ADR-008/020 supersession notes.

Verification: **797 pytest cases passed** across bounded batches of 228, 232 and
334 backend cases plus three UI wrappers. `npm test` independently passed its
API-client checks, 32 DOM cases and axe scan after `npm ci --ignore-scripts` (zero
vulnerabilities). Two initial full-suite batches reached their 60-second limit:
old HTTP fixtures waited for startup polling, and a room-failure fixture lacked an
active session. Both were corrected; targeted and complete batch reruns passed.
`git diff --check` passed. Existing Starlette/httpx and whatwg-encoding deprecation
warnings remain. No live provider requests, host restart, database reset or
native-browser acceptance was performed. Older passive-monitoring descriptions
are superseded by ADR-027; no schema migration is needed.

## Startup catch-up clarification (2026-09-11)

Marc clarified that first startup and every restart should sync history once,
while recurring idle monitoring remains off. This amends the startup-only portion
of the preceding milestone and [ADR-027](adr/ADR-027-no-passive-chat-monitoring.md).

- [x] One worker-owned startup synchronization per room/launch, including shared
      rooms, with normal pagination and measured request accounting.
- [x] Quiet hours and existing account cooldown defer the unattempted read; errors
      do not create an idle retry loop. Manual sync remains available for retry.
- [x] First import does not replay old messages; later catch-up imports new messages
      without creating a conversation. Stale inactive session markers cannot
      accidentally authorize replies to newly imported chat.
- [x] Paused rooms can catch up without resume or duplicate delivery/generation;
      startup outcomes are reported as history activity, without fake command IDs.

Changed `worker.py`, `room_worker.py`, `application.py`, dashboard sync guidance,
ADR-027 and operator docs; added 16 cases in `tests/test_startup_history.py` and
updated no-monitoring, HTTP lifecycle and DOM expectations.

Verification: **813 pytest cases passed** across bounded backend batches of 228,
232 and 350 plus three UI wrappers (API client, 32 DOM checks and axe scan).
`npm ci --ignore-scripts` reported zero vulnerabilities. Initial new-test syntax
and database-fixture setup errors were corrected before passing targeted and full
runs. `git diff --check` passed. Existing dependency deprecation warnings remain.
No live Kindroid calls, host restart, database reset or native-browser acceptance
was performed. Schema remains 13.

### Remaining external acceptance

- [ ] Marc verifies real Codex scheduling interpretation and conversational quality.
- [ ] Explicitly approved disposable-group test verifies manual selection,
      consecutive generation, speaker metadata and empty-response recovery.
- [ ] Native-browser visual and dialog acceptance on the actual operator device.
- [ ] Marc deploys/restarts on the Pi and confirms the three room schedules.

Tests use synthetic state and fake provider adapters. Operational inspection for
the unresolved mismatch was limited to redacted localhost status/command results;
no credentials, conversation bodies or remote group configuration were inspected.
