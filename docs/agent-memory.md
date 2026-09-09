# Agent context memory — M6A development implementation

This is Codex's source-backed context, not Hannah's own memory, identity, or an
approval system. Kindroid retains its existing memory responsibilities.
REL-001 manual release acceptance and MEM-010 operator acceptance remain open.
Implementation under ADR-009's development exception did not read or index live
chat, install a model, restart the relay, or alter remote Kindroid configuration.

## Contract and storage

Migration 4 adds `memory_statements`, `memory_evidence`, `memory_entities`,
`memory_links`, `memory_revisions`, `memory_extractions`, and `memory_vectors`.
Existing messages, approvals, deliveries, and stable generation IDs are preserved.
All mutations use the serial worker and existing durable command queue.
Migration 5 adds extraction uncertainty/model metadata, suggested entity matches,
and explicit/inferred connection basis, preserving schema-4 observations.

Statements retain classification, interpretation, capture origin/version, temporal
qualification, and active/disputed/superseded/retracted status. **Active means
eligible for retrieval, not true.** Original quotations and offsets are immutable;
interpretations are separately editable. Evidence refers to stored messages and
preserves both speaker metadata at capture and current metadata. Attribution
changes and provisional role-only senders are visible. `[CODEX]:` retains its
speaker attribution even when a stored display name differs. Display names alone
never establish that a statement originated from Hannah. Each statement can have
up to ten explicitly reviewed source passages.

`marc_selection` means Marc selected a source, not that Marc uttered its quote.
`system_selection` means a deterministic extractor selected it, not a Codex
interpretation. User/AI source metadata, Codex-prefixed utterances, and system
selection remain distinct. External material quoted in chat is still only a
chat-sourced statement, not independently verified external evidence. Adding an
external-source ingestion adapter is outside this first implementation.
`codex_extraction` identifies semantic extraction by the personal agent: model
interpretation and inferred connections are not the original speaker's wording.
The extractor prompt version and selected runtime model are retained; fixtures
without runtime metadata use the explicit `runtime_default` label.

A `reported_root` means the passage was selected/classified as a foundational
self-description; its source, uncertainty, and possible classification error
remain visible. It does not become a canonical Root, consent, or policy.
Capturing/correcting observations does not require identity-change confirmation.
Consequential external actions still require their existing local approval.

Entities represent people, places, topics, or events, with optional aliases.
Same-name entities from different observations stay separate candidates. An
operator can review a shared entity ID and link another statement to it using
`mentions`, `about`, `experienced_at`, or `interested_in`. These are attributed
connections, not proven relationships. Revisions, link additions/removals, and
additional evidence retain command IDs and reasons. No similarity-based person
merge or automatic contradiction resolution occurs.

## Capture and review

Use **Save observation** on a stored dashboard message. Select an exact passage,
optionally add an interpretation, temporal qualifier, and entity/aliases, then
queue the save. This explicit manual action works even with automatic memory
disabled. To attach another source, enter an existing observation ID and reason
in the save dialog. The worker revalidates the exact quote and conversation scope.
If the same words occur repeatedly in a message, the UI selects the first matching
occurrence; the API supports an explicit character offset.

The collapsed **Agent context memory** panel searches saved observations and opens
source/connection/revision details. Correct an interpretation or status with a
reason, or review an entity link by ID. Retraction excludes the observation from
retrieval without deleting its evidence. Superseded and disputed observations
retain those labels; relate conflicting observations through reviewed shared
entities. The original classification cannot currently be edited; retract a
misclassified item and retain its source for review. Context omission cannot erase
anything already present in a persistent Codex thread.

Commands return **queued**, not saved/completed. Activity/command history shows
worker completion; refresh the memory panel afterward. Memory commands do not
force a Kindroid polling cycle, resume a paused relay, or send messages. Reads use
the same loopback/Host protections; writes also require same-origin/CSRF and strict
bounded JSON. Private passages render as text, not HTML.

## Disabled-by-default configuration

`kindroid.env.example` documents these settings. Do not enable live extraction
until operator acceptance:

- `RELAY_MEMORY_ENABLED=false`: gates retrieval into Codex and extraction/indexing.
- `RELAY_MEMORY_AUTO_CAPTURE=false`: additionally gates worker automatic capture.
- `RELAY_MEMORY_EXTRACTOR=semantic`: Codex meaning-based extraction; `local`
  explicitly selects the earlier English cue-based backend instead.
