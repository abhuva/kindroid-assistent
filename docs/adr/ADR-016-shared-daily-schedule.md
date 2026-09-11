# ADR-016: Allocate Shared Ticks After Excluding Dead Time

**Status:** Accepted | **Date:** 2026-09-07\
**Participants:** Marc, Codex

**Status update, 2026-09-11:** Scheduling/UI decision superseded by
[ADR-025](ADR-025-kin-driven-check-ins-without-invitations.md). Daily allocations
are retired globally; existing state and budget records are retained.

**2026-09-08 amendment:** [ADR-019](ADR-019-shared-ticks-without-inactivity-cooldown.md)
removes the inherited inactivity rule for saved shared schedules. Pending work,
uncertainty, dead time and API limits remain guarded.

## Context

Marc requested a Schedule tab allocating a day's hourly opportunities between
social check-ins and exploration invitations. He confirmed that exploration was
the intended second activity and that dead time must reduce the available pool.
For example, excluding 22:00–06:00 leaves 16 ticks; 40/60 produces 6/10 starts.

We considered retaining independent interval timers with percentage labels,
randomly choosing each activity, and generating a deterministic daily allocation.
Independent timers could collide and would not honor the shared pool. Random
selection would make the preview and per-day counts unreliable. We chose the
deterministic allocation.

## Decision

We added a conversation-scoped, persisted daily schedule in migration 10. Until
the operator saves it, existing timing remains in effect. Saving through the
protected command queue replaces both legacy timing decisions and quiet-hour
windows. Turning the saved schedule off disables both automatic starters; it does
not restore legacy timers. The schedule does not grant exploration capability:
`RELAY_EXPLORATION_ENABLED` remains required, as does `RELAY_INITIATIVE_ENABLED`
for check-ins. Local schedule enablement supersedes `RELAY_CURIOSITY_ENABLED`.

Slots use the machine's local calendar. Dead-hour blocks can wrap midnight
and overlap; the stored union excludes each hour once. All-day dead time is valid.
Integer percentages total 100. Largest remainders produce whole counts, with
activity order resolving ties. Smooth weighted distribution spreads those counts
across available hours. Additional activity types can reuse the allocator; this
release exposes only the two existing activities.

The worker atomically records a slot, queues its command, reserves exploration
allowances and advances its deadline. A unique group/local-date/time identity
prevents a second execution across restarts, revisions and repeated clock hours.
Saving schedules strictly future ticks. Missed slots are recorded when encountered
and advance from now without a backlog; the entire downtime is not reconstructed.
Nonexistent DST hours are skipped and repeated hours are used once, at their first
occurrence, using ADR-014's existing calendar conversion.

Existing activity, pending-response, uncertainty, history and capability gates
remain. Scheduled check-ins additionally recheck their authorization after history
sync and after Codex preparation. Each tick has a three-minute start window.
Dead time prevents new scheduled activities, not continuation or manual commands.
Ongoing exploration follow-through remains bounded by its existing response chain.
No research, prompt projection or additional tools are enabled.

The tab exposes all four rolling limits: check-in attempts, shared exploration
sessions, scheduled exploration attempts and reserved interpretation calls.
Saving retains prior usage and reservations. These limits may suppress allocated
opportunities and do not imply an exact share of messages or execution time.
The existing Info check-in limit edits the same effective override.

This supersedes the independent cadence and restart-only configuration portions
of [ADR-014](ADR-014-independent-curiosity-schedule.md) when a daily schedule is
saved. It retains [ADR-007](ADR-007-dashboard-initiative-budget.md)'s accounting and
[ADR-015](ADR-015-speaker-independent-check-ins.md)'s inactivity rule.

## Consequences

**Review amendment (2026-09-07):** [ADR-017](ADR-017-exploration-review-and-role-attribution.md)
makes terminal review/failed interpretations nonblocking for both scheduled types.
Active response work and uncertain delivery remain guarded; skips show their reason.

- (+) The preview and worker use the same deterministic allocation rules.
- (+) Dead time and percentages have one explicit, durable source of authority.
- (+) Existing installations keep their behavior until a local save.
- (-) Rounding means percentages may not be exact within a single day.
- (-) Busy conversations, limits and DST can reduce actual starts.
- (-) Dead-time blocks use whole-hour boundaries and one recurring daily plan;
  weekday overrides are not included.
- (neutral) Shared scheduling runs in `serve`; headless `run` handles conversations
  but does not execute shared ticks or fall back to legacy automatic check-ins.

## Local verification

**15/30/60-minute extension (2026-09-07):** Marc requested a tick-interval button
row. We added the optional `tick_minutes` setting to the existing JSON configuration,
defaulting to 60 for older saves. No new schema or data rewrite was needed. The
allocator now assigns minute-of-day slots; the graph groups one, two or four ticks
per hour. Interval changes schedule strictly future ticks and keep budgets/history.
Top-of-hour slot IDs retain their original format; fractional ticks add minutes,
preventing duplicate top-of-hour starts across upgrades or interval changes. DST
conversion applies to every tick. The recent list is bounded to 192 records so a
full 15-minute day fits. Independent or randomized timers remain rejected for the
same reasons as the original decision.

Extension verification: all 420 Python tests passed in two bounded batches
(236 in 44.69 seconds and 184 in 31.92 seconds). The final `npm test` passed its
API-client checks and all 29 DOM/accessibility tests. Offline browser checks covered
15/30-minute selection, keyboard controls, and unclipped tick labels at 1440px,
1024px and 390px. One earlier accessibility run exceeded its existing 20-second
bound; simplifying a CSS selector resolved it without increasing the timeout.
The existing Starlette/httpx deprecation warning remains. No live restart,
configuration edits, database migration or Kindroid calls were performed.

The following verification records the original hourly implementation:

On 2026-09-07, all 403 Python tests passed in two complementary batches
(235 in 48.54 seconds; 168 in 29.00 seconds), each with a 60-second hard limit.
An initial single full-suite invocation reached its 60-second limit before
completion, without reported test failures. The final `npm test` passed its API
client checks and all 28 DOM/accessibility tests. The expanded DOM suite uses a
20-second bound after one run exceeded the previous 15-second limit.

An offline browser fixture using the actual HTML, CSS and Schedule script verified
the 22:00–06:00 example, 24 graph cells, keyboard toggling and layouts at 1440px and
390px without horizontal overflow. It did not exercise the live host or service.
The existing Starlette/httpx deprecation warning remains; no live settings,
database migration, relay restart or Kindroid calls were performed.
