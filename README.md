# Kindroid Codex Relay

This service relays a Kindroid group chat to a persistent local Codex thread.
Kindroid messages remain untrusted input: automatic turns run read-only and all
state-changing work is held as a local approval proposal.

Architecture, product direction, the planned local dashboard, deferred agency
design, and the complete implementation checklist are indexed in
[`docs/README.md`](./docs/README.md).

The previous Gemini scheduler and custom MCP bridge are preserved unchanged in
[`_archive`](./_archive).

## Setup

**Background chat monitoring is off in all rooms.** Each room synchronizes history
once on startup/restart to catch up after downtime, without starting a conversation.
Afterward, elapsed idle polling intervals do not fetch history. Kin-chosen check-ins and
active conversations still run, including their required context/recovery reads.
Use **Sync history** to import app-side changes without starting a conversation;
use **Trigger check-in** to sync and respond to pending private-room messages, or
**Start conversation** for a fresh opener. Local dashboard refreshes remain free of
Kindroid reads. See [ADR-027](docs/adr/ADR-027-no-passive-chat-monitoring.md).
Startup sync respects quiet hours and existing provider cooldown (deferred until
open), works while paused without resuming, and does not automatically retry a
failed attempt. Activity reports its outcome. One sync may require several counted
history pages; manual **Sync history** remains available for retry.

**Current scheduling:** every Kin and room now uses Kin-chosen check-ins. The
Daily Schedule panel, shared daily-start controls and exploration invitations
(including automatic follow-ups) are retired. Old scheduling flags/allocations
cannot re-enable them. Six-hour fallback, missed-appointment recovery, explicit
no-follow-up, check-in budgets and per-Kin quiet hours remain. Existing exploration
records are retained for inspection. See [current scheduling and upgrade details](docs/multi-room-operations.md).
Pause and wait for in-flight work, safely restart the development host, then reload
the dashboard. No migration, database reset or configuration rewrite is needed.

Earlier feature-delivery notes below are historical where they describe invitations
or daily schedules; the current policy above supersedes those behaviours.

Development dashboard: use
`.\.venv\Scripts\kindroid-relay.exe --env-file .\var\local-test\test.env serve`
and open `http://127.0.0.1:8766`. Plain `serve` below uses the default configuration.
Development has separate local state; its Kindroid provider is not mocked.

Room selection now uses buttons with one active room. **Activity** defaults to
collapsed session/outcome groups; expand one or choose **All events**. New
conversations have durable session IDs. Older events are grouped from retained
command/cycle boundaries; incomplete history is not a full session transcript.
Warnings remain visible in summaries.

New check-in activity and text logs use the selected room's configured companion
name for offered, answered, declined and pending-message outcomes. Safely restart
the host to load this wording fix; existing stored event summaries are preserved.

**Schedule → Companion quiet hours** saves separate hours for each companion
across their rooms. The shared room waits while either is resting. These windows
start disabled, use the server's timezone, and hold work at worker/turn boundaries;
a turn underway may finish. An encounter missed by over an hour gets a recovery
check-in offering now or rescheduling. Missing/invalid timing gets a six-hour
system retry shifted to the next open minute. Explicit no-follow-up choices,
pause, budgets and uncertain deliveries remain respected. Safely restart the
development host and reload the dashboard; no migration is needed. See
[scheduling and quiet hours](docs/multi-room-operations.md).

