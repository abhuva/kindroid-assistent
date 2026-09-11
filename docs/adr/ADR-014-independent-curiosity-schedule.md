# ADR-014: Schedule Curiosity Independently of Social Check-ins

**Status:** Accepted | **Date:** 2026-09-06
**Participants:** Marc, Codex

**Status update, 2026-09-11:** Scheduling decision superseded by
[ADR-025](ADR-025-kin-driven-check-ins-without-invitations.md). No new exploration
invitations or automatic follow-ups run; historical observations remain stored.

**2026-09-07 amendment:** [ADR-016](ADR-016-shared-daily-schedule.md) supersedes
independent timing and restart-only schedule settings after the first local daily
schedule save. This ADR remains the legacy behavior until then; delivery,
reservation and attribution safeguards continue to apply.

## Context

Marc requested scheduled exploration after confirming only social check-ins ran
automatically. M9.1 needed independent cadence, attention limits and safe coexistence
with conversations. Existing invitation/interpretation/garden machinery was reusable.

We considered replacing check-ins, attaching invitations to every idle poll, or
using a separate durable scheduler. Replacing check-ins conflated two purposes;
poll-driven invitations risked repeated pressure and catch-up bursts. We chose a
separate schedule using the existing serialized worker and safe delivery path.

## Decision

We implemented M9.1 under the existing development-only exception. Research
experiences, reflection, recurrence prompts and evaluation remain M9.2 work. No
identity promotion, prompt projection or execution grant was added. Curiosity
defaults off and requires exploration enabled as well.

The combined `serve` worker owns scheduling; request threads and legacy headless
`run` do not schedule invitations. Fixed system-local hours or intervals with
deterministic bounded jitter are supported. OS calendar round trips skip nonexistent
DST hours and select the earlier occurrence of repeated hours. Enabling or changing
configuration schedules a future slot instead of immediately sending.

Migration 9 added conversation-scoped schedule state and slot records. One short
transaction reserves the slot/budget, queues `scheduled_exploration` and advances
the deadline. Unique slots prevent duplicates across restarts or concurrent
connections. Missed/blocked slots are skipped, not accumulated. Interrupted
commands retain the existing conservative pause/no-retry behavior.

Eligibility is checked locally before queuing, after normal budgeted history sync,
and after context assembly before preparing the invitation. Pause, both quiet-hour
policies, recent activity, pending messages, unresolved delivery/generation/exploration,
other commands, nearby social check-ins and quotas block scheduling. Scheduled
requests and automatic response activation are system-origin events, not Marc
manually asking or Hannah choosing.

Scheduled commands have a rolling attempt ceiling and share the existing combined
manual/scheduled exploration ceiling. Automatic response flows reserve two model
calls against a separate conservative allowance. The reservation timestamp extends
when interpretation starts, so late follow-through remains accounted for. Reservations
are not actual usage counts or refunds. Unfinished response work blocks new offers.
Scheduled follow-through respects curiosity disablement/quiet hours; explicitly
requested manual interpretation is separate local authority.

Operations → Exploration shows cadence, next due time, reservations and the ten
latest outcomes. There is no public force-scheduled-command endpoint. Local startup
configuration is the opt-in authority; chat/model output cannot enable scheduling.

## Consequences

**Review amendment (2026-09-07):** [ADR-017](ADR-017-exploration-review-and-role-attribution.md)
excludes terminal review/failed responses from scheduling blockers and reservation
extensions beyond 24 hours. Active work and uncertain effects remain blocking.

- (+) Exploration can introduce breadth without replacing social check-ins.
- (+) Durable slots, fresh-context checks and quotas bound prompting and retries.
- (+) Existing memory, garden, source/outbox linkage and persona restoration are reused.
- (-) Activity or uncertainty can suppress many slots; nothing catches up afterward.
- (-) Conservative reservations can leave unused capacity after post-sync cancellations.
- (-) Settings require restart; legacy headless `run` stays unscheduled.
- (neutral) Live timing, Windows timezone behavior and intrusiveness need acceptance.

This extends the manual-only portions of [ADR-011](ADR-011-one-shot-observational-exploration.md)
and [ADR-012](ADR-012-exploration-response-follow-through.md), preserving their
no-research/no-promotion and uncertain-effect boundaries.
