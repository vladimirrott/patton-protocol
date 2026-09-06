# Mission Contract

Patton Prime gives each worker one bounded mission. The mission states the work
boundary, the resources the worker may use, and the proof the worker must return.
Patton Prime keeps the mission record with its report so another worker can
resume or verify the work.

## Required fields

### Objective

State one result the worker must produce. Use an observable outcome, such as a
file change, a test result, or an investigation finding.

### Inputs

List the facts, files, decisions, and prior reports the worker may use. Include
the version or revision when that context affects the result.

### Allowed paths

List the files and directories the worker may read or change. The worker treats
paths outside this list as read-only unless Patton Prime expands the mission.

### Stopping condition

Name the condition that ends the mission. The worker stops after producing the
objective and required evidence, or reports `blocked` when the condition cannot
be met within the boundary.

### Budget

Set a limit for the worker's time, tokens, commands, or other host resource.
Patton Prime records the unit and the limit before dispatch.

### Timeout

Set the maximum wall-clock duration. A timeout produces a partial report with
the evidence collected before the host ended the mission.

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
objective: "Identify the source of the failing validation case"
inputs:
  - "repository revision: abc123"
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
