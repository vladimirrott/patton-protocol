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
unless Patton Prime expands the mission.

### Candidate untracked paths

`candidate_untracked_paths` lists untracked files that Prime explicitly includes
in the candidate manifest. Prime gets tracked files from the source-control
state at `source_revision`; ignored and generated outputs stay outside the
manifest. Each explicit untracked entry records its path and a portable
executable flag. Prime rejects an entry that names an ignored or generated
output.

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
values, and the Verifier must recompute and match both values before and after
verification. A post-build mutation makes the recorded identity stale. Prime
rejects stale evidence, marks the candidate unverified, and opens a bounded
mission for a new candidate identity.

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
candidate_untracked_paths: []
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

The worker reports a result against this mission. A lead may revise the next
mission after verification finds new facts.