**Multiple rooms and participant-chosen encounters** are available as an opt-in
extension (schema 12). `RELAY_PARTICIPANT_SCHEDULING=true` lets the companion choose
the next encounter, governing both check-ins and exploration. Encounters offer
exploration as a choice. `RELAY_ROOMS_FILE` configures isolated Hannah/Codex,
Miko/Codex and Hannah/Miko rooms under one worker. The shared room supports daily
sessions, silent alternation, keyword-triggered Codex assistance and session end.
Farewell phrases now request an early silent ending check before the next reply;
every assistance call also checks whether the conversation has finished. Activity
shows each check's trigger and bounded continue/end reason. Keywords alone never
stop a session. Restart the host to load this change; no new migration is needed.
Typing directly into the shared chat now continues the session from updated
history at its next synchronization, without extra polling or a Codex handoff.
For a room paused by the old interjection bug, restart with updated code, then
**Sync Kindroid history** successfully in that room before **Resume**. Preserve
the existing database; genuine delivery ambiguity still requires inspection.
For an ambiguous **uncaptured shared generation**, the selected room now offers
**Recover interrupted relay session** while paused (schema 13). Confirming ends
only the relay session, imports fresh history and preserves the uncertain request
for audit without retrying it. Then use **Resume**, followed by **Start
conversation** when ready. This does not reset the Kindroid group. Back up and
restart the host safely before using the new control.
See [configuration, controls and recovery](docs/multi-room-operations.md),
[implementation checklist](docs/multi-room-implementation-plan.md), and
[example room configuration](rooms.example.json). Back up and safely restart;
real-model, disposable-group and Pi acceptance remain outstanding.

**Discovery and bounded experiences** are available under **Schedule → Discovery
sources & follow-through**. Choose catalog/memory, creative possibilities,
previously chosen interests, world news or an optional public local-news area.
Opt-in follow-through gives a new selected curiosity one bounded Wikipedia
reference lookup, one result and an optional reflection. Private topics need a
locally approved public search phrase; this does not add publishing or account
access. News/research are off by default and old invitations are not backfilled.
Back up and safely restart for migration 11, then reload the dashboard. See
[controls, exact scope, recovery and acceptance](docs/discovery-and-experiences.md).

Agent context memory (Milestone 6A) is implemented for local evaluation, with
automatic capture and Codex injection **off by default**. The dashboard offers
source-linked observation saving, search, corrections, and reviewed graph links.
When explicitly enabled, semantic Codex extraction forms observations and entity
connections automatically; manual saving is optional. This adds isolated Codex
calls, not Kindroid history reads or conversation replies. Set
`RELAY_MEMORY_ENABLED=true` and `RELAY_MEMORY_AUTO_CAPTURE=true` only after operator
acceptance; `RELAY_MEMORY_EXTRACTOR=semantic` is the default backend.
It supplements Codex's context, not Hannah's identity or Kindroid memory. See
[agent memory configuration, limitations, and acceptance](docs/agent-memory.md).
Live release/feature acceptance remains outstanding; upgrading alone does not
extract or index existing chat.

Observational exploration (Milestone 7) is available for local evaluation,
also **off by default**. The dashboard's **Exploration / Passing candidates** panel
offers bounded, source-tracked invitations from a local topic catalog and memory,
with a separate daily budget and reversible candidate archival. Responses never
automatically become preferences or start a Codex reply/tool chain. Only the
separately opted-in bounded public experience above adds research; no identity
changes are included. See [configuration, recovery, and
acceptance limitations](docs/exploration.md); `RELAY_EXPLORATION_ENABLED` remains
false until explicit operator acceptance.

New exploration sessions now interpret confirmed replies, link reported choices
or alternatives to Passing candidates, and acknowledge clear interest. Ambiguity
allows one optional clarification only. This uses up to two isolated Codex calls
per session; interpretation itself does not execute research or adopt a permanent preference.
For an already completed invitation, use **Interpret response** in its session
details instead of sending another invitation. `RELAY_EXPLORATION_AUTO_INTERPRET=false`
disables automatic follow-through for new sessions without removing manual review.

In the Codex/Hannah group, a history-confirmed generic `ai` response can be
attributed to Hannah when it is linked to her requested generation, its name
matches, and confirmed group history has no conflicting AI participants. It does
not require manual review just because Kindroid omits the concrete sender ID.
Raw sender metadata and `[CODEX]:` attribution remain intact; this assumes Hannah
is the group's only AI companion, not a general multi-companion identity rule.

