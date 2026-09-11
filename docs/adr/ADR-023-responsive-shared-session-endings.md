# ADR-023: Responsive, silent shared-session ending checks

**Status:** Accepted | **Date:** 2026-09-11
**Participants:** Marc (approval), Codex (implementation)

## Context

Marc reported a shared conversation continuing after reciprocal goodnights. A
casual Codex mention took the assistance path and reset the checkpoint counter;
the following goodbye without a mention could wait several generated replies
before being assessed. Assistance instructions did not emphasize ending detection
equally with periodic checks. Existing tests verified a mocked stop flag, not
real-model recognition of the reported exchange.

## Decision

We retained the single worker, room isolation, durable attention records and
budgeted history boundaries from [ADR-020](ADR-020-room-sessions-and-chosen-encounters.md).
We added a cheap, case-insensitive farewell-phrase hint on new companion messages.
A match requested silent attention before the next generation, independently of
the periodic counter. It never stopped a session by itself. A mention or active
assistance exchange still used one combined assistance/ending call, not two calls.

We required every new model verdict to assess the latest exchange for completion,
including assistance calls, and supply a bounded reason code consistent with its
stop flag. Completed farewells and a clear wish to stop/rest could end the session;
an unfinished goodbye, active conversation or insufficient context could continue.
All checks counted toward the periodic cadence because all now assessed ending.
Each subsequent farewell remained eligible for its own immediate check.

We recorded the trigger and verdict as source-correlated, sanitized activity,
without model rationale prose or transcript excerpts. Decisions stayed durable in
the existing attention JSON. Previously saved decisions remained replayable without
a new model call. No database migration or provider/configuration change was needed.

## Alternatives considered

- Stop directly on farewell keywords: rejected because quotes, jokes and negation
  need context; it could also cut off a final goodbye response.
- Run a model on every reply: rejected because ordinary alternation does not need
  the extra calls or history reads.
- Only strengthen the prompt: insufficient when no attention check is due.
- Never reset the counter after assistance: redundant now that each assistance
  call explicitly evaluates ending; farewell hints cover the next closing reply.
- Infer a next shared appointment from goodnight: rejected as a separate scheduling
  decision; a social ending is not an agreed time or broader authority.

## Consequences

Farewell-like text may add an attention call and a budgeted history read. The hint
is a small English phrase set, not a complete multilingual ending detector;
periodic checks and existing hard limits remain fallbacks. Real-model semantic
accuracy still requires operator acceptance; offline tests assert routing, prompt
contracts and deterministic effects, not model judgment.

Stopping ends only the current relay session. Daily scheduling, approval rules,
uncertain-effect guards and the app-interjection behavior in
[ADR-021](ADR-021-shared-room-interjections.md) remain unchanged. Newer synchronized
context can supersede an older pending check. Activity verdicts describe the saved
decision; `shared_session_ended` confirms that termination actually completed.
