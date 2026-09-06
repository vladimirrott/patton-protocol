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

## Candidate snapshot and digest

Prime computes the candidate-relevant repository snapshot after the Builder
stops mutating files and confirms the allowlist. The candidate manifest contains
tracked files from the source-control state at `source_revision` plus files
Prime explicitly names in `candidate_untracked_paths`, the candidate untracked
paths list. Ignored/generated
outputs, including ordinary test artifacts such as `__pycache__/`, stay outside
the manifest. Prime rejects an explicit candidate path that names an ignored
or generated output. The manifest excludes `.git/` and the Patton mission
ledger (`.patton/ledger/`). Prime rejects symlinks in the manifest instead of
following them. The manifest scope and the `allowed_paths` mutation boundary
serve separate controls: a behavior-affecting file outside `allowed_paths` may
still invalidate the candidate digest, while a worker may not mutate it. A
behavior-affecting file outside the mutation allowlist therefore remains in
the candidate-relevant snapshot.

Prime normalizes each relative path to `/` separators and sorts paths by their
lossless path tokens. A path token percent-encodes each raw path byte as ASCII,
leaving only unreserved ASCII bytes and `/` separators. Hosts with Unicode-only
path APIs encode Unicode scalar values as UTF-8 before percent-encoding; hosts
with raw filename bytes preserve those bytes, including non-UTF-8 names. For
each sorted file, Prime appends this record to the digest input:

```text
lossless path token + NUL + ASCII executable flag + NUL + ASCII byte length
+ NUL + exact file bytes + LF
```

Prime serializes portable executable semantics as one ASCII executable flag, `0`
or `1`. For a
tracked file, `1` means the source-control entry marks the file executable. For
an explicit untracked file, Prime records the flag in its manifest entry. Hosts
derive the flag from repository metadata or explicit candidate metadata, not
from POSIX mode bits, Windows ACLs, timestamps, ownership, or another local
permission model. This keeps the manifest platform-neutral.

Prime computes `content_digest` as `sha256:` followed by the lowercase
64-hex-digit SHA-256 digest of the complete record stream. The path, executable
flag, byte length, and exact bytes define the content; line endings remain
unchanged. Prime excludes timestamps, ownership, and other host-local metadata.
The empty snapshot hashes the empty record stream. Prime obtains
`candidate_revision` from the host's post-mutation revision. If the host has no
revision, Prime uses `candidate:sha256:<digest>` as a deterministic fallback.

Prime, not the Builder, computes and locks both candidate fields. The Builder
may report observations, but cannot choose or rewrite the identity. The
Verifier recomputes the snapshot from current content before checks and again
after checks. Prime and Verifier require both recomputed values to match the
locked ledger values. A mismatch, including a mutation outside `allowed_paths`,
means post-build mutation or stale evidence, so Prime blocks the candidate and
opens a new bounded mission.

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
