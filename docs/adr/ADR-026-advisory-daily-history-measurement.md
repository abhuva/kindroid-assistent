# ADR-026: Advisory daily history measurement

Status: Accepted

Date: 2026-09-11

Participants: Marc, Codex

## Context

The relay enforced an account-wide rolling 24-hour local history limit. It aged
requests out individually rather than resetting the visible counter together.
Marc requested a daily measurement and UI warning, never a local permission gate.
Only a real provider rejection should fail work for exhausted history access.

Kindroid documents a 600-request/24-hour history allowance and HTTP 429 for rate
limits, but does not specify a reset boundary. This reporting window must not
claim to reproduce the provider's accounting. Source: [Kindroid API documentation](https://kindroid.ai/v2/docs/api-documentation/), checked 2026-09-11.

## Decision

We decided to count history request attempts in fixed UTC calendar days, resetting
at 00:00 UTC, exactly 24 hours apart without daylight-saving changes. The UI shows
the reset timestamp and identifies this as local measurement. Existing durable
request timestamps are reused; no database migration, reset job or history wipe
is needed. Reads of the counter reflect a new day even after downtime.

`RELAY_MAX_HISTORY_REQUESTS_24H` remains compatible configuration, but is now only
a warning threshold. All rooms share the account counter. Each requested page,
including failed requests, counts; exceeding the threshold never blocks fetching,
recovery or scheduling. Operation-level attempt counts remain correct across a
daily reset. The separate rolling check-in/initiative budget is unchanged.

Actual history HTTP 429 fails the active session and pauses that room. Commands
that encounter the rejection are failed even if their operation returns a partial
result. Successful sends and approval execution remain recorded as successful
effects; no effect is undone or retried. Active shared sessions end while retaining
their pending-generation evidence. An explicit recovery read in an already paused
room preserves that room's session and original pause reason when rejected.

Account-wide minimum request spacing and durable provider cooldown remain in force.
`Retry-After` takes precedence over the existing exponential fallback. The local
daily reset does not clear a provider cooldown or resume a failed session. After
cooldown, the operator may sync/reconcile while paused and resume separately.
HTTP 429 is reported as rate limiting, not proof of the exact daily quota boundary.

This supersedes the local hard-history-budget portions of
[ADR-008](ADR-008-direct-replies-and-context-sync.md),
[ADR-020](ADR-020-room-sessions-and-chosen-encounters.md), and
[ADR-022](ADR-022-operator-shared-session-recovery.md).

## Alternatives considered

- Keep a rolling counter and hard limit: rejected because it blocks useful work
  based on an estimate and does not provide the requested visible daily reset.
- Reset 24 hours after the first request: valid, but makes reset times vary across
  installs. UTC days provide predictable, restart-independent reporting.
- Reset at local midnight: rejected because daylight-saving transitions produce
  23/25-hour windows and deployment timezones can differ.
- Guess Kindroid's reset time or clear cooldown at local reset: rejected because
  the API does not establish that boundary.
- Automatically resume after 429: rejected for this change; the requested failed
  session and any uncertain effects require inspection, not silent continuation.

## Consequences

Local undercounting or conservative thresholds no longer prevent work. More reads
may reach Kindroid and exhaust its real allowance. Other clients or installations
are not included in this local count, so the UI remains advisory. Existing request
rows and all message/generation/outbox audit data are preserved by the upgrade;
ordinary bounded pruning of old counter rows continues. Historic command outcomes
may still show the retired `history_budget` reason.

Verification uses synthetic state, mocked provider responses and DOM tests; no live
quota exhaustion test is authorized or required.
