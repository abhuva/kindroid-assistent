# ADR-019: Let Shared Ticks Determine Conversation Spacing

**Status:** Accepted | **Date:** 2026-09-08\
**Participants:** Marc, Codex

## Context

A 00:30 exploration tick was skipped after a check-in ended at 00:17:50. The
shared 15-minute schedule still inherited a thirty-minute exploration inactivity
rule. Codex's own closing messages counted as activity, so check-ins could repeatedly
suppress the allocated exploration opportunities. Marc requested removal of this
cooldown after diagnosis of the skip and a separate reply-control inconsistency.

## Decision

We removed inactivity thresholds from the saved shared schedule's effective
settings, including social check-ins, curiosity invitations and bounded experience
follow-through. The existing tick allocation and dead-time controls determine
spacing. Recent completed or newly synchronized messages alone no longer block a
tick. Explicit manual Trigger check-in also uses these effective settings.

We retained synchronization, pending Hannah messages, command serialization,
unresolved exploration/delivery/generation gates, pause, start windows, history/API
cooldowns and rolling budgets. No uncertain effect is replayed. Skipped ticks are
not caught up. Installations without a saved daily schedule retain their legacy
inactivity settings; updating code does not silently enable the shared schedule.

This supersedes only the shared-schedule inactivity portions of
[ADR-016](ADR-016-shared-daily-schedule.md) and its references to
[ADR-014](ADR-014-independent-curiosity-schedule.md) and
[ADR-015](ADR-015-speaker-independent-check-ins.md). No schema or configuration
rewrite is needed. The host must be safely restarted; a schedule re-save is not
required.

## Alternatives considered

- Retain or shorten the cooldown: still hides a second timing policy behind tick
  allocation and can suppress opportunities after longer exchanges.
- Ignore only Codex messages: leaves speaker-dependent timing and still confuses
  recently completed exchanges with unfinished work.
- Remove every gate: rejected because unfinished work and uncertain external
  effects require coordination, independently of elapsed time.
- Remove legacy settings too: unnecessary for the shared schedule correction;
  preserving them avoids unrelated behavior changes for unswitched installations.

## Consequences

- Shared tick eligibility reflects actual unresolved work rather than message age.
- A new scheduled topic can follow shortly after a completed exchange or a recent
  Marc message. This is an intentional consequence of removing inactivity gates.
- Actual starts can still be fewer than previewed because of safety and budgets.
- Bounded experience results may return at the next synchronized worker boundary
  without an extra inactivity wait; they remain opt-in and bounded.
- Mocked tests cover recent-history acceptance and retained blockers. Live timing
  and conversational quality remain operator acceptance checks.

## Verification

All 543 Python tests passed in three complementary bounded batches: 252 in
23.56 seconds, 47 host/lifecycle tests in 21.50 seconds, and 244 targeted/UI tests
in 32.69 seconds. Each final batch had a 60-second OS-level timeout. `npm ci
--ignore-scripts` completed with zero vulnerabilities; `npm test` passed the
API-client checks, 30 DOM behavior tests and the axe accessibility scan.
Existing Starlette/httpx and whatwg-encoding deprecation warnings remain.
`git diff --check` passed. No live Kindroid/public-service calls, relay restart,
live database changes or real-browser acceptance were performed.
