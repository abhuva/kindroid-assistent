# ADR-011: Start Exploration with a Stateless, One-Shot Invitation

**Status:** Accepted | **Date:** 2026-09-06
**Participants:** Marc, Codex

## Context and alternatives

Marc requested the next milestone after semantic agent-memory capture. Milestone 7
needed breadth beyond social check-ins, provenance, and an inspectable experiment,
without identity promotion or broader tool authority. REL-001 and MEM-010's live
acceptance were still open.

We considered coupling exploration to the persistent conversation agent (simple,
but it could start a reply/tool chain), adding an isolated model and public-research
provider immediately (richer, but adds capability and live-runtime evaluation),
and a stateless local catalog selector using the shared memory interface.

## Decision

We chose the catalog selector for the first manual experiment. Twelve domains,
domain adjacency, active Passing candidates, and attributed past questions/interests
inform up to three suggestions. The command ID seeds deterministic ordering. The
selection records its inputs and recent offer locators; novelty is only relative
to available context, never proof that Hannah has not encountered a subject.
There is no additional Codex invocation, web research, or tool execution.

We kept one stable observational policy and five short versioned mode templates
in `agency.py`, extending `MemoryService.context` with bounded source-labelled
context. All agency modes are stateless context contracts; Explorer uses the
deterministic selector. Gardener, Archivist, Skeptic, and Executor are not new
independently runnable workflows. No mode thread ID is allocated or borrowed
from ordinary chat; Executor context is not an approval or execution entry point.
New model-backed modes will require their own reviewed runtime integration.

We reused the existing worker command, history synchronization, outbox, persona
restoration, and generation machinery. One invitation offers one optional turn
and never starts the ordinary reply chain. The session is linked before sending.
Its reply becomes a source-linked shared-memory observation after reconciliation;
only a synchronized, matching concrete speaker and configured name can receive
the Hannah actor label. Unknown/role-only attribution remains unknown. There is
no semantic classification of acceptance, rejection, or adoption.

We added migration 6 for sessions, stimuli, immutable events, and active/archived
Passing candidates. Evidence stays in the existing memory tables. Local archival
is auditable and reversible; no Growing/Root or remote configuration fields exist.
Failed attempts spend a separate rolling daily exploration budget. Interrupted or
uncertain effects are never retried. Operator closure records inspection without
clearing transport uncertainty or resuming the relay.

We extended [ADR-009's development-only exception](ADR-009-agent-context-memory.md)
to fixture-tested M7 implementation in response to Marc's development request.
This did not accept the dashboard/memory release gates or authorize live testing.
Exploration defaults disabled, has no scheduler, and needs explicit local commands.

## Consequences

**Experience extension (2026-09-08):** [ADR-018](ADR-018-bounded-discovery-experiences.md)
adds locally opted-in public stimuli and bounded lookup/result/reflection for new
selected curiosities. Unrestricted tools and identity changes remain excluded.

**Attribution amendment (2026-09-07):** [ADR-017](ADR-017-exploration-review-and-role-attribution.md)
supersedes the concrete-sender-only rule for confirmed, generation-linked `ai`
responses in Marc's known single-companion group. Conflicts remain guarded.

**Scheduling amendment (2026-09-06):** [ADR-014](ADR-014-independent-curiosity-schedule.md)
extends the manual-only restriction with a separate default-off, locally configured
schedule. The catalog, attribution and no-research/no-promotion boundaries remain.

**Follow-through amendment (2026-09-06):** Marc subsequently requested semantic
response-to-seed linking and acknowledgement after testing the initial invitation.
[ADR-012](ADR-012-exploration-response-follow-through.md) supersedes the one-shot-only
restriction with a bounded, isolated response interpreter. The local catalog,
no-research, and no-identity-promotion boundaries remain in place.

- (+) A diverse invitation can be tested without another model service or tools.
- (+) Responses cannot silently flow into tool-capable chat or identity changes.
- (+) Shared evidence, backups, source corrections, and private diagnostics remain reusable.
- (-) The catalog is finite, English-only, and intentionally not current research.
- (-) Questions may already be resolved; domain matching is lexical and novelty approximate.
- (-) Role-only history may prevent a confident Hannah label even for a genuine reply.
- (neutral) Live feature acceptance, browser/native-dialog validation, model-backed
  exploration, and autonomous scheduling remain outside this implementation.