- `RELAY_MEMORY_EXTRACTION_TIMEOUT_SECONDS=45`: SDK extraction deadline, 5–120
  seconds, followed by owned-child cleanup. This does not guarantee server-side
  cancellation or reverse already incurred Codex usage.
- `RELAY_MEMORY_MAX_MESSAGES_24H=30`: rolling attempt budget, including failures.
- `RELAY_MEMORY_MODEL_PATH=`: optional absolute path to a trusted, preinstalled
  SentenceTransformer model directory; no hosted model ID or URL.
- `RELAY_MEMORY_MODEL_REVISION=operator-local-v1`: change this identifier whenever
  model weights/tokenizer or embedding behavior change. Prefer a snapshot commit
  or digest of the installed model rather than a mutable label.

When enabled, **Capture next 3 local messages** backfills already captured chat in
timestamp/ID order, without fetching remote history. Automatic mode does the same
at most once per ten minutes while unpaused, provided its deadline fits before the
next relay poll/check-in. This automatic mode needs no manual Save observation
action. Each message is charged/attempted only once per extractor version, even
after restart. Default 30 messages/day permits at most ten full three-message
batches (or 30 single-message batches); failures count too.

The default `semantic-observations-v1` backend uses a dedicated ephemeral Codex
thread and the existing `RELAY_CODEX_MODEL` selection. It sees at most three target
messages (4,000 characters each), six earlier context-only messages (1,000 each),
and up to 12 existing-entity hints (4,000 serialized characters total). Candidate
hints scan at most 500 entities and are not a complete entity catalog. Long target
prefixes are reported as `truncated_sources` in the command/last-capture result;
the remainder is not scanned automatically. At most three source-backed
observations and five entities per observation are accepted. Small talk can yield
zero observations and still complete successfully. Classification is by meaning,
not English cue words; semantic accuracy remains subject to model evaluation.

The extractor preserves negation, third-party quotation, uncertainty and temporal
qualifiers in its instructions. Host validation rejects fabricated/non-unique
quotes, out-of-batch source IDs, extra authority/speaker fields, inconsistent
entity keys and unsupplied existing-entity IDs. Whole batches save atomically.
Batch-local entity keys connect observations; cross-batch identity matches remain
review suggestions. **Sources / review / correct** displays uncertainty and
possible-match buttons that prefill a link for review; they do not apply it. A
reason and explicit Link entity action are still required.

The SDK runs read-only with approval denied in an empty temporary directory,
independent of the persistent conversation thread. Configured MCP/plugins/apps,
shell/web tools, hooks and unrelated Codex memories are disabled for that thread.
Only selected text and metadata enter the model prompt, not runtime configuration.
No Kindroid history reads/replies are incurred, but extra Codex calls and its
existing remote processing/usage boundary are involved. See
[ADR-010](adr/ADR-010-semantic-memory-extraction.md) for alternatives and controls.

Invalid, failed, timed-out or interrupted semantic batches remain failed, retaining
source chat but saving no partial batch. No automatic retry or cue-based fallback
occurs. The dashboard shows failed progress and sanitized last-capture counts.
Manually select a source to recover, or deliberately introduce a new extractor
version for another pass. Capture failure never retries a delivery or pauses an
otherwise healthy relay. No retroactive correction of prior observations is
performed by the extractor.

The `local-cue-spans-v1` backend remains available via `local`: first 8,000
characters, three sentence passages selected using English cues, no additional
Codex calls. It is less capable and can miss implicit/non-English expressions.
Switching backend selects its separate progress/version and can backfill already
captured messages again; existing source evidence is not discarded.

## Search and optional semantic backend

Exact passages, names and aliases, token matching, and bounded spelling similarity
provide lexical/fuzzy entry points. Empty queries browse recent saved statements.
Retrieval ranks relevance first, then status, resolved source metadata, bounded
evidence count, and source recency, with stable IDs breaking ties. Ranking never
establishes truth. Temporal scope is a displayed qualification, not automatically
interpreted natural-language dates; mark outdated observations superseded.

