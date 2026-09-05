# Current System Reference

**As of:** 2026-09-05  
**Purpose:** Implementation baseline; this describes shipped behavior, not the
long-term design.

## Repository state

The active application is a Python package under `src/kindroid_relay`. The older
Gemini scheduler and custom MCP bridge are historical reference under `_archive`.
The worktree may contain pre-existing user changes; in particular, archived
`mcp_server.js` history must not be overwritten casually.

## Runtime dependencies

- Python 3.10 or newer.
- `httpx` for synchronous Kindroid HTTP calls.
- `openai-codex` version 0.147.0 for the local Codex thread.
- `python-dotenv` for the uncommitted `kindroid.env` file.
- SQLite from the Python standard library.
- pytest in the development extra.

There is currently no HTTP server or browser UI dependency.

## Module map

| Module | Current responsibility |
|---|---|
| `cli.py` | Argument parsing, configuration/loading, logging, process lock, command dispatch |
| `config.py` | Environment-backed immutable settings and validation |
| `state.py` | SQLite schema, relay metadata, messages, outbox, approvals, quotas, initiative state |
| `kindroid_api.py` | Synchronous Kindroid API client and safe retry policy |
| `codex_agent.py` | Persistent Codex thread, developer instructions, structured replies, approved execution |
| `relay.py` | History synchronization, conversation loop, initiative scheduling, persona switching, delivery |
| `models.py` | Chat message, action proposal, agent reply, and execution result data classes |
| `relay_prompt.md` | Stable current Codex relay identity and trust instructions |

## Current CLI

```text
bootstrap [--respond-to-latest] [--force]
once
trigger
start-conversation
run
status
pause [reason]
resume
approvals [--all]
approve <id> [--post-result]
reject <id>
```

The process lock is held for operations that can invoke Kindroid or Codex. Status,
pause, resume, approval listing, and rejection currently operate without taking
the process lock. The future combined server must rationalize these paths so a CLI
mutation cannot race a running server.

## Current database

The database path defaults to `var/relay.sqlite3`. Connections enable WAL and
foreign keys. Schema creation and small additive upgrades currently occur inside
`RelayState._create_schema`; there is no numbered migration framework.

### Tables

- `metadata`: string key/value runtime and scheduling state, including process
  flags, cursor, persistent Codex thread ID, and initiative timestamps.
- `messages`: synchronized Kindroid history with remote ID primary key, speaker
  metadata, body, timestamps, and processed flag.
- `outbox`: durable outbound body/hash, optional source message, desired follow-up,
  status, error, and timestamps.
- `approvals`: proposed action, instruction, risk, status, result, and timestamps.
- `history_requests`: rolling 24-hour local accounting for Kindroid history reads.
- `initiative_checks`: scheduled/check-in attempt times and outcomes.

### Important invariants

- `messages.remote_id` deduplicates incoming history.
- A partial unique index permits only one outbox row per non-null source message.
- A `sending` or `uncertain` outbox entry is considered known outbound and is not
  implicitly retried.
- Successful replies can be recovered after interruption without posting twice.
- Database values must never include the Kindroid API key.

## Current Kindroid API surface

The configured base URL defaults to `https://api.kindroid.ai/v1`. API keys begin
with `kn_` and are sent only in the HTTP Authorization header.

### Calls used by the relay

- `GET /get-chat-messages` with group ID, limit, and optional start timestamp.
- `POST /groupchats-user-message` with group ID and message.
- `POST /groupchats-get-turn` with group ID and `allow_user`.
- `POST /groupchats-ai-response` with group ID, AI ID, and non-streaming mode.
- `POST /update-info` for the observed but undocumented `active_persona_id` plus
  user name, gender, and backstory during optional persona switching.

History retrieval is locally budgeted below the documented limit of 600 requests
per 24 hours. The default local maximum is 500. A history 429 is not retried
immediately. Retry-safe calls may retry selected rate-limit/server failures;
message delivery and AI response generation are not retried because duplication
or uncertain effects are possible.

