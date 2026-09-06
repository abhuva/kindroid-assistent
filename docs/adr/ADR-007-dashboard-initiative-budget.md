# ADR-007: Persist a Narrow Dashboard Override for the Initiative Budget

**Status:** Accepted | **Date:** 2026-09-06
**Participants:** Marc, Codex

## Context

Marc requested a default of 50 check-in attempts per rolling 24 hours, an editable
dashboard limit, and correction of budget consumption by non-sending skips. This
is a narrow extension of the operational UI, not general environment editing.

Alternatives considered were rewriting the environment file, changing an in-memory
setting until restart, or persisting an audited override in existing SQLite state.
Environment rewriting risks unrelated configuration/credentials; an in-memory
value loses the operator's choice at restart. Immediate HTTP writes would bypass
the existing [serialized command boundary](ADR-005-local-dashboard-command-queue.md).

## Decision

We decided to enqueue a local setting command through the same-origin protected
API. The worker validates and atomically stores the numeric override and audit
event. The override survives restart and takes precedence over configuration;
without one, explicit environment configuration still takes precedence over the
new default of 50. Dashboard input is limited to integers from 1 through 1000.

Only attempts that reach Codex/check-in work consume budget. Eligibility skips,
including existing historical skip rows, do not. Work-started, failed, uncertain,
and unrecognized historical outcomes remain charged conservatively. Lowering the
limit does not erase usage. The dashboard shows usage and the time enough entries
expire for another attempt. Manual triggers do not bypass this budget or Kindroid's
separate history budget/cooldowns. Saving a setting does not unpause the relay.

## Consequences

- (+) Operator changes persist without touching credentials or remote configuration.
- (+) Settings use existing idempotency, CSRF, worker serialization, and audit rules.
- (+) Known non-sending skips no longer exhaust the allowance.
- (-) Saving waits for the worker's current operation to reach a safe boundary.
- (-) Configuration has an explicit dashboard-override precedence to explain.
- (neutral) No schema migration or generic settings editor is required.
