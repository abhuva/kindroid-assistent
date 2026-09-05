# ADR-006: Deliver the Dashboard Before Self-Change Automation

**Status:** Accepted | **Date:** 2026-09-05  
**Participants:** Marc, Codex

## Context

The proposed self-change protocol includes dedicated reflection, confirmation,
cooldowns, versioned prompt updates, verification, and rollback. It is intentionally
deliberate and depends on strong operational visibility. The existing terminal-only
workflow makes it difficult to inspect state, schedules, failures, and decision
history as the system grows.

Alternatives considered were:

1. Implement prompt self-modification and the dashboard together.
2. Implement informal self-modification first and add safeguards later.
3. Build the operational dashboard first, then observational agency, then deliberate
   self-change.

## Decision

We decided to implement a localhost operational dashboard as the next pass. The
first agency pass may collect attributed observations and run read-only curiosity
experiments, but it will not automatically promote interests, modify Roots, or
write Kindroid configuration.

The formal confirmation protocol is preserved as a later design and must be
implemented before automated self-change.

## Consequences

- (+) Later agency behavior will be observable and recoverable from its beginning.
- (+) The dashboard's command, event, and approval infrastructure is reusable.
- (+) Early experiments cannot silently alter Hannah's persistent identity.
- (-) Hannah's direct self-editing capability arrives later.
- (-) Some future UI requirements will only be validated after the garden model
  exists.
- (neutral) The product gains operational value before the complete agency vision
  is implemented.