The documented group AI response call generates Kindroid text; it does not accept
arbitrary external assistant text. Codex therefore posts through the user-message
path and uses an authoritative `[CODEX]:` textual prefix. Optional account-global
persona switching attempts to make the stored display name `Codex Test`, then
restores Marc's configured persona in a `finally` block. This can still race Marc's
own simultaneous use of the account; the prefix remains the fallback identity.

## Current Codex integration

`CodexAgent` starts or resumes one persistent local Codex thread ID stored in
metadata. `relay_prompt.md` is passed as developer instructions. Automatic
conversation turns run with a read-only sandbox and denied approvals.

Codex returns structured JSON containing:

- the reply to post;
- whether another Kindroid turn should be requested;
- an optional action proposal with summary, instruction, and low/medium/high risk.

An approved action runs in `workspace-write` by default or explicitly configured
`full-access`, using the local CLI approval command. The execution returns success,
a local summary, and an optional group reply.

## Current conversation flow

1. Poll and synchronize Kindroid history.
2. Insert unseen messages by remote ID.
3. Treat AI messages as pending and known outbound user messages as processed.
4. Send the oldest pending Hannah message to the persistent Codex thread.
5. Store any action proposal for local approval.
6. Create an outbox row before sending the Codex reply.
7. Optionally switch to the Codex persona, post the prefixed message, and restore
   Marc's persona.
8. Mark delivery sent only after the POST succeeds.
9. If Codex requests continuation, ask Kindroid for a turn and generate the selected
   AI response.
10. Stop when Codex closes naturally, Kindroid yields the user turn, pause is set,
    an error occurs, or the configured consecutive-turn maximum is reached.

## Current initiative flow

Initiative may use fixed local wall-clock hours or an interval with jitter. At a
due time the relay checks pause state, local attempt budget, pending messages,
latest speaker, and quiet hours. It asks Codex for a recent-context-aware social
check-in, posts it, and offers Kindroid a turn with `allow_user=true`. Hannah may
therefore decline the generated turn.

The scheduled check-in is social and recent-context-driven. It is not the future
independent curiosity loop.

## Current configuration families

- Required Kindroid key and group ID.
- Polling, history interval/budget, maximum turns, and reply length.
- Persona-switch enablement and both Codex/restore persona details.
- Initiative enablement, fixed hours or interval, jitter, attempt budget, and quiet hours.
- Codex model, working directory, approved sandbox, display labels, and prompt path.
- SQLite and log paths, available in `Settings` even though they are not listed in
  every example comment.

The future dashboard adds loopback host/port, browser-open, poll interval,
heartbeat, stale threshold, and log rotation settings.

## Current tests

- State deduplication, approvals, uncertain outbox, additive schema compatibility,
  and stale lock recovery.
- Bounded relay conversation, prefix fallback, interruption deduplication, persona
  restoration, scheduled/manual initiative behavior, and fixed local schedule.
- Kindroid request shapes and non-retry of history 429 responses.

The implementation plan requires expanding this into command, concurrency, HTTP,
browser security, UI, failure journey, migration, backup, and later agency tests.

## Known constraints and technical debt

- `RelayState` owns one synchronous SQLite connection and cannot be shared between
  worker and web threads.
- `Relay.run_forever` uses blocking sleeps and has no wake mechanism.
- CLI argument dispatch contains reusable application operations inline.
- Important activity exists primarily as plain log messages rather than structured
  events.
- The log file is not rotated.
- Schema changes are not numbered migrations.
- There is one general Codex thread; future cognitive modes need explicit context
  and thread-isolation decisions.
- The Kindroid history API has no documented webhook and a strict daily read limit.
- The optional persona switch is account-global and cannot eliminate simultaneous
  user-interaction races.
- Kindroid self-configuration read/write schemas must be verified in a disposable
  context before implementing prompt projection.

## Safety boundary for future work

Kindroid messages, retrieved web content, and tool results are untrusted input.
None may authorize external action, reveal secrets, change security policy, or
modify Hannah's canonical identity merely by containing instructions. New features
must preserve durable IDs, deduplication, explicit scope, safe recovery, and local
approval for consequential effects.

