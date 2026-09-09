# Observational exploration

Milestone 7 is implemented for local evaluation. REL-001, MEM-010, and live M7
acceptance remain open. No live feature was enabled by this change.

## Operator behavior

**2026-09-08 extension:** [Discovery and bounded experiences](discovery-and-experiences.md)
adds optional source categories and one locally opted-in public lookup/result/reflection
for a new selected curiosity. The original invitation/interpretation behavior below
remains the default. No projects or identity promotion are added.

The compact **Exploration / Passing candidates** dashboard disclosure shows recent
sessions, budget, suggestion origins, response sources, and archive/restore controls.
**Start exploration** requires confirmation because it sends an invitation to the
configured group. Requests only queue commands; the worker owns every external call.

After prerequisite acceptance and explicit live-test authorization, configuration is:

```dotenv
RELAY_EXPLORATION_ENABLED=true
RELAY_EXPLORATION_MAX_SESSIONS_24H=3
```

The default is disabled. M9.1 adds an independently enabled, default-off
[curiosity schedule](scheduled-curiosity.md); neither path alters the social
check-in schedule. Three attempts per rolling 24 hours is the default combined
separate budget; each session allows exactly one invitation and one optional
initial Kindroid response. The response follow-through described below adds up to
two Codex interpretations, one optional clarification turn, and one acknowledgement.
Failures after session reservation remain charged. Paused relay,
quiet hours, pending AI messages, unresolved reconciliation, and history cooldowns
block new exploration. It does not reset or expand the ordinary conversation limit.

Selection occurs after the normal budgeted history synchronization. A stateless,
local 12-domain catalog combines a past observation when available, an adjacent
topic, and deliberate breadth; absent memory it uses broad catalog alternatives.
An enabled memory service contributes lexical/fuzzy/optional semantic retrieval,
plus up to three stored question/interest observations. Active Passing domains
contribute adjacency. Recent offer locators de-prioritize repeats; the command ID
is the reproducible seed. The invitation explicitly allows rejection, different
options, or no response. Invitation selection itself uses no Codex call or research tool.

The reply is consumed by exploration, not the ordinary Codex reply chain. A direct
reply may initially show `awaiting_sync`; the next normal synchronization records
its evidence and changes the session to `observed`. No extra post-reply polling
loop is added. A deliberate synchronization before response interpretation may
confirm it sooner. An observed response is not a durable preference, accepted task,
canonical Root, or authorization. Repeated or contradictory responses remain
separate observations. Long replies use an exact first-2,000-character evidence
passage with a link to the complete captured message.

Hannah attribution requires confirmed history and a matching display name. In
Marc's single-AI-companion group, `sender=ai` is accepted when the response is
linked to a reconciled generation for the expected AI and confirmed group history
contains no conflicting AI names or concrete IDs. The generic default `Kindroid`
name can use the existing single-name history resolver; an unresolved name,
mismatching concrete sender, or ambiguous source is not labelled Hannah.
`[CODEX]:` remains authoritative regardless of a display label. Raw speaker data
stays intact. This is a single-companion attribution rule, not an external-action
approval or support for ambiguous multi-companion groups; see
[ADR-017](adr/ADR-017-exploration-review-and-role-attribution.md).

## Response-to-seed follow-through

New invitations use `RELAY_EXPLORATION_AUTO_INTERPRET=true` by default when
exploration itself is enabled. Setting it false keeps one-shot invitation capture;
the dashboard can still explicitly interpret an existing response.

After synchronization, an isolated Codex turn receives the offered options and
the captured reply, not the ordinary agent's thread or tools. The existing Codex
model/service is used even if general automatic memory capture is disabled. The
existing `RELAY_MEMORY_EXTRACTION_TIMEOUT_SECONDS` also bounds this interpreter.

- **Choose:** link the exact evidence to the selected candidate(s); acknowledge
  reported interest and suggest a short overview/comparison as a possible next step.
- **Alternative:** create a provisional Passing candidate linked to Hannah's words;
  its descriptive title is labelled as a Codex interpretation. Acknowledge it once.
- **Defer:** retain the response and any named candidate links; send no pressure prompt.
- **Decline:** retain the response and stop without another prompt.
- **Unclear:** ask one optional clarification. A second ambiguous reply stops for
  review; a clear reply can receive one acknowledgement. Silence ends the flow.

Acknowledgements do not request an AI turn or start research. Maximum per session:
two model calls, two follow-up messages, one extra Kindroid response turn. Each
follow-up has its own durable source/outbox link and uses the existing history
budget. Exact quotations and candidate IDs are validated before an atomic commit.
The dashboard shows classification, interpretation, quotation, response status,
and source-linked reactions beneath candidates. Original Codex suggestions retain
their origin; recording interest does not promote a seed to Growing or Roots.

