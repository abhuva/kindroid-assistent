# Observational garden (Milestone 8)

Open **Operations → Garden / Roots, Growing, Passing**, then Refresh garden.
This view reads stored state only: no model calls, Kindroid history requests,
invitations, research or prompt changes.

## Meaning and provenance

- **Roots / foundations:** protected continuity anchors, awaiting reviewed manual bootstrap.
- **Growing / nurtured interests:** deliberately continued interests, not automatically populated.
- **Passing / seeds:** exploration suggestions and reported alternatives, still provisional.

Self, shared and infrastructure ownership describe scope, not execution permission.
Active, paused, archived and superseded describe record status, not confidence or
emotion. Current creation is restricted to Passing. Reported Root observations
remain in Agent context memory; migration does not install canonical Roots.

Filter by layer/status. All statuses includes archived items. **History / sources**
shows immutable versions, candidate-specific reactions, original session events,
connections/conflicts and project records. Versions and session events paginate
chronologically. Session context is explicitly distinguished from this seed's
choices. No scores measure growth, desire or emotion.

Observations come from shared memory on each read. Retractions, uncertainty,
attribution changes and source mismatches remain visible. Source buttons open
stored messages. Credential-shaped text is redacted for display; private evidence
is not rewritten. Correct the underlying observation in Agent context memory.

## Administrative annotations

An annotation requires a reason and adds a Marc-authored version. It leaves
original content, evidence, layer, ownership and status unchanged. Clearing it
preserves earlier versions. Stale-version corrections fail; reopen and inspect
rather than overwriting another change.

Submission is only queuing. Inspect Recent commands on Info, then refresh Garden.
A lost response freezes the form so Retry sends the same payload/key. Closing and
reopening is not a retry procedure for an unknown command outcome.

Exploration's Archive candidate / Restore candidate also appends a version with
the operator reason, without deleting evidence or implying Hannah's preference.

## Storage and API

Migration 8 adds `garden_items`, `garden_versions`, `garden_relationships`,
`garden_projects`, `garden_project_items`, and `garden_attention`. Existing
candidates get a `migration_baseline` version with their stored status and linked
earlier history. New candidate/status versions commit in the original worker
transaction. Triggers protect history and block layer/ownership changes.

- `GET /api/v1/garden?layer=&status=&after=&limit=30`: item ID pagination, max 100.
- `GET /api/v1/garden/{id}?after=0&limit=30`: item and version history, max 100.
- `GET /api/v1/garden/{id}/trail?after=0&limit=30`: original session event cursor.
- `POST /api/v1/garden/corrections`: CSRF-protected durable command; payload
  `item_id`, `expected_version`, `annotation`, `reason`.

All reads are conversation-scoped. Detail summaries cap connections at 100,
projects at 20, and each project's recent attention entries at 100, with explicit
truncation indicators. There are no Root creation, promotion, project-execution,
grant or prompt endpoints.

Project repository support is planning infrastructure only, not a runnable feature.
It requires 1–10 Growing links, an outcome, next step and stopping condition.
Accounting uses minutes or tool calls, with at most 10,000 allocated units of
either kind per project. Consumption cannot exceed allocation or occur while
paused. Project pause does not demote an interest. Completion/abandonment preserves
results/reasons and blocks subsequent activity. No reaction creates a project.

## Upgrade and remaining acceptance

Back up first using the existing backup command. Pause and wait for in-flight work
to finish, restart using the [hidden-host procedure](operations.md#starting-and-restarting),
then reload the dashboard. Garden storage was added by migration 8; current startup
also applies [scheduled curiosity's migration 9](scheduled-curiosity.md). Do not
downgrade old code against the newer database. Garden itself needs no new dependency
or environment setting.
Development/testing does not migrate live state or restart the service.

**Manual Root bootstrap remains outstanding.** Marc and Hannah must separately
review actual wording, sources and ownership; record it explicitly as manual
bootstrap and back up the agreed state. No bootstrap UI/import command is exposed
yet. Do not insert documentation examples or infer agreement from casual chat.

Fixtures cover migration, atomic capture, immutable history, stale corrections,
group boundaries, source retractions, archive/restore, project budgets, HTTP
security, backup privacy, literal rendering and keyboard/accessibility behavior.
Verification on 2026-09-06: 308 pytest tests passed in 50.46 seconds under the
60-second hard limit; 23 dashboard tests and the API-client checks passed separately.
One UI run reached its existing 15-second timeout; the completed rerun passed.
The existing Starlette/httpx and npm whatwg-encoding deprecation warnings remain.
Live and real-browser/narrow-layout acceptance remain outstanding; mock data is
not an acceptance of initial Roots or semantic accuracy. The older pre-dashboard
system/operations snapshots still describe their historical baseline.

See [ADR-013](adr/ADR-013-versioned-observational-garden.md). Later milestones add
scheduling/reflection and then deliberate self-change, not this observational view.
