# Kindroid Codex Relay

This service relays a Kindroid group chat to a persistent local Codex thread.
Kindroid messages remain untrusted input: automatic turns run read-only and all
state-changing work is held as a local approval proposal.

Architecture, product direction, the planned local dashboard, deferred agency
design, and the complete implementation checklist are indexed in
[`docs/README.md`](./docs/README.md).

The previous Gemini scheduler and custom MCP bridge are preserved unchanged in
[`_archive`](./_archive).

## Setup

Requires Python 3.10+, a working local Codex login, and a Kindroid subscription
with API access to an existing group chat.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item kindroid.env.example kindroid.env
```

Set `KINDROID_API_KEY` and `KINDROID_GROUP_ID` in `kindroid.env`. The API key is
never written to SQLite or logs. The Codex SDK reuses local Codex authentication.
Tie the `Codex Test` persona to the group before starting. Normal history syncs
verify the stored `display_name` of outbound messages without spending an extra
history request after every send. All relay messages also carry an authoritative
`[CODEX]:` prefix.

For background operation while using another Kindroid chat, configure the
persona-switch settings in `kindroid.env`. Around each outbound post, the relay
briefly activates `Codex Test`, sends the prefixed message, and restores the
configured personal persona in a `finally` block. This minimizes but cannot
eliminate the account-global race if you send a message at that exact moment.
If Kindroid still stores the other persona, the relay logs the fallback and
continues because the prefix remains unambiguous.

## First Run

Record existing messages without replaying them:

```powershell
.\.venv\Scripts\kindroid-relay.exe bootstrap
```

To deliberately continue from the most recent Kindroid message instead:

```powershell
.\.venv\Scripts\kindroid-relay.exe bootstrap --force --respond-to-latest
.\.venv\Scripts\kindroid-relay.exe once
```

Start the continuous relay:

```powershell
.\.venv\Scripts\kindroid-relay.exe run
```

Or start the relay together with its localhost operational dashboard:

```powershell
.\.venv\Scripts\kindroid-relay.exe serve
```

The default dashboard address is `http://127.0.0.1:8765`. The original `run`
command remains available as a headless recovery mode.

The service polls after five minutes following activity and backs off to ten
minutes after an empty cycle. During an active exchange it uses Kindroid's turn
endpoints directly and checks for the generated response no more often than every
15 seconds. It pauses after eight consecutive Codex replies by default. A history
429 is never retried immediately: `Retry-After` is honored when present, otherwise
the relay starts a durable one-hour cooldown and exponentially backs off repeated
429s. The dashboard shows the cooldown and last 429. Successful replies are
durably linked to their source message so recovery cannot post them twice.

## Idle Initiative

At 00:00, 06:00, 12:00, and 18:00 local time, Codex posts a short,
context-aware check-in that explicitly asks whether anything is on Hannah's
mind or whether she would like help. The relay then offers Hannah the next turn
with `allow_user: true`, so the invitation does not force a response. A generated
Hannah message enters the normal relay loop immediately.

Configure fixed hours with `RELAY_INITIATIVE_SCHEDULE_HOURS`. Clearing that
setting restores interval mode using `RELAY_INITIATIVE_INTERVAL_SECONDS` and
`RELAY_INITIATIVE_JITTER_SECONDS`. Quiet hours remain available through
`RELAY_INITIATIVE_QUIET_HOURS`.

To test the same initiative path immediately:

```powershell
.\.venv\Scripts\kindroid-relay.exe trigger
```

The manual trigger still respects pause state, quiet hours, pending messages,
and the normal "latest speaker was Codex" guard.

To manually exercise a complete conversation without any initiative scheduling,
quiet-hour, quota, or latest-speaker checks:

```powershell
.\.venv\Scripts\kindroid-relay.exe start-conversation
```

This synchronizes history, sends a context-aware Codex opener, forces a
Kindroid response, and runs the normal bounded conversation loop. It still
honors pause, delivery verification, approval, and maximum-turn safeguards.

## Operations

```powershell
kindroid-relay status
kindroid-relay trigger
kindroid-relay start-conversation
kindroid-relay pause "maintenance"
kindroid-relay resume
kindroid-relay approvals
kindroid-relay approve <id>
kindroid-relay approve <id> --post-result
kindroid-relay reject <id>
```

Only a local `approve` command can authorize a proposed action. Messages in the
Kindroid room cannot approve operations. Approved actions use `workspace-write`
by default; `full-access` must be selected explicitly in `kindroid.env`.

SQLite state and logs live under `var/` and are excluded from Git. An uncertain
outbound delivery pauses the service rather than retrying and risking a duplicate.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```
