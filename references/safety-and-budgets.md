# Safety and Budgets

Patton Prime applies these controls to every mission. Prime records the chosen
values in the mission ledger before dispatch. A host may impose stricter
limits, and Prime may lower a limit for a risky objective.

## Default limits

| Limit | Default ceiling | Control |
| --- | --- | --- |
| Worker count | 4 active workers, excluding Prime | Prime starts no additional worker after the ceiling. |
| Mission budget | 20 command units or 10 000 generated tokens | Quartermaster records usage and signals the budget ceiling; Prime solely stops the mission. |
| Timeout | 15 minutes per mission | Quartermaster records elapsed time and signals the timeout ceiling; Prime solely stops the mission and classifies the report by usable evidence. |
| Retries | 2 retries after transient failure | A retry keeps the same objective, inputs, and allowed paths. |
| Mutable-file overlap | 0 paths for concurrent missions | Prime serializes a shared path and records an ownership handoff. |
| Approvals | 1 explicit human approval per irreversible action | Prime records the human approver, scope, decision, and evidence before action. Automated host-only approval does not satisfy the gate. |
| Unresolved disagreement | 0 at closure | Prime runs an independent verification mission, then asks the human to choose recovery, risk acceptance, or abandonment if disagreement remains; the candidate stays blocked and the irreversible gate stays closed. |

Prime counts a worker's retry as another attempt within the worker-count and
mission ledger. Prime never increases a limit to force a mission through a
timeout, budget ceiling, ownership collision, denied approval, or disagreement.

Live Observe wraps each Verifier check and runs concurrently with it. The
observer records progress and signals timeout or budget before the Verifier
report. Prime alone stops the mission on either signal; a long-running check
cannot emit a terminal report after Prime stops it.

## Delegation threshold

Prime delegates when the objective splits into at least two independent
missions with separate mutable paths, useful stopping conditions, and evidence
that a Verifier can check. Prime runs one mission in serial order when the
split creates overlap, when the host cannot spawn workers, or when coordination
cost exceeds the expected gain. Serial fallback preserves the same limits,
contracts, reports, rechecks, and approvals.

The delegation threshold applies to optional parallel work. The mandatory
independent read-only Verifier is exempt from that threshold and remains
required after one Builder. These decisions apply:

| Work shape | Decision |
| --- | --- |
| One Builder + mandatory independent read-only Verifier | Run the required Builder and Verifier missions, even when optional delegation is not selected. |
| Parallel Builders with independent missions and separate mutable paths | Delegate in parallel when the threshold and coordination-cost check pass. |
| Parallel Builders with overlapping mutable paths | Serialize the missions and record an ownership handoff. |
| No-spawn host | Use serial fallback while preserving the mandatory independent Verifier. |

Serial fallback prohibits a Builder from serving as its own Verifier. Prime
assigns a distinct Verifier `actor_id`. If the host cannot provide one, Prime
asks a human to verify. A same-actor Builder and Verifier result stays
unverified and the mission stays `blocked`.

For a no-spawn host, the Verify step requires a host-enforced deadline or
command timeout while Observe remains active. If the host cannot enforce the
bound safely, the mission remains `blocked`; Prime does not accept the check as
verification, and no late terminal report is accepted.

## Immutable ownership

Prime assigns each Builder an immutable mutable-path allowlist. The Builder may
create, edit, or delete only those paths. Workers may inspect other paths as
read-only. A path counts as overlapping when two active missions can mutate it,
including a directory and a file beneath that directory. Prime pauses one
mission, records the reason, and performs an explicit handoff before allowing
shared-path work. An out-of-boundary Builder mutation blocks the mission. Prime
never widens an allowlist in place: Prime creates a new mission with a new
identity, records the prior mission as a handoff, and assigns the new Builder
an immutable boundary. A worker cannot widen its own allowlist.

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
the tracked paths Prime records from the post-mutation candidate state in
`candidate_tracked_paths`, plus files Prime explicitly names in
`candidate_untracked_paths`, the candidate untracked paths list. Prime does not
infer untracked membership from a directory scan. Prime queries the
source-control ignore state at the same post-mutation point. Untracked paths
marked ignored, and untracked paths not listed in the explicit manifest, stay
outside the candidate. This rule excludes generated outputs without a
host-specific filename allowlist. Prime rejects an explicit candidate path that
names an ignored output. The manifest excludes `.git/` and the Patton mission
ledger (`.patton/ledger/`). Prime rejects symlinks in the manifest instead of
following them. Prime filters excluded untracked paths before this symlink
check. A symlink selected by `candidate_tracked_paths` or
`candidate_untracked_paths`, including a symlink ancestor, is rejected. The
manifest scope and the `allowed_paths` mutation boundary
serve separate controls: a behavior-affecting file outside `allowed_paths` may
still invalidate the candidate digest, while a worker may not mutate it. A
behavior-affecting file outside the mutation allowlist therefore remains in
the candidate-relevant snapshot.