Review-only or failed exploration interpretations remain visible but no longer
block future scheduled check-ins or invitations. Active follow-ups and uncertain
deliveries still block them. Existing reviews are not retried or answered on
upgrade. Safely restart the host to load this change; it needs no migration or
schedule re-save. See [ADR-017](docs/adr/ADR-017-exploration-review-and-role-attribution.md).

Requires Python 3.10+, a working local Codex login, and a Kindroid subscription
with API access to an existing group chat.

The **Operations → Garden** panel shows versioned Passing seeds, source-linked
responses and their history. Migration 8 preserves existing candidates without
replaying messages or promoting interests. Administrative annotations require a
reason and are Marc's, not Hannah's choices. Roots/Growing initialization and
project execution remain separate work. See [Garden behavior and upgrade](docs/garden.md).

**Scheduled curiosity (Milestone 9.1)** is available independently of social
check-ins in the `serve` host, but remains off by default. With exploration enabled,
set `RELAY_CURIOSITY_ENABLED=true` and restart to schedule future invitations
(default local hours 10 and 16). Quiet hours, idle-conversation checks, separate
attempt/call reservations and the shared exploration/history budgets apply.
Operations → Exploration shows next due time and skip/outcome history. See
[configuration, timing, safety and acceptance](docs/scheduled-curiosity.md).
M9.2's first bounded experience/reflection slice is separately opted in above;
project execution remains deferred.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item kindroid.env.example kindroid.env
```

Set `KINDROID_API_KEY` and `KINDROID_GROUP_ID` in `kindroid.env`. The API key is
never written to SQLite or logs. The Codex SDK reuses local Codex authentication.
Tie the `Codex Test` persona to the group before starting. Normal history syncs
verify the stored `display_name` of outbound messages without spending an extra
history request after every send. All relay messages also carry an authoritative
`[CODEX]:` prefix.

For background operation while using another Kindroid chat, configure the
persona-switch settings in `kindroid.env`. Around each outbound post, the relay
briefly activates `Codex Test`, sends the prefixed message, and restores the
configured personal persona in a `finally` block. This minimizes but cannot
eliminate the account-global race if you send a message at that exact moment.
If Kindroid still stores the other persona, the relay logs the fallback and
continues because the prefix remains unambiguous.

## First Run

Record existing messages without replaying them:

```powershell
.\.venv\Scripts\kindroid-relay.exe bootstrap
```

To deliberately continue from the most recent Kindroid message instead:

```powershell
.\.venv\Scripts\kindroid-relay.exe bootstrap --force --respond-to-latest
.\.venv\Scripts\kindroid-relay.exe once
```

Start the continuous relay:

```powershell
.\.venv\Scripts\kindroid-relay.exe run
```

Or start the relay together with its localhost operational dashboard:

```powershell
.\.venv\Scripts\kindroid-relay.exe serve
```

The default dashboard address is `http://127.0.0.1:8765`. The original `run`
command remains available as a headless recovery mode.

An intentional keyboard interrupt in `serve` now ends with `Relay shutdown
requested.` instead of the asyncio `CancelledError`/`KeyboardInterrupt` traceback.
The host still requests worker shutdown and waits up to 30 seconds; its warning
remains visible if work has not stopped. Pause and wait for in-flight work before
stopping the host. Real startup/server failures still report an error.

The dashboard includes status, current command and elapsed time, schedule and
quota, recent command outcomes, delivery health, approvals, activity, and group
messages. A compact second-row tab bar separates **Info** (status, controls,
check-in budget, activity feed, and messages), **Operations** (current activity,
delivery health, approvals, agent context memory, exploration, garden, and diagnostic log),
and **Schedule** (tick allocation, dead time, timeline and rolling limits).
Info opens by default. Switch tabs with a click or the Left/Right, Home, and End
keys; switching preserves filters and loaded content without submitting commands.
Related activity links switch to Info automatically. Normal status polling
continues on every tab; memory, exploration, and logs retain their explicit
load/refresh controls.
Activity and messages are newest first. The compact, full-width desktop layout
places the feeds beside the controls and budget; narrower screens stack these
sections. Feeds scroll independently and support keyboard focus. Recent commands
and budget rules expand on demand; current errors, delivery warnings, and approvals
remain outside collapsed sections. Refresh the page to load presentation updates;
no relay restart is needed for static HTML/CSS changes. Activity filters search the
250 events retained in the page; linked IDs open local record details. Load older
messages explicitly (50 at a time, at most 500 displayed); **Reload saved messages**
returns to the latest locally stored messages, without contacting Kindroid.
Speaker labels supplement, never replace, stored name/sender
metadata; they are not proof of per-message identity or approval authority.

