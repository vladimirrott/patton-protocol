# Worker Report Contract

Each worker returns one report for one mission. Patton Prime stores the report
with the mission identity and preserves it when the worker times out, fails, or
loses its host.

## Required fields

### Mission identity

`mission_identity` identifies the mission, worker role, actor, and source
revision. A report without this identity cannot be reconciled with other
reports. `actor_id` identifies the actor that produced the report. In serial
fallback, Prime rejects a report when the Builder and Verifier use the same
actor identity.

### Status

`status` uses one of these values:

- `completed`: the worker met the objective and supplied the required evidence.
- `partial`: the worker supplied useful results while the objective remains open.
- `blocked`: a stated constraint prevented the worker from completing the objective.
- `failed`: the worker attempted the mission and produced no usable result.

Workers use the status that matches the evidence. They do not invent a success
status to satisfy a deadline.

Patton Prime maps a timeout with usable evidence to `partial`; a timeout with no
usable evidence maps to `failed`.

### Files changed

`files_changed` lists paths the worker changed. An empty list records that the
worker made no file changes.

### Commands run

`commands_run` records each command or check the worker used, with its outcome.
The worker omits secrets and records enough context for a verifier to repeat a
safe check.

### Evidence

`evidence` contains observations that support the status. Each item names its
source, such as a file path, test result, or inspection finding.

### Risks

`risks` records unresolved defects, assumptions, boundary concerns, and effects
that a verifier or lead must consider. An empty list means the worker found no
additional risk within its mission.

### Next action

`next_action` gives Patton Prime a concrete follow-up. It can request
verification, open a bounded mission, resolve a missing input, or close the
objective.

### Phase-specific envelopes

A pre-candidate Builder report may omit `candidate_revision`, `content_digest`,
`candidate_tracked_paths`, and `candidate_untracked_paths`, or carry them as
nullable `null` values. The Builder reports its work and evidence, but does not
author candidate identity. The Builder terminal report carries these fields as
omitted or null. Prime authors and locks a candidate record before the Verifier
mission starts, then
includes it in the Verifier mission. The Verifier authors a report that echoes
the locked revision, digest, and manifest membership.

### Candidate identity

The candidate revision and content digest identify the post-mutation state.
`candidate_revision` and `content_digest` identify the exact post-mutation
candidate that the report describes. A Builder report carries no candidate
identity. Prime authors and locks the record after the Builder terminal report.
The Verifier recomputes the canonical snapshot from
current content and checks that both values match the ledger before and after
its checks. Prime rejects stale evidence when a post-build mutation changes
either value or when the report does not match the candidate.

### Candidate tracked paths and candidate untracked paths

`candidate_tracked_paths` and `candidate_untracked_paths` echo the manifest
membership Prime recorded after mutation. The Verifier compares both sets with
the current candidate before and after checks. A post-lock mutation to locked
manifest membership, bytes, or executable mode makes the report stale. An
unlisted generated untracked addition stays outside the locked manifest and
does not make the report stale. A selected path addition or deletion, or an
unapproved change to locked manifest state, leaves the mission `blocked`.

The [outer lifecycle and canonical phase sequence](../SKILL.md#lifecycle) govern
this report. Within its local mission rules, Prime plans, dispatches, and
observes the Builder mission; the Builder returns a terminal report; Prime
reconciles the Builder report and locks the candidate; Prime plans, dispatches,
and observes the Verifier mission; the Verify phase runs the Verifier checks.
Live Observe checks and records timeout and budget before the Verifier returns a
report. Prime observes and reconciles the Verifier report. Prime owns reconcile
in both cycles. The Verifier never reconciles reports.

## YAML-shaped report envelope

This envelope gives hosts a portable shape. A host may serialize the same data
through another interface, but the field meanings and status values stay fixed.

```yaml
mission_identity:
  mission_id: "mission-042"
  worker_role: "verifier"
  actor_id: "actor-verifier-07"
  source_revision: "abc123"
candidate_revision: "candidate:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
content_digest: "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
candidate_tracked_paths: []
candidate_untracked_paths: []
status: completed
files_changed: []
commands_run:
  - command: "validation check"
    outcome: "pass"
evidence:
  - source: "tests/validation.txt"
    finding: "The candidate matches the validation requirement"
risks: []
next_action: "Patton Prime may reconcile this report"
```

The nullable pre-candidate Builder report shape is:

```yaml
candidate_revision: null
content_digest: null
candidate_tracked_paths: null
candidate_untracked_paths: null
```

Workers return evidence and leads. A lead is a reasoned direction for the next
step, grounded in the recorded evidence. Only an explicit human approval gate
makes approval decisions. A worker report does not approve a merge,
deployment, deletion, publication, or other irreversible action.

## Serial fallback

When a host cannot spawn workers, Patton Prime executes each bounded mission in
serial order. Prime assigns the Builder and Verifier distinct `actor_id`
values. If the host cannot provide a distinct Verifier actor, Prime requires a
human to perform verification. A Builder that also acts as Verifier leaves the
candidate unverified and the mission `blocked`. The main loop uses the same
mission fields, report envelope, candidate identity, status values, and
verification step.
