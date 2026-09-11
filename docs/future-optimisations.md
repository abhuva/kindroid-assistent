# Future optimisations

Deferred investigation and improvement candidates, not accepted runtime changes
or completed implementation work.

## Delayed Kindroid replies and empty-response recovery

**Recorded:** 2026-09-11. **Status:** Deferred at Marc's request.

### Observation and current behaviour

Marc reports that replies can take 1–3 minutes to appear under server load and
that frequent `Kindroid returned no reply text` warnings consume history allowance.

The relay calls `/groupchats-ai-response` with `stream: false` and waits for the
complete HTTP response. Non-empty text is captured directly; this is not a missing
`await`. The HTTP client has a 180-second read timeout and a 15-second connection
timeout. The read timeout limits waiting for incoming data, not total generation
duration.

The quoted warning specifically means a completed, successful HTTP request
returned empty/whitespace-only text. A transport timeout follows a different
failure path. After an empty response the relay attempts one budgeted history
synchronization, including pagination where needed, subject to spacing/cooldowns.
It never repeats generation to retrieve the reply. See
[ADR-008](adr/ADR-008-direct-replies-and-context-sync.md).

Once an HTTP response ends, waiting longer cannot receive more text on that
response. A message appearing later in the app does not prove it was delivered
through the relay's original request. Provider-side generation/storage may outlive
that request, but the reason for these empty responses is not established.
Current activity does not provide enough timing/response metadata to diagnose it.

### Candidate work, in investigation order

- [ ] Add privacy-safe generation diagnostics: elapsed time, HTTP status, response
  byte count, and allow-listed transport/timeout category. Do not record message
  bodies, credentials, raw headers, or raw exception output.
- [ ] Use the evidence to distinguish early empty responses from slow requests
  and transport failures. Compare normal requests without adding history polling.
- [ ] Evaluate a configurable longer read timeout, for example five minutes.
  This can help genuine timeouts, but cannot fix a completed empty response.
- [ ] Evaluate durable deferred history recovery when generation may still be
  running, using measured timing to choose a grace period. Avoid blocking the
  shared worker with a sleep or spending a lookup before the reply exists.
  Delay alone does not eliminate the history read needed for an empty response.
- [ ] Evaluate the documented `stream: true` option in an explicitly approved
  disposable-chat test. Streaming may help response delivery, but is not a proven
  fix and does not supply remote message IDs or remove normal context syncs.

### Guardrails and future verification

Preserve worker-owned external effects, durable generation intent, room/speaker
isolation, conservative reconciliation, history budgets and cooldowns. Never
automatically retry uncertain generation or delivery, accept a partial stream as
a complete reply, or bypass ambiguity checks. Any deferred recovery must survive
restart and respect pause and room scheduling rules.

Use mocks first for delayed complete replies, empty success, timeout, partial
stream failure, missing/ambiguous history, budget exhaustion, restart, and
cross-room isolation. Live testing needs Marc's explicit approval and a disposable
chat, with the exact external effects stated first. No live calls or runtime
changes were made for this investigation.

References: [Kindroid API documentation](https://kindroid.ai/v2/docs/api-documentation/)
and [HTTPX timeout semantics](https://www.python-httpx.org/advanced/timeouts/),
checked 2026-09-11. Recheck provider behaviour before implementation.