For an invitation sent before this update, expand its session and click
**Interpret response**, then confirm. It uses the existing reply and never resends
the invitation. Upgrades do not process old sessions automatically. If captured
conversation has moved on, the interpretation/seed links are retained but no stale
acknowledgement is posted. Check the response note rather than triggering a new invitation.

`needs_review` may mean unconfirmed speaker attribution, an oversized response
(over 8,000 characters), a changed/retracted source interpretation, a moved-on
conversation, or unresolved ambiguity. Failed/invalid/timed-out interpretations
are not automatically retried. The model's semantic accuracy still requires live
acceptance; exact source validation alone does not prove its interpretation right.

## Failure recovery and privacy

`needs_review` and failed interpretations stop only that response's follow-through;
they do not block later scheduled check-ins or invitations. No manual closure is
needed to keep scheduling running. Their reservations still count for the normal
rolling 24 hours but are not extended indefinitely. Active follow-through and
unresolved delivery/generation still block new scheduled work. Existing reviews
are not automatically retried, relabelled, answered or closed on upgrade.

`uncertain` on a session or response means inspect the Kindroid app, session, and outbox; do not start another
invitation to test delivery. Interrupted commands never replay. **Close after
inspection** is an audited local acknowledgement only: it sends nothing, leaves
transport uncertainty intact, and does not resume. If a linked generation is later
recovered, its response is still consumed and retained as an observation.

Archive/restore changes only local candidate visibility and requires a reason.
It preserves source evidence and event history. The dashboard bounds its list to
20 sessions, 60 candidates, and 100 events per session. Repository `history` supports
cursor-based access to the entire retained event trail; the UI is not a complete
historical browser yet.

Migrations 6 and 7 add tables without replacing existing chat, evidence, or corrections.
Normal private SQLite backups include the new data. Diagnostic exports exclude
private candidate text, response quotations, selection inputs and inspection reasons.
No model downloads, credentials, remote configuration, or live service changes are
part of this feature.

## Mode contracts and limitations

`MemoryService.context(query, mode=..., session_id=...)` provides Explorer,
Gardener, Archivist, Skeptic, and Executor contexts. They share a stable policy
prefix, explicit untrusted dynamic data, and a default 12,000-character serialized
bound with deterministic whole-item truncation. Templates and events share the
`observational-agency-v1` version. No agency mode resumes the ordinary chat thread.
Explorer has local selection plus a separately isolated response interpretation
workflow; the other mode contracts are preparation for later work, not autonomous agents.

The static catalog is English-only and finite. Adjacency is domain-based; “unfamiliar”
means outside the available context, not genuinely new to Hannah. Past questions
are not verified unfinished tasks. Public reference lookup is now separately opted
in through Discovery; broader research and semantic invitation generation remain
deferred. Mock tests do not establish live Kindroid acceptance,
native-dialog behavior, or the quality of invitations in real conversation.

## Local verification — 2026-09-06

Response-follow-through update: **298 Python tests passed** in 46.72 seconds under
the isolated 60-second hard limit; **19 dashboard DOM/axe tests passed** separately,
plus API-client checks. The focused response suite passed all 28 tests. Migration 7,
source/quotation validation, alternatives, one-clarification limits, late recovery,
retraction, pause/resume, source changes, legacy-session opt-in, and diagnostic
privacy use fixtures and mocked interpreters/services. An initial standalone UI
run hit the existing 15-second file timeout; its completed rerun passed without
increasing that timeout. Existing Starlette/httpx and npm dependency-deprecation
warnings remain. No live settings, service restart, actual Codex interpretation,
Kindroid follow-up delivery, or native-browser acceptance was performed.

Initial one-shot implementation verification (before response follow-through):

- 22 agency tests passed; combined agency/semantic-memory verification: 40 passed.
- Full isolated Python regression: **270 passed**, 49.77 seconds, within the
  60-second hard limit. The existing Starlette/httpx deprecation warning remains.
- `npm ci --ignore-scripts`: successful, audit reported zero vulnerabilities.
  `npm test`: **18 dashboard DOM/axe tests passed**, plus API-client checks.
- An initial UI run reached the existing 15-second file limit. Accessibility checks
  now scan the whole page once and each newly exposed dialog separately, including
  exploration; the timeout was not increased. Final runs completed normally.
- A stale migration-5 expectation was updated to include migration 6. Upgrade,
  backup preservation, source attribution, contradictory observations, no replay,
  late recovery, budgets, command/CSRF boundaries and privacy use fixtures only.
- No live Kindroid requests, live extraction, service restart, native-browser
  acceptance, or operator release sign-off was performed.
