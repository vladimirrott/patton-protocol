# Mission Contract

Patton Prime gives each worker one bounded mission. The mission states the work
boundary, the resources the worker may use, and the proof the worker must return.
Patton Prime keeps the mission record with its report so another worker can
resume or verify the work.

## Required fields

### Mission identity

Every mission includes `mission_id`, `worker_role`, and `source_revision`.
`mission_id` identifies the bounded unit of work, `worker_role` records the
assigned function, and `source_revision` identifies the input state. A worker
copies these values into its report.

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

## Example mission

The envelope below shows the shape of a mission. Hosts may carry it in another
transport while preserving these fields and meanings.

```yaml
mission_id: "mission-042"
worker_role: "verifier"
source_revision: "abc123"
objective: "Identify the source of the failing validation case"
inputs:
  - "failing test output"
allowed_paths:
  - "src/validation/"
  - "tests/"
stopping_condition: "Return a cause with evidence or report blocked"
budget: "20 command runs"
timeout: "10 minutes"
retry_limit: 1
evidence:
  - "file and line reference"
  - "reproduction or test result"
```

The worker reports a result against this mission. A lead may revise the next
mission after verification finds new facts.
