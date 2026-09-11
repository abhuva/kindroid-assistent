# ADR-020: Isolated rooms and participant-chosen encounters

Monitoring update (2026-09-11): [ADR-027](ADR-027-no-passive-chat-monitoring.md)
removes idle history polling in all rooms while preserving active sessions.

History policy update (2026-09-11): [ADR-026](ADR-026-advisory-daily-history-measurement.md)
supersedes local hard/rolling history limits with daily UTC advisory measurement.
Actual history 429 now fails the session and pauses its room; cooldown remains.

**Status:** Accepted | **Date:** 2026-09-10
**Participants:** Marc, Codex

**2026-09-11 amendment:** [ADR-025](ADR-025-kin-driven-check-ins-without-invitations.md)
supersedes invitation workflows, optional timing and shared daily starts with
Kin-driven check-ins in every room. Room isolation and effect safeguards remain.

## Context

Marc requested three rooms, including a scheduled Hannah/Miko conversation with
silent turn advancement. Hannah requested control over her next encounter.
Marc explicitly included exploration in that choice: an encounter starts as a
check-in offering exploration, rather than independently scheduled invitations.

## Decision

We retained one host and one side-effect worker, with a database and Codex thread
per room. The existing database remains the primary room and account history
ledger. Secondary rooms share only history quota, spacing and cooldown; they do
not retrieve another room's messages, memory, approvals or agent context.

We added an opt-in, locally bounded participant scheduling permission. A source-
linked next-time or no-follow-up choice supersedes both automatic starters in its
room. Only the worker applies validated choices. Other capabilities, limits and
local approval requirements do not change. Check-ins offer exploration; an explicit
choice can invoke the existing bounded invitation workflow when enabled.

We separated session start policy from continuation. The shared room uses a daily
start time and manual participant selection. Ordinary code alternates configured
AI IDs. New participant messages containing `codex` wake assistance; occasional
silent checks decide whether to end. Normal endings stop only the current session.
Durable generation records can anchor to an AI message, and uncertain effects are
reconciled before continuation, never retried.

## Alternatives considered

- Three independent processes: rejected because persona changes and quota would race.
- Re-scope every historical SQL table in one database: larger migration and leakage
  risk than retaining existing per-room stores at this scale.
- A model invocation every turn: unnecessary for alternation and keyword detection.
- Keep independent exploration timers: conflicts with the requested next encounter.
- A general agent-controlled scheduler: exceeds the bounded room-specific choice.

## Consequences

Room databases must be backed up individually together with the primary ledger.
The single worker yields between turns but cannot interrupt an in-flight call.
Natural endings are detected at checkpoints unless Codex is explicitly called.
Empty generation bodies still require budgeted history recovery. The new behavior
is opt-in; existing configuration remains valid. This amends ADR-005, ADR-008,
ADR-016, ADR-017 and ADR-019 for configured multi-room/participant encounter modes.
Joint participant scheduling and cross-room memory remain deferred.

[ADR-024](ADR-024-encounter-recovery-and-companion-quiet-hours.md) adds missed
appointment recovery, system retry timing and companion-specific quiet windows.

[ADR-021](ADR-021-shared-room-interjections.md) records shared-room continuation
and safe history matching when Marc or the app adds messages during a session.

[ADR-023](ADR-023-responsive-shared-session-endings.md) adds early farewell-triggered
attention and explicit ending assessment on every assistance call.
