# Discovery sources and bounded experiences

## Enablement and operator controls

After a backup and safe host restart (migration 11), open **Schedule → Discovery
sources & follow-through**. No environment edit is needed. Without a local save,
existing catalog/memory invitation behavior remains unchanged. News and research
are off by default. Saving only queues a local configuration change; it does not
send an invitation, resume the relay, or backfill old sessions.

Select one or more sources:

- **Unfamiliar and adjacent topics:** the existing finite local catalog.
- **Memory and unfinished questions:** attributed local observations. Their
  continued relevance is not assumed, and quotations are not exported to search.
- **Creative possibilities:** private notebooks, zines, personal websites,
  discussion spaces, and short-form video concepts. These are offers, not projects
  or permission to create accounts or publish.
- **Previously chosen interests:** active candidates with a source-backed choice
  or alternative, introduced at least 24 hours ago. Generic enthusiasm and rejected
  offers do not qualify. This does not promote an interest to Growing.
- **World news:** BBC World RSS, short titles/snippets with publication dates.
- **Local news:** experimental Google News RSS search for a locally entered public
  city/region. The place name is sent to Google News. English/Germany edition
  coverage is not comprehensive; do not enter a private address. It stays disabled
  until an area is chosen. This endpoint is observational, not a stable supported API.

Sources rotate equally across invitations, with up to three candidates total.
This release offers source selection, not per-source percentage weights. Recent
locators (up to 60 from the last seven days) are suppressed. News must be at most
three days old; each feed has a one-hour success/failure cache. An unavailable feed
does not pause conversation. Other enabled sources can supply candidates; with no
fresh candidates, the invitation is skipped rather than repeating offers.
The panel shows feed outcomes, recent public source links and publication dates.

## What a selected curiosity now does

Enable **Allow bounded research…** and choose a rolling 24-hour attempt allowance
(default two, allowed range 1–20). Exploration capability and response interpretation
must also be enabled. Only invitations created under this opt-in receive an
experience record. Existing invitations never gain automatic research on upgrade.

Once a choice or alternative is interpreted and acknowledged:

1. One selected candidate can receive one experience. If multiple options were
   chosen, this first slice follows only the first linked candidate.
2. Public catalog/creative topics use a curated related search phrase; news uses
   its public headline. Private-memory topics and Hannah's reported alternatives
   wait in `needs_public_topic`. Marc can enter and approve a public search phrase
   locally, without exporting the original private response.
3. The worker reserves one attempt and performs one Wikipedia reference lookup,
   returning up to three introductory excerpts, titles, links and retrieval times.
   No model tools, shell, arbitrary webpage crawling, or connected apps are used.
4. Results are saved locally before any delivery. After another synchronized worker
   boundary, one attributed result is sent (only legacy scheduling still waits
   for the normal check-in idle interval; saved shared schedules have no cooldown)
   to the existing group with an optional reflection question. It does not force
   a reply or launch the normal tool-capable conversation chain.
5. A history-confirmed reflection is retained as a source-linked observation. Silence
   is terminal. There is no automatic interest promotion, project execution, or
   additional follow-up.

This is a **reference-based starting point**, not open-ended research, independent
fact-checking, or a verified current-news briefing. It quotes Wikipedia introductory
text with source links and CC BY-SA attribution. Results can be irrelevant; failure
or lack of a result does not mean Hannah lost interest. Broader research providers,
semantic synthesis, a separate follow-through tick allocation, and project
proposals/prototypes remain later slices.

Research advances at ordinary synchronized worker boundaries, not through a new
independent timer. Existing two-way tick percentages are unchanged. Pause, pending
conversation, unresolved transport, saved schedule disablement/dead time, or legacy
quiet hours hold follow-through. A choice whose source/attribution changed, whose
observation was retracted, or whose candidate was archived is cancelled. Unstarted
or undelivered work expires after 24 hours; it does not accumulate for catch-up.

## Safety, recovery and limits

Each lookup makes one request to a fixed public destination. Reads use fresh clients,
no ambient proxy/auth settings, no redirect following, five-second I/O timeouts,
a 256 KiB response cap and a 15-second checked streaming budget. This is not an
OS-level overall network deadline. RSS entity/DOCTYPE declarations are rejected.
Retrieved content is evidence only; it cannot change permissions or become executable
instructions. Raw public source text is rendered as text, not HTML.

Failed or interrupted lookups are charged and never automatically retried.
Their records, and review-only results, do not block unrelated scheduled activity.
Oversized result messages are retained for review rather than silently truncated.
Sending/uncertain delivery is never retried: the relay pauses and retains its outbox
and generation links. **Close after inspection** closes the exploration locally;
it never clears transport uncertainty or resumes the relay. Late recovered
reflection replies are consumed without starting a new tool chain.

Original choice, public search authorization, candidate, lookup result, delivery
and reflection remain linked in SQLite. Backups retain these private records;
diagnostic exports exclude the new source, query, result and reflection tables.
No live state, credentials, feeds or Kindroid messages are modified by installation.

## Acceptance still required

Tests use fake public sources, Kindroid and interpreters. Verify the original
invitation/choice/acknowledgement flow with operator-authorized live acceptance
before enabling automatic research. Then separately accept public-source relevance,
the result/reflection flow, and local-news coverage. No live acceptance is implied
by mocked tests. Real-browser visual/native interaction acceptance remains open
when no browser connection is available.

See [ADR-018](adr/ADR-018-bounded-discovery-experiences.md) for scope and alternatives.