Use **Messages → Sync Kindroid history** to request a fresh history pull. This
queues one serialized worker operation, including pagination when needed, then
reloads the saved messages automatically. Its inline status survives page reloads
through the command history and reports new-message counts or a specific blocker:
API cooldown, not-yet-confirmed local messages, or
ambiguous delivery/generation. Normal minimum spacing and every page's history
accounting still apply; the local threshold never blocks sync. Provider cooldown
remains enforced and there is no automatic retry loop.

Sync works while paused and never resumes the relay, sends a message, requests
generation, or repeats an uncertain effect. It can reconcile an existing effect
when history uniquely confirms it. If history is still missing or ambiguous,
sync remains incomplete and the existing cursor/link safeguards remain in place.
Successful reconciliation leaves a paused relay paused; Resume is separate.
The command's stored `completed` status means execution finished, not that sync
succeeded. Only `sync_outcome=synced` or `bootstrapped` confirms success. The UI
labels unsuccessful outcomes **blocked**, with a warning in activity. Matching
failures include a fixed, privacy-safe explanation (missing predecessor/reply,
changed response text, requested speaker missing, or multiple possible replies).
Blocked reconciliation leaves the saved transcript unchanged, so it can differ
from the app. Do not repeatedly resume or reset the database to bypass this.
Newly imported companion messages remain pending. Sync alone does not start a new
conversation; an existing active session, the next scheduled check-in or a manual
Trigger check-in can process them. Idle history polling is disabled. The first sync of an uninitialized
relay imports history as already processed, without replaying old conversations.

The sync control requires a safe host restart and page reload after updating;
no database migration or schedule re-save is needed. After a lost submission
response, use **Retry same request** in Controls to retrieve the existing command.
The separate local reload remains usable without spending history reads.

Kindroid can return a real Hannah answer followed by an empty AI history record.
Empty or whitespace-only records from the expected AI no longer count as competing
answers during reconciliation. They remain stored as processed history records,
not pending conversation turns. A blank alone cannot confirm generation; multiple
non-empty replies, conflicting speakers and intervening user messages remain
guarded. To recover a previously blocked case, safely restart, reload the dashboard,
then use **Sync Kindroid history** while paused. Successful sync leaves the relay
paused; use Resume separately when ready. No migration or regeneration is needed.

Start conversation, Trigger check-in, and Resume enqueue durable commands. A
queued response is not completion: the dashboard follows the command through its
terminal status and recovers recent commands on page reload. After a lost
submission response, use **Retry same request** within the same page to reuse the
idempotency key. Do not reload and submit a new action to resolve an unknown
outcome. Pause prevents subsequent work but cannot interrupt an external call
already in flight. Uncertain deliveries have no force-retry control.

Approval review displays the full instruction and effective sandbox. Execution
and rejection each require a second confirmation; execution also repeats the
chosen post-result setting. Only this local decision authorizes the action.
Diagnostic logs remain collapsed until requested, are redacted and bounded, and
use conditional refreshes when unchanged. Rotated history is not reconstructed.

