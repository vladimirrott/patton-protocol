# Safety and Budgets

Patton Prime applies these controls to every mission. Prime records the chosen
values in the mission ledger before dispatch. A host may impose stricter
limits, and Prime may lower a limit for a risky objective.

## Default limits

| Limit | Default ceiling | Control |
| --- | --- | --- |
| Worker count | 4 active workers, excluding Prime | Prime starts no additional worker after the ceiling. |
| Mission budget | 20 command units or 10 000 generated tokens | Quartermaster records the selected unit and stops work at the ceiling. |
| Timeout | 15 minutes per mission | Prime records the timeout and classifies the report by usable evidence. |
| Retries | 2 retries after transient failure | A retry keeps the same objective, inputs, and allowed paths. |
| Mutable-file overlap | 0 paths for concurrent missions | Prime serializes a shared path and records an ownership handoff. |
| Approvals | 1 explicit human approval per irreversible action | Prime records the human approver, scope, decision, and evidence before action. Automated host-only approval does not satisfy the gate. |
| Unresolved disagreement | 0 at closure | Prime runs an independent verification mission, then asks a human to decide if disagreement remains. |

Prime counts a worker's retry as another attempt within the worker-count and
mission ledger. Prime never increases a limit to force a mission through a
timeout, budget ceiling, ownership collision, denied approval, or disagreement.

## Delegation threshold

Prime delegates when the objective splits into at least two independent
missions with separate mutable paths, useful stopping conditions, and evidence
that a Verifier can check. Prime runs one mission in serial order when the
split creates overlap, when the host cannot spawn workers, or when coordination
cost exceeds the expected gain. Serial fallback preserves the same limits,
contracts, reports, rechecks, and approvals.

Serial fallback prohibits a Builder from serving as its own Verifier. Prime
assigns a distinct Verifier `actor_id`. If the host cannot provide one, Prime
asks a human to verify. A same-actor Builder and Verifier result stays
unverified and the mission stays `blocked`.

## Immutable ownership

Prime assigns each Builder an immutable mutable-path allowlist. The Builder may
create, edit, or delete only those paths. Workers may inspect other paths as
read-only. A path counts as overlapping when two active missions can mutate it,
including a directory and a file beneath that directory. Prime pauses one
mission, records the reason, and performs an explicit handoff before allowing
shared-path work. A worker cannot widen its own allowlist.

## Evidence rechecking

Prime treats a worker report as a lead until independent verification confirms
it. After mutation, Prime locks the candidate's `candidate_revision` and
`content_digest` in the mission ledger. Rechecking must identify the source
revision, inspect claimed files, repeat the relevant safe command or
observation, and compare the result with the stopping condition. Builder and
Verifier evidence must match both immutable candidate values. Prime and
Verifier each compare their evidence with the ledger values. A post-build
mutation changes the candidate, so Prime rejects stale, missing, or
unverifiable evidence and keeps the mission open.

## Conflicts and disagreement

Prime preserves conflicting reports with their mission identities and source
revisions. Prime asks a Verifier with no ownership of the disputed change to
test the competing claims. Prime selects the claim supported by reproducible
evidence and records why the other claim failed. If the evidence remains
inconclusive, Prime records unresolved disagreement and requests human
approval for the decision. Prime does not use a worker vote or automated
host-only decision as an approval.

## Partial-failure recovery

Prime preserves the report, command record, evidence, and changed-path list
when a worker times out, loses its host, or fails after making changes.
Quartermaster marks those changes unverified and checks the ownership boundary.
Prime may retry a transient failure within the retry limit. Prime opens a new
recovery mission when the objective, source revision, or allowed paths change.
The recovery mission names whether it will verify, continue, or revert the
preserved changes. Prime reports `partial`, `blocked`, or `failed` according to
the worker-report contract instead of claiming completion.

## Irreversible actions

Workers may prepare evidence for a merge, deployment, deletion, publication,
credential change, or other irreversible action. Prime pauses before the action
and requests one explicit human approval tied to the exact scope and verified
evidence. A denied, expired, or missing approval leaves the mission `blocked`.
An automated host-only approval also leaves the mission `blocked`. A valid
approval records a human approver identity, the decision `approved`, exact
scope, and the candidate evidence. Workers cannot approve their own work, and
a Verifier cannot convert an evidence lead into approval.