The optional backend is `sentence-transformers==6.0.0` (`.[memory]` extra), CPU-only
with `local_files_only=True` and `trust_remote_code=False`. The intended initial
English model is a locally staged `sentence-transformers/all-MiniLM-L6-v2` snapshot;
record its actual revision in configuration before use. The
[official model documentation](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
and [local-loading API](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html)
describe the model and loading controls. No package/model was installed during
this implementation; real-model latency and retrieval quality remain unverified.

**Index next 10** builds at most ten changed/missing vectors per worker command.
Vectors are cached by statement ID, source/interpretation/entity text hash, and
model revision. Changing revision leaves evidence and previous caches intact and
builds a new index incrementally; stale vectors are not used. Missing dependencies,
models, invalid vectors, or incomplete indexes report fallback/pending status.
No private data is sent to a new embedding service. Retrieved context is still
sent to the existing Codex service when memory is explicitly enabled, just like
the recent-chat context it supplements.

Codex's worker-owned retrieval uses hybrid matching when configured. Dashboard
search deliberately remains lexical/fuzzy: it neither loads models in request
threads nor incurs indexing work. Semantic matches use a provisional cosine
threshold of 0.55, to be evaluated with the actual local model before acceptance.

Bounds are explicit: 500 query characters, 16 scored query tokens, a 1,000-statement
candidate corpus, four entry points, at most two statement/entity expansion rounds,
12 expanded entities, six neighbours per entity, eight returned statements by
default (hard maximum 20), and 16,000 serialized item characters by default (hard
maximum 20,000). Cycles are visited once; hub and character limits set truncation.
The latest 1,000 eligible saved statements form the corpus; older saved statements
outside it require direct-ID inspection. This is a known scale limitation, not a
claim to search all history. Matching candidates and traversed IDs explain results.

Ordinary replies, manual openers, check-ins, and locally approved execution share
`MemoryService.context(query)`. Exploration should reuse this interface. The
existing history synchronization and last-50 snapshot remain intact. Retrieval
failure returns an explicit unavailable section, not a claim of full context.

## Recovery, verification, and acceptance

Use the existing [backup and staged restore procedure](operations.md) before
upgrading a real database. Schemas 4 and 5 are additive; restore the whole SQLite backup,
not selected graph tables. Evidence and revision history are durable; vectors are
derived. Rebuild changed/missing vectors through the bounded indexing command.
Default diagnostic exports exclude memory tables, quotations, interpretations,
revision reasons, vectors, and command payloads. Full backups are private.

Fixture evaluation in `tests/test_memory.py` covers old statements after 60 newer
messages and restart, all four Codex context paths, aliases/misspellings, semantic
paraphrases with deterministic vectors, unrelated queries, group isolation,
provisional/changed speakers, same-name people, reviewed connections, cycles/hubs,
superseded/retracted statements, fabricated evidence and permission-like text,
multiple sources, durable retries, budget/failure behavior, database contention,
backup/restore, and diagnostic exclusion. DOM tests exercise save/review/retraction,
lost-response idempotency, literal text rendering and accessible dialog markup.

Before live enablement, Marc must accept the remaining REL-001 gates and review a
fixture demonstration of retrieval quality, source attribution, correction flows,
native dialog/keyboard behavior, and (if selected) the actual embedding model's
quality/latency. Local mocks do not complete that acceptance. Semantic extraction
is now implemented, but its real-model quality/runtime validation, automatic
cross-batch identity resolution and automatic temporal reasoning are not claimed.

Initial M6A verification on 2026-09-06: full regression **228 passed in 45.64s**
under the 60-second hidden-child timeout, including the then-16 DOM/accessibility checks.
Targeted storage/context/migration checks and subsequent worker/API/config/UI
checks also passed. The existing Starlette/httpx test-client deprecation warning
remains. `git diff --check` passed. Native-browser and live/model acceptance were
not performed. Historical `current-system.md` remains a dated pre-dashboard
snapshot; this document describes the new memory implementation.

Semantic refinement verification: targeted extraction/storage/config/worker checks
passed (58 tests); all 17 DOM/accessibility checks passed, including explicit
entity-match review. The full suite passed 248 tests under the same 60-second
limit. SDK calls and model responses were mocked; no live extraction or model
quality evaluation occurred. An initial cold dashboard-test invocation timed out
at 15 seconds; later standalone/full-suite runs completed. The existing
Starlette/httpx deprecation warning remains.
After the last adapter config-key hardening, a full rerun reached the final test
but exceeded the hard 60-second process-exit limit, so that rerun is not recorded
as a completed suite. The final semantic adapter suite separately passed all 18
tests in 2.69s. No timeout limit was increased and only the owned test child was
terminated.
