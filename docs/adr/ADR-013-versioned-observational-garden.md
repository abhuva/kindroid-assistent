# ADR-013: Version Passing Seeds Without Enabling Identity Promotion

**Status:** Accepted | **Date:** 2026-09-06
**Participants:** Marc, Codex

## Context

Marc requested Milestone 8 after exploration follow-through and the tabbed UI.
Passing candidates, agency events and shared memory already held provenance. No
initial Roots had been reviewed for this task. Live acceptance remained outstanding.

We considered a transient garden view (no immutable item versions), replacing
exploration storage (unnecessary migration/delivery risk), and a versioned garden
linked to existing records. Automatically promoting interpreted choices was
rejected: neither response classification nor an operator annotation supplies the
deferred identity consent.

## Decision

We added a versioned observational garden. Migration 8 preserved existing Passing
candidate IDs, attribution, content and current status as a baseline. Earlier
agency history remains linked rather than reconstructed. New candidates and
archive/restore operations update garden versions in their source transaction.
Shared memory remains the evidence authority, so retractions, changed attribution
and source mismatches remain visible instead of diverging between copies.

We added stable items, immutable versions, fixed layer/ownership, relationships,
and project/attention planning storage. Roots/Growing have storage semantics but
no creation, promotion, adoption, execution or projection endpoint. Initial Roots
require a separate manual bootstrap with agreed wording, provenance, ownership
and backup. Documentation examples are never seeds. Projects require Growing
links, concrete budgets and recorded stopping reasons; these are not tool grants.

Garden lives in Operations. Read endpoints are group-scoped and bounded; versions
and exploration events are paginated. Larger summaries disclose their limits.
Dynamic text is untrusted and credential-shaped values are redacted for display.

The sole new mutation is a durable administrative annotation requiring an operator
reason and expected version. It records Marc as author, never Hannah's choice,
and cannot edit original content, evidence, layer, ownership or status. Existing
candidate archive/restore also adds a version. Both are local-only and work while
paused. Stale corrections fail; duplicate command IDs do not append versions.
Database triggers prevent history deletion/rewrites and identity updates.

This extends ADR-011/012's development-only exception, not their live acceptance.
It implements the observational foundation of
[Milestone 8](../implementation-plan.md), not the deferred confirmation/projection
parts of [ADR-002](ADR-002-layered-garden-self-model.md) and
[ADR-003](ADR-003-versioned-self-change.md).

## Consequences

- (+) Existing seeds become visible after migration without replaying chat.
- (+) Version history and live evidence links preserve correction and provenance.
- (+) Administrative edits cannot bypass Root or ownership protections.
- (-) Candidate status and garden versions must remain transactionally synchronized.
- (-) Manual Root bootstrap and live/browser acceptance remain outstanding.
- (neutral) Project records prepare later work; no research, scheduling or grants
  are enabled by this milestone.
