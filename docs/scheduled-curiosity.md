# Scheduled curiosity (Milestone 9.1)

**2026-09-08 correction:** A saved shared schedule no longer applies the legacy
inactivity cooldown described below. Recent completed messages alone do not block
ticks; actual pending work, uncertainty and history/API limits still do. See
[ADR-019](adr/ADR-019-shared-ticks-without-inactivity-cooldown.md). This needs a safe
host restart, not another migration or schedule save; old skips are not replayed.

**2026-09-07 scheduling update:** The [Schedule tab](../README.md#schedule-tab)
can now replace both legacy timers with a shared hourly allocation and dead-time
blocks. The first local save activates that precedence; existing environment
timing below remains effective until then. Limits are editable in the new tab.
Migration 10 preserves curiosity slots and reservations. See
[ADR-016](adr/ADR-016-shared-daily-schedule.md).

The combined `serve` host schedules the same observational invitations used by
**Start exploration**, independently of social check-ins. It reuses local catalog
selection, bounded response interpretation and Passing/Garden linkage. It does not
execute research/projects, promote interests or implement M9.2 reflection.

## Enable deliberately

Scheduling defaults off. Set these yourself when ready, then safely restart:

```dotenv
RELAY_EXPLORATION_ENABLED=true
RELAY_CURIOSITY_ENABLED=true
RELAY_CURIOSITY_SCHEDULE_HOURS=10,16
```

This schedules future invitations at 10:00 and 16:00 in the machine's local timezone.
It does not change social check-ins or send immediately. Leave curiosity disabled
for manual-only exploration; disabling exploration disables both paths. Pending
automatically scheduled follow-through also respects curiosity disablement/quiet
hours. Evidence stays stored. Explicit manual interpretation is a separate action.
Settings are read at startup, not hot-reloaded. Development did not change live
settings. Legacy headless `run` does not run the curiosity scheduler.

## Settings

| Setting suffix (`RELAY_CURIOSITY_…`) | Default | Meaning |
|---|---|---|
| `SCHEDULE_HOURS` | `10,16` | Fixed local hours; blank selects interval mode. |
| `INTERVAL_SECONDS` | `21600` | Six hours in interval mode; minimum one hour. |
| `JITTER_SECONDS` | `1800` | Up to ±30 minutes; smaller than interval. |
| `QUIET_HOURS` | `22:00-08:00` | Additional quiet hours; social quiet hours also apply. Blank disables this window; equal start/end blocks all day. |
| `IDLE_SECONDS` | `1800` | Thirty minutes since stored conversation activity; minimum five minutes. |
| `MAX_ATTEMPTS_24H` | `2` | Scheduled command reservations per rolling 24 hours. |
| `MAX_INTERPRETATIONS_24H` | `4` | Conservative call reservations, two per automatic-response session. |
| `WINDOW_SECONDS` | `180` | Maximum lateness before skipping a slot; 30–900 seconds. |

Manual and scheduled sessions also share `RELAY_EXPLORATION_MAX_SESSIONS_24H`
(default 3). `RELAY_EXPLORATION_AUTO_INTERPRET=false` removes automatic follow-through
and its call reservation, retaining the invitation/observation path. Each normal
flow remains capped at one clarification and one acknowledgement, not an open-ended
conversation or research loop. No new tool grants are included.

Pre-queue skips spend no reservations. Once queued, an attempt stays charged even
if history synchronization finds new activity and cancels the invitation. Call
reservations extend from the latest interpretation start and are conservatively
retained for active unfinished work. Terminal `needs_review` and failed
interpretations neither block future invitations nor extend reservations beyond
the rolling window. Actual unresolved deliveries/generation remain blockers.
These are reservations, not actual model-call counts.
Explicit manual interpretation is outside the scheduled-call allowance.

Jitter is deterministic per scheduling decision and never reduces the interval
below one hour. Fixed scheduling skips a nonexistent spring-forward hour and uses
a repeated fall-back hour once (the earlier occurrence). Restart after changing
the machine timezone. Missed slots advance from now, without backlog or bursts.

## Dashboard and safeguards

Refresh **Operations → Exploration** for schedule state, cadence, next due time,
reservations and ten recent outcomes. The main command history shows actual command
state; queued does not mean delivered. Scheduled requests are system-origin events
under local configuration, not Hannah's choice or a manual Marc request.

Pause, no bootstrap, empty room, quiet hours, recent conversation, pending Hannah
messages, other commands, unresolved exploration/delivery/generation, nearby social
check-ins and exhausted budgets cause skips. Newly discovered history also counts
as recent activity. The worker synchronizes before sending and rechecks afterward
and after context assembly. Local eligibility checks use no Kindroid calls.

History still obeys the normal rolling allowance, minimum spacing and 429 cooldown.
Successful delivery may need normal reply reconciliation. Uncertain effects pause
and never automatically resend; inspect/reconcile instead of force-triggering.

Use **Start exploration** to exercise an invitation immediately; this does not test
automatic timing. For scheduled acceptance, select a future local hour, leave the
conversation idle and inspect the slot/command/delivery trail. Do not edit live
database due times to bypass safeguards.

## Upgrade and acceptance

Back up, pause, wait for in-flight work, and restart using the
[hidden-host procedure](operations.md#starting-and-restarting), then reload the page.
Startup applies migration 9 (`curiosity_schedule`, `curiosity_slots`) without
discarding prior data. No dependency was added. Do not downgrade code against the
newer database.

Fixtures cover clocks, synthetic DST transitions, concurrent SQLite connections,
mock Kindroid/Codex work and restart failures. Live timing, Windows timezone behavior
and user-perceived intrusiveness remain acceptance work. As of 2026-09-08,
[Discovery](discovery-and-experiences.md) adds the separately opted-in, bounded
reference lookup/result/reflection slice in migration 11. Recurrence prompts,
broader research, Skeptic review and multi-week evaluation remain M9.2 work.
Older pre-dashboard baseline documents remain historical.
Verification on 2026-09-06: 80 targeted pytest tests passed; the complete suite
passed 351 tests in 56.26 seconds under a 60-second hard timeout. After locked
`npm ci --ignore-scripts`, `npm test` passed the API-client checks and all 24
dashboard DOM/accessibility tests. One earlier dashboard run hit its 15-second
timeout; the standalone rerun and full-suite dashboard checks passed. The existing
Starlette/httpx deprecation warning remains. No live calls, visual browser checks,
service restart or live database migration were performed.
See [ADR-014](adr/ADR-014-independent-curiosity-schedule.md).
