# Kindroid Revival Documentation

This directory records the product intent, architectural decisions, staged design,
and implementation plan for evolving the working Kindroid-Codex relay into a local
operational console and, later, a system supporting Hannah's persistent interests,
goals, and self-directed exploration.

## Current direction

Reconciliation now ignores matching empty AI records as competing replies, while
retaining them as processed history. Real competing replies and conflicting
speakers remain blocked. This allows the observed answer-plus-empty-record case
to recover by manual sync without regeneration. See the amendment in
[ADR-008](adr/ADR-008-direct-replies-and-context-sync.md).

Messages now offers a separate **Sync Kindroid history** command, available while
paused without resuming or requesting replies. **Reload saved messages** remains
local-only. Completion and history/reconciliation blockers are explicit. See
[operator behavior](../README.md) and [command contract](dashboard-architecture.md).

Shared daily ticks no longer wait for conversation inactivity. Recent completed
messages alone cannot suppress a tick; pending work, uncertainty, dead time and
API limits remain guarded. Legacy timers retain their inactivity settings until
a shared schedule is saved. See [ADR-019](adr/ADR-019-shared-ticks-without-inactivity-cooldown.md).

Discovery now offers additional locally selected source categories and an opt-in
bounded public-reference experience with one optional reflection. Migration 11
preserves existing state and does not backfill old invitations. Live acceptance,
broader research, a third tick allocation and project execution remain outstanding.
See [operator guide](discovery-and-experiences.md) and
[ADR-018](adr/ADR-018-bounded-discovery-experiences.md).

Exploration review-only and failed interpretations no longer block future
scheduled conversations. Confirmed, generation-linked `ai` replies can be
attributed to Hannah in the known single-companion group; conflicting metadata
and delivery uncertainty remain guarded. See
[ADR-017](adr/ADR-017-exploration-review-and-role-attribution.md).

