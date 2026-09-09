# ADR-012: Link Exploration Responses to Seeds Before Acknowledging Them

**Status:** Accepted | **Date:** 2026-09-06
**Participants:** Marc, Codex

## Context and alternatives

Marc tested an invitation and confirmed that candidates were visible. Hannah's
reply was captured, but it neither updated the candidate trail nor received an
acknowledgement. Marc explicitly requested response interpretation, source-backed
choice/alternative links, and bounded follow-through.

We considered leaving replies as generic observations, adding keyword matching
(cheap but brittle for negation, alternatives, and multilingual replies), handing
them to the ordinary conversation agent (would reopen its tool/reply loop), or
using an isolated structured interpreter followed by host-controlled messages.

## Decision

We chose the isolated interpreter, reusing ADR-010's Codex runtime controls and
configured model. The runner accepts a purpose-specific instruction/schema pair;
read-only/deny-all settings, disabled tools, an ephemeral thread, empty working
directory, and the existing timeout remain unchanged. Structured output is a
proposal validated by the host, not a database command. This follows the
[official app-server structured-output interface](https://learn.chatgpt.com/docs/app-server#start-a-turn).

The interpreter classifies the supplied response as choose, decline, defer,
alternative, or unclear. It must cite an exact unique quotation and only candidate
IDs actually offered in that session. Alternatives get a bounded descriptive title,
separate from the quoted evidence. A synchronized, concrete matching speaker is
required before interpretation; ambiguous attribution stays review-only. Oversized
responses are held for review rather than classified from a truncated prefix.

We committed the interpretation, evidence, and candidate links atomically before
acknowledging them. Codex-origin suggestions keep that origin; a new alternative
is a Hannah-origin provisional Passing candidate with an explicitly identified
Codex interpretation. None is a canonical preference, Growing item, Root, project,
permission, or remote prompt change. Existing memory corrections remain available.

We allowed one acknowledgement for clear choices/alternatives, with a proposed
small next step but no research execution. Decline/defer ends without pressure.
Ambiguity permits one optional clarification turn, then at most one acknowledgement.
A session can use no more than two interpretations and two follow-up messages.
Clarification responses are consumed outside the ordinary tool-capable reply chain.

New manually started sessions enable this flow by default when exploration itself
is enabled. `RELAY_EXPLORATION_AUTO_INTERPRET=false` retains the initial one-shot
behavior. Existing sessions are never automatically backfilled or answered on an
upgrade: a local **Interpret response** command explicitly enables their flow.
That command does not resend the invitation. If captured conversation has moved
on, the choice is retained but a stale acknowledgement is withheld.

Migration 7 adds response-attempt and candidate-response link tables without
replacing earlier records. Attempts are claimed before model I/O; failed or
interrupted interpretation does not automatically retry. Each follow-up outbox is
linked before sending. Sending/uncertain effects cannot replay; restart recovery
pauses where delivery is uncertain. Operator closure never clears transport
uncertainty or grants a new send attempt.

This explicitly extends [ADR-011](ADR-011-one-shot-observational-exploration.md)'s
one-shot-only decision. It does not complete release acceptance, authorize live
tests, introduce research tools, or implement the deferred identity ceremony.

## Consequences

**Experience extension (2026-09-08):** [ADR-018](ADR-018-bounded-discovery-experiences.md)
allows one separately opted-in public reference experience after interpretation
and acknowledgement. Old sessions are not backfilled and uncertain effects cannot replay.

**Review/attribution amendment (2026-09-07):** [ADR-017](ADR-017-exploration-review-and-role-attribution.md)
permits confirmed, generation-linked role-only AI attribution in the known
single-companion group. Review-only/failed responses no longer block future
scheduled activity, but are not automatically retried or closed.

**Scheduling amendment (2026-09-06):** [ADR-014](ADR-014-independent-curiosity-schedule.md)
allows locally opted-in scheduled sessions to use this bounded flow with system
provenance, call reservations, disablement and quiet-hour gates.

- (+) Hannah's response affects the visible seed trail and can receive closure.
- (+) Original suggestion provenance and exact response evidence remain distinct.
- (+) Durable attempts prevent repeated acknowledgements and clarification pressure.
- (-) Interpretation adds Codex usage and latency and may still be semantically wrong.
- (-) Strict speaker/source gates can require review despite an apparently clear reply.
- (-) Failed interpretation is not automatically retried; source evidence remains inspectable.
- (neutral) A proposed overview/comparison is an offer only; actual exploration work,
  research, and permanent garden promotion remain future stages.