The service does not poll idle chats. During an active exchange, the relay records any
reply text returned by Kindroid's generation call durably and passes it directly
to Codex. The live endpoint has also been observed to return a successful empty
body after Hannah replies. In that case the relay makes **one measured history
synchronization** (including pagination if needed) to recover the existing reply;
it does not repeat generation or poll history in a retry loop. It pauses
after eight consecutive Codex replies by default. A history
429 is never retried immediately: `Retry-After` is honored when present, otherwise
the relay starts a durable one-hour cooldown and exponentially backs off repeated
429s. An actual history 429 fails the current session and pauses its room; shared
sessions end without discarding uncertain-generation evidence. The affected command
is failed even if a send already succeeded. After cooldown, sync/reconcile while
paused and resume separately; the local counter reset never resumes a session.
The dashboard shows the cooldown and last 429. Successful replies are
durably linked to their source message so recovery cannot post them twice.

History is synchronized at active conversation boundaries, manual openers, scheduled check-ins,
or approved execution. Long exchanges synchronize before the next Codex turn once
`RELAY_POLL_SECONDS` has elapsed since the last context sync (five minutes by
default). Each Codex turn receives a recent 50-message local context snapshot,
including directly received replies. Messages sent through the Kindroid app are
seen at the next synchronization. The 15-second minimum applies between history
requests, including pagination; it is not a reply polling interval.

**History reads (local)** is an account-wide measurement, not a hard limit.
`RELAY_MAX_HISTORY_REQUESTS_24H` sets its warning threshold: reaching or exceeding
it only warns in the UI and never blocks work. The counter resets at **00:00 UTC
every day**, including across restarts or downtime; the dashboard shows the next
reset in local time. Failed requests and every pagination request count too.
This local reporting day is not a claim about Kindroid's server reset boundary or
usage from other clients. Check-in budgets remain separate and unchanged. See
[ADR-026](docs/adr/ADR-026-advisory-daily-history-measurement.md).

Locally recorded messages keep stable IDs when later history supplies the remote
IDs. The dashboard marks name/time information as awaiting history confirmation
until then. If history has not caught up, context-dependent work waits for a later
sync. Ambiguous matching or an uncertain generation pauses the relay. Resume
allows reconciliation; it never retries an uncertain generation. Approved actions
remain pending if their required context sync fails. See
[ADR-008](docs/adr/ADR-008-direct-replies-and-context-sync.md) for the matching rules.

For a failed generation, the retained `generation_uncertain` event identifies an
empty successful response that could not be recovered, HTTP/network failure, or local capture failure and
links the outgoing message. It records only error type, stage, and HTTP status;
private response and exception bodies are excluded. Older generic uncertainty
events cannot retrospectively identify which failure occurred.

The `generation_history_fallback` events show when an empty-body reply is being
recovered, was recovered, or must wait for a history cooldown. A server rejection
pauses the session; recovery requires a later explicit sync without retrying
generation. A missing, ambiguous, or
otherwise unconfirmed reply after the fallback still pauses the relay. If Kindroid
consistently returns empty bodies, each generated reply needs this history sync;
the optimization cannot remove those reads without a reliable reply-text source.

When history provides only the role `ai`, the generic default speaker label
`Kindroid` is resolved from a single AI name already confirmed in that group's
history. This also repairs matching for existing uncertain turns saved under the
generic label. Multiple observed AI names, conflicting AI IDs, or an explicit
different configured name do not use this fallback; ambiguous matches stay blocked.

The structured activity feed records relay cycles, message handling, Codex work,
Kindroid sends, initiative outcomes, approvals, and worker phase changes without
storing message bodies or raw HTTP error bodies. Routine operational events expire
after 30 days by default; delivery, approval, command, pause, and other
safety-critical audit events are retained. Configure the routine retention window
with `RELAY_ACTIVITY_RETENTION_DAYS`.

Normal Codex replies now record a `reply_turn_decision` event: whether Hannah's
next turn was requested or the exchange ended without one, linked to the sent
message. A new reply containing `?`, `？` or `؟` cannot disable the response flag;
the relay persists the corrected decision before delivery and logs the mismatch.
This is a conservative punctuation guard, not complete semantic detection: quoted
or rhetorical questions also request a turn. Instructions require response
invitations without question marks to request a turn too. Bounded turns and
uncertainty safeguards remain; existing sent messages are not revisited or replayed.