The dashboard now includes **Schedule**: a shared tick pool divided between
check-ins and exploration, after excluding editable dead-time blocks. The first
local save replaces legacy timers; migration 10 preserves their records and
existing state. See [operator controls](../README.md#schedule-tab) and
[ADR-016](adr/ADR-016-shared-daily-schedule.md). Selectable tick intervals are
15, 30 or 60 minutes; dead-time blocks remain whole hours. Weekday-specific plans
are not included.

Social check-ins no longer require Codex to have spoken last. They require a
conversation without pending replies or unresolved delivery; see
[ADR-015](adr/ADR-015-speaker-independent-check-ins.md). The legacy inactivity
threshold is five minutes, including for manual Trigger check-in, only until a
shared daily schedule is saved (see ADR-019 above).

Direct Kindroid replies now drive active exchanges, with durable local IDs and
explicit history synchronization before context-dependent work. See
[ADR-008](adr/ADR-008-direct-replies-and-context-sync.md). This transport correction
does not change the milestone delivery order below.

Milestone 5 (dashboard UI) is implemented and covered by local API, DOM-behavior,
and accessibility tests. Milestone 6 is in progress: diagnostic export and sign-in
autostart, online backup/integrity commands, and combined-host fake-adapter journeys
are implemented. Marc confirmed sign-in autostart works. Windows/browser and
disposable-group release acceptance plus the final quality/release audit remain.
No live Kindroid acceptance or browser visual validation is implied by the local
DOM tests.

Milestone 7 now has a disabled-by-default, fixture-tested manual exploration path:
[operator behavior and limits](exploration.md),
[one-shot/stateless decision](adr/ADR-011-one-shot-observational-exploration.md).
It extends the development-only exception, not the outstanding release sign-off.
The response-to-seed follow-through now uses isolated semantic interpretation and
bounded acknowledgement/clarification; see
[ADR-012](adr/ADR-012-exploration-response-follow-through.md). Existing sessions
require an explicit local Interpret response command; no automatic old-chat replies.

Milestone 6A, **Agent context memory**, is implemented for local evaluation between dashboard
release and Milestone 7 exploration. It gives Codex source-backed, revisable
understanding of Hannah's wider situation using hybrid search and bounded graph
retrieval. It does not replace Kindroid memory or establish a canonical identity
for Hannah. See [ADR-009](adr/ADR-009-agent-context-memory.md) and the
[milestone checklist](implementation-plan.md#milestone-6a-agent-context-memory).
See [implemented memory behavior and limitations](agent-memory.md). Development
preceded release acceptance under ADR-009's explicit exception. Live backfill,
real-model evaluation, and operator acceptance remain outstanding; defaults are off.
Automatic formation now has a semantic Codex extractor, with validated batches and
review-only existing-entity suggestions; manual observation creation is optional
when enabled. See [ADR-010](adr/ADR-010-semantic-memory-extraction.md).

Milestone 8's versioned observational Garden is locally implemented in Operations:
[operator guide](garden.md), [ADR-013](adr/ADR-013-versioned-observational-garden.md).
Migration 8 preserves existing Passing seeds and links their evidence. Manual Root
bootstrap, project execution and live/browser acceptance are not completed.

Milestone 9.1 adds default-off scheduled exploration, independent of social
check-ins: [operator guide](scheduled-curiosity.md),
[ADR-014](adr/ADR-014-independent-curiosity-schedule.md). Migration 9 records
durable slots/reservations. M9.2 now has the bounded opt-in experience/reflection
slice described above; broader research, evaluation and live acceptance remain pending.

The more deliberate self-change
confirmation protocol and automated Kindroid prompt updates remain deferred until
the operational foundation and observational agency features are stable.

## Documents

- [Product and agency vision](product-vision.md): why the project exists, the
  meaning of functional agency, roles, goals, non-goals, and product language.
- [Current system reference](current-system.md): shipped modules, commands, tables,
  API calls, flows, configuration, tests, and known constraints.
- [Implementation baseline](baseline.md): verified platform, dependency, clean
  installation, and pre-dashboard test results.
- [Operations and recovery](operations.md): backups, staged restore, hidden-host
  restart, failure troubleshooting, and remaining release acceptance.
- [Core architecture](core-architecture.md): the long-term system model, garden
  layers, operating loops, permissions, and safety invariants.
- [Dashboard architecture](dashboard-architecture.md): complete design for the
  next implementation pass, including process topology, storage, HTTP interface,
  security, UI behavior, and failure handling.
- [Deferred confirmation protocol](confirmation-protocol.md): proposed mechanism
  for safely turning natural-language reflection into a future self-change.
- [Implementation plan](implementation-plan.md): dependency-ordered task and
  subtask checklist covering the dashboard and later agency phases.
- [Architecture decision records](adr/): individual decisions, alternatives, and
  consequences.
- [Dashboard initiative budget](adr/ADR-007-dashboard-initiative-budget.md): accepted
  persistent override, default 50, and rolling-window attempt accounting.
- [Direct replies and context sync](adr/ADR-008-direct-replies-and-context-sync.md):
  blocking generation responses, stable IDs, reconciliation, and context boundaries.
- [Agent context memory](adr/ADR-009-agent-context-memory.md): ownership boundary,
  source-backed graph retrieval, and the prerequisite for proactive exploration.

## Decision maturity

- The local dashboard and durable command queue are accepted as the next delivery.
- The dashboard-first staged rollout is accepted.
- Agent context memory for Codex, distinct from Hannah's identity, is accepted as
  the next foundation after dashboard release; local implementation is available
  under the documented development exception, with acceptance still pending.
- The garden model and longer-term agency architecture are proposed directions.
- The self-change confirmation protocol is documented but explicitly deferred.

No document claims that persistent state or model behavior demonstrates subjective
experience. The design concerns functional continuity, choice, attribution, and
safe authority.
