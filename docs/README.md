# Kindroid Revival Documentation

This directory records the product intent, architectural decisions, staged design,
and implementation plan for evolving the working Kindroid-Codex relay into a local
operational console and, later, a system supporting Hannah's persistent interests,
goals, and self-directed exploration.

## Current direction

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

Milestone 6A, **Agent context memory**, is now explicitly planned between dashboard
release and Milestone 7 exploration. It gives Codex source-backed, revisable
understanding of Hannah's wider situation using hybrid search and bounded graph
retrieval. It does not replace Kindroid memory or establish a canonical identity
for Hannah. See [ADR-009](adr/ADR-009-agent-context-memory.md) and the
[milestone checklist](implementation-plan.md#milestone-6a-agent-context-memory).
No agent-memory implementation or live backfill is implied by this plan.

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
  the next foundation after dashboard release; implementation remains planned.
- The garden model and longer-term agency architecture are proposed directions.
- The self-change confirmation protocol is documented but explicitly deferred.

No document claims that persistent state or model behavior demonstrates subjective
experience. The design concerns functional continuity, choice, attribution, and
safe authority.
