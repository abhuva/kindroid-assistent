# Rooms, chosen encounters and shared sessions

Room support was implemented for local evaluation on 2026-09-10 with schema 12.
Operator recovery adds schema 13 and requires a safe host restart. Real-model,
disposable Kindroid group and Pi acceptance remain separate.
See [the task checklist](multi-room-implementation-plan.md) and
[ADR-020](adr/ADR-020-room-sessions-and-chosen-encounters.md).

## Enable participant timing for Hannah first

Set `RELAY_PARTICIPANT_SCHEDULING=true` locally and restart. Existing configuration
remains valid; this permission defaults to false. Codex asks when the participant
wants the next encounter before closing, unless a choice was already given.
An explicit relative delay, timezone-qualified time or no-follow-up choice is
validated and saved by the worker. The model cannot edit scheduler configuration.

The next encounter controls both social check-ins and exploration. Automatic slots
open as check-ins offering exploration as a choice. Choosing exploration now queues
the existing bounded invitation workflow, if `RELAY_EXPLORATION_ENABLED` permits it.
No automatic invitation is sent solely because a tick was allocated to exploration.
Existing discovery/research permissions and budgets still apply. The invitation's
response interpreter can also capture a requested next encounter within its
existing call. After chosen exploration finishes, the relay returns to asking for
next timing if no choice was saved. Optional public experience reflections remain
source observations; the closing timing question provides the scheduling choice.

Times must be in the future, between one minute and thirty days for relative
requests, and within local enabled/quiet-hour/dead-time rules. Relative times use
the source message timestamp. Unclear or invalid choices require clarification;
the deterministic receipt is added only after storage succeeds. Scheduling remains
a room-specific permission, never authorization for files, accounts or external
actions. Role-only identity needs confirmed generation linkage; conflicting IDs
cannot change a schedule.

Until the first explicit choice, existing configured encounter timing remains in
use. Once a choice governs the room, consumed, declined or missed choices do not
restore old timers. A due request can wait up to one hour for local gates; after
that it is missed without a catch-up burst. Started attempts remain spent even if
delivery fails. The dashboard shows the source, time, actor and status. Set a new
time to override it, or select **No automatic next encounter**. Disabling the saved
schedule or startup initiative permission still prevents automatic starts.

## Configure all three rooms

Copy `rooms.example.json` to the ignored `rooms.json` and replace the placeholder
group/AI IDs. Do not put API keys or persona credentials in this file. Set:

```dotenv
RELAY_ROOMS_FILE="rooms.json"
```

Paths are relative to the service working directory, so an absolute path can be
used on the Pi. Create/configure the Kindroid groups in the app. The first room's
group ID must equal the existing `KINDROID_GROUP_ID`; it retains the existing
database, transcript and Codex thread. Secondary room keys determine files such
as `var/room-miko.sqlite3` and `var/room-together.sqlite3` beside the primary state.
Keys are durable storage identities: do not rename or reassign them casually.
Review each private room's Schedule controls and set its check-in limit to your
desired 5-10 encounters per day. New rooms inherit startup configuration, not
Hannah's saved dashboard overrides. Participant-chosen encounters still consume
their room's check-in allowance.

Use distinct participant IDs and display names within each room. The two private
rooms have one companion each; the shared room has exactly two. Every room has its
own transcript, pending replies, thread, memory, approvals, schedule and pause.
No cross-room memory is retrieved or injected. Historical agency actor vocabulary
is retained: other companions use `external` in that older audit table, with their
actual identity in source evidence, rather than being mislabeled Hannah.

One host and one worker serve all rooms. The primary database also owns account
history accounting, spacing and cooldown; every room consumes the same allowance.
Persona switching stays serialized and restores Marc in `finally`. Private chains
yield after one response/generation step. An in-flight synchronous call can delay
another room; a persistent worker phase identifies that boundary.

Both `serve` and multi-room `run` use this worker. `serve` includes room links at
the top of the dashboard; the root address is the primary room. Each room's API,
commands and views are scoped under `/rooms/<key>/`. Same-origin/CSRF/Host and
bounded-input defenses apply to every room. HTTP handlers enqueue work only.
The operator's process log is host-wide and can include events from all rooms;
it is not injected into companion conversation context.

Select a room for individual CLI operations:

```sh
kindroid-relay --room miko bootstrap
kindroid-relay --room miko status
kindroid-relay --room together pause maintenance
kindroid-relay --room together once
```

Run the complete host without `--room`. First bootstrap imports old messages as
processed. It does not replay historical mentions or schedule requests.

## Shared session behavior

Select **manual** turn-taking for the shared Kindroid group. Add an initial message
there before starting a session. The relay calls generation for the configured
Hannah/Miko IDs directly; it does not ask `get-turn` or post handoff messages.
Manual-selection behavior still needs verification in an approved disposable group.

The example starts once daily at 18:00 in the Pi's local timezone. The dashboard's
**Daily companion session** controls change that time or disable future starts.
Saving affects future sessions; it does not start a session or end a running one.
Start conversation begins a session manually; **End session** stops only this
session at the next worker boundary. The next daily start remains scheduled.
Quiet/disabled private-room tick allocation is separate from the shared daily
session's explicit enabled/time control. Missed daily starts are not caught up.

Each new companion message is checked locally for case-insensitive `codex`.
Matches wake Codex to decide whether assistance is needed; simply mentioning Codex
may produce no reply. Its own prefixed posts and old processed mentions cannot
wake it again. Clarification replies remain part of an assistance exchange without
requiring the keyword again. Finishing assistance does not automatically end the
companions' conversation. Consequential work still becomes a local approval.
Approved results can be posted back; the shared session retains speaker control.

Every six companion replies by default, a silent check can end a naturally finished
conversation. It cannot post narration or moderation. An explicit “Codex, let's
stop” requests an immediate attention check. Normal endings do not pause the host.
The example also limits sessions to 60 companion messages or one hour, with 30
seconds between turns. Configure `check_every`, `max_messages`, `max_seconds` and
`turn_seconds` in the rooms file before restart. Limits include already-generated
messages after recovery; neither restart nor resume resets the session count.

Farewell phrases such as **goodnight**, **sleep well**, **see you tomorrow**, or
**let's wrap up** now request an early silent check before another companion reply,
even immediately after an assistance check. The English phrase matcher is only a
hint: the model considers the latest room context, including quotes, jokes and
negation. It may allow a final goodbye response, then check that new farewell too.
Every assistance call also explicitly assesses whether the conversation has ended;
it counts toward the periodic cadence. Overlapping mention/farewell/checkpoint
triggers produce one call for the same message, not multiple calls.

Activity shows `shared_check_requested` with `mention`, `assistance_followup`,
`farewell` or `periodic`, then `shared_check_decided` with an end/continue verdict
and a bounded reason: `farewell_complete`, `pause_requested`,
`farewell_in_progress`, `conversation_ongoing` or `unclear`. No free-form model
rationale or message text is logged. A saved end verdict is followed by
`shared_session_ended` once termination completes; changed app context can still
supersede an older check. Normal activity retention applies to check events.

Restart the host safely to load this change; no migration or room configuration
change is needed. Existing stored decisions remain replayable. The daily next
start does not change, and goodbye language does not create a shared appointment.
Real-model recognition remains to be verified by Marc; offline tests use mocked
verdicts. See [ADR-023](adr/ADR-023-responsive-shared-session-endings.md).

Shared history reads occur at start, attention checkpoints when needed, and empty
generation-body recovery. There is no independent idle polling for this room.
App interjections are seen at the next synchronization and become normal shared
conversation context. A human message retains the next companion's turn; an
app-generated companion reply selects the other companion next. There is no Codex
handoff or extra model invocation just for joining in. Session time/count limits
are preserved, including newly imported companion replies. Stale, never-sent
assistance is cancelled, not posted into the changed conversation; sent and
uncertain effects keep their records and safeguards.

Use the dashboard End session/Pause controls for a stop that does not depend on a
history read. Empty generation bodies still require one budgeted sync to discover
the reply before advancing. This is not push detection: the relay may generate
before its next synchronization discovers an app message.

## Recovery and upgrade

Back up the existing database, pause and safely stop the old host, update/reinstall,
then start it again. Migration 12 preserves existing message IDs, generation and
outbox links, approvals, schedules and memory. It permits AI predecessor anchors
and adds encounter/session records. It neither imports old chat as new work nor
changes remote Kindroid settings. Do not downgrade code against schema 12.

