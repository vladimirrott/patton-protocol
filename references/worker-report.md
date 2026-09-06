# Worker Report Contract

Each worker returns one report for one mission. Patton Prime stores the report
with the mission identity and preserves it when the worker times out, fails, or
loses its host.

## Required fields

### Mission identity

`mission_identity` identifies the mission, worker role, and source revision. A
report without this identity cannot be reconciled with other reports.

### Status

`status` uses one of these values:

- `completed`: the worker met the objective and supplied the required evidence.
- `partial`: the worker supplied useful results while the objective remains open.
- `blocked`: a stated constraint prevented the worker from completing the objective.
- `failed`: the worker attempted the mission and produced no usable result.

Workers use the status that matches the evidence. They do not invent a success
status to satisfy a deadline.

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

## YAML-shaped report envelope

This envelope gives hosts a portable shape. A host may serialize the same data
through another interface, but the field meanings and status values stay fixed.

```yaml
mission_identity:
  mission_id: "mission-042"
  worker_role: "verifier"
  source_revision: "abc123"
status: completed
files_changed: []
commands_run:
  - command: "validation check"
    outcome: "pass"
evidence:
  - source: "tests/validation.txt"
    finding: "The candidate reproduces the expected result"
risks: []
next_action: "Patton Prime may reconcile this report"
```

Workers return evidence and leads. A lead is a reasoned direction for the next
step, grounded in the recorded evidence. Patton Prime or a designated human
approval gate makes approval decisions. A worker report does not approve a
merge, deployment, deletion, publication, or other irreversible action.

## Serial fallback

When a host cannot spawn workers, Patton Prime executes each bounded mission in
serial order. The main loop uses the same mission fields, report envelope,
status values, and verification step. Serial execution changes scheduling and
keeps the contract intact.
