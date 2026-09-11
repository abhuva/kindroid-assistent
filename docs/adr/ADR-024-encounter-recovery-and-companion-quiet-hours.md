# ADR-024: Encounter recovery and companion quiet hours

**Status:** Accepted | **Date:** 2026-09-11

**Participants:** Marc, Codex

## Context

Marc requested recovery for missed participant-chosen appointments, a six-hour
fallback for unusable timing, and separate night-time windows for Hannah and Miko.
ADR-020 previously left missed choices consumed without a follow-up.

## Decision

We kept the one-hour grace period. After that, the worker records an appointment
as missed and creates one durable recovery invitation. At the next permitted
opportunity it explains that the relay missed the encounter and offers meeting
now or rescheduling, without asserting a specific outage cause. Its deterministic
opener uses the ordinary check-in allowance, history sync, outbox and generation
safeguards; it needs no extra model call.

We added a system-authored six-hour retry when timing is absent or unusable.
A fallback is also saved at encounter start, covering a declined turn or a crash
before a timing choice. A valid participant choice supersedes it; an explicit
no-follow-up choice prevents it. Invalid structured replies are recorded without
publishing raw model output; the source is marked processed and the retry remains
scheduled. Other provider failures and uncertain effects keep their safety gates.

We moved fallbacks inside quiet hours or room dead time to the first open minute.
Disabled schedules or a day without open time keep them pending without sending.
Late fallback invitations wait rather than creating catch-up bursts. Commands
are consumed before external work and attempted effects are never replayed.

We stored companion quiet windows in existing account metadata, keyed by configured
participant identity and audited through durable commands. Availability policy is
shared across that companion's rooms without sharing conversation context. Either
companion's quiet window holds the shared room. Windows default to disabled, use
the host's timezone, and reject equal start/end so part of the day remains open.

Quiet hours hold automatic polling, conversations, exploration and background model
work at worker/turn boundaries. Queued external commands wait; local configuration,
pause/end controls and explicit history sync remain available. A turn underway may
finish. Shared-session lifetime limits keep counting while held. Existing room
dead time and capability controls still apply to encounters; shared daily starts
keep their separate no-catch-up policy.

Schema 13 already supports these encounter kind/actor and metadata records; no
schema transformation or database reset was needed.

## Alternatives considered

- Drop missed appointments: leaves no way to negotiate recovery.
- Replay planned work immediately: removes the companion's choice and can repeat
  an effect after interrupted delivery.
- Ask repeatedly for timing: creates an unbounded clarification loop.
- One host-wide quiet window: cannot represent different availability.
- Independent room quiet windows: can wake a companion in another room.
- Interrupt synchronous calls at night-time: creates ambiguous effects.

## Consequences

**2026-09-11 amendment:** [ADR-025](ADR-025-kin-driven-check-ins-without-invitations.md)
applies this recovery/fallback policy in every room and retires exploration and
old daily dead-hour/enable controls. Per-companion quiet hours remain authoritative.

Recovery spends ordinary check-in/history allowances. Paused or uncertain work
still needs inspection. Primary account metadata must be backed up with room state.
Host timezone/DST governs open minutes; checking real epoch minutes skips missing
local times. Offline tests do not establish real-model or Pi outage acceptance.

This amends [ADR-020](ADR-020-room-sessions-and-chosen-encounters.md) and quiet-time
precedence in [ADR-016](ADR-016-shared-daily-schedule.md).
