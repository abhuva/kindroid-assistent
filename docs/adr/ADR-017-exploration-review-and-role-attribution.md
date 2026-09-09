# ADR-017: Keep Exploration Review Local to Its Response

**Status:** Accepted | **Date:** 2026-09-07\
**Participants:** Marc, Codex

## Context and alternatives

A scheduled check-in was skipped because an older exploration response needed
speaker review. Kindroid history returned `sender=ai`, even though the response
was reconciled to the requested generation and labelled Hannah. Marc confirmed
the deployed group contains Codex and Hannah, with Hannah its only AI companion,
and requested automatic attribution plus removal of the global review block.

We considered retaining mandatory concrete IDs (incompatible with this endpoint),
trusting any AI role or display label (would ignore conflicting participants),
and using the known single-companion setup with existing history linkage. We chose
the latter. We also rejected automatically closing/replaying old review items or
ignoring every unfinished response: those would discard review context or risk
overlapping and duplicate effects.

## Decision

We treated `needs_review` and failed interpretation as terminal, nonblocking
response states for both shared ticks and legacy scheduled curiosity. Records
remain inspectable and are not automatically retried or closed. Active response
work still blocks new scheduled activity, as do uncertain deliveries, pending
outboxes, unreconciled generation, interrupted sessions, pause and existing quotas.
Closing a session still cannot clear transport uncertainty.

Scheduled call reservations remain charged for the normal rolling 24-hour window.
Terminal reviews/failures no longer extend reservations indefinitely beyond it.
Skipped schedule activity summaries now include the recorded reason.

We used one attribution check for response observations, Hannah-origin events and
interpretation. A generic `ai` sender is accepted when its source is linked to a
reconciled generation for the expected AI, the display name matches, and confirmed
group history contains no conflicting AI names or concrete IDs. An unresolved
default `Kindroid` label is not a Hannah identity; the existing single-name history
resolver can resolve that default. Explicit configured names remain strict.
Concrete mismatching IDs, user messages, unlinked/unconfirmed sources and
`[CODEX]:` content cannot use the fallback. Raw sender metadata is preserved.

This is conversational attribution under Marc's stated topology, not general
multi-companion identity authentication. Adding another AI companion requires
reconsidering that assumption; observed conflicts disable role-only attribution.
No additional history requests, capability grants, permanent preferences or
external approvals were introduced. Old review records and closed sessions are
not replayed on upgrade. No schema migration or live database rewrite is needed.

This amends the concrete-sender-only portions of [ADR-011](ADR-011-one-shot-observational-exploration.md)
and [ADR-012](ADR-012-exploration-response-follow-through.md), and the review and
reservation gates of [ADR-014](ADR-014-independent-curiosity-schedule.md) and
[ADR-016](ADR-016-shared-daily-schedule.md).

## Consequences

- (+) Normal role-only Hannah responses can be recorded and acknowledged without
  operator review, including bounded clarification responses.
- (+) Review items no longer suspend unrelated future scheduled conversations.
- (+) Duplicate-delivery safeguards and the provenance trail remain intact.
- (-) Attribution relies on the known single-companion setup and available history;
  a display name alone is not independent identity proof.
- (neutral) Existing review items remain historical review items, and real delivery
  uncertainty still requires inspection. Live behavior requires operator acceptance.

## Verification

The focused exploration/scheduling run passed 195 tests in 17.82 seconds under
a 30-second hard limit. After adding three additional attribution-conflict cases,
all 473 Python tests passed across bounded regression batches and a UI rerun:
424 passed in 35.15 seconds; the complementary batch passed 48 tests but its
dashboard accessibility check hit the existing 20-second Node file timeout.
The isolated UI wrapper rerun passed both tests in 11.64 seconds. Separately,
`npm test` passed all 29 DOM/accessibility checks plus API-client checks.
No timeout or unrelated UI code was changed. `npm ci --ignore-scripts` reported
zero vulnerabilities; existing npm dependency and Starlette/httpx deprecations
remain. `git diff --check` passed.

Fixtures cover role-only history (including empty generation responses), default
name resolution, choices/alternatives/clarification, raw metadata preservation,
conflicting names/IDs, unlinked or unconfirmed sources, Codex prefixes, terminal
review scheduling, active follow-through, rolling reservations, and non-replayed
uncertain effects. A read-only check of the deployed group's stored history found
no conflicting AI attribution metadata. No live database writes, restart, Kindroid
requests or real model interpretation were performed; live acceptance remains open.
