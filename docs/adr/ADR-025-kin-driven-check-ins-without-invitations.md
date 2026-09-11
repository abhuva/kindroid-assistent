# ADR-025: Kin-driven check-ins without exploration invitations

**Status:** Accepted | **Date:** 2026-09-11
**Participants:** Marc, Codex

## Context

Marc found scripted exploration invitations artificial and ineffective for the
companions. Participant-chosen encounters still coexisted with daily tick
allocations, optional invitation workflows and a separate shared-room daily start.
Removing only the Daily Schedule panel would leave those hidden timers active.

## Decision

We decided to retire exploration invitations and automatic interpretation/research
follow-ups, and use Kin-selected next check-ins globally in all private and shared
rooms. Ordinary conversation can still follow their interests naturally. Existing
approval boundaries for consequential work remain unchanged.

Runtime configuration always enables participant scheduling; old environment and
room opt-outs cannot restore daily scheduling. Daily allocations, legacy curiosity
timers and shared daily starts no longer govern work. Their saved rows remain for
audit. New requests to retired mutation endpoints are rejected; queued legacy
commands finish with a retired outcome and no external effects.

The dashboard no longer loads the Daily Schedule panel, tick history editor,
shared daily-start controls or discovery configuration. Companion quiet hours and
room-scoped next-encounter overrides remain. Historical exploration evidence and
inspection/closure controls remain available, without new invitations.

Either companion can supply a source-linked next time in a shared room. Existing
silent attention checks extract a choice without extra model calls or prolonging
a goodbye. Only attributable current-session context can schedule. The most recent
valid choice governs that room, not other rooms. Global means one scheduling
policy, not a shared next-time value or shared conversation context.

We retained [ADR-024](ADR-024-encounter-recovery-and-companion-quiet-hours.md)'s
missed-appointment recovery, six-hour system fallback and per-companion quiet hours.
A room without an existing choice gets a durable fallback, rather than reverting
to daily timers. Explicit no-follow-up remains valid. Shared check-ins also spend
the room's check-in allowance. Quiet hours, pause, budgets and uncertain effects
still gate work. Old daily enable/dead-hour switches do not silently block it.

Safe finished or never-sent exploration sessions are closed locally with an audit
event. Records are not deleted. Uncertain sends/generations and unresolved session
deliveries are retained for explicit inspection, never treated as successful or
automatically retried because the feature was removed. No schema change is needed.

## Alternatives considered

- Hide the panel only: leaves hidden timers and queued invitation effects active.
- Keep invitations opt-in or selectable per room: contradicts the requested global
  removal and preserves the artificial interaction.
- Retain daily shared starts: gives the shared companions different scheduling
  authority from private rooms.
- Delete old schedules and exploration records: loses evidence and can conceal
  uncertain external effects.
- Ask a timing question after every shared goodbye: prolongs a finished exchange;
  use existing checks and the bounded fallback instead.

## Consequences

The UI and automatic behaviour are simpler. Missing choices produce explicit
system retries, not attributed preferences. Shared timing quality still requires
real-model acceptance; fixtures verify validation and transitions, not semantics.
Nullable structured fields do not establish semantic correctness; see the
[official Structured Outputs guidance](https://developers.openai.com/api/docs/guides/structured-outputs).

Pause, wait for in-flight work, safely restart the development host, then reload
the dashboard. Do not reset state or resend uncertain work. No live provider calls
or running-host changes were made to implement this decision.

This supersedes invitation/timer portions of
[ADR-020](ADR-020-room-sessions-and-chosen-encounters.md),
[ADR-016](ADR-016-shared-daily-schedule.md) and
[ADR-014](ADR-014-independent-curiosity-schedule.md), while retaining their audit
records and the original isolation, budget and authority safeguards.
