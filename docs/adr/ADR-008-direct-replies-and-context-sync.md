# ADR-008: Consume Direct Replies and Synchronize at Context Boundaries

**Status:** Accepted | **Date:** 2026-09-06
**Participants:** Marc, Codex

## Context

The group response endpoint is documented to wait for generation and return reply
text with `stream: false`. The relay discarded that text, then read history up to four
times to obtain a message with a remote ID. History reads have a strict rolling
budget. Codex also received recent group history only when its thread was seeded,
so later synchronization did not necessarily update its conversation context.

Alternatives considered:

1. Continue polling after generation: retains immediate remote IDs but spends
   history quota to retrieve text already returned by the API.
2. Use the single-conversation send endpoint: returns text too, but changes the
   conversation surface and does not preserve the group interaction.
3. Consume group replies directly, persist stable local IDs, and reconcile history
   at explicit context boundaries. Streaming would change delivery latency, not
   solve remote-ID association, so it was not needed for this change.

## Decision

We decided to retain the synchronous group endpoints and worker ownership from
[ADR-005](ADR-005-local-dashboard-command-queue.md). Successful sends and generated
replies are recorded locally immediately. Generation intent is committed before
the HTTP call; successful reply capture is atomic with its completed status.

Migration 3 preserves existing message keys and source links. The legacy
`messages.remote_id` column remains the stable record key; `kindroid_id` stores
the actual remote ID. Locally captured messages keep their local key permanently.
Remote timestamps and speaker metadata replace provisional observations on sync.

History synchronization precedes normal cycle entry, manual conversation opening,
scheduled check-ins, and approved execution. During a long exchange, the next
Codex turn synchronizes when the activity polling interval has elapsed (default
five minutes). Short exchanges use the direct replies and local transcript. Idle
polling remains necessary to discover messages sent through the Kindroid app.
Every Codex invocation receives a recent 50-message local snapshot, marked as
untrusted and bounded, with the last successful context-sync time. Context can
inform an approved action but cannot enlarge its locally approved instruction.

Reconciliation reads all cursor pages before matching. Local outgoing messages
are associated within their saved sync window by exact text and occurrence order;
duplicate counts must agree. Each generated reply must be the sole AI message
between its associated outgoing message and the next user message, with matching
AI identity and returned text (apart from surrounding whitespace). When history
only supplies a sender role, configured speaker name is the weaker fallback.
The generic default `Kindroid` is resolved to the single AI name already confirmed
in the same group's history; a concrete matching sender ID can resolve its name
directly. This also applies to earlier persisted uncertain turns. We rejected
accepting any AI after the outgoing anchor or using names first seen in the
unmatched response: multiple observed names, conflicting IDs, and explicit
different configured names remain blocked by the role-only fallback.
Generation that lost its response can recover from a unique AI message in this
same window. Already processed local replies stay processed after association.

Unresolved local records pin the next history cursor. Missing history after a
successful response defers context-dependent work until a later scheduled sync;
there is no inner history polling loop. Ambiguous history or generation with an
unknown outcome pauses the relay. Resume permits reconciliation, never a retry
of an uncertain generation. An unresolved case remains blocked for inspection.

### Empty successful response fallback (2026-09-06)

Marc's running relay demonstrated that a successful generation request can have
an empty response body while Hannah's reply exists in the Kindroid app. We decided
to make one budgeted, paginated history synchronization immediately in this case,
and continue from the uniquely matched existing reply. Generation is never retried.
History 429 cooldowns defer recovery to a later regular cycle; missing, ambiguous,
or failed history confirmation still pauses. The automatic turn limit also applies
when a generated reply becomes available after a cooldown or restart.

Alternatives were to pause on every empty body (the initial implementation,
which prevented exchanges on the observed endpoint), restore a repeated history
polling loop, or assume streaming would fix the provider's response. We chose the
single-sync fallback because it works with the observed API behavior without an
untested protocol switch or repeated generation. The history budget and spacing
still apply. Fallback attempts and results are recorded without message bodies.

### Empty AI history records (2026-09-08)

An operator-approved diagnostic read found one real Hannah answer followed by an
empty AI record in the same outbound window. Both passed the existing speaker
check, so counting every record as a possible answer falsely reported ambiguity.
Marc requested the targeted correction after diagnosis.

We excluded empty/whitespace-only AI records from the matching window only when
they passed the expected-speaker check. User boundaries, other roles, conflicting
speakers and multiple non-empty replies remained guarded. A blank-only window did
not establish successful generation. The complete paginated window still had to
reconcile atomically before importing new messages or advancing its cursor.

We retained blank records on successful import, with their original text/metadata
and processed status, so they could not trigger Codex replies. We rejected dropping
all blank records (loss of history evidence) or accepting arbitrary multiple AI
replies (loss of unambiguous source linkage). This changes neither generation
retry policy nor authority: existing uncertain turns can reconcile on the next
budgeted sync, without regeneration or automatic resume. No migration is needed.

This supersedes only the literal "sole AI message" rule above: the answer must be
the sole non-empty reply after excluding matching blank AI records. General remote
message editing/replacement remains outside this correction.

## Consequences

For shared-room generations only, [ADR-021](ADR-021-shared-room-interjections.md)
amends the user-message boundary and competing-speaker rules to permit app
participation. Private outbox-backed reply windows retain the rules above.

- Replies containing text consume no additional history calls. Empty successful
  responses require one synchronization before Codex can answer; if every response
  is empty, every generated reply still needs that synchronization.
- Remote-ID association preserves outbox, approval, and activity references.
- Context is refreshed deliberately, including for an already seeded Codex thread.
- App interjections are observed at the next sync; they are not push notifications.
- API text lacks message IDs. Association remains conservative observational
  matching, not a provider-supported idempotency or correlation guarantee.
  Interleaving, editing, or ambiguous duplicates may require operator inspection.
- The dashboard labels provisional name/time information until history confirms it.
- Schema upgrades are forward-only; this change does not migrate a live database
  during development or authorize live Kindroid tests.