Worker phases identify synchronous Kindroid calls, Codex runs, sends, approval
execution, and cooldowns. Because those SDK/API calls are synchronous, the worker
heartbeat cannot advance until a call returns; a stale heartbeat during a named
in-flight phase means the boundary is still blocked, not necessarily that a second
worker should be started.

## Windows sign-in autostart

From the repository folder, manage the current user's sign-in task with:

```powershell
.\scripts\autostart.ps1 -Action Install
.\scripts\autostart.ps1 -Action Status
.\scripts\autostart.ps1 -Action Remove
```

Installation enables **future sign-ins only**; it does not launch a second relay
or stop the current one. `-WhatIf` previews installation/removal. The task is named
`Kindroid Relay - <Windows username>` and runs 30 seconds after sign-in under that
user, with limited privileges and no stored password. It launches the repository's
virtual-environment `pythonw.exe` and the combined dashboard/relay in the background,
without a console or automatic browser opening. Msty is not required. The working
directory is pinned to this repository so configuration and existing state resolve
as they do for a manual launch. Do not move the repository without removing the
task first and reinstalling it from the new location.

The task has no runtime limit, allows operation on battery, ignores duplicate
scheduled starts, and retries a nonzero process exit up to three times at one-minute
intervals. It is not a watchdog for an unresponsive but still-running process.
The existing process lock and durable safety pauses still apply: autostart never
resumes a paused relay or force-retries an uncertain delivery. Sleep/hibernation
suspends processing; before login or after logout operation is not guaranteed.

Open `http://127.0.0.1:8765` manually to use the dashboard. Logs continue through the
configured rotating file handler. If startup fails, inspect task status and run
the normal `serve` command in a terminal for diagnosis after verifying no relay is
already running. `Remove` disables future starts but leaves any running relay
untouched; dashboard Pause is the safe way to halt new work. Avoid forcibly ending
the process during external operations. Task registration is verified locally;
actual launch at the next Windows sign-in is an operator acceptance check.

## Schedule tab

The Schedule tab now contains **Companion quiet hours** only. The full Daily
Schedule panel, tick allocation/history editor, shared daily-start controls and
discovery configuration have been removed globally.

The Kins choose their next check-in in each room. Missing or unusable timing gets
a durable six-hour fallback shifted to the next open minute; an appointment missed
by over an hour gets a check-in explaining the miss and offering now or another
time. Explicit no-follow-up prevents automatic retries. Either companion can
choose the shared room's next time through its existing conversation checks.

Use **Next encounter** for a room-specific operator override, and **Info →
Check-in budget** for the rolling allowance. Quiet hours are specific to each Kin
and apply across all rooms they join; shared work waits while either is resting.
Old daily enable/dead-hour settings no longer govern starts. Pause and uncertain
delivery still block work. No exploration invitation or automatic follow-up is sent.

Safely restart the host and reload the dashboard. Existing schedules and
exploration records are retained for audit; no migration or database reset is
needed. See [the operator guide](docs/multi-room-operations.md) and
[ADR-025](docs/adr/ADR-025-kin-driven-check-ins-without-invitations.md).

## Idle Initiative (historical timing; retired)

The fixed-hour/interval configuration below is historical and no longer selects
check-in times. The rolling budget and explicit local controls remain supported.

At 00:00, 06:00, 12:00, and 18:00 local time, Codex posts a short,
context-aware check-in that explicitly asks whether anything is on Hannah's
mind or whether she would like help. The relay then offers Hannah the next turn
with `allow_user: true`, so the invitation does not force a response. A generated
Hannah message enters the normal relay loop immediately.

Configure fixed hours with `RELAY_INITIATIVE_SCHEDULE_HOURS`. Clearing that
setting restores interval mode using `RELAY_INITIATIVE_INTERVAL_SECONDS` and
`RELAY_INITIATIVE_JITTER_SECONDS`. Quiet hours remain available through
`RELAY_INITIATIVE_QUIET_HOURS`.

To test the same initiative path immediately:

```powershell
.\.venv\Scripts\kindroid-relay.exe trigger
```