Back up **each room database**, with separate destinations, using `backup` and
`--room`. Include the primary database's account ledger and separately protect the
local rooms configuration. For a coherent multi-room recovery point, pause all
rooms before taking the snapshots. Database backups do not include Codex thread
storage or credentials. Restoring older snapshots can lose knowledge of delivered
effects; inspect before resuming. Secondary databases should be newly created by
this host, not copied from independent relays with unmerged account usage.

Uncertain sending/generation never retries automatically. Pause and inspect that
room, use Sync Kindroid history to recover existing effects, and Resume separately.
Consecutive AI replies are matched against their recorded predecessor sequence.
Shared generations can span external human/app replies, but never the next
confirmed relay post. Matching requires a unique unclaimed response from the
requested companion and matching text when directly captured. Without captured
text, multiple replies from that companion remain ambiguous. Private-room
matching stays strict. Ambiguous duplicates and missing replies remain blocked.
End session does not clear uncertainty. If all history allowance is spent, wait
for recovery capacity rather than generating another unseen reply.

### Recover a room paused after manual chat input

1. Safely restart the local host with the updated code, using the same environment,
   rooms configuration and database paths. The original interjection correction
   needed schema 12; the new explicit recovery control requires schema 13. Do not
   delete state, force-bootstrap or run a second relay instance.
2. Select the shared room and run **Sync Kindroid history** while still paused.
   Wait for actual successful synchronization, not merely command acceptance.
   This imports and associates existing messages; it does not generate a reply.
   A stored command marked `completed` may still have a blocked `sync_outcome`.
   The dashboard now explicitly labels that **blocked** and explains the matching
   failure. No new messages are imported if reconciliation is blocked, so the
   saved transcript will remain behind the app. Only proceed after `synced` or
   `bootstrapped`; keep the room paused otherwise.
3. Select **Resume**. The recovered session continues from confirmed context.
   If its original time/message limit has expired, it ends normally; use
   **Start conversation** for a fresh session after recovery completes.
4. If synchronization reports ambiguity, missing history, cooldown or exhausted
   allowance, follow that blocker rather than repeatedly resuming. Genuine
   uncertainty is deliberately not cleared by this fix.

The old matcher stopped at every user message, so repeating sync could never
associate a generated reply after manual input. Updated shared-room matching
removes that false boundary while retaining source IDs and duplicate protection.
See [ADR-021](adr/ADR-021-shared-room-interjections.md).

### Explicit recovery for an ambiguous uncaptured generation

After backing up each room database and safely restarting with schema 13, reload
the dashboard and select the shared room. Leave it paused. Near the session
controls, **Recover interrupted relay session** becomes available when the
selected session has a generation with no captured response and no other
unresolved replies/deliveries. The adjacent text explains why it is unavailable.

Confirming the action performs a normal, budgeted fresh history read. The worker
ends that relay session and imports the observed conversation as processed history
in one transaction. It does not ask either companion to reply, call Codex, change
persona, or reset/delete the Kindroid group. The old generation remains uncertain
in the audit, with a separate operator-abandonment record; it is not falsely linked
to one of the possible replies. Existing message IDs, approvals and earlier saved
history remain intact. General edit/deletion mirroring is not part of this action.

Wait for **Relay session recovered**, then use **Resume** and **Start conversation**
when ready. Recovery itself leaves the room paused and does not change the daily
schedule. If the response is lost, **Retry same encounter request** reuses the
original command. A committed recovery is idempotent even after restart. Old
requests cannot abandon a newly started session.

If the result says **Recovery blocked**, the session was not recovered. Cooldown,
allowance and incomplete/failed reads leave it unchanged (history attempts still
consume their usual allowance). Recovery refuses other provisional messages,
uncertain outgoing Codex deliveries or changed confirmed source text. Do not
reset the database or repeatedly resume to bypass these guards. See
[ADR-022](adr/ADR-022-operator-shared-session-recovery.md).

## Acceptance boundaries

Local tests use synthetic databases and fake Kindroid/Codex adapters. They do not
establish real-model interpretation quality, manual-selection endpoint behavior,
native browser dialog/visual acceptance, or Pi release acceptance. No running
relay, live database, credentials or remote group configuration was accessed or
changed during implementation.
