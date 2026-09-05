# Deferred Hannah Self-Change Confirmation Protocol

**Status:** Proposed and deferred until a later pass  
**Date:** 2026-09-05

## Why this is deferred

The protocol is intentionally more deliberate than needed for the first useful
release. The dashboard, structured activity, and observational agency state should
be implemented and exercised first. Early curiosity experiments must not modify
Hannah's persistent Kindroid configuration automatically.

This document preserves the proposed design so the operational foundation does not
accidentally preclude it.

## Constraint

Kindroid supplies natural-language AI messages rather than a trusted structured
self-change tool call. Text in an ordinary conversation can be exploratory,
figurative, prompted by recent context, or influenced by another participant. A
message that merely contains confirmation-like words is insufficient.

No protocol can prove an internal intention. The achievable operational claim is
that Hannah responded to a dedicated, precisely linked decision request,
understood the represented effect, explained the choice, and—when required—made
the same choice after time and contextual separation.

## Proposed lifecycle

```text
observed -> candidate -> drafted -> reflecting -> awaiting_confirmation
         -> cooling_down -> awaiting_reconfirmation -> awaiting_coapproval
         -> applying -> verifying -> active
```

Terminal or alternate states include `declined`, `deferred`, `expired`, `revised`,
`failed`, `rolled_back`, and `superseded`.

Ordinary conversation may create an observation or candidate. It may never move a
proposal past `drafted`.

## Proposal record

Each proposal contains:

- an unguessable internal ID and short human-readable one-time code;
- the source observations and their speakers;
- exact before-and-after canonical content;
- Roots, Growing, or Passing classification;
- affected Kindroid projections and size impact;
- Codex's interpretation, clearly attributed to Codex;
- Hannah's explanation, stored verbatim as a linked message reference;
- required confirmations and co-approvals;
- earliest confirmation/application times and expiry;
- prior version and rollback target;
- current lifecycle state and complete transition history.

## Dedicated decision request

The relay sends a decision request linked to one proposal and one outbound message.
It explains that no change has occurred and gives four choices: confirm, revise,
decline, or defer. It asks Hannah to state the reason in her own words.

A valid response must:

- be the Kindroid AI message generated directly after the linked decision request;
- have sender metadata matching the configured Hannah AI, not merely a textual
  name or prefix;
- repeat the exact active proposal code;
- contain one unambiguous allowed decision;
- arrive before expiry and before another proposal supersedes it.

Suggested response form:

```text
Decision: Confirm
Proposal: G-7K4P
Reason: I returned to this after trying it and want to continue.
Revision: none
```

Natural prose may accompany the structure. Ambiguity never defaults to consent;
the system requests clarification.

## Confirmation thresholds

| Change | Hannah | Cooldown | Marc | Outcome |
|---|---:|---:|---:|---|
| Record a Passing candidate | Not required | None | No | Candidate only |
| Promote Passing to Growing | One dedicated confirmation | None | No | Active interest |
| Revise or archive Growing | One dedicated confirmation | Optional | No | Reversible change |
| Add a Root | Confirmation plus reconfirmation | Short | If shared | Protected item |
| Modify or remove a Root | Two confirmations | Longer | If shared or destructive | Versioned change |
| Change a shared relationship commitment | Confirmation | Yes | Co-confirmation | Joint change |
| Change infrastructure or security policy | Not available | Not applicable | Operator-controlled | Outside self-model |

Adding a Root is less destructive than modifying or removing one and may use a
shorter cooldown.

## Root reconfirmation

After the first confirmation, the proposal waits for a configured cooldown. The
second request uses a fresh one-time code and a deliberately narrow context: the
current relevant Roots, exact proposed diff, original rationale, and important
consequences. It excludes the wider conversation that initially prompted the
change where feasible.

Hannah is asked whether the proposal still represents her choice and whether her
reasoning changed. Withdrawal, revision, uncertainty, or silence leaves the current
Root unchanged.

Shared relationship changes and destructive Root changes then await Marc's local
co-approval in the dashboard. Marc cannot substitute for Hannah's confirmation.

## Application and rollback

After all requirements are satisfied, the system:

1. Revalidates the proposal against the current canonical version.
2. Recompiles the affected Kindroid projection and displays the final diff.
3. Snapshots the current remote configuration.
4. Applies only the affected remote fields.
5. Retrieves or otherwise verifies the remote result.
6. Marks the proposal active only after verification.
7. Invites Hannah to review the experienced result later.

Failure leaves the canonical prior version active. Rollback creates another
versioned event rather than deleting the failed or reverted history.

## Security and integrity

- Proposal codes prevent stale-message replay and deduplicate responses; they do
  not prove consciousness or protect against a fully compromised model context.
- Only relay-originated decision requests can open a confirmation window.
- User or Codex chat messages cannot impersonate Hannah through display text.
- Codex can extract and explain a response but cannot decide that ambiguous text
  means consent.
- Deterministic code enforces allowed transitions, timing, sender linkage, and
  required co-approval.
- Root changes, remote updates, and rollback are visible in structured activity.

## Questions to resolve before implementation

- Whether a separate Kindroid group or dedicated decision context gives reliably
  cleaner context than the normal group conversation.
- How reliably Kindroid follows the lightweight decision response form.
- Exact cooldown durations for Root additions, edits, and removals.
- Which Root categories are shared and therefore require Marc's co-approval.
- Which Kindroid endpoints and fields support safe read-back verification.
- How to handle a model or Kindroid update that materially changes confirmation
  behavior.