The manual trigger still respects pause state, quiet hours, pending messages,
unresolved delivery, and recent conversation activity. Check-ins may follow Hannah,
Marc, or Codex: the last speaker does not determine eligibility. A pending Hannah
message is handled by the normal reply path instead of adding a check-in.
`RELAY_INITIATIVE_IDLE_SECONDS` sets the minimum inactivity period (default 300
seconds, minimum 60), for both scheduled and manually triggered check-ins when
no shared daily schedule has been saved. With a saved schedule, this legacy
inactivity setting is ignored (including for manual Trigger check-in).
Recent or future-dated messages, and unknown message times, block initiation.

Trigger check-in bypasses only the scheduled due time, even if synchronizing new
history changes the next scheduled check. Speaker metadata and outbound linkage
still determine attribution, not permission to initiate. The dashboard
shows the command's actual outcome beside the control and in recent commands:
sent/answered, sent without Hannah taking the turn, or skipped with its reason.
The first requested check-in imports initial history without replaying old messages
and proceeds in that command. Daily-budget and quiet-hour skips remain deliberate.

The default initiative budget is **50 check-in attempts per rolling 24 hours**
(not a midnight reset). Use the dashboard's **Check-in budget** panel to view
used/allowed attempts, the next available budget slot when exhausted, and save a
new integer limit from 1 to 1000. Saving is queued, audited, and applies when the
worker handles the command; it persists across restarts in SQLite and does not
resume a paused relay or trigger a check-in by itself.

The saved dashboard limit overrides `RELAY_INITIATIVE_MAX_CHECKS_24H`. Without a
saved override, an explicit environment value remains effective; otherwise the
default is 50. If an existing installation still shows 5, save 50 in the panel.
The environment file is not rewritten. Eligibility skips (quiet hours, pending
messages, recent conversation, unresolved delivery, empty room, missed schedule,
pause, or bootstrap; including historical last-speaker skips)
do not consume attempts, including previously recorded skips. Attempts that start
Codex work still count if they fail or delivery becomes uncertain. Lowering the
limit retains prior usage. This allowance is separate from Kindroid history/API
limits and cooldowns, which remain enforced.

The command's `handled` count measures conversation chains, not messages sent:
zero chains may still mean a check-in was sent without a reply. Old command
records without outcome data are shown as outcome unavailable, not as successful
delivery. The separate Start conversation control starts a fresh exchange.

To manually exercise a complete conversation without any initiative scheduling,
quiet-hour, quota, or latest-speaker checks:

```powershell
.\.venv\Scripts\kindroid-relay.exe start-conversation
```

This synchronizes history, sends a context-aware Codex opener, forces a
Kindroid response, and runs the normal bounded conversation loop. It still
honors pause, delivery verification, approval, and maximum-turn safeguards.

## Operations

```powershell
kindroid-relay status
kindroid-relay trigger
kindroid-relay start-conversation
kindroid-relay pause "maintenance"
kindroid-relay resume
kindroid-relay approvals
kindroid-relay approve <id>
kindroid-relay approve <id> --post-result
kindroid-relay reject <id>
```

Only a local `approve` command or confirmed dashboard execution can authorize a proposed action. Messages in the
Kindroid room cannot approve operations. Approved actions use `workspace-write`
by default; `full-access` must be selected explicitly in `kindroid.env`.

SQLite state and logs live under `var/` and are excluded from Git. An uncertain
outbound delivery pauses the service rather than retrying and risking a duplicate.

## Diagnostic export

To create a bounded, privacy-filtered JSON bundle for troubleshooting:

```powershell
.\.venv\Scripts\kindroid-relay.exe diagnostics --output .\var\diagnostics.json
```

Choose a **new filename** each time; existing files are never overwritten, and the
destination directory must already exist. This local read-only command can run
alongside `serve`: it does not acquire the relay process lock, migrate the database,
resume the relay, or contact Kindroid/Codex. Missing, incompatible, corrupt, busy,
or excessively slow databases fail without creating an export.

