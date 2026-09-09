# ADR-018: Broaden Discovery and Return One Bounded Experience

**Status:** Accepted | **Date:** 2026-09-08\
**Participants:** Marc, Codex

## Context and alternatives

Marc requested more varied stimuli and a path from a selected curiosity to an
experience and eventually self-chosen projects. The schedule already worked, but
live invitation follow-through acceptance remained outstanding. We agreed to
prioritize one bounded research-and-return slice, not public account creation,
publishing or an unrestricted autonomous agent.

We considered a timer for every source (would fragment the shared schedule),
unrestricted model browsing (would expand tool authority and privacy exposure),
and host-controlled public providers feeding the existing invitation flow. We
chose the latter. A full third tick allocation and project execution were deferred
until this experience loop is accepted. Equal source rotation was chosen over
another percentage editor for the first slice.

## Decision

We added migration 11 for local discovery settings, bounded feed caches, immutable
source snapshots and durable exploration experiences. A protected dashboard save
opts into source categories and research allowance without changing the environment
or immediately running work. Existing configuration/behavior is retained until
save; public reads and research default off. Per-invitation opt-in prevents old
responses being automatically researched on upgrade.

The existing selector was extended with creative possibilities, previously chosen
interests, BBC World RSS and optional experimental local Google News RSS. Dates,
source links, source failures and selection reasons remain visible. Recent repeat
suppression and empty-source skips prevent repeatedly pressing the same offer.
Sources are influences offered by Codex, not Hannah-origin preferences.

We implemented the first public research provider as a single Wikipedia query
returning at most three introductory reference excerpts. The adapter uses the
[MediaWiki TextExtracts interface](https://www.mediawiki.org/wiki/Extension:TextExtracts).
World news uses the BBC's [published feed infrastructure](https://support.bbc.co.uk/platform/feeds/NewsFeeds.htm).
Local Google News RSS remains explicitly experimental and unverified in this release.
These provider choices deliberately limit capability; they do not constitute
comprehensive research or guaranteed relevant/current results.

Public candidate topics can be looked up automatically under local opt-in. Private
memory and Hannah-origin alternatives require a separately approved public phrase:
chat text never authorizes its own disclosure to a search service. No unrestricted
model/tool process is added. Existing source attribution and interpretation gates
remain, including [ADR-017](ADR-017-exploration-review-and-role-attribution.md).

Each experience reserves one rolling-budget attempt before I/O, stores results
before delivery, and binds its outbox before sending. One result and one optional
reflection are allowed. The reflection is consumed outside the ordinary agent
chain and stored as a source-backed observation, without identity promotion.
Pause, quiet/dead time, pending conversation and unresolved delivery still gate
work. Stale work expires after 24 hours. Read failures are terminal/nonblocking;
uncertain external effects remain paused and cannot replay.

This extends the no-research portions of [ADR-011](ADR-011-one-shot-observational-exploration.md)
and [ADR-012](ADR-012-exploration-response-follow-through.md) only for locally opted-in
bounded public experiences. It does not complete M9 evaluation or authorize live
Kindroid tests, publishing, spending, account changes, canonical self-change, or
project execution. The broader staged vision remains intact.

## Consequences

- (+) Selected curiosity can produce a real, inspectable result instead of only
  an acknowledgement, followed by a neutral reflection opportunity.
- (+) Source diversity grows without adding independent timers or model tools.
- (+) Public search disclosure, original choice, delivery and reflection are distinct.
- (-) Wikipedia reference lookup is narrow; it can miss the intended topic and is
  not independent verification of a news event.
- (-) The local-news endpoint is experimental; coverage and provider availability
  require live acceptance. Failed feeds do not block other sources.
- (neutral) Tick percentages stay two-way. Source weights, richer research,
  experience trails in Garden, projects, and public-channel execution are deferred.

See [operator behavior, budgets and recovery](../discovery-and-experiences.md).

## Local verification

All 521 Python tests passed on 2026-09-08 in isolated bounded groups: 471 in
56.13 seconds, 47 host/recovery tests in 31.72 seconds, and three UI wrappers in
16.17 seconds. The earlier combined host/UI group reached its 60-second hard limit
after individual tests had reported passing; completion was then verified in the
smaller groups. Focused checkpoints passed 113 and 117 tests during development.

`npm ci --ignore-scripts` reported zero vulnerabilities. Final `npm test` passed
30 DOM behavior tests, the accessibility scan, and API-client checks. The prior
combined DOM/accessibility file occasionally hit its existing 20-second limit;
behavior and accessibility now run separately with the same per-run bound. An
initial negative-lookahead filter did not isolate the scan reliably under the
Windows runner, so the final command uses Node's explicit test-skip-pattern option.
No production timeout was raised. `git diff --check` passed. Existing dependency
and Starlette/httpx deprecation warnings remain.

Fixtures cover bounded HTTP reads, redirects/rate-limit/oversize/deadline failure,
RSS validation, source fallbacks, explicit public-topic approval, no backfill,
choice/result/reflection, silence, retraction, pause/dead time, quotas, expiry,
uncertain delivery/no replay, backup/privacy, schema preservation, and UI retry/XSS.
The browser runtime reported no available browser, so real-browser visual/native
acceptance remains unverified. No live database migration, configuration update,
host restart, public-source adapter requests, actual model interpretation or
Kindroid calls were performed. Provider documentation was consulted separately;
it is not live feature acceptance.
