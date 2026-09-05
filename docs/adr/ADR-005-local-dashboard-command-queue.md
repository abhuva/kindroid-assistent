# ADR-005: Host a Local Dashboard with a Durable Command Queue

**Status:** Accepted | **Date:** 2026-09-05  
**Participants:** Marc, Codex

## Context

The relay currently runs and is operated through terminal commands. The project
needs a browser dashboard for status, logs, schedules, conversations, and approval
controls. Relay operations include account-global persona switching, durable
outbound delivery, and a persistent Codex thread, so concurrent calls from HTTP
request handlers would introduce races.

Alternatives considered were:

1. Run an independent web process that reads SQLite and invokes CLI subprocesses.
2. Convert the whole relay to asynchronous HTTP and database code.
3. Host the web server and synchronous relay worker in one process, with durable
   commands as their boundary.

## Decision

We decided to add `kindroid-relay serve`, which hosts a loopback-only web server and
one relay worker in the same process. HTTP handlers use separate SQLite connections
for reads and command insertion. A single worker serially performs all Kindroid,
Codex, persona, delivery, and approved execution side effects.

The browser polls initially. Commands and structured activity are durable. A wake
event allows queued commands to interrupt the worker's outer polling wait.

## Consequences

- (+) The dashboard remains responsive while relay work is running.
- (+) Existing synchronous Kindroid and Codex code can be retained.
- (+) Durable command status survives browser reloads and process restarts.
- (+) One worker preserves outbound ordering and existing safety guarantees.
- (-) A process failure takes down both the worker and dashboard.
- (-) The code must enforce connection and object ownership across threads.
- (-) Synchronous inner waits can delay queued commands until a safe boundary.
- (neutral) SQLite remains appropriate for this single-user local workload.

