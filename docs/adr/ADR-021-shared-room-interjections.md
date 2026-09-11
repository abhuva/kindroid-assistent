# ADR-021: Continue shared sessions across app participation

**Status:** Accepted | **Date:** 2026-09-11
**Participants:** Marc (stability report), Codex (implementation decision)

## Context

Marc reported that typing into the Hannah/Miko/Codex group paused the relay and
that history sync followed by resume did not recover it. Offline regression tests
reproduced the failure: the matcher inherited a private-exchange rule that stopped
at every user message. A generated shared reply after that message could never
match its saved predecessor. Successful synchronization also previously treated a
new user tail as a session end and a new AI tail as an error.

The shared room deliberately avoids history polling or a model invocation every
turn. App participation can therefore arrive between a saved predecessor and its
generation, or while an assistance decision is pending. Chat text cannot authorize
effects, and uncertainty cannot be resolved by repeating generation.

## Decision

We treated app participation as conversation content and continued the active
session at its next existing synchronization boundary. We retained the single
worker, history budget and no-polling policy from
[ADR-020](ADR-020-room-sessions-and-chosen-encounters.md).

For shared generations without an outbound-message association, we permitted
matching across external user messages and other companion responses. The saved
confirmed predecessor and next confirmed relay post bound the search. A match
must be unique, unclaimed, from the requested AI, and have the captured response
text when available. Without captured text, only one unclaimed response from the
requested AI is sufficient; two remain ambiguous. Speaker fallback, stable local
IDs, atomic reconciliation and no uncertain retries remain as in
[ADR-008](ADR-008-direct-replies-and-context-sync.md). Private outbox-backed windows
were not relaxed.

After a complete sync, the worker adopts the confirmed nonblank history tail once.
A human tail retains the next speaker; a companion tail selects the other
companion. Older snapshots cannot rewind newer provisional effects. Session age
and counters are not reset, and newly imported external AI replies count toward
the session limit. This uses existing metadata and records, without a migration.

Changed context supersedes pending attention and cancels only a never-attempted
outbox answer. Sent effects, proposals and source links remain recorded;
sending/uncertain effects still block continuation. Joining in does not itself
invoke Codex or post narration. The worker preserves a specific reconciliation
pause reason instead of replacing it with a generic room-failure reason.

## Alternatives considered

- End every session on manual participation: interruptive for ordinary joining-in
  and does not repair a generation already crossing the old matching boundary.
- Ignore new app context: leaves a stale anchor and can post an obsolete answer.
- Synchronize before every generation: spends scarce history quota and still
  cannot eliminate the race with input during the network call.
- Match the latest reply or clear uncertain records: loses reliable source
  association and risks repeating real external effects.

## Consequences

Ordinary interjections no longer force a pause or automatic end. An already
paused instance can recover by updating/restarting, successfully syncing that
room, then resuming, without resetting its database or regenerating its reply.
Expired sessions still end at their original limits.

Detection is delayed until an existing history boundary, not instantaneous. A
saved but unsent assistance answer can be cancelled after context changes; its
audit record is retained. Truly ambiguous same-speaker responses, edits, missing
history and uncertain outbound sends still require inspection. Provider history
does not supply a generation correlation guarantee. Tests use synthetic state
and mocked providers; the reported live state was not read or modified.

The later operator-approved diagnostic confirmed a genuinely ambiguous uncaptured
generation. [ADR-022](ADR-022-operator-shared-session-recovery.md) adds explicit
session abandonment and fresh-history adoption without relaxing this matcher.