A source-control gitlink (submodule entry) is not a regular candidate file.
Prime rejects a selected gitlink before digesting it. The digest does not
serialize a gitlink object ID or follow the linked repository.

Canonical ignore input consists of repository-controlled ignore rules only.
Clone-local rules and user-global rules do not affect the candidate manifest.
Hosts that cannot isolate ambient ignore configuration must use the explicit
manifest and record the repository-controlled ignore result. An explicit
non-ignored entry remains included regardless of its filename, while an
explicit entry marked ignored is rejected.

An ignored untracked behavior-affecting path cannot be silently excluded as
generated output. This schema has no inclusion form for ignored behavior inputs,
so Prime blocks candidate lock until a future schema defines one. Ignored
generated outputs remain excluded.

Prime classifies every non-ignored untracked path that can affect the objective
or verification. Prime includes a behavior-affecting path in
`candidate_untracked_paths`, or records an explicit non-candidate output
classification in `non_candidate_untracked_paths` in the mission ledger. Prime
discovers and records the complete post-mutation non-ignored untracked set.
Every discovered path appears exactly once in either list. Silent omission
blocks candidate lock.

Each `candidate_untracked_paths` entry has exactly two fields: `path`, a
nonempty lossless path token, and `executable`, an integer `0` or `1`. Prime
records these entries from the post-mutation candidate state and does not infer
untracked membership from a directory scan.
`non_candidate_untracked_paths` is a sorted, unique list of scalar lossless path
tokens. The candidate and non-candidate lists partition the complete discovered
non-ignored untracked set with no overlap or omission.

The `candidate_tracked_paths` list equals the complete post-mutation tracked
regular-file inventory, subject to the named exclusions `.git/` and
`.patton/ledger/`. Prime rejects an omitted or extra tracked regular file.
Prime records this inventory from post-mutation source-control state. A
tracked deletion removes its path from the manifest. A tracked addition enters
the manifest. A generated file that remains tracked in the post-mutation state
stays in the manifest and receives normal verification; generated status
excludes only untracked output. Prime records
`candidate_untracked_paths` at the same point and locks both membership sets
with the candidate identity. The Verifier recomputes membership before and
after checks. A post-lock mutation to the locked manifest makes the candidate
stale only when it changes locked manifest membership, bytes, or executable
mode. A post-lock new non-ignored untracked path invalidates the lock pending
classification, even when it appears to be generated. Only a
repository-ignored generated output remains digest-neutral without post-lock
reclassification. A classified non-candidate output remains outside the digest
only after Prime relocks the candidate. A selected path
addition or deletion, or a change to locked bytes or executable mode, makes
Prime reject the candidate and reopen the mission.

The [outer lifecycle and canonical phase sequence](../SKILL.md#lifecycle) govern
these safety rules. This reference defines operational limits, ownership
controls, candidate snapshot rules, and recovery requirements used within that
sequence.

Prime normalizes each relative path to `/` separators and sorts paths by their
lossless path tokens. A path token percent-encodes each raw path byte as ASCII,
leaving only unreserved ASCII bytes and `/` separators. Hosts with Unicode-only
path APIs encode Unicode scalar values as UTF-8 before percent-encoding; hosts
with raw filename bytes preserve those bytes, including non-UTF-8 names. For
each explicit untracked entry, Prime requires a nonempty relative token with
no `.` or `..` component, no absolute prefix, no encoded `/` or `\\`, and only
canonical uppercase percent escapes for bytes outside the unreserved set.
Prime rejects duplicate untracked tokens and any token that overlaps
`candidate_tracked_paths`. For
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
revision, Prime sets `candidate_revision = "candidate:" + content_digest` as
the deterministic fallback.

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
inconclusive, Prime records unresolved disagreement and asks the human to choose
recovery, risk acceptance without verification, or abandonment. Recovery opens
a bounded mission and keeps the candidate `blocked` and `unverified`. Risk
acceptance records the accepted risk without marking the candidate verified,
keeps the candidate `blocked` and `unverified`, and keeps the irreversible gate
closed. Abandonment closes the attempt without success and keeps the candidate
`blocked` and `unverified`. A human cannot mark an inconclusive
candidate verified or authorize an irreversible action. Prime does not use a
worker vote or automated host-only decision as an approval. The irreversible
gate stays closed until independent verification resolves the evidence and the
explicit approval gate is satisfied.

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
