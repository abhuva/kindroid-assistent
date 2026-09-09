# ADR-010: Extract Agent Memory Semantically in an Isolated Codex Turn

**Status:** Accepted | **Date:** 2026-09-06
**Participants:** Marc, Codex

## Context and alternatives

ADR-009's initial implementation provided source-backed storage/retrieval but
selected automatic observations using English keyword cues. Marc clarified that
he should not have to create Codex's memory manually and requested semantic
extraction of meaningful statements, entities, and connections.

We considered retaining cue selection (cheap but incomplete), adding observations
to ordinary reply output (less overhead but couples failures and misses backfill),
using a new model service (another credential/privacy boundary), and a dedicated
extraction turn using the existing Codex runtime and configured model.

## Decision

We chose a dedicated ephemeral Codex extraction turn for up to three captured
messages, with bounded nearby context and possible existing-entity hints. We
retained the existing Codex service/model configuration, not a new provider.
The extractor does not resume or update the conversation thread, request Kindroid
history, send replies, or execute proposals. It starts in an empty temporary
directory, uses read-only/deny-all approval settings, and disables configured MCP
servers/plugins/apps, shell/web tools, hooks and Codex's unrelated global memories.
Configuration is read only to derive disable overrides; it is never put in a
prompt or diagnostic output. These controls follow the pinned SDK and
[official Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

We made semantic extraction the backend selected when capture is explicitly
enabled. Both memory enablement and automatic capture still default off. The old
cue extractor remains an explicit `local` option, not a silent failure fallback.
Semantic capture sends bounded captured text to the existing Codex service and
consumes its usage allowance. This implementation does not itself enable live use.

The host validates the complete result, including exact unique quotations,
target IDs, field/relationship allowlists and entity suggestion scope. It commits
the batch atomically with progress; attempts are charged before model I/O. Invalid,
timed-out or interrupted batches fail without chat replay or automatic retry.
The SDK deadline closes only the extraction-owned child process, not the live
conversation agent. This is a local SDK timeout plus cleanup, not an OpenAI
server-side cancellation guarantee.

We kept quotations, interpretations, uncertainty, model/version metadata and
explicit/inferred connection basis separate. Same batch-local keys can connect
observations, but old entities are suggestions for explicit review, never
similarity-based identity merges. Model output cannot retract earlier claims,
adopt Roots, grant consent or change approval policy. Migration 5 adds only
metadata to existing memory tables and preserves all earlier evidence/revisions.

## Consequences

- (+) Important observations can be formed without manual passage selection,
  including implicit meanings, named people/places and non-English statements.
- (+) Extraction failure stays separate from conversation delivery and approvals.
- (+) Existing storage, graph retrieval, inspection and correction flows are reused.
- (-) Extra Codex calls add latency/usage; bounded idle scheduling and attempt
  budgets are required. Model interpretation can still be wrong.
- (-) Cross-batch ambiguous identities require review; this is not fully automatic
  entity resolution. Input truncation and limited nearby context remain visible
  limitations, not evidence of complete understanding.
- (neutral) Tests use fixture/model/SDK doubles. Real-model quality, runtime
  integration and live operator acceptance remain unverified, not inferred from
  schema validation. ADR-009's development-only release exception still applies.
