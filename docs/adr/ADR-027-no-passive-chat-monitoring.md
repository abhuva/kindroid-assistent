# ADR-027: No passive chat monitoring

**Status:** Accepted | **Date:** 2026-09-11

**Participants:** Marc, Codex

## Context

Private rooms still fetched history every five/ten minutes although check-ins
were Kin-scheduled. Marc explained that the companions need a prompt or explicit
generation command; unexpected app activity is initiated by him and he can request
a response locally. Idle polling therefore spent substantial history allowance
without serving the intended workflow.

## Decision

We decided to remove passive history monitoring globally, rather than lengthen its
interval. Each room performs one read-only history synchronization on every host
startup/restart; after that, idle rooms perform no periodic history reads.
Existing polling configuration cannot re-enable it. The worker still wakes for
local commands, scheduling, heartbeats and optional local-memory maintenance;
these wakes are not remote history polls.

The following retain their necessary history access:

- one startup synchronization per room (first launch and every restart);
- Kin-chosen check-ins, missed-time recovery and six-hour fallbacks;
- manual Start conversation / Trigger check-in and explicit CLI operations;
- manual Sync history and explicit shared-session recovery;
- required context refreshes and empty-response reconciliation during an active
  conversation, plus context synchronization for approved execution.

Private automatic continuation requires an existing activity session and pending
reply/reconciliation work. A stored pending message alone does not authorize a new
conversation. A stale activity marker without work does not cause polling either.
Shared sessions retain their existing explicit lifecycle and attention boundaries.
Posting an approved result establishes its private continuation session. Completed
sessions return to a read-free idle state. Uncertainty is never cleared or an
external effect retried merely to disable polling.

Sync history remains a read/reconciliation operation. It can update an already
active session, but cannot create a new one. Marc can use Trigger check-in to sync
and answer pending private-room messages; Start conversation retains its fresh-
opener behavior. Pending imports do not block a later Kin-chosen check-in forever:
that scheduled start can process them then, without sending a redundant opener.

On Marc's follow-up clarification, startup synchronization was retained to catch
messages added while offline. First launch imports existing history as processed;
later launches import/reconcile changes without starting a new conversation.
An existing active session may continue through its normal guarded workflow.
A stale session marker without pending work is closed before import so newly
discovered messages cannot accidentally create a conversation.

The startup attempt is consumed before I/O and never becomes an idle retry loop.
Quiet hours or an existing account cooldown defer the unattempted sync until open;
a failed attempt is reported in Activity and can be retried using manual Sync
history. Paused rooms may sync, but are never resumed by the startup read.
Pagination may require several counted requests to complete the one synchronization.
No provider effects are repeated and uncertain-delivery evidence is preserved.
If initial context is still absent when a check-in runs, that check-in can import
it without replaying old chat. Read-only bootstrap/sync never starts a conversation.

Quiet hours, pause, bounded turns, independent room state, approvals, request
spacing and actual provider cooldown remain unchanged. Local history measurement
remains advisory under [ADR-026](ADR-026-advisory-daily-history-measurement.md).
The dashboard's own local-data refresh is unchanged and consumes no history reads.

## Alternatives considered

- Keep idle polling or merely lengthen the interval: still spends reads on passive
  detection Marc explicitly does not need.
- Omit startup sync too: rejected in the follow-up because the local transcript
  may miss substantial activity during downtime; one catch-up read is worthwhile.
- Disable every automatic read: breaks chosen check-ins, active context updates
  and safe empty-response reconciliation.
- Pause all rooms: also disables scheduled conversations and requires manual resume.
- Treat every cached pending message as active work: a manual sync or restart could
  unexpectedly start a conversation.
- Delete/mark imported messages processed: hides evidence and changes deduplication
  semantics; retained messages remain available for an explicit/scheduled start.

## Consequences

Idle monitoring uses zero history reads after startup catch-up. Later app-side
changes are not detected until an explicit sync/start, a scheduled check-in, or an active session's
necessary context boundary. The local transcript can therefore remain stale while
idle. Savings vary with prior uptime, quiet hours and session activity; required
session reads still spend the provider allowance. No schema migration, live state
reset or provider mutation is needed. Restart the host and refresh its dashboard.

This supersedes the idle-monitoring portions of
[ADR-008](ADR-008-direct-replies-and-context-sync.md) and
[ADR-020](ADR-020-room-sessions-and-chosen-encounters.md), while preserving
[ADR-025](ADR-025-kin-driven-check-ins-without-invitations.md)'s scheduling policy.
