---
name: patton-protocol
description: Coordinate bounded coding-agent missions when independent delegation improves the task.
---

# Patton Protocol

Patton Protocol is a portable Agent Skills package for coordinating bounded
coding-agent missions. It targets any host that implements the Agent Skills
`SKILL.md` format. The core names roles, contracts, limits, and evidence rules;
the host decides how it starts workers.

Patton Prime owns the lifecycle, the mission ledger, and the final decision.
Prime delegates only when a delegation threshold is met: the work contains at
least two independent bounded missions, each mission has a useful stopping
condition, and coordination costs less than serial execution. Prime keeps one
mission in the main loop when that threshold is not met.

The mission and report contracts define the data exchanged across each
lifecycle stage: [mission contract](references/mission-contract.md), [worker
report](references/worker-report.md), and [safety and budgets](references/safety-and-budgets.md).
When a host cannot spawn workers, Patton Prime uses serial fallback and keeps
the same contracts, evidence checks, and human approval gates.

## Roles

- **Patton Prime** scopes the objective, creates missions, assigns roles,
  enforces limits, reconciles reports, requests verification, and owns closure.
  Prime never treats a worker report as approval.
- **Scout** investigates repository state, dependencies, constraints, and
  likely causes. Scout returns findings and evidence without changing files.
- **Builder** changes files inside one immutable ownership boundary and returns
  the change plus reproducible evidence.
- **Verifier** independently rechecks claims against the recorded source
  revision, runs the required safe checks, and reports pass, failure, or open
  risk. Verifier does not verify its own change.
- **Quartermaster** tracks missions, revisions, budgets, timeouts, retries,
  changed paths, reports, and handoffs. Quartermaster preserves partial failure
  records for Prime.

Prime may combine Scout and Quartermaster roles in a serial mission when the
host has no worker primitive. Serial fallback prohibits one actor from serving
as both Builder and Verifier. Prime assigns a distinct Verifier identity, or a
human performs verification. Without either, the candidate stays unverified
and the mission stays `blocked`.

## Limits

Prime records these limits before dispatch and may lower them for a mission:

- worker count: at most 4 active workers, excluding Patton Prime;
- mission budget: at most 20 command units or 10 000 generated tokens per
  mission, with the selected unit recorded in the mission;
- timeout: at most 15 minutes of wall-clock time per mission;
- retries: at most 2 retries after transient failure, against the same mission
  boundary;
- mutable-file overlap: 0 paths across concurrent missions; a shared path
  requires serial execution and an explicit ownership handoff;
- approvals: 1 explicit human approval for each irreversible action; automated
  host-only approval does not satisfy the gate;
- unresolved disagreement: 0 disagreements at closure. Prime opens a bounded
  verifier mission and then asks for human approval when reports still differ.

The [safety and budgets reference](references/safety-and-budgets.md) defines
the controls, exceptions, and recovery record for these limits.

## Lifecycle

Patton Prime runs the outer lifecycle as mission-family phases. The outer
phases map to separate per-mission cycles in this canonical order:
`Scope -> Plan Builder -> Dispatch Builder -> Observe Builder report -> Prime
reconcile Builder and lock candidate -> Plan Verifier -> Dispatch Verifier ->
Verify Verifier checks -> Observe Verifier live checks and monitor timeout and
budget -> Verifier report -> Prime observe and reconcile Verifier -> Final Report`.
Prime owns reconciliation at both handoffs. A host may repeat a bounded
mission cycle after retry or recovery, while preserving the source revision and
report history.

The Builder and Verifier cycles are separate mission cycles. Prime plans,
dispatches, and observes Builder mission. The Builder mission ends with the
Builder terminal report. Prime reconciles Builder report, then Prime locks
candidate identity. Prime plans, dispatches, and observes Verifier mission as a
separate mission cycle. The Verify phase runs the Verifier checks. Observe
Verifier live checks and monitor timeout and budget before Verifier report.
Verifier returns report, then Prime observes and reconciles Verifier report.
Prime owns reconcile in both cycles. Verifier never reconciles reports, and
the Builder report never serves as the Verifier report.

### Scope

Prime states one objective, identifies the source revision, records inputs and
risks, and selects the smallest useful set of roles. Prime applies the
delegation threshold before creating parallel work. Prime marks irreversible
actions for a human approval gate before dispatch.

### Plan

