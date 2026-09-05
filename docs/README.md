# Kindroid Revival Documentation

This directory records the product intent, architectural decisions, staged design,
and implementation plan for evolving the working Kindroid-Codex relay into a local
operational console and, later, a system supporting Hannah's persistent interests,
goals, and self-directed exploration.

## Current direction

The existing relay is a working transport and safety foundation. The next delivery
is a localhost-only dashboard for operating and observing that relay. The more
deliberate self-change confirmation protocol and automated Kindroid prompt updates
are intentionally deferred until the operational foundation and observational
agency features are stable.

## Documents

- [Product and agency vision](product-vision.md): why the project exists, the
  meaning of functional agency, roles, goals, non-goals, and product language.
- [Current system reference](current-system.md): shipped modules, commands, tables,
  API calls, flows, configuration, tests, and known constraints.
- [Implementation baseline](baseline.md): verified platform, dependency, clean
  installation, and pre-dashboard test results.
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

## Decision maturity

- The local dashboard and durable command queue are accepted as the next delivery.
- The dashboard-first staged rollout is accepted.
- The garden model and longer-term agency architecture are proposed directions.
- The self-change confirmation protocol is documented but explicitly deferred.

No document claims that persistent state or model behavior demonstrates subjective
experience. The design concerns functional continuity, choice, attribution, and
safe authority.
