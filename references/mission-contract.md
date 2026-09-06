# Mission Contract

Patton Prime gives each worker one bounded mission. The mission states the work
boundary, the resources the worker may use, and the proof the worker must return.
Patton Prime keeps the mission record with its report so another worker can
resume or verify the work.

## Required fields

### Mission identity

Every mission includes `mission_id`, `worker_role`, `actor_id`, and
`source_revision`. `mission_id` identifies the bounded unit of work,
`worker_role` records the assigned function, `actor_id` identifies the actor,
and `source_revision` identifies the input state. A worker copies these values
into its report. Prime uses `actor_id` to prevent one actor from claiming both
Builder and Verifier independence in serial fallback.

### Objective

State one result the worker must produce. Use an observable outcome, such as a
file change, a test result, or an investigation finding.

### Inputs

List the facts, files, decisions, and prior reports the worker may use. Include
the version or revision when that context affects the result.

### Allowed paths

Treat `allowed_paths` as the mutation allowlist. List the files and directories
the worker may create, edit, or delete. The worker may inspect supplied inputs
and other paths as read-only, but it may not mutate a path outside this list
or widen the list. An out-of-boundary mutation blocks the mission. Prime never
expands `allowed_paths` in place: Prime creates a new mission with a new
identity, records the prior mission as a handoff, and gives the new Builder its
own immutable boundary.

### Phase-specific envelopes

A pre-candidate Builder mission may omit `candidate_revision`,
`content_digest`, `candidate_tracked_paths`, `candidate_untracked_paths`, and
`non_candidate_untracked_paths`, or carry them as nullable `null` values. The Builder does not author candidate
identity. The Builder terminal report carries these fields as omitted or null.
After the Builder stops mutating, Prime authors and locks a candidate record
before the Verifier mission starts. That record has non-null revision, digest,
and complete candidate and non-candidate manifest membership values. Prime includes it in the
Verifier mission. The Verifier authors a report that echoes the locked record.

### Candidate tracked paths

`candidate_tracked_paths` records the tracked manifest membership after
mutation. Prime records this post-mutation set before it locks the candidate
identity. Prime includes tracked additions and omits tracked deletions. A
post-lock mutation to locked manifest membership, bytes, or executable mode
makes the candidate stale. An unlisted generated untracked addition stays
outside the locked manifest and does not make the candidate stale.

### Candidate untracked paths

`candidate_untracked_paths` lists untracked files that Prime explicitly includes
in the candidate manifest. Prime gets `candidate_tracked_paths` from the
post-mutation source-control state, so a tracked deletion removes a path and a
tracked addition includes a path. Ignored and generated outputs stay outside
the manifest when they are untracked and not explicitly listed. Prime uses
source-control ignore state to reject an explicit untracked entry marked
ignored. An explicit non-ignored entry is included regardless of its filename.
A generated file remains included when source control records it as tracked. Each
explicit untracked entry has a `path` field of type lossless token
and an `executable` field of type integer `0 | 1`. Prime rejects an entry that
names an ignored output.

The candidate entry schema is `path: lossless token` and `executable: 0 | 1`.

Prime classifies every non-ignored untracked path that can affect the objective
or verification. Prime includes a behavior-affecting path in
`candidate_untracked_paths`, or records an explicit non-candidate output
classification in `non_candidate_untracked_paths` in the mission ledger. Prime
discovers and records the complete post-mutation non-ignored untracked set.
Every discovered path appears exactly once, in either the candidate list or the
non-candidate list. Silent omission blocks candidate lock.

Canonical ignore input consists of repository-controlled ignore rules only.
Clone-local rules and user-global rules do not affect the candidate manifest.
Hosts that cannot isolate ambient rules must use the explicit manifest and
record the repository-controlled ignore result.

### Non-candidate untracked paths

`non_candidate_untracked_paths` is a sorted list of unique scalar lossless path
tokens for explicitly classified non-candidate outputs. The non-candidate
untracked paths list and the candidate list partition the complete discovered
non-ignored untracked set, with no overlap or omission.

### Stopping condition

Name the condition that ends the mission. The worker stops after producing the
objective and required evidence, or reports `blocked` when the condition cannot
be met within the boundary.

### Budget

Set a limit for the worker's time, tokens, commands, or other host resource.
Patton Prime records the unit and the limit before dispatch.

### Timeout

Set the maximum wall-clock duration. A timeout produces a partial report with
usable evidence collected before the host ended the mission. A timeout with no
usable evidence produces a failed report.

### Retry limit

Set the maximum number of retries after a transient failure. A retry repeats the
same mission boundary. Patton Prime creates a new mission when the objective or
allowed paths change.

### Evidence

Specify the proof the worker must return. Evidence can include test output,
inspection results, file paths, or a reasoned finding tied to an input.

### Candidate identity

The candidate revision and content digest identify the post-mutation state.
After a Builder finishes a mutation, Prime records `candidate_revision` and
`content_digest` for the exact candidate state. Prime locks both values for that
candidate. Prime computes the digest from the canonical snapshot in the
[safety and budgets reference](safety-and-budgets.md). The Builder may report
observations but cannot choose these values. The report echoes the locked
values. The Verifier mission and report echo them, and the Verifier must
recompute and match both values before and after verification. A post-build
mutation makes the recorded identity stale. Prime
rejects stale evidence, marks the candidate unverified, and opens a bounded
mission for a new candidate identity.

The candidate snapshot rejects source-control gitlinks, including submodule
entries. Gitlink object IDs are not serialized and the linked repository is not
followed.

The [outer lifecycle and canonical phase sequence](../SKILL.md#lifecycle) govern
this contract. This reference defines the mission fields, candidate rules, and
phase-specific envelope requirements used within that sequence.

## Example mission

The envelope below shows the shape of a mission. Hosts may carry it in another
transport while preserving these fields and meanings.

```yaml
mission_id: "mission-042"
worker_role: "verifier"
actor_id: "actor-verifier-07"
source_revision: "abc123"
objective: "Verify the candidate validation result against the stopping condition"
inputs:
  - "failing test output"
allowed_paths:
  - "src/validation/"
  - "tests/"
candidate_tracked_paths: []
candidate_untracked_paths: []
non_candidate_untracked_paths: []
stopping_condition: "Return a verified result with evidence or report blocked"
budget: "20 command runs"
timeout: "10 minutes"
retry_limit: 1
evidence:
  - "candidate manifest and digest"
  - "reproduction or test result"
candidate_revision: "candidate:sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
content_digest: "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
```

Non-empty `candidate_untracked_paths` entries use this shape:

```yaml
path: "config/local.ini"
executable: 0
```

The nullable pre-candidate Builder shape is:

```yaml
candidate_revision: null
content_digest: null
candidate_tracked_paths: null
candidate_untracked_paths: null
non_candidate_untracked_paths: null
```

The worker reports a result against this mission. A lead may revise the next
mission after verification finds new facts.
