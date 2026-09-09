# ADR-015: Make Check-ins Independent of the Last Speaker

**Status:** Accepted | **Date:** 2026-09-07
**Participants:** Marc, Codex

**2026-09-08 amendment:** [ADR-019](ADR-019-shared-ticks-without-inactivity-cooldown.md)
supersedes inactivity eligibility after a shared daily schedule is saved, including
manual Trigger check-in. This ADR's inactivity rule remains the legacy behavior.

## Context

Marc observed a midnight check-in being skipped because Codex was not the last
speaker. The old rule conflated initiation with turn continuation: even a processed
Hannah reply could prevent future check-ins. The due slot had already advanced,
so it was lost. Pending replies already had a separate guard.

We considered retaining the speaker restriction, removing it without any activity
replacement, and making eligibility depend on inactivity and unresolved work.
Retaining it preserved the missed-opportunity bug; removal alone could interrupt
an active Marc/Hannah conversation. We chose the third approach.

## Decision

We removed last-speaker eligibility from scheduled and manually triggered social
check-ins. Hannah, Marc, and Codex may each be the last speaker; attribution rules
and the authoritative outbound prefix remain unchanged. We added a five-minute
default inactivity requirement (`RELAY_INITIATIVE_IDLE_SECONDS`, minimum 60 seconds).
It uses the synchronized message timestamp, accepting seconds or milliseconds,
not the time an old transcript was imported. Unknown or future times fail closed.

Synchronization and generation reconciliation still precede context-dependent
work under [ADR-008](ADR-008-direct-replies-and-context-sync.md). Pending AI messages
take priority. Pending/sending/uncertain outbox entries prevent a new check-in and
are never resent by this path. Pause, quiet hours, history limits, daily budgets,
bounded turns, and persona restoration remain intact. Activity and delivery skips
do not spend check-in budget under [ADR-007](ADR-007-dashboard-initiative-budget.md);
historical `latest_not_codex` outcomes remain readable and non-spending.

We retained skip/no-catch-up behavior for genuinely blocked slots. No replay of the
reported midnight slot, automatic resume, live configuration edit, schema change,
or change to the separate curiosity schedule was included.

## Consequences

- (+) An idle conversation can receive a check-in regardless of who ended it.
- (+) Pending replies and active conversations remain protected without speaker bias.
- (-) Manual Trigger check-in also waits for inactivity; it bypasses only due time.
- (-) Genuine activity at a fixed slot still causes a skip, not deferred delivery.
- (neutral) Defaults apply after restart; live timing acceptance remains separate.
