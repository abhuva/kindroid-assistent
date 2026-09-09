# ADR-009: Keep Agent Context Memory Separate from Hannah's Identity

**Status:** Accepted | **Date:** 2026-09-06
**Participants:** Marc, Codex

## Context

The relay preserves captured chat, but explicitly supplies only recent messages
to Codex. Important older statements, connections, and unfinished conversations
have no dedicated retrieval path. The observational-agency and garden plans cover
parts of this problem but mix agent understanding with a future canonical
self-model and identity-change governance.

Marc clarified that Kindroid owns Hannah's memory experience. The immediate need
was for her personal agent to understand the wider situation and offer more useful,
varied assistance, not to construct a replacement memory or identity for Hannah.
He proposed finding graph entry points through fuzzy/semantic search and then
following connections between facts, people, places, and other relevant entities.

Alternatives considered were:

1. Rely on recent chat and the persistent Codex thread alone. This left important
   older context without explicit retrieval or an inspectable source trail.
2. Expand the garden/self-model first and treat remembered statements as canonical
   Roots. This conflated recording what Hannah said with deciding her identity.
3. Use semantic passage search alone. It preserved useful wording but did not
   explicitly represent relationships, corrections, or why connected context mattered.
4. Build source-backed agent context memory with hybrid search and bounded graph
   expansion, independently of future identity-change mechanisms.

## Decision

We chose the fourth approach and added Milestone 6A, after dashboard release
acceptance and before Milestone 7's proactive exploration. Existing milestone
numbers and task IDs were retained. This refined the staged order in
[ADR-006](ADR-006-stage-dashboard-before-self-change.md), without bypassing its
dashboard release gate or authorizing self-change.

We defined memory as Codex's attributable, revisable understanding of Hannah's
context: people, places, significant experiences, expressed values, interests,
unfinished questions, prior offers and outcomes, and conversational commitments.
A claim that something is a Root records who described it that way and its source;
it does not establish a canonical Root or modify Kindroid configuration.

We separated entities, attributed statements/typed relationships, original evidence,
and agent interpretations. We selected graph-shaped tables in the existing SQLite
database as the initial storage direction, with derived, rebuildable search indexes.
We planned hybrid lexical/fuzzy/semantic entry-point retrieval followed by bounded
relationship expansion and source-backed context assembly. Model/backend selection
and exact schema remain implementation tasks, not decisions already deployed.

We kept ordinary memory capture outside the self-change confirmation ceremony:
recording a statement is not adopting it as identity. Ambiguous attribution,
contradictions, temporal changes, and corrections must remain visible. Search
similarity and graph connectivity cannot establish truth, preference, or consent.

We retained the trust and context boundaries in
[ADR-008](ADR-008-direct-replies-and-context-sync.md): synchronization precedes
context-dependent work, retrieved content remains untrusted, and memory never
grants permissions or overrides local policy. Existing chat can be backfilled
locally without sending messages or spending extra Kindroid history requests.
Private source text or embeddings must not be sent to a new remote service without
explicit operator authorization.

The proposed garden and identity-governance ADRs
[001](ADR-001-hannah-owns-identity.md),
[002](ADR-002-layered-garden-self-model.md),
[003](ADR-003-versioned-self-change.md), and
[004](ADR-004-separate-consent-and-authority.md) were left proposed. Their canonical
self-model applies only to that separate future scope, not to agent context memory.

## Consequences

### Development sequencing amendment — 2026-09-06

Marc subsequently requested a checkpoint commit followed by implementation of the
next milestone. We allowed local, fixture-tested M6A development before REL-001's
remaining manual acceptance. Alternatives were blocking all implementation until
live acceptance, or incorrectly treating mock tests as release acceptance. This
exception permits neither release sign-off nor live extraction. Automatic capture
and context injection default off; no existing chat is indexed merely by upgrading.
REL-001 and MEM-010's operator acceptance remain outstanding. The cost is carrying
an unreleased feature alongside the remaining dashboard acceptance work.

For the first local implementation we chose deterministic exact-passage candidate
selection rather than an additional model-based extraction turn. This bounds cost,
avoids another private-data processing boundary, and leaves original attribution
inspectable, but misses implicit/non-English observations and richer relationships.
The alternative of model-based extraction remains a possible evaluated upgrade,
not an assumed capability of this implementation. Entity identity resolution stays
explicitly reviewed. We selected optional local SentenceTransformer embeddings
with versioned caches and lexical fallback; no embedding model was installed or
validated on private conversation data. See [implementation and limits](../agent-memory.md).

Marc then requested semantic extraction so memory formation would not require
manual selection. [ADR-010](ADR-010-semantic-memory-extraction.md) superseded the
cue-only default with isolated, validated Codex extraction. The local cue backend
remains optional; the identity/authority and live-enablement boundaries above
remain unchanged.

- (+) Codex can retrieve older context and explain its sources without taking
  ownership of Hannah's memory or self-description.
- (+) The same foundation supports exploration, avoiding repeated offers, and
  following up on unfinished questions.
- (+) Existing SQLite backup, migration, worker ownership, and local dashboard
  boundaries remain reusable; no separate graph service is required initially.
- (-) Extraction, entity resolution, stale assumptions, and excessive graph
  expansion introduce new failure modes requiring dedicated evaluation.
- (-) Semantic indexes add model/version and privacy considerations; lexical
  fallback and safe degraded operation are required.
- (neutral) Remembered commitments or permission-like statements are contextual
  evidence, never a replacement for the authoritative local approval mechanism.
- (neutral) This records a planned milestone; no memory extraction, backfill,
  schema migration, or Kindroid mutation occurred when this ADR was added.