Prime converts the objective into bounded missions. Each mission uses the
[mission contract](references/mission-contract.md), names an allowed path
allowlist, sets a stopping condition, records budget, timeout, retry limit,
and evidence requirements, and assigns one worker role. Prime gives each
concurrent mission an immutable ownership boundary. Workers may inspect other
paths as read-only. An out-of-boundary Builder mutation blocks the mission.
Prime never widens `allowed_paths` in place. Prime creates a new mission with a
new identity and records the prior mission as a handoff before assigning a new
boundary. After a Builder mutation, Prime records immutable
`candidate_revision` and `content_digest` values for the exact candidate.
Prime computes those values from the candidate-relevant repository snapshot,
which remains separate from the Builder's `allowed_paths` mutation allowlist.
The manifest uses tracked files plus explicit `candidate_untracked_paths` and
excludes untracked files unless the explicit manifest selects them and the
source-control ignore state permits them. Behavior-affecting files outside the allowlist still
participate in candidate verification. Prime records
`candidate_tracked_paths` and `candidate_untracked_paths` from the
post-mutation state of the candidate: tracked additions enter, tracked deletions leave,
and a tracked generated file remains included when source control records it.
Prime locks this membership with the candidate identity. Canonical ignore input
comes from repository-controlled ignore rules only. Clone-local rules and
user-global rules do not affect the candidate manifest. An unlisted generated
untracked addition stays outside the locked manifest. A post-lock mutation to
locked manifest membership, bytes, or executable mode makes the evidence stale
and reopens the mission. A locked membership change after the lock has the same
effect; unlisted generated additions do not count as locked membership.

Prime locks the candidate identity after the mutation. A post-build mutation
changes the candidate and invalidates prior evidence. Prime rejects stale
evidence and opens a new bounded mission instead of changing the recorded
identity in place.

### Dispatch

Prime records the mission ledger and source revision before dispatch. Prime
starts no more than the worker-count limit, rejects overlapping mutable paths,
and passes each worker its own mission contract. A host with no worker spawn
capability uses serial fallback in mission order. Serial fallback does not
remove verification or approval requirements.

### Observe

Quartermaster records starts, progress, commands, changed paths, budget use,
and timeout or host-loss events. Prime stops a mission at its timeout or budget
limit. Prime preserves any report and changes from a partial failure, labels
the evidence as unverified, and prevents a failed worker from approving or
promoting its own result.

### Reconcile

Prime matches each [worker report](references/worker-report.md) to its mission
identity and checks that files changed stay within the immutable ownership
boundary. Prime rechecks every material evidence claim against the source
revision, candidate identity, report data, and safe command output, including
behavior-affecting files outside the mutation allowlist. Prime
requires Prime's and the Verifier's evidence to match the immutable
`candidate_revision` and `content_digest`. Prime rejects stale or mismatched
evidence. Prime compares conflicting
reports by evidence and requests an independent Verifier check. Prime does not
resolve a conflict by majority vote or discard a minority finding.

### Verify

Verifier repeats the required checks from a clean or explicitly recorded state,
inspects the claimed files, and checks that the objective and stopping
condition hold. Verifier confirms that `candidate_revision` and
`content_digest` still match before and after those checks. A mismatch means
post-build mutation, so Verifier reports the candidate unverified. Prime sends
partial or failed work to a bounded recovery
mission, which may resume from preserved evidence or revert its own in-boundary
changes under the host's normal recovery mechanism. Prime retries transient
failures within the retry limit and creates a new mission when the objective or
allowed paths change. Prime keeps the outcome blocked when evidence remains
insufficient.

Explicit human approval remains required before a merge, deployment, deletion,
publication, credential change, or other irreversible action. Prime records the
human approver, decision, scope, and evidence. An automated host-only decision
does not grant approval. A worker, Verifier, or Quartermaster cannot grant that
approval.

### Report

Prime reports the final status, mission identities, files changed, checks run,
verified evidence, risks, approvals, unresolved disagreements, and next
action. Prime reports `blocked` when a required approval is denied or evidence
cannot resolve a disagreement. Prime retains partial and failed reports so a
later mission can resume without inventing evidence.

## Contracts

Workers use the [mission contract](references/mission-contract.md) for scope,
inputs, limits, and evidence requirements. They return a [worker
report](references/worker-report.md) with status, evidence, risks, and a next
action. Hosts may serialize these records through another interface while
preserving their fields and meanings.