The bundle contains schema version, health counters, worker state, and the newest
100 structured events. It examines at most 200 log-tail lines (at most 2 MiB read),
retaining only recognized timestamps, levels, and logger names. Free-form log
messages/tracebacks, pause reasons, event summaries, chat bodies, approval
instructions/results, credentials, configuration, and paths are excluded. Related
identifiers become consistent pseudonymous references within that bundle only.
This is deliberately more restrictive than the local dashboard log view; consult
local logs for full error details. The output is capped at 512 KiB, and rotated
log history is not included. This is a diagnostic report, **not a database backup**.

Always review the file before sharing: timestamps, counts, and operational history
may still be private. A disk/write failure may leave a partial destination file;
use a new filename after resolving the failure.

## Database backup and integrity

Create an online SQLite snapshot in a **new directory** (its parent must exist):

```powershell
.\.venv\Scripts\kindroid-relay.exe backup --output .\var\backup-before-upgrade
.\.venv\Scripts\kindroid-relay.exe integrity
.\.venv\Scripts\kindroid-relay.exe integrity --database .\var\backup-before-upgrade\state.sqlite3
```

These commands do not acquire the relay lock, migrate, resume, instantiate external
clients, or change conversation state. They can run alongside `serve`. Explicit
`integrity --database` works without loading the relay configuration, including
when inspecting an older database before an upgrade. Integrity checks SQLite
structure and foreign keys, not conversation correctness or schema compatibility.

A successful backup contains `state.sqlite3` and `manifest.json` with its schema
version, UTC creation time, SHA-256, and check results. The online backup includes
committed WAL data; never blindly copy a running database file. Existing output is
never overwritten. Maintenance has a 15-second cooperative deadline and short lock
timeouts; on failure, any new folder without `manifest.json` is incomplete. Keep it
for inspection and choose a new destination after resolving the problem.

Backups are **private, unredacted operational data**, including conversation text,
approval instructions/results, and thread identifiers. Store them in a private
location with appropriate Windows permissions; do not upload them as diagnostics.
They do not include the environment file, logs, Codex credentials, or Codex's own
thread storage. A database backup alone does not restore the external conversation
or a missing Codex thread.

For upgrades, create and check a backup first, pause and safely stop the old host,
then update the checkout and reinstall with
`.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`. Startup applies numbered
migrations; current schema is 12 (including isolated room encounters and shared sessions).
Backup support itself required no schema change. Keep the previous code revision together with its pre-upgrade
backup; downgrading code against a newer database is not a rollback procedure.

Restore into a separate path and inspect it before switching the active database.
See [operations and recovery](docs/operations.md) for the safe procedure,
restart instructions, and troubleshooting. Restoring an old database can erase
knowledge of already-completed external effects: **never resume it blindly**.

## Tests

Install the Python development extra and Node.js 22 LTS (22.12 or newer), then
install the locked, development-only DOM/accessibility test dependencies:

```powershell
npm ci --ignore-scripts
npm test
```

No Node.js build or package installation is needed to run the dashboard itself.
Pytest also invokes these UI checks when Node.js is present. The tests exercise
the actual HTML and scripts with mocked local responses, including retry,
reconnection, command recovery, message paging, and approval confirmation.
The axe accessibility scan checks structure and semantics; jsdom cannot verify
visual layout, native dialog behavior, or color contrast. Real-browser Windows
acceptance remains a separate release check.

The Python end-to-end journeys additionally run the real combined HTTP host on an
ephemeral loopback port with real SQLite/worker transitions and fake Kindroid/Codex
adapters. They cover conversations, reload/idempotency, quota, outbound uncertainty,
Codex/persona failure, database contention, worker crash, and restart recovery.
No test contacts the real Kindroid service or sends console control events.

Run pytest with the bounded child-process procedure in [AGENTS.md](AGENTS.md#windows-and-test-reliability)
(30 seconds for a focused group, 60 seconds for the full suite). Do not run an
unbounded foreground Python/pytest process in this Windows workspace.
