# ADR-022: Operator-directed recovery of an interrupted shared session

**Status:** Accepted | **Date:** 2026-09-11
**Participants:** Marc, Codex

## Context

An explicitly approved diagnostic of the paused Hannah/Miko room found an
uncaptured generation with two possible replies from the requested companion.
Neither another sync nor a looser text matcher can establish the missing source
link. Marc approved an explicit recovery action after clarification that a
"session" is the relay's automatic exchange, not the Kindroid group/chat.

The no-uncertain-retry invariant in
[ADR-008](ADR-008-direct-replies-and-context-sync.md) and
[ADR-021](ADR-021-shared-room-interjections.md) must remain intact. Recovery cannot
turn a guessed association into evidence or reset the remote conversation.

## Decision

We added a local, explicitly confirmed, worker-owned `recover_shared` command.
Its payload identifies the selected session and pending generation, preventing a
stale click from abandoning a newer exchange. The room must be paused. Only a
shared generation without captured reply text or an outbox association is
eligible; other unresolved generations, provisional messages, pending/uncertain
outgoing effects and unfinished assistance/result delivery block this action.

The worker reads a fresh complete history window, using ordinary account-wide
quota, spacing, pagination and cooldown. It then rechecks eligibility and commits
the imported history, ended relay session, baseline cursor/tail and recovery audit
atomically. Imported messages are processed; historical mentions are not replayed.
The room remains paused. Resume and starting another conversation are separate
operator actions; the existing daily schedule is not changed.

Migration 13 adds `shared_recoveries` and an active-generation view without
rebuilding or rewriting existing generation/session records. Each recovery keeps
the operator command, session, generation and adopted history anchor linked.
The original generation keeps its uncertain status and the old session retains
its pending-generation link and counters. The active view excludes only explicitly
abandoned generations from continuation gates and reconciliation. It never marks
them reconciled or attributes a candidate reply to them.

Retries after a lost response or commit/restart return the existing recovery
without another read or state change. History failure, incomplete pagination,
changed eligibility and transaction failure do not partially abandon a request.
Previously confirmed source text is not silently rewritten; conflicting text
still requires separate inspection. Earlier saved history is retained, not purged
to mirror remote deletions. No Kindroid send, generation, persona change, group
reset or model call belongs to this recovery operation.

## Alternatives considered

- Guess which response belongs to the request: rejected because attribution
  uncertainty would be hidden behind a false confirmed link.
- Retry generation: rejected because the original external effect may have happened.
- Delete/reset local state: rejected because it discards delivery, provenance,
  approvals and quota history, making duplicate effects harder to prevent.
- Mark the generation reconciled: rejected because an operator deciding to move
  on is not a provider confirmation.
- Add a new generation status and rebuild the existing linked table: feasible,
  but a separate append-only recovery record preserves the original observation
  and avoids rebuilding referenced session/generation tables.
- Automatically recover every ambiguity: rejected because abandoning an exchange
  is an operator choice, and uncertain outgoing messages need separate handling.

## Consequences

The observed uncaptured-generation ambiguity has an explicit recovery path while
its audit remains honest. Recovery does not prove which response the request
caused. The control is deliberately narrower than a general transcript repair.
A complete history read can cost multiple requests and remains quota-limited.
The UI explains eligibility and asks for confirmation before enqueueing work.
Schema 13 needs a safe host restart; it must not be applied under a running older
worker. No live recovery or provider calls are authorized by automated tests.
